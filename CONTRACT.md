# Onion prototype v0.1 internal contract

This prototype uses real execution of a pthread/queue fixture and Clang 18 textual CFG export. It is not an OAI runtime trace. M1/T3 adds a separate LibTooling source-extraction boundary; the v0.1 demo retains the textual adapter for comparison.

Python package: `onion`, Python >=3.10, standard library only. JSON schema_version = 1.

M1 adds a separate, versioned execution-structure boundary implemented in
`onion/execution.py`: [`docs/execution-structure.md`](docs/execution-structure.md).
It defines scoped identities, typed static relations, per-run evidence and
cross-run support. Its documents have an explicit `kind` discriminator and do
not replace or broaden the function-local CFG, Plan, Trace or Core API below.

## Model

`{schema_version, build_id, provenance, function, nodes, edges, region, sites}`.
nodes: `[{id, label, sites: [site_id], entry: bool, exit: bool}]`.
edges: `[{source, target}]` (all statically possible successors, unknown ones explicit in metadata).
region: `{entry: node_id, exit: node_id}` bounds processing of one job.
sites: array `{id, symbol, label, kind, anchor: bool, estimated_cost: number, order: integer}`.
`work.begin` / `work.end` are the region anchors; candidate site IDs `branch.slow`, `branch.fast`, `retry.ok`, `retry.fail`.
Fixture function to extract: `process_job`. Symbols: `S_WORK_BEGIN`, `S_WORK_END`, `S_BRANCH_SLOW`, `S_BRANCH_FAST`, `S_RETRY_OK`, `S_RETRY_FAIL`.
Extract exact model per build. `provenance` must state textual adapter, source hash and compiler version. Keep unmapped anchors errors, do not invent mappings.

## Plan

`{schema_version, plan_id, build_id, parent_plan_id, enabled_sites:[str], anchor_sites:[str], rationale, budget}`.
Fixture command: `fixture --run-id R --plan-id P --sites comma,separated,ids --seed N --jobs 12` prints one JSON object trace to stdout after threads joined.

## Trace

`{schema_version, run_id, build_id, plan_id, enabled_sites:[str], clock:"CLOCK_MONOTONIC", complete:bool, dropped:int, events:[...], results:[...]}`.
Events `{seq, thread, thread_seq, timestamp_ns, site, job}` with optional `value`, `queue`. Thread string `main` or `worker`. `seq` can be serialization order after collection, NOT claimed cross-thread causal order. job=-1 for lifecycle.
Thread-local bounded storage, events printed only after pthread_join. Per-thread dropped sums reported. Build ID passed as compile macro BUILD_ID. Independent runs need different run_id. Results can include actual computed values but must not reveal internal branch to reconstruction (these are diagnostic validation only).
Always-record lifecycle/queue sites separate from per-job region sites: `main.begin`, `thread.spawn`, `thread.begin`, `queue.enqueue`, `queue.dequeue`, `thread.end`, `main.end`. Enqueue recorded inside existing lock before consumer-visible release, job unique per run.

## Core API

`reconstruct(model, plan, trace) -> {run_id, plan_id, build_id, status, jobs, transfers, warnings, ...}`.
jobs: `[{job, observed_sites, candidate_paths:[[node_id]], candidate_count, status, elapsed_ns, ...}]`.
Path enumeration bounded: cycle/path limit must produce `incomplete`, never unique certainty. Project candidate paths through enabled sites and match ordered observed site sequence. Only use absent events to prune when trace complete, dropped==0 and enabled set matches plan. Results must never drive branch inference. Check build, plan and run consistency; refuse wrong build / plan.
`recommend(model, plan, reconstruction, max_new_sites=1) -> next_plan` preserves anchors and previous enabled sites; ranks candidates by additional pair discrimination / estimated_cost, counts observed job ambiguity, deterministic order tie-break. Say cost is proxy, not measured nanoseconds. For incomplete evidence return no certainty-based recommendation or explicit diagnostic reason.
`accumulate(reconstructions)`: list/run provenance and per-edge supporting run/job facts only; must not synthesize one historical timeline from different runs.

## CI boundary

Required tests run without OAI data. `scripts/ci.py` always creates fresh session data before discovery and treats any skipped required test as a failure. Optional OAI corpus tests live outside `tests/`.


## M1 source extraction boundary

`tools/libtooling` (Clang 18) and `onion/libtooling.py` consume the T2 manifest and
produce `clang_source_extraction` / `source_extraction_manifest` v1 documents.
See [docs/libtooling-extractor.md](docs/libtooling-extractor.md) for schema,
argument adjustments, failure behavior and scoped IDs. These are source CFG and
call-site records, not runtime evidence or T1 execution_structure documents.
The adapter preserves uncertainties and exact marker mappings into Model v1.
