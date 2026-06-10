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

Do not treat tmux or dashboard uptime as supervisor health. The dashboard can
remain up while the supervisor and auto workers are dead.

## Install

Preferred install:

```bash
python3 scripts/supervisor_watchdog_install.py --method auto --start-now
```

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
- a fresh watchdog state update, normally under 180 seconds old

For a live shell check:

```bash
ps -p "$(cat .orchestrator/supervisor.pid)" -o pid,ppid,stat,etime,cmd
tail -n 40 .orchestrator/logs/supervisor-watchdog-cron.log 2>/dev/null || true
jq '.last_decision' .orchestrator/watchdog-state.json
```

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
