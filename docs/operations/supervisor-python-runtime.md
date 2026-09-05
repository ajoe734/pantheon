# Supervisor Python runtime contract

Status: active operating rule
Task: OPS-SUPERVISOR-PYTHON-RUNTIME-PREREQUISITE-001

## The failure this closes

On 2026-09-05 the promoted supervisor was launched with `/usr/bin/python3`.
The main loop stayed healthy (heartbeat, ai-status sync, worker dispatch all
kept working), but every signed dev-bridge packet drain failed silently with
`ModuleNotFoundError: No module named 'pydantic'`. The failure was invisible
in health checks because the health surface never exercised the drain path.

Two independent bugs produced this:

1. Nothing in the promotion chain ever selected a dependency-carrying
   interpreter. `scripts/bootstrap-orchestrator-runtime.sh` and
   `scripts/sync-dev-root.sh` called `promote_supervisor_runtime.py` without
   `--python`, so it defaulted to `sys.executable` -- whatever interpreter
   happened to run the bootstrap/sync script, which on a fresh host is the
   ambient `/usr/bin/python3`.
2. Even when a venv interpreter *was* passed explicitly,
   `scripts/provision_live_supervisor_config.py` called
   `Path(args.python).expanduser().resolve()`. A venv's `bin/python` is a
   symlink chain to the base interpreter. CPython's own startup walks that
   chain looking for `pyvenv.cfg` and stops as soon as it finds one -- but
   only when it is invoked *through* the symlink. Fully resolving the path
   before storing it collapses the chain in Python, so the stored
   `supervisor_command` launches the base interpreter directly, which never
   finds `pyvenv.cfg` and silently loses every package the venv provided.

## The runtime contract

There is exactly one supported way to select the supervisor's Python
environment, and it is threaded through the same bootstrap -> config
renderer -> promotion -> watchdog path that already exists. There is no
second dispatcher, no ambient dependency workaround, and no per-tick import
shim.

- **Dependency contract**: `.orchestrator/requirements.txt` in the exact
  promoted command source. It is intentionally minimal -- only what the
  supervisor's bridge/task-store code actually imports (`pydantic`,
  `cryptography`) -- not the product BFF's full dependency set.
- **Environment**: a dedicated venv at
  `$PANTHEON_DEPLOY_ROOT/runtime/supervisor-python`, created with
  `python3 -m venv` and kept current with
  `pip install -r .orchestrator/requirements.txt` from the exact candidate
  being promoted. This directory is deploy-root owned (not inside any Git
  worktree), so it survives command-runtime pruning and re-promotion.
- **Selection**: both `scripts/bootstrap-orchestrator-runtime.sh` (fresh
  host) and `scripts/sync-dev-root.sh` (ongoing promotion) explicitly pass
  `--python "$SUPERVISOR_PYTHON"` to `promote_supervisor_runtime.py`
  pointing at that venv's `bin/python3`.
- **Propagation**: `promote_supervisor_runtime.py` renders the exact
  `--python` path (unresolved) into `watchdog.supervisor_command` in the
  live config. `scripts/supervisor_watchdog_install.py` and
  `.orchestrator/supervisor_watchdog.py` restart the supervisor by re-running
  that recorded `supervisor_command`, so the accepted interpreter survives
  every subsequent watchdog restart without re-selection.
- **Preflight, not blind trust**: before either
  `provision_live_supervisor_config.py` or `promote_supervisor_runtime.py`
  will accept a candidate interpreter, they run
  `validate_python_dependencies()`, which executes
  `python -c "import importlib.metadata; ..."` *inside that exact
  interpreter* and requires every package named by
  `.orchestrator/requirements.txt` to resolve with a real installed version.
  A failed preflight raises before any incumbent state is touched: promotion
  never stops the running supervisor, never writes the live config, and
  never disturbs worker leases or the watchdog/cron binding.

## Fresh-host reproducibility

`scripts/bootstrap-orchestrator-runtime.sh` creates the venv and installs
`.orchestrator/requirements.txt` into it as an explicit, scripted step (no
reliance on whatever happens to already be importable from the chatbox shell
that ran the script). Re-running the script is idempotent: an existing venv
is reused, and `pip install` of an already-satisfied requirements file is a
no-op.

## What this does not change

- No second supervisor launcher, packet dispatcher, or cron path was added.
- No signed packet validation was disabled, no mock keys were injected, no
  `sys.modules` import shim was added.
- `scripts/ai_status.py`, the task archive, and `TaskStore` changes are owned
  by the separate archive-reconciliation prerequisite and are not touched
  here.
- This task is source-only. Exact-version governed promotion, confirming the
  launched interpreter, automatic pending -> processed admission, and a
  fresh heartbeat on the live host are coordinated separately after merge.

## Rollback

Roll back with the existing exact-version promotion flow
(`scripts/promote-supervisor-runtime.sh --promote`) pointed at the last
verified command source and the last verified `--python` interpreter. Task
packets, admission receipts, and historical audit are untouched by a
promotion rollback.
