# SUP-RUNTIME-V10 promotion pycache self-reject repair

This packet binds the repair for the immutable supervisor promotion pipeline's
self-generated Python bytecode failure. Candidate `__pycache__` and `.pyc`
remain forbidden. The repair prevents their creation during candidate
provisioning, promotion preflight, supervisor launch, and watchdog restart.

The implementation anchor is `3a889bf14d3761a7f13ae55e23ec4d198058f91e`.
Before independent review, the focused promotion/deploy suite passed 280 tests,
shell syntax checks passed, and the touched Python files compiled successfully.

Independent review must bind [evidence.json](evidence.json) to the exact PR
head. Runtime acceptance remains pending until the merged task head is promoted
through `sync-dev-root.sh`, the transaction records three fresh successful
loops, the live command identity contains PR #4582 and PR #4625, and the
immutable runtime remains free of bytecode debris.
