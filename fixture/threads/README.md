# T4 thread entrypoint fixture

Small C source inputs for native Clang 18 extraction. This is authored test code,
not a copy or full build of OAI. Target names and threadCreate's signature model
the pinned OAI API. See ../../docs/thread-entrypoints.md for audited OAI locations.

The fixture deliberately contains missing and duplicate definitions. Configure,
validate syntax/dependencies and extract; do not link/run these executables.
Use the existing ../fixture.c for pthread execution and sanitizer acceptance.
