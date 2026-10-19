"""Versioned execution-structure boundary, independent of the local CFG model.

These helpers validate declarations and provenance, not the truth of an
extractor's analysis or a trace producer's measurements. No static relation is
converted into an observed event. See CONTRACT.md for the JSON contract.
"""

import copy
from hashlib import sha256
import json
import math
from pathlib import PurePosixPath
import re


IDENTITY_FIELDS = {
    "program": {"origin", "revision"},
    "target": {"program_id", "name"},
    "build": {"target_id", "configuration"},
    "compilation": {"build_id", "source", "directory", "compiler", "arguments", "input_digest"},
    "function": {"compilation_id", "symbol", "linkage"},
    "location": {"compilation_id", "path", "content_digest", "start_offset", "end_offset"},
    "thread_role": {"build_id", "key"},
    "queue": {"build_id", "key"},
    "callback": {"compilation_id", "key"},
}
RELATION_TYPES = {
    "calls": ({"function"}, {"function"}),
    "spawns": ({"function"}, {"thread_role"}),
    "starts": ({"thread_role"}, {"function"}),
    "submits": ({"function"}, {"queue"}),
    "dequeues": ({"queue"}, {"function"}),
    "invokes": ({"function", "thread_role"}, {"callback"}),
    "binds": ({"callback"}, {"function"}),
}


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - set(optional):
        raise ValueError(f"expected object fields {sorted(required)}; optional {sorted(optional)}")


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonempty string")
    return value


def _choice(value, choices):
    if _text(value) not in choices:
        raise ValueError(f"unsupported value {value!r}; expected {sorted(choices)}")
    return value


def _array(value):
    if not isinstance(value, list):
        raise ValueError("expected array")
    return value


def _natural(value):
    if type(value) is not int or value < 0:
        raise ValueError("expected nonnegative integer")
    return value


def _json(value):
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        for child in value.values():
            _json(child)
    elif isinstance(value, list):
        for child in value:
            _json(child)
    elif value is None or type(value) in (str, bool, int):
        pass
    elif type(value) is float and math.isfinite(value):
        pass
    else:
        raise ValueError("expected finite JSON data")


def _identifier(kind, payload):
    _json(payload)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    return kind + ":" + sha256(encoded).hexdigest()


def _path(value, directory=False):
    _text(value)
    if ("\\" in value or "\x00" in value or PurePosixPath(value).is_absolute() or
            ".." in PurePosixPath(value).parts or str(PurePosixPath(value)) != value or
            (value == "." and not directory)):
        raise ValueError("expected canonical root-relative POSIX path")


def _digest(value):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", _text(value)):
        raise ValueError("expected sha256:<64 lowercase hex digits>")


def _identity(kind, identity):
    _choice(kind, IDENTITY_FIELDS)
    _fields(identity, IDENTITY_FIELDS[kind])
    _json(identity)
    for key, value in identity.items():
        if key == "configuration":
            if not isinstance(value, dict) or not value:
                raise ValueError("build configuration must be a nonempty JSON object")
        elif key == "arguments":
            if not _array(value) or any(not isinstance(arg, str) for arg in value):
                raise ValueError("arguments must be a nonempty ordered string array")
        elif key.endswith("_offset"):
            _natural(value)
        elif key in ("input_digest", "content_digest"):
            _digest(value)
        elif key in ("source", "path", "directory"):
            _path(value, directory=key == "directory")
        else:
            _text(value)
    if kind == "function":
        _choice(identity["linkage"], {"internal", "external", "none"})
    if kind == "location" and identity["end_offset"] < identity["start_offset"]:
        raise ValueError("location end precedes start")


def make_entity(kind, identity, *, label=None):
    """Create a scoped identity; display labels never determine entity IDs."""
    _identity(kind, identity)
    result = {"id": _identifier(kind, identity), "kind": kind, "identity": copy.deepcopy(identity)}
    if label is not None:
        result["label"] = _text(label)
    return result


def make_relation(kind, source, target, resolution, evidence):
    """Construct a static claim. Validate references in validate_structure()."""
    payload = copy.deepcopy({"kind": kind, "source": source, "target": target,
                             "resolution": resolution, "evidence": evidence})
    _relation_shape(payload)
    return {"id": _identifier("relation", payload), **payload}


def _relation_shape(relation):
    _fields(relation, {"kind", "source", "target", "resolution", "evidence"})
    _choice(relation["kind"], RELATION_TYPES)
    resolution = _choice(relation["resolution"], {"resolved", "candidate", "unresolved"})
    endpoints = (relation["source"], relation["target"])
    if resolution == "unresolved":
        if sum(endpoint is None for endpoint in endpoints) != 1:
            raise ValueError("unresolved relation requires exactly one null endpoint")
    elif any(endpoint is None for endpoint in endpoints):
        raise ValueError("resolved/candidate relation requires both endpoints")
    for endpoint in endpoints:
        if endpoint is not None:
            _text(endpoint)
    evidence = relation["evidence"]
    _fields(evidence, {"location_ids", "conditions", "method", "reason"})
    locations = _array(evidence["location_ids"])
    for location in locations:
        _text(location)
    if not locations or len(set(locations)) != len(locations):
        raise ValueError("static evidence needs distinct source locations")
    for condition in _array(evidence["conditions"]):
        _text(condition)
    _fields(evidence["method"], {"name", "version"})
    for value in evidence["method"].values():
        _text(value)
    if resolution == "resolved":
        if evidence["reason"] is not None:
            raise ValueError("resolved relation reason must be null")
    else:
        _text(evidence["reason"])


def _structure_payload(entities, relations):
    return {"entities": sorted(entities, key=lambda row: row["id"]),
            "relations": sorted(relations, key=lambda row: row["id"])}


def make_structure(entities, relations):
    payload = copy.deepcopy(_structure_payload(entities, relations))
    structure = {"schema_version": 1, "kind": "execution_structure",
                 "structure_id": _identifier("structure", payload), **payload}
    validate_structure(structure)
    return structure


def _header(value, kind, fields):
    _fields(value, {"schema_version", "kind"} | fields)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != kind:
        raise ValueError("unsupported execution document kind/schema_version")
    _json(value)


def _index(rows):
    result = {}
    for row in _array(rows):
        if not isinstance(row, dict):
            raise ValueError("expected record")
        key = _text(row.get("id"))
        if key in result:
            raise ValueError("duplicate ID")
        result[key] = row
    return result


def _ref(index, key, kinds):
    row = index.get(_text(key))
    if row is None or row["kind"] not in kinds:
        raise ValueError(f"unknown reference or wrong entity type: {key}")
    return row


def _build(index, entity):
    kind, identity = entity["kind"], entity["identity"]
    if kind == "build":
        return entity["id"]
    if kind in ("compilation", "thread_role", "queue"):
        return identity["build_id"]
    if kind in ("function", "location", "callback"):
        return index[identity["compilation_id"]]["identity"]["build_id"]
    return None


def _relation_build(index, relation):
    # Shape/reference validation guarantees at least one well-typed endpoint.
    endpoint = relation["source"] if relation["source"] is not None else relation["target"]
    return _build(index, index[endpoint])


def validate_structure(structure):
    """Reject malformed/dangling/ill-typed/cross-build static declarations.

    Return unresolved and candidate relation IDs as diagnostics. These are valid
    structure, not invalid references and not observed execution.
    """
    _header(structure, "execution_structure", {"structure_id", "entities", "relations"})
    entities = _index(structure["entities"])
    relations = _index(structure["relations"])
    for entity in entities.values():
        _fields(entity, {"id", "kind", "identity"}, {"label"})
        _identity(entity["kind"], entity["identity"])
        if entity["id"] != _identifier(entity["kind"], entity["identity"]):
            raise ValueError("entity ID does not match identity")
        if "label" in entity:
            _text(entity["label"])
    for entity in entities.values():
        for field, expected in (("program_id", "program"), ("target_id", "target"),
                                ("build_id", "build"), ("compilation_id", "compilation")):
            if field in entity["identity"]:
                _ref(entities, entity["identity"][field], {expected})
    diagnostics = []
    for relation in relations.values():
        _fields(relation, {"id", "kind", "source", "target", "resolution", "evidence"})
        payload = {key: value for key, value in relation.items() if key != "id"}
        _relation_shape(payload)
        if relation["id"] != _identifier("relation", payload):
            raise ValueError("relation ID does not match claim")
        source_kinds, target_kinds = RELATION_TYPES[relation["kind"]]
        known_endpoints = [_ref(entities, relation[field], kinds)
                           for field, kinds in (("source", source_kinds), ("target", target_kinds))
                           if relation[field] is not None]
        build = _relation_build(entities, relation)
        for endpoint in known_endpoints:
            if _build(entities, endpoint) != build:
                raise ValueError("relation crosses builds")
        for location_id in relation["evidence"]["location_ids"]:
            location = _ref(entities, location_id, {"location"})
            if _build(entities, location) != build:
                raise ValueError("relation evidence crosses builds")
        if relation["resolution"] != "resolved":
            diagnostics.append({"relation_id": relation["id"], "resolution": relation["resolution"],
                                "reason": relation["evidence"]["reason"]})
    payload = _structure_payload(structure["entities"], structure["relations"])
    if structure["structure_id"] != _identifier("structure", payload):
        raise ValueError("structure_id does not match content")
    return sorted(diagnostics, key=lambda row: row["relation_id"])


def _event_key(reference):
    _fields(reference, {"thread", "thread_seq"})
    return _text(reference["thread"]), _natural(reference["thread_seq"])


def validate_run(structure, run, trace):
    """Validate run evidence against its exact static structure and raw trace.

    Direct relation observations require an explicit relation_id in the raw
    event. Site-only events can support inferences, never direct edge claims.
    This checks provenance, not whether an inference algorithm is sound.
    """
    validate_structure(structure)
    _header(run, "execution_run", {"structure_id", "build_id", "run_id", "plan_id", "trace_ref",
                                    "observations", "inferences"})
    if run["structure_id"] != structure["structure_id"]:
        raise ValueError("run structure_id mismatch")
    entities, relations = _index(structure["entities"]), _index(structure["relations"])
    _ref(entities, run["build_id"], {"build"})
    for field in ("run_id", "plan_id", "trace_ref"):
        _text(run[field])
    if not isinstance(trace, dict) or type(trace.get("schema_version")) is not int or trace["schema_version"] != 1:
        raise ValueError("unsupported raw trace schema_version")
    for field in ("run_id", "build_id", "plan_id"):
        if trace.get(field) != run[field]:
            raise ValueError(f"raw trace {field} mismatch")
    events = {}
    for event in _array(trace.get("events")):
        if not isinstance(event, dict):
            raise ValueError("expected raw event object")
        for field in ("run_id", "build_id", "plan_id"):
            if field in event and event[field] != run[field]:
                raise ValueError(f"raw event {field} mismatch")
        key = _event_key({"thread": event.get("thread"), "thread_seq": event.get("thread_seq")})
        if key in events:
            raise ValueError("duplicate raw thread/thread_seq event reference")
        events[key] = event

    def relation_ref(key):
        relation = relations.get(_text(key))
        if relation is None:
            raise ValueError("unknown run relation reference")
        if _relation_build(entities, relation) != run["build_id"]:
            raise ValueError("run relation crosses builds")
        return relation

    seen = set()
    for observation in _array(run["observations"]):
        _fields(observation, {"relation_id", "event_ref"})
        relation = relation_ref(observation["relation_id"])
        key = _event_key(observation["event_ref"])
        if key not in events or events[key].get("relation_id") != relation["id"]:
            raise ValueError("direct observation requires matching raw relation event")
        if relation["resolution"] == "unresolved":
            raise ValueError("unresolved endpoint cannot be a directly observed connection")
        claim = (relation["id"], key)
        if claim in seen:
            raise ValueError("duplicate observation")
        seen.add(claim)
    seen = set()
    for inference in _array(run["inferences"]):
        _fields(inference, {"relation_id", "basis", "event_refs", "reason"})
        relation = relation_ref(inference["relation_id"])
        basis = _choice(inference["basis"], {"candidate_only", "unique_under_model"})
        _text(inference["reason"])
        keys = [_event_key(reference) for reference in _array(inference["event_refs"])]
        if len(keys) != len(set(keys)) or any(key not in events for key in keys):
            raise ValueError("invalid inference event references")
        if basis == "unique_under_model":
            if relation["resolution"] != "resolved" or not keys:
                raise ValueError("unresolved/candidate connection cannot earn model uniqueness")
            if trace.get("complete") is not True or type(trace.get("dropped")) is not int or trace["dropped"] != 0:
                raise ValueError("incomplete trace cannot earn model uniqueness")
        if relation["id"] in seen:
            raise ValueError("duplicate inference for relation in run")
        seen.add(relation["id"])


def accumulate_execution(structure, run_traces):
    """Accumulate (execution_run, raw_trace) pairs without merging histories.

    Static relations alone add no support. Different builds require an explicit
    future mapping, even when they coexist in one structure document.
    """
    validate_structure(structure)
    runs, support, seen, builds = [], {}, set(), set()
    for run, trace in run_traces:
        validate_run(structure, run, trace)
        if run["run_id"] in seen:
            raise ValueError("duplicate run_id would conflate histories")
        seen.add(run["run_id"])
        builds.add(run["build_id"])
        if len(builds) > 1:
            raise ValueError("cross-build evidence requires explicit mapping")
        runs.append({key: run[key] for key in ("run_id", "build_id", "plan_id", "trace_ref")})
        for observation in run["observations"]:
            support.setdefault(observation["relation_id"], []).append({
                "run_id": run["run_id"], "plan_id": run["plan_id"], "basis": "direct_observation",
                "event_refs": [copy.deepcopy(observation["event_ref"])]})
        for inference in run["inferences"]:
            support.setdefault(inference["relation_id"], []).append({
                "run_id": run["run_id"], "plan_id": run["plan_id"], "basis": inference["basis"],
                "event_refs": copy.deepcopy(inference["event_refs"]), "reason": inference["reason"]})
    return {"schema_version": 1, "kind": "execution_evidence", "structure_id": structure["structure_id"],
            "build_id": next(iter(builds), None), "runs": runs,
            "relations": [{"relation_id": key, "support": value} for key, value in sorted(support.items())],
            "merges_timelines": False}
