"""Replaceable Clang DumpCFG adapter, not a whole-program/LibTooling extractor.

Only information present in the dump is imported.  Block numbering and function
IDs must be interpreted within their build / translation-unit provenance; they
are not stable source identities across builds.  Unknown and explicitly
unreachable successors are retained but never turned into ordinary edges.
"""

from __future__ import annotations

import copy
import hashlib
import re


_BLOCK = re.compile(r"^\s*\[(B\d+)(?:\s+\(([^)]+)\))?\]\s*$")
_EXPR = re.compile(r"^\s+(\d+):\s*(.*)$")
_RELATION = re.compile(r"^\s+(Preds|Succs)\s+\((\d+)\):\s*(.*)$")
_SUCCESSOR = re.compile(r"(?:B\d+|NULL)(?:\([^)]*\))?")
_TOKEN = re.compile(r"\b[A-Za-z_]\w*\b")
_STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')


def _function_name(signature: str) -> str:
    # DumpCFG emits the ordinary C functions used by this prototype on one line.
    # Unsupported headers are an error rather than a source-code guess.
    match = re.search(r"\b([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\(", signature)
    if not match:
        raise ValueError(f"Cannot identify function in CFG header: {signature!r}")
    return match.group(1)


def _relations(raw: str, expected: int, direction: str) -> list[dict]:
    matches = list(_SUCCESSOR.finditer(raw))
    residue = _SUCCESSOR.sub("", raw).strip()
    result = []
    for index, match in enumerate(matches):
        token = match.group()
        block = re.match(r"B\d+", token)
        annotation = token[token.find("(") + 1 : -1] if "(" in token else None
        kind = "normal"
        if "Unreachable" in (annotation or ""):
            kind = "unreachable"
        elif block is None or annotation:
            kind = "unknown"
        result.append({"clang_id": block.group() if block else None,
                       "raw": token, "ordinal": index, "kind": kind,
                       "annotation": annotation})
    if residue or len(matches) != expected:
        result.append({"clang_id": None, "raw": raw, "kind": "unknown",
                       "ordinal": len(result), "annotation":
                       f"Unparsed {direction} list or count mismatch: expected {expected}"})
    return result


def _parse_function(signature: str, body: list[str], occurrence: int) -> dict:
    name = _function_name(signature)
    digest = hashlib.sha256(signature.encode()).hexdigest()[:12]
    function_id = f"{name}:{digest}"
    if occurrence:
        function_id += f":occurrence{occurrence}"
    nodes = []
    node = None
    current_expression = None
    for line in body:
        match = _BLOCK.match(line)
        if match:
            clang_id, tag = match.groups()
            node = {"id": f"{function_id}:{clang_id}", "clang_id": clang_id,
                    "label": f"{clang_id}" + (f" ({tag})" if tag else ""),
                    "sites": [], "entry": tag == "ENTRY", "exit": tag == "EXIT",
                    "noreturn": tag == "NORETURN", "tag": tag,
                    "text": [], "expressions": [], "terminator": None,
                    "predecessors": [], "successors": []}
            nodes.append(node)
            current_expression = None
            continue
        if node is None or not line.strip():
            continue
        relation = _RELATION.match(line)
        if relation:
            direction, count, raw = relation.groups()
            key = "successors" if direction == "Succs" else "predecessors"
            node[key].extend(_relations(raw, int(count), direction))
            current_expression = None
            continue
        expression = _EXPR.match(line)
        if expression:
            current_expression = {"index": int(expression.group(1)),
                                  "text": expression.group(2)}
            node["expressions"].append(current_expression)
        elif re.match(r"^\s+T:", line):
            node["terminator"] = line.split("T:", 1)[1].strip()
            current_expression = None
        elif current_expression is not None:
            # Clang can render a GNU statement expression on several lines,
            # including an unindented closing `});`.
            current_expression["text"] += "\n" + line
        node["text"].append(line)

    by_clang_id = {node["clang_id"]: node for node in nodes}
    if len(by_clang_id) != len(nodes):
        raise ValueError(f"Duplicate CFG block in {signature}")
    if sum(n["entry"] for n in nodes) != 1 or sum(n["exit"] for n in nodes) != 1:
        raise ValueError(f"CFG must contain one ENTRY and one EXIT: {signature}")
    edges, uncertainties = [], []
    for node in nodes:
        node["text"] = "\n".join(node["text"])
        for direction in ("predecessors", "successors"):
            for relation in node[direction]:
                target = by_clang_id.get(relation["clang_id"])
                relation["target"] = target["id"] if target else None
                if relation["kind"] == "normal" and target is None:
                    relation["kind"] = "unknown"
                    relation["annotation"] = "Referenced block is absent from dump"
        for successor in node["successors"]:
            target = successor["target"]
            if successor["kind"] == "normal":
                edges.append({"source": node["id"], "target": target,
                              "ordinal": successor["ordinal"]})
            else:
                uncertainties.append({"source": node["id"], "target": target,
                                      "kind": successor["kind"] + "_successor",
                                      "raw": successor["raw"],
                                      "affects_completeness": successor["kind"] != "unreachable"})
    return {"id": function_id, "name": name, "signature": signature,
            "nodes": nodes, "edges": edges, "uncertainties": uncertainties,
            "adapter": "clang-textual-dumpcfg-v1"}


def parse_dump(text: str) -> list[dict]:
    """Parse the C function CFGs in Clang's mixed diagnostic/DumpCFG output.

    Each ENTRY starts a function.  Its immediately preceding nonblank line is
    the emitted signature.  Function identities are unique within this dump;
    callers must retain the TU/build provenance when combining dumps.
    """
    lines = text.splitlines()
    starts = []
    for index, line in enumerate(lines):
        block = _BLOCK.match(line)
        if block and block.group(2) == "ENTRY":
            header = index - 1
            while header >= 0 and not lines[header].strip():
                header -= 1
            if header < 0:
                raise ValueError("CFG ENTRY has no function header")
            signature = lines[header].strip()
            _function_name(signature)
            starts.append((header, index, signature))
    functions, seen = [], {}
    for position, (_, start, signature) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        body = lines[start:end]
        # Diagnostics may follow the final graph.  They are not CFG expressions.
        for index, line in enumerate(body):
            if re.match(r"^.*:\d+:\d+: (?:warning|error|note):", line) or re.match(
                r"^\d+ (?:warnings?|errors?) generated\.", line
            ):
                body = body[:index]
                break
        occurrence = seen.get(signature, 0)
        seen[signature] = occurrence + 1
        functions.append(_parse_function(signature, body, occurrence))
    return functions


def build_model(text: str, function: str, sites: list[dict], build_id: str,
                provenance: dict) -> dict:
    """Attach explicit probe markers and bound a work.begin/work.end region.

    Markers are exact identifier tokens in numbered CFG expressions (quoted
    strings do not count).  Every region site must map once.  The fixture's
    lifecycle/queue sites belong to other functions and are explicitly excluded.
    Missing or repeated region markers are errors.
    """
    matches = [item for item in parse_dump(text)
               if function in (item["name"], item["signature"], item["id"])]
    if len(matches) != 1:
        raise ValueError(f"Expected one CFG for {function!r}; found {len(matches)}")
    parsed = matches[0]
    if not build_id:
        raise ValueError("build_id is required")
    sites = [site for site in sites if site.get("kind") not in ("lifecycle", "queue")]
    if len({site["id"] for site in sites}) != len(sites):
        raise ValueError("Duplicate site IDs")
    if len({site["symbol"] for site in sites}) != len(sites):
        raise ValueError("Duplicate site symbols")
    by_symbol = {site["symbol"]: site for site in sites}
    occurrences = {symbol: [] for symbol in by_symbol}
    for node in parsed["nodes"]:
        for expression in sorted(node["expressions"], key=lambda expr: expr["index"]):
            tokens = _TOKEN.findall(_STRING.sub("", expression["text"]))
            for token in tokens:
                if token in by_symbol:
                    occurrences[token].append((node, expression["index"]))
                    node["sites"].append(by_symbol[token]["id"])
    mapped_sites = []
    for site in sites:
        hits = occurrences[site["symbol"]]
        if len(hits) != 1:
            raise ValueError(f"Site {site['id']!r} ({site['symbol']}) must map exactly once; found {len(hits)}")
        node, expression_index = hits[0]
        mapped_sites.append({**copy.deepcopy(site), "node": node["id"],
                             "expression_index": expression_index})
    site_by_id = {site["id"]: site for site in mapped_sites}
    for anchor in ("work.begin", "work.end"):
        if anchor not in site_by_id or not site_by_id[anchor].get("anchor"):
            raise ValueError(f"Required anchor missing or not marked anchor: {anchor}")
    return {"schema_version": 1, "build_id": build_id,
            "provenance": {**copy.deepcopy(provenance), "adapter": parsed["adapter"],
                           "cfg_sha256": hashlib.sha256(text.encode()).hexdigest()},
            "function": parsed["name"], "function_id": parsed["id"],
            "signature": parsed["signature"], "nodes": parsed["nodes"],
            "edges": parsed["edges"], "uncertainties": parsed["uncertainties"],
            "region": {"entry": site_by_id["work.begin"]["node"],
                       "exit": site_by_id["work.end"]["node"]},
            "sites": mapped_sites}
