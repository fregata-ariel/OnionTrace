# T5: Actor / task callbacks and execution contexts

Issue #7 consumes the same T2/T3 artifacts as T4. `onion.callbacks` recomputes
T4, scopes alternatives to one executable target, and emits
`callback_execution_graph` v1. This intermediate document remains separate from
T1 `execution_structure` and runtime evidence. Issue #9 owns that adapter.

## Extracted source facts

`tools/libtooling` adds `callback_writes` per function and `callee_value` /
`callback_api` per call. Values retain source ranges, canonical function USR,
linkage and signature, unmodified parameter positions, local declaration USRs,
record fields and designated/positional initializers, conditional alternatives,
constructor-call arguments, callback member declarations, and null constants.
Recursive expression expansion is bounded at 16 levels; deeper values are opaque.
Opaque casts and computed returns do not resolve to a guessed designator.

API recognition trusts the OAI names plus AST argument/record/callback shapes:

| Contract | Shape | Protocol |
| --- | --- | --- |
| `newNotifiedFIFO_elt` | four parameters; last `void (*)(void *)`; returned record has `processingFunc` | Actor registration |
| `pushNotifiedFIFO` | two parameters; second points to record with `processingFunc` | Actor submission |
| `pushTpool` / `pushTpool_mask` | two / three parameters; second record has `func` | Pool submission |
| Member invocation | field `processingFunc` / `func`, typed `void (*)(void *)` | Actor / pool execution site |

These are trusted protocol contracts, not a claim about arbitrary same-shaped
user APIs. Queue implementation semantics and object identities belong to T6.

## Output

`input_manifest_id`, `extraction_digest`, and `thread_graph_digest` identify
inputs. Each target graph contains source functions, T4 spawns, registrations,
submissions, invocations, retrievals, relations, frontiers and failures.

Registrations preserve `candidates` (scoped function IDs), `nullable`, `unknown`,
`resolution`, original expanded `values`, source conditions and role contexts.
States are distinct:

- `resolved`: one direct function designator, without another alternative.
- `candidate`: a finite set of multiple designators and/or a NULL alternative.
- `null`: NULL only; no `binds` function relation is emitted.
- `unresolved`: opaque/storage alternatives or blocked source semantics remain,
  including when a known candidate is also retained.

These resolutions concern source values, not an observed dispatch. All local
storage is conservative: initializers and explicit writes form flow-insensitive
alternatives; aliases, escapes and definite assignment are not proven. Unknown
assignments remain unknown even when one initializer names a function. Direct
parameter forwarding retains the originating compilation, including across TUs.

Role contexts start at main and resolved T4 thread start IDs, follow only resolved
direct calls, and retain call paths and conditions. Indirect callback candidates
never extend a proven role path. Unreached source sites retain an explicit unknown
role. Recursion, depth and context bounds produce frontiers and `ready: false`.

`binds` connects a registration to a function. `submits` associates a supplied task
or Actor candidate with its submission. `invokes` connects an execution site to
registration candidates within its target/protocol. The latter connections are
always **candidates**, with both source ranges and an explicit unresolved
object/queue alias reason. They do not establish a particular Actor or pool
instance, a feasible complete path, or a fixed worker index. Same function IDs
can appear in several registrations and be invoked from several roles or callers.

Inline execution is represented at the actual indirect call with lexical guards
and caller contexts: `len_thr == 0` in `pushTpool`, or the else branch of UE
Actor-count checks. Actor NULL checks remain guards. The worker's shutdown
sentinel check and subsequent call remain in its CFG, without an invented
feasibility result. `try_pop_not_q` / `pop_not_q` retrieval expressions and loop
conditions retain acquisition/steal evidence; worker affinity and ordering are
explicitly not inferred.

`ready` means inputs were extracted and bounded expansion completed. Unresolved
callback/alias candidates are valid output and do not imply whole-program
completeness. Blocked/unreachable CFG call sites stay in frontiers and cannot
produce resolved callback values.

## Run and validate

```sh
python -m onion.callbacks --manifest out/inputs/manifest.json \
  --extraction out/extraction/extraction.json --out out/callbacks
python scripts/ci.py --cc clang-18 --out out
```

CLI emits `callbacks.json` and `failures.json`, returning nonzero on invalid
inputs, missing/failed selected TUs, or traversal bounds. Old T3/T4 extraction
must be regenerated. Required CI builds a fresh multi-TU callback fixture,
retains artifacts and runs native tests without skips. The fixture is an
extraction input, not a scheduling simulation or an OAI runtime trace.

## Pinned OAI source and full-build boundary

`config/oai-callback-sites.json` records complete-source SHA256 and exact byte
ranges at the pinned M1 commit. It audits 13 sites: Actor invocation and NULL
registration, UE DL/TX registrations and inline paths, DL submission, worker
invocation and acquisition/steal, pool inline invocation and LDPC encoder/decoder
submissions. Function-pointer values in LDPC task initializers remain candidates
when read from local storage.

```sh
python -m onion.oai_callbacks --source-root /path/to/pinned/oai \
  --out out/oai-callback-source-audit.json
python -m onion.oai_callbacks --manifest out/oai/manifest.json \
  --extraction out/oai/extraction/extraction.json --out out/oai-callback-acceptance.json
```

A source audit explicitly reports `full_extraction_verified: false`. The actual
extraction gate recomputes callback graphs, requires both executable targets,
and matches semantic records by owner, exact source offsets and dependency hash.
Missing selected source/plugin TUs remain failures. OAI dynamic coding libraries
must be included in the captured input closure for LDPC site acceptance; their
existence in a checkout does not establish executable reachability. Issue #11
owns full OAI build/input coverage and final acceptance. Source evidence alone
cannot pass that gate.
