# Execution identity fixture

`catalog.json` is compact **test input**, not the execution JSON wire format.
`tests/test_execution.py` builds and JSON-round-trips a validated execution
structure from it, using the content digests of `a.c` and `b.c`.

The cases cover an internal `handler` in two translation units, the same `a.c`
in two targets, and the same `a.c` with normal/FAST compilation conditions.
Thread-role, logical Queue, callback, resolved/candidate/unresolved relations
are hand-authored declarations for boundary validation. They are not extracted
from these C files, are not runtime observations, and require no OAI checkout.

The unresolved cases cover both an unknown call target and an unknown Queue
source (`dequeues`, `source: null`). The latter keeps the known consumer,
pull-site location and alias-resolution reason without inventing a Queue.
