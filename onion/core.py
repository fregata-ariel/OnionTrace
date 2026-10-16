"""Evidence-preserving reconstruction and budgeted probe selection.

This deliberately small prototype enumerates bounded *static* region paths.  It
does not recover unobserved history, use diagnostic results to infer branches,
or impose the collector's serialization order on concurrent threads.
"""

from collections import Counter, defaultdict
from hashlib import sha256
from itertools import combinations
import json
import math


MAX_PATHS = 4096
MAX_DEPTH = 512
MAX_EXPANSIONS = 100000
ALWAYS_SITES = {
    "main.begin", "thread.spawn", "thread.begin", "queue.enqueue",
    "queue.dequeue", "thread.end", "main.end",
}


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate(model, plan):
    if model.get("schema_version") != 1 or plan.get("schema_version") != 1:
        raise ValueError("unsupported model/plan schema_version")
    if not model.get("build_id") or model["build_id"] != plan.get("build_id"):
        raise ValueError("model and plan build_id mismatch")
    if not plan.get("plan_id"):
        raise ValueError("plan_id is required")
    nodes = {n["id"]: n for n in model["nodes"]}
    sites = {s["id"]: s for s in model["sites"]}
    if len(nodes) != len(model["nodes"]) or len(sites) != len(model["sites"]):
        raise ValueError("duplicate node/site IDs")
    for node in nodes.values():
        if any(s not in sites for s in node.get("sites", [])):
            raise ValueError("node contains an unmapped site")
    enabled = plan["enabled_sites"]
    anchors = plan["anchor_sites"]
    if len(set(enabled)) != len(enabled) or len(set(anchors)) != len(anchors):
        raise ValueError("duplicate plan enabled/anchor sites")
    if not set(enabled) <= sites.keys() or not set(anchors) <= set(enabled):
        raise ValueError("unknown enabled site or disabled anchor")
    required = {s["id"] for s in sites.values() if s.get("anchor")}
    if not required <= set(anchors):
        raise ValueError("plan must retain all model anchors")
    for edge in model["edges"]:
        if edge["source"] not in nodes or edge["target"] not in nodes:
            raise ValueError("edge refers to an unknown node")
    for name in ("entry", "exit"):
        if model["region"][name] not in nodes:
            raise ValueError("unknown region boundary")
    return nodes, sites


def _enumerate(model, nodes):
    adjacency = defaultdict(list)
    reverse = defaultdict(list)
    for edge in model["edges"]:
        if edge["target"] not in adjacency[edge["source"]]:
            adjacency[edge["source"]].append(edge["target"])
            reverse[edge["target"]].append(edge["source"])
    end = model["region"]["exit"]
    can_reach_end, todo = set(), [end]
    while todo:
        node = todo.pop()
        if node not in can_reach_end:
            can_reach_end.add(node)
            todo.extend(reverse[node])
    reasons = []
    for uncertainty in model.get("uncertainties", []):
        if not isinstance(uncertainty, dict) or uncertainty.get("affects_completeness", True):
            reasons.append("model has unresolved control-flow successors")
            break
    paths = []
    todo = [(model["region"]["entry"], [])]
    expansions = 0
    while todo:
        if expansions >= MAX_EXPANSIONS:
            reasons.append("graph expansion limit reached")
            break
        expansions += 1
        current, prefix = todo.pop()
        if current in prefix:
            reasons.append("cycle encountered: repeated paths are not exhaustively enumerated")
            continue
        path = prefix + [current]
        if len(path) > MAX_DEPTH:
            reasons.append("path depth limit reached")
            continue
        if current == end:
            paths.append(path)
            if len(paths) >= MAX_PATHS and todo:
                reasons.append("path count limit reached")
                break
            continue
        if current not in can_reach_end:
            # An entered region can fail to return. Do not claim completeness
            # from only the paths that reach the selected closing anchor.
            reasons.append("a reachable region path does not reach its exit")
            continue
        for successor in reversed(adjacency[current]):
            todo.append((successor, path))
    if not paths:
        reasons.append("no complete entry-to-exit path enumerated")
    reasons = list(dict.fromkeys(reasons))
    return paths, {
        "complete": not reasons, "path_count": len(paths), "reasons": reasons,
        "limits": {"max_paths": MAX_PATHS, "max_depth": MAX_DEPTH,
                   "max_expansions": MAX_EXPANSIONS, "repeated_nodes_per_path": 0},
        "expansions": expansions,
    }


def _path_sites(path, nodes, enabled=None):
    return tuple(site for node in path for site in nodes[node].get("sites", [])
                 if enabled is None or site in enabled)


def _subsequence(needle, haystack):
    position = 0
    for value in haystack:
        if position < len(needle) and value == needle[position]:
            position += 1
    return position == len(needle)


def _thread_order(events):
    by_thread = defaultdict(list)
    for event in events:
        by_thread[str(event.get("thread", "<missing>"))].append(event)
    ordered, reliable, warnings = {}, {}, []
    for thread, group in by_thread.items():
        values = [e.get("thread_seq") for e in group]
        good = all(_integer(n) and n >= 0 for n in values)
        good = good and len(values) == len(set(values))
        if good:
            group = sorted(group, key=lambda e: e["thread_seq"])
            sequence = [e["thread_seq"] for e in group]
            good = (sequence[0] in (0, 1) and
                    all(b == a + 1 for a, b in zip(sequence, sequence[1:])))
        if thread == "<missing>":
            good = False
        ordered[thread], reliable[thread] = group, good
        if not good:
            warnings.append(f"thread {thread}: missing, duplicated, or discontinuous thread_seq")
    return ordered, reliable, warnings


def _event_view(event, phase=None):
    result = {k: event[k] for k in ("seq", "thread", "thread_seq", "timestamp_ns", "site", "job", "value", "queue")
              if k in event}
    if phase:
        result["phase"] = phase
    return result


def _transfers(trace):
    groups = defaultdict(lambda: {"enqueue_events": [], "dequeue_events": []})
    for event in trace["events"]:
        if event.get("site") in ("queue.enqueue", "queue.dequeue"):
            key = (event.get("job"), event.get("queue", "<unspecified>"))
            direction = "enqueue" if event["site"] == "queue.enqueue" else "dequeue"
            phase = "before_publish_under_queue_lock" if direction == "enqueue" else "after_acquire"
            groups[key][direction + "_events"].append(_event_view(event, phase))
    transfers = []
    for (job, queue), group in sorted(groups.items(), key=lambda p: (str(p[0][0]), str(p[0][1]))):
        enqueues, dequeues = group["enqueue_events"], group["dequeue_events"]
        elapsed = None
        if len(enqueues) > 1 or len(dequeues) > 1:
            status = "ambiguous"
        elif not enqueues:
            status = "missing_enqueue"
        elif not dequeues:
            status = "missing_dequeue"
        else:
            status = "paired"
            start, end = enqueues[0].get("timestamp_ns"), dequeues[0].get("timestamp_ns")
            if trace.get("clock") == "CLOCK_MONOTONIC" and _integer(start) and _integer(end) and end >= start:
                elapsed = end - start
            else:
                status = "paired_timestamp_uncertain"
        transfers.append({"run_id": trace["run_id"], "job": job, "queue": queue,
                          "status": status, **group, "elapsed_ns": elapsed,
                          "phase_semantics": "fixture enqueue before publish; dequeue after acquire; interval is not pure queue residence"})
    return transfers


def reconstruct(model, plan, trace):
    """Reconstruct candidate paths without treating missing evidence as absence.

    Invalid build/plan identity raises ValueError. Missing/incomplete evidence
    remains usable for positive constraints, but cannot earn ``unique`` status.
    """
    nodes, sites = _validate(model, plan)
    if trace.get("schema_version") != 1:
        raise ValueError("unsupported trace schema_version")
    if trace.get("build_id") != model["build_id"]:
        raise ValueError("trace build_id mismatch")
    if trace.get("plan_id") != plan["plan_id"]:
        raise ValueError("trace plan_id mismatch")
    if not isinstance(trace.get("run_id"), str) or not trace["run_id"]:
        raise ValueError("nonempty trace run_id required")
    if not isinstance(trace.get("events"), list):
        raise ValueError("trace events must be a list")
    for event in trace["events"]:
        for field in ("run_id", "build_id", "plan_id"):
            if field in event and event[field] != trace[field]:
                raise ValueError(f"event {field} mismatch")
    paths, enumeration = _enumerate(model, nodes)
    streams = [_path_sites(p, nodes) for p in paths]
    warnings = []
    enabled = set(plan["enabled_sites"])
    manifest = trace.get("enabled_sites", [])
    manifest_matches = (isinstance(manifest, list) and len(manifest) == len(set(manifest))
                        and set(manifest) == enabled)
    global_reasons = []
    if not manifest_matches:
        global_reasons.append("enabled-site manifest differs from plan")
    if trace.get("complete") is not True:
        global_reasons.append("trace is incomplete")
    if not _integer(trace.get("dropped")) or trace["dropped"] != 0:
        global_reasons.append("events were dropped or drop count is unknown")
    ordered, reliable, thread_warnings = _thread_order(trace["events"])
    warnings.extend(thread_warnings)
    if thread_warnings:
        global_reasons.append("thread-local event continuity is uncertain")
    if any(e.get("site") not in sites and e.get("site") not in ALWAYS_SITES
           for e in trace["events"]):
        global_reasons.append("trace contains unknown event sites")
    if any(e.get("site") in sites and e["site"] not in enabled for e in trace["events"]):
        global_reasons.append("trace contains a region event disabled in its plan")
    warnings.extend(global_reasons)
    warnings.extend(enumeration["reasons"])
    job_ids = {e.get("job") for e in trace["events"]
               if _integer(e.get("job")) and e["job"] >= 0}
    jobs = []
    for job_id in sorted(job_ids):
        by_thread = {}
        for thread, events in ordered.items():
            selected = [e for e in events if e.get("job") == job_id and e.get("site") in sites]
            if selected:
                by_thread[thread] = selected
        # Concatenation is for display only. Cross-thread candidate constraints
        # below use separate subsequences, never this concatenated ordering.
        observed_events = [e for thread in sorted(by_thread) for e in by_thread[thread]]
        observed = [e["site"] for e in observed_events]
        reasons = list(global_reasons)
        if len(by_thread) != 1:
            reasons.append("region observations do not have one known executing thread")
        for anchor in plan["anchor_sites"]:
            if observed.count(anchor) != 1:
                reasons.append(f"anchor {anchor} is missing or repeated")
        if "work.begin" in observed and "work.end" in observed and observed.index("work.begin") >= observed.index("work.end"):
            reasons.append("region anchors are out of order")
        absence_usable = not reasons
        candidates = []
        for path, stream in zip(paths, streams):
            if absence_usable:
                matches = tuple(observed) == tuple(s for s in stream if s in enabled)
            else:
                # If order itself is unreliable, only occurrence multiplicity
                # is usable. Reliable per-thread sequences still constrain.
                matches = not (Counter(observed) - Counter(stream))
                matches = matches and all(
                    not reliable[thread] or _subsequence([e["site"] for e in group], stream)
                    for thread, group in by_thread.items())
            if matches:
                candidates.append(path)
        complete_evidence = absence_usable and enumeration["complete"]
        if not enumeration["complete"]:
            reasons.extend(enumeration["reasons"])
        if not candidates and complete_evidence:
            status = "inconsistent"
            reasons.append("observations match no modeled path")
        elif not complete_evidence:
            status = "incomplete"
            if not candidates:
                reasons.append("no enumerated candidate; missing paths or conflicting positive evidence remain possible")
        elif len(candidates) == 1:
            status = "unique"
        else:
            status = "ambiguous"
        elapsed = None
        begin = [e for e in observed_events if e["site"] == "work.begin"]
        end = [e for e in observed_events if e["site"] == "work.end"]
        if len(begin) == len(end) == 1 and begin[0].get("thread") == end[0].get("thread"):
            start_ns, end_ns = begin[0].get("timestamp_ns"), end[0].get("timestamp_ns")
            if trace.get("clock") == "CLOCK_MONOTONIC" and _integer(start_ns) and _integer(end_ns) and end_ns >= start_ns:
                elapsed = end_ns - start_ns
        edge_sets = [set(zip(path, path[1:])) for path in candidates]
        union = set().union(*edge_sets) if edge_sets else set()
        common = set.intersection(*edge_sets) if edge_sets and complete_evidence else set()
        edge_evidence = [
            {"source": source, "target": target,
             "evidence": ("unique_under_model" if status == "unique" else
                          "common_to_candidates" if (source, target) in common else "candidate_only")}
            for source, target in sorted(union)
        ]
        jobs.append({"job": job_id, "observed_sites": observed,
                     "observed_events": [_event_view(e) for e in observed_events],
                     "observed_order": "thread_local" if len(by_thread) == 1 else "display_grouping_only",
                     "candidate_paths": candidates, "candidate_count": len(candidates),
                     "status": status, "absence_usable": absence_usable,
                     "elapsed_ns": elapsed, "reasons": list(dict.fromkeys(reasons)),
                     "edge_evidence": edge_evidence})
    if not jobs:
        warnings.append("no job observations; no region execution can be reconstructed")
    status = ("inconsistent" if any(j["status"] == "inconsistent" for j in jobs) else
              "incomplete" if not jobs or global_reasons or not enumeration["complete"] or any(j["status"] == "incomplete" for j in jobs) else
              "ambiguous" if any(j["status"] == "ambiguous" for j in jobs) else "unique")
    return {"schema_version": 1, "run_id": trace["run_id"], "plan_id": plan["plan_id"],
            "build_id": model["build_id"], "status": status,
            "enabled_sites": list(plan["enabled_sites"]), "enumeration": enumeration,
            "jobs": jobs, "transfers": _transfers(trace),
            "warnings": list(dict.fromkeys(warnings)),
            "certainty_scope": "unique means unique within the complete static model and declared observation contract; not every basic block was directly observed"}


def recommend(model, plan, reconstruction, max_new_sites=1):
    """Greedily separate remaining path pairs under a relative cost proxy."""
    nodes, sites = _validate(model, plan)
    if reconstruction.get("build_id") != model["build_id"] or reconstruction.get("plan_id") != plan["plan_id"]:
        raise ValueError("reconstruction build/plan identity mismatch")
    if not _integer(max_new_sites) or max_new_sites < 0:
        raise ValueError("max_new_sites must be a nonnegative integer")
    enabled = set(plan["enabled_sites"])
    selected, rounds = [], []
    jobs = [j for j in reconstruction["jobs"] if j["status"] == "ambiguous"]
    blocked = reconstruction["status"] in ("incomplete", "inconsistent")
    reason = ("Incomplete or inconsistent evidence: repair trace/model coverage before certainty-based probe selection."
              if blocked else "No remaining modeled ambiguity can be separated by available sites.")
    if not blocked:
        for _ in range(max_new_sites):
            ranked = []
            for site_id, site in sites.items():
                if site_id in enabled:
                    continue
                unit_cost = site.get("estimated_cost", 1)
                if not isinstance(unit_cost, (int, float)) or isinstance(unit_cost, bool) or not math.isfinite(unit_cost) or unit_cost <= 0:
                    raise ValueError(f"site {site_id} must have positive finite estimated_cost")
                pair_gain, weighted_gain, occurrences, per_job = 0, 0.0, 0.0, []
                for job in jobs:
                    paths = job["candidate_paths"]
                    old = [_path_sites(path, nodes, enabled) for path in paths]
                    new = [_path_sites(path, nodes, enabled | {site_id}) for path in paths]
                    total_pairs = len(paths) * (len(paths) - 1) // 2
                    gain = sum(old[a] == old[b] and new[a] != new[b]
                               for a, b in combinations(range(len(paths)), 2))
                    weight = 1.0 / total_pairs if total_pairs else 0.0
                    pair_gain += gain
                    weighted_gain += gain * weight
                    occurrences += sum(_path_sites(path, nodes).count(site_id) for path in paths) / len(paths)
                    per_job.append({"job": job["job"], "newly_separated_pairs": gain,
                                    "total_pairs": total_pairs, "pair_weight": weight})
                cost = unit_cost * occurrences
                score = weighted_gain / cost if cost else 0.0
                ranked.append({"site": site_id, "pair_gain": pair_gain,
                               "weighted_gain": weighted_gain, "cost_proxy": cost,
                               "site_cost_proxy": unit_cost, "score": score,
                               "per_job": per_job, "order": site.get("order", 0)})
            ranked.sort(key=lambda c: (-c["score"], -c["weighted_gain"], c["order"], c["site"]))
            rounds.append({"selected_before_round": list(selected), "candidate_scores": ranked})
            if not ranked or ranked[0]["pair_gain"] == 0:
                break
            chosen = ranked[0]["site"]
            selected.append(chosen)
            enabled.add(chosen)
        if selected:
            reason = "Select sites that distinguish remaining candidate path pairs; retain all earlier sites and anchors."
    next_enabled = list(plan["enabled_sites"]) + selected
    rationale = {
        "reason": reason, "selected_sites": selected,
        "candidate_scores": rounds[0]["candidate_scores"] if rounds else [],
        "rounds": rounds, "source_run_id": reconstruction["run_id"],
        "cost_unit": "relative proxy, not measured nanoseconds",
        "weighting": "each ambiguous job has total pair weight 1; occurrence cost assumes uniform candidate paths, not an observed probability",
        "preserved_anchors": list(plan["anchor_sites"]),
    }
    identity = json.dumps({"parent": plan["plan_id"], "build": plan["build_id"],
                           "enabled": next_enabled, "source_run": reconstruction["run_id"]}, sort_keys=True).encode()
    return {"schema_version": 1, "plan_id": "plan-" + sha256(identity).hexdigest()[:12],
            "build_id": plan["build_id"], "parent_plan_id": plan["plan_id"],
            "enabled_sites": next_enabled, "anchor_sites": list(plan["anchor_sites"]),
            "rationale": rationale,
            "budget": {**plan.get("budget", {}), "max_new_sites": max_new_sites,
                       "cost_unit": "relative proxy, not measured nanoseconds"}}


def accumulate(reconstructions):
    """Attach run/job provenance to structural edges, never merge timelines."""
    runs, edge_support, seen = [], defaultdict(list), set()
    builds = {r["build_id"] for r in reconstructions}
    if len(builds) > 1:
        raise ValueError("cross-build accumulation requires an explicit node mapping")
    for reconstruction in reconstructions:
        run_id = reconstruction["run_id"]
        if run_id in seen:
            raise ValueError("duplicate run_id would conflate histories")
        seen.add(run_id)
        runs.append({"run_id": run_id, "build_id": reconstruction["build_id"],
                     "plan_id": reconstruction["plan_id"], "status": reconstruction["status"],
                     "job_count": len(reconstruction["jobs"])})
        for job in reconstruction["jobs"]:
            for edge in job["edge_evidence"]:
                edge_support[(edge["source"], edge["target"])].append({
                    "run_id": run_id, "job": job["job"], "plan_id": reconstruction["plan_id"],
                    "evidence": edge["evidence"]})
    return {"schema_version": 1, "build_id": next(iter(builds), None), "runs": runs,
            "edges": [{"source": source, "target": target, "support": support}
                      for (source, target), support in sorted(edge_support.items())],
            "merges_timelines": False,
            "warnings": ["Edges retain per-run/job evidence; candidate_only edges are possible paths, not observed traversal. Multiple runs do not form one historical timeline."]}
