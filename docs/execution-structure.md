# M1 execution structure contract (v1)

This is T1 (#3), under the M1 integration issue #12. It defines the boundary
consumed by later input-manifest, extraction and integration work. It does not
extract OAI, instrument programs, collect events or execute a whole-program CFG.
The existing local CFG/Plan/Trace schemas in CONTRACT.md remain unchanged.

Implementation: `onion/execution.py`, Python >=3.10, standard library only.
All new documents use integer `schema_version: 1` and an explicit `kind`.
Unknown fields/kinds/versions are rejected, so semantic extensions require an
explicit contract revision. Validation raises `ValueError`; it never drops an
unknown record silently. This is an executable Python JSON contract, not a
claim of compliance with a separate JSON Schema standard.

## Identity and ownership

An entity is `{id, kind, identity, label?}`. `label` is display text only.

| kind | Exact identity fields | Meaning and scope |
| --- | --- | --- |
| `program` | `origin, revision` | Explicit source origin and immutable source revision/snapshot |
| `target` | `program_id, name` | Executable/build target within that program |
| `build` | `target_id, configuration` | One target build with its complete input configuration |
| `compilation` | `build_id, source, directory, compiler, arguments, input_digest` | A translation unit under one exact compiler invocation |
| `function` | `compilation_id, symbol, linkage` | Function in that compilation; linkage is `internal`, `external` or `none` |
| `location` | `compilation_id, path, content_digest, start_offset, end_offset` | Half-open byte range `[start_offset, end_offset)` in source/generated content |
| `thread_role` | `build_id, key` | Static role/creation context, not an OS thread instance |
| `queue` | `build_id, key` | Logical Queue binding, not a runtime allocation/address |
| `callback` | `compilation_id, key` | Callback slot/binding context, not a runtime invocation |

All fields are nonempty strings except the following:

- `configuration`: nonempty JSON object. The producer must include the complete
  build-manifest identity: source/generated inputs, toolchain, dependencies and
  relevant configuration. T2 will produce this manifest; T1 cannot verify its
  completeness from the declarations alone.
- `arguments`: nonempty ordered array of strings, preserving empty individual
  arguments and argument order. Record the actual compiler invocation. Expand
  response-file content or include it in `input_digest`.
- `input_digest`, `content_digest`: `sha256:` followed by 64 lowercase hex
  digits. The compilation input digest must cover source, included/generated
  inputs and response files. The location digest identifies that file's bytes.
- `start_offset`, `end_offset`: nonnegative integers, end >= start. Booleans
  are not integers. Line/column display can be derived from the identified
  content; offsets are not claimed stable after edits.

`source`, `path` and `directory` are canonical root-relative POSIX paths with
no `..`, repeated slash, backslash or leading slash. `directory` alone may be
`.`. Producers must establish a common root layout for source and generated
files. No implicit path, origin, argument, Unicode or symbol normalization is
performed. Producers must supply compiler identity including its relevant
version/toolchain digest, not just a mutable command alias.

Function `symbol` must be an unambiguous extractor key (e.g. Clang USR or a
qualified signature/declaration key), not the unqualified display name.
Role/Queue/callback `key` must distinguish binding and creation context, including
the compilation/call site when relevant. Matching labels or possible aliases
must not automatically identify two bindings. Alias and callback uncertainty
belongs in candidate/unresolved relations.

ID algorithm:

1. Serialize `identity` with Python `json.dumps(sort_keys=True,
   separators=(",", ":"), ensure_ascii=True, allow_nan=False)`.
2. Hash the ASCII bytes with full SHA-256.
3. Prefix with `<kind>:`. For example, `function:<64 hex digits>`.

Only JSON objects with string keys, arrays, strings, booleans, null, integers
and finite floats are accepted. No tuples, NaN or infinity. Numeric type and
array order are significant. This specifies the Python v1 serialization; a
different language must reproduce those bytes rather than assume a different
canonical-JSON standard.

Every child includes its parent's ID, so identity propagates through target,
build and compilation scope. Same-named internal functions in different TUs,
the same file in two targets, and alternate compilation conditions get different
IDs. Even external symbols in two TUs stay distinct compilation occurrences;
linkage alone does not merge them. Cross-TU resolution is explicit analysis
work. Cross-build equivalence is out of scope and never inferred from a name.

## Static structure

`{schema_version, kind:"execution_structure", structure_id, entities, relations}`

`make_entity`, `make_relation` and `make_structure` produce JSON-compatible
records with deterministic IDs. Constructors copy input data. `validate_structure`
checks record shapes, hashes, duplicates, parent references, relation endpoint
types, source locations and build ownership. It returns candidate/unresolved
relation IDs and reasons as diagnostics; those relations are valid structure.
An absent referenced ID is invalid data, not an unresolved relation.

A relation is:

```json
{
  "id": "relation:<hash>",
  "kind": "calls",
  "source": "function:<hash>",
  "target": null,
  "resolution": "unresolved",
  "evidence": {
    "location_ids": ["location:<hash>"],
    "conditions": ["worker mode enabled"],
    "method": {"name": "extractor-or-manual-analysis", "version": "1"},
    "reason": "function pointer target is not proven"
  }
}
```

Placeholders above illustrate shape, not valid content hashes.

| relation kind | source kind | target kind | Meaning |
| --- | --- | --- | --- |
| `calls` | function | function | Synchronous function call |
| `spawns` | function | thread_role | Creates a thread role at a source site |
| `starts` | thread_role | function | Role's start function |
| `submits` | function | queue | Enqueues/submits work |
| `dequeues` | queue | function | Work is acquired by this consumer |
| `invokes` | function or thread_role | callback | Invokes a callback slot |
| `binds` | callback | function | Slot has this possible/resolved function binding |

Known endpoints and evidence locations must belong to the same build. Different
compilations within a build are allowed; cross-TU resolution is not implied by
this permission. Evidence needs at least one distinct source location and a
named/versioned analysis method. `conditions` records prerequisites such as
build guards, runtime mode, wrapper branch or alias assumptions as strings.
They are explanatory predicates, not executable expressions. An empty array
means no additional conditions were recorded, not a proof of unconditional
execution. Multiple conditions/locations retain their declared order.

Resolution semantics:

- `resolved`: both endpoints non-null and well-typed, `reason: null`. The static
  analysis resolved the connection under its stated conditions. It does not mean
  it ran.
- `candidate`: both endpoints non-null and well-typed, plus a nonempty reason
  explaining uncertainty about that possible connection. Alternative candidates
  are separate relations with their own provenance; they do not imply coverage
  of every possible target.
- `unresolved`: exactly one of `source` or `target` is `null`, plus a nonempty
  reason. The known endpoint must exist and have the type required by the
  relation kind; the unknown endpoint's expected type also follows from that
  kind. Both endpoints null is rejected because no known binding anchors the
  connection. No fake function/Queue ID is invented. A non-null missing ID is
  invalid, even on an unresolved relation.

For example, a FIFO pull whose consumer and call site are known but whose Queue
alias is unresolved is `dequeues` with `source: null`, `target: <consumer ID>`
and reason `queue alias could not be resolved`. Its `evidence.location_ids`
retains the pull site. An unknown call target instead uses `source: <caller ID>`
and `target: null`. The build is determined by the known endpoint, and every
evidence location must belong to that build. The same ownership rule applies
when validating run references and accumulating candidate support.

Relation IDs hash the complete `{kind, source, target, resolution, evidence}`
using the same algorithm with prefix `relation:`. Different source sites,
conditions or methods remain separate claims. A `structure_id` hashes
`{entities, relations}` with each array sorted by ID, prefixed `structure:`.
List order does not change a structure ID; labels or provenance changes do.
There are no `observed`, `run_id` or runtime counters in this static document.

## Run evidence and accumulation

A per-run sidecar is:

```text
{schema_version, kind:"execution_run", structure_id, build_id, run_id,
 plan_id, trace_ref, observations, inferences}
observations: [{relation_id, event_ref:{thread, thread_seq}}]
inferences: [{relation_id, basis, event_refs:[{thread, thread_seq}], reason}]
```

`trace_ref` is a nonempty reference to the raw Trace artifact. It is retained
for provenance, never followed automatically. `validate_run(structure, run,
trace)` requires the actual raw Trace as a separate argument. It verifies
build/plan/run identity, the exact `structure_id`, referenced relations and
per-thread event references. Duplicate raw `(thread, thread_seq)` keys and
mixed-identity events are rejected. `seq` is not a causal order. Runtime
`thread` names identify instances within that run, independently of static roles.

An `observation` declares a directly measured connection. The referenced raw
event must explicitly contain the matching `relation_id` in addition to its
ordinary Trace fields. Producers may emit that optional extension only when
the event actually measures that relation and its endpoint mapping. A
site/function entry observation alone cannot certify a whole inter-function
edge. A relation with either endpoint unresolved cannot be directly observed as
a concrete connection. A candidate may be observed in a particular run without rewriting
the static relation or proving it occurs in every run. Dropped events do not
invalidate a surviving positive direct observation.

Inference `basis` is restricted to `candidate_only` or `unique_under_model`,
never `direct_observation`. Every inference carries a nonempty explanation.
Event references must exist in the raw trace. `unique_under_model` additionally
requires a resolved relation, nonempty evidence references and a declared
complete, zero-drop trace. These are necessary boundary checks, not a proof
of model completeness, correct probe placement or inference soundness. The
producer must also satisfy the reconstruction algorithm's full observation
contract. Candidates may have empty references (static possibilities retained
for a run); they must not become executed edges merely by being listed.

`accumulate_execution(structure, [(run, trace), ...])` validates its inputs and
returns:

```text
{schema_version, kind:"execution_evidence", structure_id, build_id,
 runs:[{run_id, build_id, plan_id, trace_ref}],
 relations:[{relation_id, support:[{run_id, plan_id, basis, event_refs, reason?}]}],
 merges_timelines:false}
```

Direct support has basis `direct_observation`; inference support retains its
original basis and reason. Static relations alone produce no run support.
Duplicate run IDs and accumulation across builds are rejected. Empty input has
`build_id: null`, empty runs and empty relations. No global event timeline is
created, and later evidence never upgrades an earlier run's inference. This
derived output is produced from validated raw inputs; there is no independent
import/trust path for a hand-edited cumulative artifact in v1.

## Mapping to existing reconstruction

| Existing record | M1 boundary / later integration responsibility |
| --- | --- |
| local CFG `build_id` | Must exactly identify the chosen M1 build; legacy fixture build IDs remain local and unchanged |
| local CFG `function` and parser-local function/block IDs | T3/T7 must explicitly bind to a scoped function occurrence, region and local IDs; names alone are insufficient |
| `nodes`, `edges`, `region`, `sites` | Stay inside one function-local reconstruction model; calls/spawns/Queue edges are not CFG successors |
| Plan `plan_id`, `build_id`, `parent_plan_id` | Preserve exact plan/build identity and refinement ancestry; `plan_id` is an opaque versioned plan identity |
| Trace `run_id`, thread-local events | `execution_run` references that exact run, plan and source event; no synthetic observations from static structure |
| reconstruction `unique_under_model` | Inference with the same scope, never a directly measured edge |
| reconstruction `candidate_only` | Remains a possibility, with run-local provenance |
| core `accumulate` | Same no-merged-history rule; execution support is keyed by scoped relation ID instead of local CFG node pairs |

T1 provides the contract and validation only. T3 supplies extraction identities;
T4–T6 supply thread/callback/Queue semantics; T7 implements validated bindings
and passes unresolved boundaries to the existing explorer. A local unique path
must not certify an unresolved inter-function boundary. No implicit conversion
from textual CFG names or legacy lifecycle/queue markers is implemented here.

## Fixture and validation

`fixture/execution/catalog.json` plus `a.c` and `b.c` define the collision cases.
`tests/test_execution.py` materializes and JSON-round-trips the static boundary,
tests every relation kind, checks unknown source/target endpoints versus dangling
references,
and verifies build/plan/run/event provenance and evidence separation. An unknown
Queue source retains its consumer, pull-site location and reason as a diagnostic;
it may have run-local candidate support but cannot gain direct-observation or
model-uniqueness support, even with a matching raw event. The
fixture's connection declarations are hand-authored, not claimed extractions.

```bash
python3 -m unittest discover -s tests -p test_execution.py -v
python3 scripts/ci.py --cc clang-18 --out out
```

The regular skip-forbidden CI discovers these tests alongside the unchanged
reconstruction and run-isolation regression tests. No OAI or USRP is required.
