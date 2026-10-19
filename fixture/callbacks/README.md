# Callback source-extraction fixture

Multi-TU extraction-only C input. Models Actor `processingFunc`, task `.func`,
parameter forwarding, multiple roles, conditional/NULL/opaque values, explicit
unknown writes, inline Actor/pool branches, and worker acquisition/steal.
By-value task wrappers cover unknown/known member overwrites, callback-member
address escape, and unchanged forwarding across translation units.
`nr-softmodem` / `nr-uesoftmodem` are target-scope fixture labels, not OAI binaries.
External acquisition and opaque-pointer factories deliberately have no bodies;
CMake configuration supplies compilations but these targets are not linked/run.
Required `tests/test_callbacks.py` checks fresh native AST/CFG extraction.
