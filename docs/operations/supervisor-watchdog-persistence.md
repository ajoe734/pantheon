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

## Install

Preferred install:

```bash
python3 scripts/supervisor_watchdog_install.py --method auto --start-now
```

When the supervisor command checkout and canonical status checkout are
different, pin the generated live config explicitly. Relative config paths are
not safe for this split-root topology:

```bash
python3 scripts/provision_live_supervisor_config.py \
  --repo-config /home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/config.json \
  --live-config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json \
  --command-root /home/lupin/pantheon-ci-deploy/dev-root \
  --status-root /home/lupin/pantheon
python3 scripts/supervisor_watchdog_install.py \
  --repo /home/lupin/pantheon-ci-deploy/dev-root \
  --config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json \
  --method auto \
  --start-now
```

The dev root deployment performs these commands automatically after the root
stack validations pass. It fails the deployment unless the user-systemd timer
(or cron fallback), watchdog probe, singleton supervisor, and canonical
heartbeat all become healthy. For user systemd it also enables and verifies
login linger, so the timer starts after a reboot without requiring an
interactive login.

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
