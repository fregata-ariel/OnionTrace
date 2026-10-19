# T6 source fixture

This fixture is extracted with the real Clang tool, not executed as a concurrent
FIFO implementation. `runtime.c` supplies minimal compatible signatures only.
Publication/acquisition semantics in the graph describe pinned OAI's locked
notifiedFIFO contract, not this stub's runtime behavior.

Covers distinct objects with the same field, direct argument forwarding,
two actor roles, response metadata and re-publication, NULL callback gNB-style
processing/new completion, opaque and mutated aliases, dead sites and a second
executable's isolation. No actual job identity, payload or timing is collected.

`review.c` adds producer-side response-field changes before submission (direct
and forwarded), a different element's unrelated write, pointer-field writes in
array elements 0/1 and another array, dynamic indices and computed bases.
