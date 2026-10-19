# M1/T3: Clang 18 source CFG and call-site extraction

T3 (#5, parent #12) consumes T2's `build_input_manifest`. It provides source CFGs
and call sites for T4's thread-entrypoint extraction. It does not perform T4's
cross-TU resolution or T7's execution-structure/navigation integration.

## Build and run

Ubuntu 24.04, Python >=3.10, Clang/LLVM 18:

```sh
sudo apt-get install clang-18 libclang-18-dev llvm-18-dev cmake ninja-build
cmake -S tools/libtooling -B build-libtooling -G Ninja \
  -DLLVM_DIR=/usr/lib/llvm-18/lib/cmake/llvm \
  -DClang_DIR=/usr/lib/llvm-18/lib/cmake/clang
cmake --build build-libtooling --parallel 2
python3 -m onion.libtooling --manifest out/build-inputs/manifest.json \
  --tool build-libtooling/onion-extract --out out/source-extraction
```

Prepare/capture inputs first as described in [oai-build-inputs.md](oai-build-inputs.md).
`python3 scripts/ci.py` builds the extractor, extracts the small T2 fixture, and
runs the native acceptance tests without skips. Custom installs can supply `LLVM_DIR`
and `Clang_DIR` environment variables to the CI script. The default v0.1 demo continues
to use the textual adapter; the test passes native CFGs through the same
reconstruction/recommendation functions, with the same runtime compile command.

## Input and failure behavior

One process and a temporary single-entry compile DB are used for **each selected
compilation**, including multiple entries for the same source. The ordered
original arguments and tooling arguments, removed output/link-only switches, added warning
policy and builtin-header resource directory are recorded. LibTooling's standard
syntax-only/output adjusters are also recorded. The exact link-only allowlist is
`-rdynamic` (OAI places it in CFLAGS); its removal is recorded separately in
`adjustments.removed_link_arguments`. Semantic `-D`, `-I`, `-include`,
language/target and other flags remain. Response files/plugins are rejected by
T2. Unknown flags fail, rather than being removed to make extraction succeed.

The manifest content ID, context inputs, environment, compiler binary/version,
primary input and all dependencies are checked. Dependency validation is repeated
before and after extraction. A changed source/header/compiler requires recapture;
no persistent extraction cache is used. This is not an atomic filesystem snapshot.

`extraction.json` preserves a result for every input entry, including exclusions
and upstream failures. `entry-N.json` contains native data and `entry-N.log`
contains diagnostics. A failed native output is not consumed as successful data.
`failures.json` and a nonzero CLI status expose failures. A successful frontend
is separate from CFG/call completeness: unresolved calls and structural
uncertainties remain in successful output.

## Native schema, version 1

`kind: clang_source_extraction`, `compilation_id`, `clang_version`, `complete`,
`functions`, `cfg_policy`, `limitations`.

| Record | Fields and meaning |
| --- | --- |
| Function | Scoped `id`, Clang `usr`, name/qualified name, function type, linkage, source range, blocks, edges, calls, uncertainties |
| Block | Scoped ID, local Clang block number, entry/exit, ordered CFG elements, terminator, condition, loop target, successors |
| Element | Index, AST kind, printed text, expression type, source range; DeclRefExpr also has referenced symbol/USR |
| Successor | Ordinal, normal/unreachable/unknown, target or null. Only normal successors become edges |
| Call | Scoped ID, caller, direct/indirect, expression/result/callee types, arguments, source range, CFG placement or explicit absence, callee USR/name/linkage, target/resolution/reason |
| Range | Physical expansion and spelling spans; begin, exclusive token end and inclusive last-token position, each with path/offset/line/column; macro flag. Invalid locations are null |

`complete` means that the frontend returned success, not complete call resolution
or proven path feasibility. CFG defaults plus `setAllAlwaysAdd()` retain expression
granularity comparable to `debug.DumpCFG`. Pruned successors remain explicit.
Unsupported implicit CFG elements, inline assembly, indirect goto, coroutine,
constructor/new/delete semantics and lambda bodies have explicit completeness
uncertainties. Virtual dispatch remains indirect. Ordinary C is the tested scope;
all C++ functions carry a completeness uncertainty; this is not a complete C++ semantic call graph. System-header definitions are
omitted and calls into them remain unresolved.

A direct target is connected only to an emitted definition in the same TU.
Cross-TU direct calls retain their USR and linkage but have null target and an
unresolved reason, even if another output contains a matching definition. Indirect
calls retain the callee expression/type without inventing a points-to target.
Call placement can be null for unevaluated/pruned AST expressions; the AST call
record does not assert reachability or execution.

The compilation scope is T2's cache key (including build conditions and hashed
inputs). Function IDs add the USR; call IDs add the enclosing function and AST
occurrence. Block IDs add the Clang block number. These distinguish variants and
same-named static functions, but **are not stable across builds or edits**. USRs
alone are insufficient to join internal linkage, variants or different builds.
Raw IDs belong to this document kind; they do not masquerade as T1's hashed
`execution_structure` entities. T7 creates those entities with explicit scope; see [model-integration](model-integration.md).

`onion.libtooling.build_model` maps semantic marker DeclRefExprs exactly once and
adapts one selected function to the existing local Core API. Missing/duplicate
markers, ambiguous function selection and dangling references are errors. It
preserves CFG uncertainties so reconstruction cannot claim uniqueness from an
incomplete model. It does not automatically insert probes or attach static
calls to runtime evidence.

## Acceptance evidence

`tests/test_libtooling.py` runs native extraction (missing tools fail). It checks:

- pthread `process_job`: entry/exit, exactly three region paths, marker projections
  matching the textual CFG from the same compile command, three runtime stages,
  and matching recommendations;
- direct calls, function pointers, unresolved cross-TU declarations and repeated
  static names; same source compiled with two definitions yields separate IDs;
- macro expansion/spelling positions, branch conditions, loop targets and block/
  edge correspondence to Clang's textual dump;
- changed inputs, tampered manifests, retained failures/exclusions, unknown flags,
  explicit unsupported assembly, invalid references and missing markers.

Fixed OAI 2026.w07 full-build/corpus acceptance remains #11. No OAI execution,
optimized-instruction mapping, whole-program points-to analysis or cross-build
source-position stability is claimed.

## T4 additive fields

T4 adds canonical function types, parameter counts, semantic argument values
(function designators, unmodified parameter references, opaque expressions and
conditional candidates), lexical call conditions and validated `thread_api`
contracts. Existing v1 fields and the local CFG adapter are preserved. Older
v1 extraction files must be re-extracted for [T4](thread-entrypoints.md).

## T5 additive fields

T5 adds `callback_writes` (scoped owner/write IDs, target and value shapes,
conditions and ranges), `callee_value` and `callback_api`. Semantic values add
NULL, local declaration references, record initializers, member references and
call-result arguments. These are source facts, not points-to results. The
[callback context adapter](callback-contexts.md) requires fresh T5 extraction,
keeps storage/alias alternatives unknown and dispatch associations as candidates.
The v1 local CFG adapter and T4 remain compatible with the additive fields.
