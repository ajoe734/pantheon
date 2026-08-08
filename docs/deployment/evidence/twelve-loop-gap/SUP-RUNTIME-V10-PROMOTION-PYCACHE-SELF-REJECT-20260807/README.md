# SUP-RUNTIME-V10 promotion pycache self-reject repair

This packet binds the repair for the immutable supervisor promotion pipeline's
self-generated Python bytecode failure. Candidate `__pycache__` and `.pyc`
remain forbidden. The repair prevents their creation during candidate
provisioning, promotion preflight, supervisor launch, watchdog restart, and
governed status-command child processes.

The initial implementation anchor is
`3a889bf14d3761a7f13ae55e23ec4d198058f91e`. Claude2 rejected PR #4629 head
`9f2b6b0165a3be8899b7f96f1fb0fd12a9d0bf23` because `python -B` is not
inherited by child interpreters. The review repair is anchored at
`8041de89a4b39dc40acc7310b592485685600700`: governed launch, watchdog
restart, supervisor status subprocesses, and the worker status wrapper now
propagate `PYTHONDONTWRITEBYTECODE=1`, while `-B` remains in Python argv.

After that repair, the promotion/deploy suite passed 284 tests and the added
supervisor/watchdog/status contracts passed 84 tests. Shell syntax checks
passed, the touched Python files compiled successfully, and real child-process
tests left the isolated command runtime free of bytecode.

Independent review must bind [evidence.json](evidence.json) to the exact PR
head. Runtime acceptance remains pending until the merged task head is promoted
through `sync-dev-root.sh`, the transaction records three fresh successful
loops, the live command identity contains PR #4582 and PR #4625, and the
immutable runtime remains free of bytecode debris.
