# Supervisor Python runtime contract

Status: active operating rule
Task: OPS-SUPERVISOR-PYTHON-RUNTIME-PREREQUISITE-001

## The failure this closes

On 2026-09-05 the promoted supervisor was launched with `/usr/bin/python3`.
The main loop stayed healthy (heartbeat, ai-status sync, worker dispatch all
kept working), but every signed dev-bridge packet drain failed silently with
`ModuleNotFoundError: No module named 'pydantic'`. The failure was invisible
in health checks because the health surface never exercised the drain path.

Independent review of the first delivery (head `cbea5c308bf2994e7ce1b3c238f39ab1374582b0`)
found two more real defects beyond the original two, all four now closed here:

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
3. (found in review) `scripts/bootstrap-orchestrator-runtime.sh` generated
   the dev-bridge keypair -- which imports `cryptography` -- with the
   ambient `python3`, before the supervisor venv was ever created. A clean
   host with no ambient `cryptography` could not bootstrap at all.
4. (found in review) `validate_python_dependencies()` called
   `importlib.metadata.version()` only: it never enforced the
   `requirements.txt` version specifier and never imported the module, so an
   incompatible version or a broken native extension could report a passing
   preflight. Separately, `scripts/sync-dev-root.sh` pip-installed every
   promotion into one fixed venv directory -- the same directory a currently
   running incumbent supervisor had already launched from -- before any
   preflight ran, so a partial or failed install could break the live
   incumbent regardless of whether the candidate was ever accepted.

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
  `$PANTHEON_DEPLOY_ROOT/runtime/supervisor-python/<exact-candidate-SHA>`,
  created with `python3 -m venv` and installed with
  `pip install -r .orchestrator/requirements.txt` from that exact candidate.
  This directory is deploy-root owned (not inside any Git worktree), so it
  survives command-runtime pruning and re-promotion. It is versioned per
  exact candidate SHA, not kept at one fixed path: a fixed path would mean an
  install (or a failed reinstall) for a *new* candidate mutates the very
  directory a currently *running* incumbent supervisor already launched
  from, so a partial or failed install could break the live process before
  promotion ever decides whether to accept the candidate. A per-SHA
  directory makes that impossible by construction -- an install can never
  touch the directory backing a different, already-promoted SHA -- and every
  prior verified environment stays on disk, usable for rollback.
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
  `validate_python_dependencies()` *inside that exact interpreter*. The
  probe parses each requirements line into a distribution name and version
  specifier, calls `importlib.metadata.version()` for the real installed
  version, enforces the specifier with a self-contained numeric comparator,
  and then calls `importlib.import_module()` on the package -- so a wrong
  version *or* a broken native extension (an unloadable `pydantic_core`, for
  example) fails the preflight, not just a missing package. Checking
  distribution metadata alone was not enough: metadata can report a version
  string that satisfies every specifier while the module itself fails to
  import. `scripts/sync-dev-root.sh` also runs this preflight explicitly
  (`provision_live_supervisor_config.py --validate-python-dependencies-only`)
  right after installing the candidate's per-SHA venv and before invoking
  `promote-supervisor-runtime.sh`, so a bad candidate install is caught and
  logged with the untouched incumbent root named in the failure, in addition
  to the preflight `promote_supervisor_runtime.py` always runs before writing
  live config. A failed preflight raises before any incumbent state is
  touched: promotion never stops the running supervisor, never writes the
  live config, and never disturbs worker leases or the watchdog/cron binding.

## Fresh-host reproducibility

`scripts/bootstrap-orchestrator-runtime.sh` materializes the immutable
command root, then creates the per-SHA venv and installs
`.orchestrator/requirements.txt` into it as an explicit, scripted step (no
reliance on whatever happens to already be importable from the chatbox shell
that ran the script) -- **before** generating the dev-bridge Ed25519 keypair.
Keypair generation imports `cryptography` and now runs under that venv's
`python3`, never the ambient one: a fresh host has no reason to carry
`cryptography` on its system interpreter, and bootstrap must not depend on it
being there. Re-running the script is idempotent: an existing per-SHA venv is
reused, and `pip install` of an already-satisfied requirements file is a
no-op.

## Real signed-intake proof, not just a metadata probe

`.orchestrator/development_bridge/tests/test_dev_bridge_inbox_bootstrap_python_runtime.py`
builds a real interpreter the same way these scripts do (`python3 -m venv`
plus `pip install -r .orchestrator/requirements.txt`) and genuinely queues
and drains a real Ed25519-signed `DevTaskPacket` through it -- proving the
bridge's pydantic parsing and cryptography signature verification actually
work end to end under that interpreter, not merely that a dependency-metadata
probe accepts it. A second test in the same file builds a bare venv (never
given the requirements file) and proves the real drain fails closed instead
of looking healthy, reproducing the original incident's failure mode
deterministically.

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
