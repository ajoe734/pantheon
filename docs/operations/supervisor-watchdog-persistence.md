# Supervisor Watchdog Persistence

Status date: 2026-06-06

## Problem This Fixes

On 2026-06-06 the supervisor/auto-worker loop was not actually running even
though `.orchestrator/state.json` still said `lifecycle=running`. The stale
runtime facts were:

- `.orchestrator/supervisor.pid` pointed at a dead process.
- `tmux ls` showed only dashboard sessions, not `pantheon-supervisor`.
- crontab contained log rotation only; it did not run the watchdog.
- no user systemd unit existed for the supervisor watchdog.
- the last supervisor heartbeat was `2026-06-04T15:00:03Z`, more than a day
  stale.

That means ready tasks in `ai-status.json` were visible but could not be
dispatched. The root cause was not task assignment or persona configuration; it
was missing OS-level persistence for the supervisor watchdog.

## Durable Runtime Model

The supervisor itself is still started by `.orchestrator/supervisor.py`.
Persistence belongs to the non-LLM watchdog:

```bash
bash scripts/run-supervisor-watchdog.sh --restart
```

The watchdog checks the singleton supervisor flock, pid, heartbeat, resource
pressure, restart budget, and circuit breaker state. If the supervisor is dead
or stale and restart gates allow it, the watchdog writes safe mode into
`.orchestrator/state.json` and starts the supervisor.

The systemd service is a oneshot with `KillMode=process`. The watchdog main
process exits after each probe, but a supervisor child started by a permitted
restart must remain alive in its own session. The singleton flock and later
watchdog ticks, not the oneshot cgroup teardown, govern that child.

Do not treat tmux or dashboard uptime as supervisor health. The dashboard can
remain up while the supervisor and auto workers are dead.

## Replacement Layout

Supervisor replacement has no incumbent compatibility or rollback path.  The
only accepted topology is:

- immutable command source: `/home/lupin/pantheon-ci-deploy/command-runtimes/<exact-commit-sha>`;
- stable development-tool coordination worktree:
  `/home/lupin/pantheon-ci-deploy/coordination-root`;
- mutable staging checkout: `/home/lupin/pantheon-ci-deploy/dev-root`.

`sync-dev-root.sh` never reads `/proc/<pid>/cwd` or a product-root PID file to
infer authority. It refreshes staging, materializes the exact command runtime,
then invokes the promoter with the explicit coordination root. The promoter
reads an existing installed config only to locate the incumbent PID during a
status-root replacement; it creates and validates the approval marker before
TERM, installs the V2 config, and launches the exact runtime.

## Install

Preferred install:

```bash
python3 scripts/supervisor_watchdog_install.py --method auto --start-now
```

Create the coordination worktree before the first replacement. It must be a
Git worktree containing the current authoritative `ai-status.json` projection
and `.orchestrator/` directory. Then use explicit roots; neither `dev-root`
nor the product checkout is valid for runtime coordination:

```bash
COORDINATION_ROOT=/home/lupin/pantheon-ci-deploy/coordination-root
bash scripts/sync-dev-root.sh \
  /home/lupin/pantheon-ci-deploy/dev-root \
  /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json \
  "$COORDINATION_ROOT"
python3 scripts/supervisor_watchdog_install.py \
  --repo /home/lupin/pantheon-ci-deploy/command-runtimes/<exact-commit-sha> \
  --config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json \
  --method auto \
  --start-now
```

Provisioning converts the watchdog state, normal metrics, and contention
metrics paths to absolute paths beneath the canonical status root. This keeps
the watchdog writer and `supervisor_runtime_health.py` reader on the same files;
relative watchdog paths would otherwise resolve beneath the immutable command
checkout and make a healthy persistent loop appear unmonitored.

The deployment runbook must fail the replacement unless the user-systemd timer
(or cron fallback), watchdog probe, singleton supervisor, and canonical
heartbeat all become healthy. For user systemd it also enables and verifies
login linger, so the timer starts after a reboot without requiring an
interactive login.

Before the old supervisor is stopped, split-root promotion also creates the
ignored canonical `.orchestrator/approval-queue.json` marker when it is absent.
Creation is exclusive and owner-only; an existing valid v2 queue is validated
and preserved byte-for-byte so pending or historical approvals are never reset.
This marker is part of the isolated worker's coordination-root contract, not a
Git-tracked deployment artifact.

On a first deployment, an absent `.orchestrator/state.json` is an expected
bootstrap condition: the watchdog may start the supervisor, which creates the
canonical state under its singleton and runtime-state locks. This exception is
exact. An empty file, invalid JSON, unreadable file, or invalid top-level schema
still suppresses restart as `resource_pressure:state_read_failed`.

`--method auto` prefers a user systemd timer and falls back to cron when user
systemd is unavailable.

Systemd user units:

```bash
python3 scripts/supervisor_watchdog_install.py --method systemd --start-now
systemctl --user status pantheon-supervisor-watchdog.timer
systemctl --user status pantheon-supervisor-watchdog.service
```

Cron fallback:

```bash
python3 scripts/supervisor_watchdog_install.py --method cron
crontab -l | rg 'pantheon-supervisor-watchdog'
```

Uninstall:

```bash
python3 scripts/supervisor_watchdog_install.py --method systemd --uninstall
python3 scripts/supervisor_watchdog_install.py --method cron --uninstall
```

## Verify

After install, run:

```bash
python3 scripts/supervisor_runtime_health.py --require-watchdog --json
```

Healthy output must have:

- `healthy: true`
- `supervisor.alive: true`
- `supervisor.heartbeat_age_seconds` under the configured watchdog threshold
- a fresh watchdog probe, normally under 180 seconds old, proven by either the
  serialized watchdog state update or a valid lock-contention metric

For a live shell check:

```bash
ps -p "$(cat .orchestrator/supervisor.pid)" -o pid,ppid,stat,etime,cmd
tail -n 40 .orchestrator/logs/supervisor-watchdog-cron.log 2>/dev/null || true
jq '.last_decision' .orchestrator/watchdog-state.json
```

## Lock Contention & Single-Flight Behavior

To prevent supervisor watchdog processes from queuing up and accumulating under lock contention (e.g., when `runtime-admission.lock` is held for a long time by a running supervisor or other worker loops), the watchdog implements a bounded, nonblocking single-flight protocol:

1. **Nonblocking Contention Detection**:
   - The watchdog attempts to acquire the `runtime-admission.lock` using a nonblocking lock.
   - If the lock is already held, it immediately returns with a `skip` decision and the `lock_contention` reason without blocking or waiting.

2. **Zero Locked-State Writes & Drop-Safe Metrics**:
   - Under contention, the watchdog does not attempt to write to the main `watchdog-state.json` or `metrics.jsonl` files (which would trigger blocking writes).
   - Instead, it attempts a nonblocking write to `.orchestrator/metrics/supervisor-watchdog-contention.jsonl` using a separate `.lock` file.
   - If the contention metrics lock is also contended, the metric write is dropped and a message is written to `sys.stderr` to prevent secondary blocking loops.
   - Runtime health uses the newest valid contention metric as probe-freshness
     evidence while still requiring the serialized watchdog state file to exist.

3. **Diagnostics & JSON Contract**:
   - When run with `--json`, a contended watchdog probe exits with code `0` and outputs a structured JSON contract detailing the contention event:
     ```json
     {
       "decision": "skip",
       "reason": "lock_contention",
       "pid": 123,
       "new_pid": null,
       "heartbeat_age_seconds": 45.0,
       "resource": { ... },
       "lock_held": true
     }
     ```
   - Standard exit code for a contended run is `0` (since it is a recognized and handled operational state rather than a script crash).
   - Under systemd or cron, multiple overlapping ticks will exit instantly instead of stacking up.

## Acceptance For Auto Worker Readiness

Supervisor/auto-worker readiness is not satisfied until all of these are true:

1. Persistent watchdog is installed through systemd timer or cron.
2. `scripts/supervisor_runtime_health.py --require-watchdog` exits 0.
3. `ai-status.json` contains at least one dependency-ready task, and the
   supervisor creates or reconciles queue/worker state on the next cycle.
4. Worker starts are visible in `.orchestrator/state.json` or logs, unless all
   eligible providers are intentionally paused by guardrails.

## Incident Repair Boundary

Manual `scripts/run-supervisor-watchdog.sh --restart` is only temporary live
repair. It restores the current process but does not make the system durable.
The durable repair is this installer plus the OS-level timer/cron entry.
