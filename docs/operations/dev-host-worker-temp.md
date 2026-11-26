# Development host: per-worker temp directory

Register class RC3 (resource with no owner or bound), development tooling. Task: DEV-WORKER-TEMP-DIR-BOUND-20261117.

## Behaviour
- Root: `PANTHEON_WORKER_TEMP_ROOT` (or `worker_worktree_cleanup.worker_temp_root` in the supervisor config); default `/tmp/pantheon-worker-temp`.
- `worker_runner.py` creates `<root>/<runner-pid>-<run-id>` (mode 0700) and launches the worker process tree with `TMPDIR`, `TMP`, `TEMP` set to it, so `tempfile` and pytest `basetemp` (`pytest-of-<user>`) land inside it. The sandbox binds it writable (`/tmp` already is; a root elsewhere is bound explicitly).
- Normal exit, kill, timeout, handoff: the runner removes the directory in its `finally` block.
- A runner killed with SIGKILL cannot clean up: the supervisor's existing orphan pass (`prune_orphan_worktrees`, setting `orphan_prune_interval_seconds`, default 600 s) calls `prune_orphan_worker_temp`. No new timer and no host-local script.
- The orphan pass removes only entries directly under the root named `<pid>-<run-id>` whose runner process (`/proc/<pid>/cmdline` containing `worker_runner`) is gone. Symlinks are unlinked, never followed. Directories of live runners are never removed. A recycled pid can only keep a directory longer.

## Runtime budget
- Maximum disk use = concurrent workers x largest single run (about 3 GB measured in the 2026-11-17 incident, market data store fixtures).
- An orphan directory survives at most one cleanup interval (600 s by default) after its runner dies, plus one supervisor cycle.
- Measurement on this host: `scripts/tests/test_source_ingest_scheduler_once.py` run with the per-worker TMPDIR put `pytest-of-<user>` inside that directory (about 1 MB, 8 tests). The 3 GB figure is incident data, not re-measured here.

## Not covered
- Tests run by hand outside a worker still use the shared `/tmp`.
- Existing files under `/tmp` outside the root are not touched by this change.
- Takes effect only after a runtime promotion of the supervisor/worker runtime.
