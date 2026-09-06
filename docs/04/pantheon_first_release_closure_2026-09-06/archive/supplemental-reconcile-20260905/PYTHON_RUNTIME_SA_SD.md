# OPS-SUPERVISOR-PYTHON-RUNTIME-PREREQUISITE-001

## SA: observed intake failure

On 2026-09-05 the supervisor runs immutable source
`20282eba2ce2304560ab7eab0cd27af824a22b8b` as PID 96329. Its launch command
uses `/usr/bin/python3`. The main loop stays healthy, but every packet drain
emits `assistant_dev_packet_drain_unavailable: ModuleNotFoundError: No module
named 'pydantic'`. A signed prerequisite remained pending from 01:41:18 until
Human/Ops used the existing verifier-backed drain API at 01:45:14.

Read-only probes prove `/usr/bin/python3` has cryptography but no pydantic.
The configured workspace `.venv/bin/python` has pydantic 2.13.5 and
cryptography 50.0.1 and successfully ran that unchanged official drain API.
The latter is temporary intake assistance, not proof of automatic recovery.

Relevant existing paths:

- `scripts/sync-dev-root.sh` invokes config provisioning/promotion via python3.
- `scripts/promote_supervisor_runtime.py` already accepts `--python` and
  passes it into the single `build_live_config` renderer.
- `scripts/provision_live_supervisor_config.py` accepts `--python` but its CLI
  resolves symlinks, potentially converting a venv executable back into the
  system interpreter and losing its environment.
- `scripts/supervisor_watchdog_install.py` and the rendered watchdog command
  must preserve the same accepted interpreter on subsequent restart.

Do not create a second supervisor launcher, packet dispatcher or cron.

## SD: repair the existing runtime contract

Define one explicit supported supervisor Python environment and a minimal
development-tooling dependency contract. Prefer reuse of existing environment
provisioning; if none exists, add one scoped requirements file consumed by
the existing bootstrap flow. Do not require the product BFF image, full
product dependencies, product credentials or a product login for task intake.

Thread the selected interpreter through current bootstrap -> config renderer
-> promotion -> watchdog paths. Preserve a venv invocation path rather than
blindly resolving it to the base interpreter. Validate the executable and
its actual dependency imports before replacing a healthy incumbent. A failed
dependency preflight must leave incumbent PID, live config, leases and cron
binding unchanged. A fresh-host setup must have documented reproducible
dependency installation and cannot rely on a chatbox shell's ambient packages.

Reuse the current bridge, TaskStore, authority validation and exact-source
promotion mechanisms. Do not disable signed packet validation, inject mock
keys, add import shims/sys.modules aliases, auto-install packages every tick,
or alter canonical state JSON. Bootstrap and watchdog must not silently reset
an explicitly selected interpreter to `/usr/bin/python3` on the next sync.

## Acceptance and execution

Owner Claude, independent reviewer Codex. Functional development-tooling
source work, no product/hosted deployment or live capital changes.

1. Reproduce the missing-pydantic intake failure using a real subprocess
   environment; verify a venv symlink is not accidentally de-virtualized.
2. Add focused tests for selected interpreter propagation, fresh environment
   dependencies, failed preflight preserving the incumbent, watchdog restart,
   sync no-op/re-promotion behavior and signed packet intake under the chosen
   interpreter. Existing promotion/config/bridge tests remain green.
3. Commit scoped code, tests, runbook and genuine JSON evidence bound to the
   tested head. Use the current independent-review/integrator delivery flow.
4. Root will perform exact-version governed promotion after merge, confirm
   the launched interpreter, automatic pending -> processed admission and
   canonical readback, fresh heartbeat, and worker lease preservation. Do not
   claim automatic intake recovery from a passing manual drain or source merge.

Do not independently promote while root is coordinating the current workers.
The separate archive-reconciliation prerequisite owns scripts/ai_status.py,
task archive and TaskStore changes. Do not overlap that implementation.

Rollback uses existing exact-version promotion with the last verified Python
environment. Preserve task packets, admission receipts and historical audit.
