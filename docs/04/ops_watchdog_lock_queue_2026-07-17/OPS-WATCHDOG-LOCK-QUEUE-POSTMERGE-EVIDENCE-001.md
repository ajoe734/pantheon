# Post-Merge Evidence: OPS-WATCHDOG-LOCK-QUEUE-POSTMERGE-001

Generated: 2026-07-17T06:18Z
Updated: 2026-07-17T06:57Z

## Disposition

Product acceptance is blocked at the live scheduler readback gate. The merged
implementation is present in the dev-root checkout and isolated contention
proof passes, but the live watchdog sample is stale because the real supervisor
currently holds `runtime-admission.lock` and every cron watchdog tick exits with
the bounded `skip/lock_contention` result.

This artifact is evidence-only. It does not change watchdog implementation,
runtime config, canonical task state, or activity archives.

## Source Identity

- Implementation merge under acceptance:
  `3705570e4e2a2cb26ba282603a9ca36f0da3c228`.
- Isolated task worktree:
  `/tmp/pantheon-worker-worktrees/pantheon/ops-watchdog-lock-queue-postmerge-001`.
- Task worktree HEAD:
  `d38c25bcea4513a5e27095fe59582169b3845760`.
- `origin/dev`:
  `1c9d32dddc89a1ac8513f536c0b36fd33f3f5811`.
- `git merge-base --is-ancestor 3705570e4e2a2cb26ba282603a9ca36f0da3c228 HEAD`
  exited `0`.
- Dev-root checkout:
  `/home/lupin/pantheon-ci-deploy/dev-root`.
- Dev-root HEAD:
  `1c9d32dddc89a1ac8513f536c0b36fd33f3f5811`.
- Dev-root `git merge-base --is-ancestor 3705570e4e2a2cb26ba282603a9ca36f0da3c228 HEAD`
  exited `0`.
- Dev-root watchdog source files had no diff against `origin/dev`:
  `.orchestrator/supervisor_watchdog.py`,
  `scripts/run-supervisor-watchdog.sh`,
  `scripts/supervisor_runtime_health.py`,
  `docs/operations/supervisor-watchdog-persistence.md`.

Dev-root had dirty generated task-brief files, but no dirty watchdog source
files in the implementation surface above.

## Exact-Head Watchdog Suite

Command:

```bash
python3 .orchestrator/test_supervisor_watchdog.py
```

Result:

```text
.................................
----------------------------------------------------------------------
Ran 33 tests in 1.061s

OK
```

This 2026-07-17T06:53Z rerun corrects the earlier merged evidence defect that
recorded 32 tests.

## Isolated Contention Fixture

Fixture:

```bash
python3 docs/04/ops_watchdog_lock_queue_2026-07-17/archive/postmerge_lock_contention_fixture.py --repo .
```

The fixture creates a repo-external temp runtime root under `/tmp`, holds the
runtime admission lock, launches cron-equivalent watchdog subprocesses, and then
releases the lock for a post-release health check.

Observed fixture-file run at `2026-07-17T06:17:38Z`:

| Check | Result |
|---|---|
| Primary held `runtime-admission.lock` probes | 12/12 exit code 0 |
| Primary elapsed wall time | 0.502483s |
| Primary decisions | `{"skip": 12}` |
| Primary reasons | `{"lock_contention": 12}` |
| Primary terminal processes | 12 |
| Primary contention metrics | 11 written + 1 explicit stderr drop = 12 |
| Primary watchdog state writes | none |
| Primary normal metric writes | none |
| Primary contention metric SHA-256 | `fadf0944c7cabeab99906ad6ea133f06fd3f31a45736caa9ad28d4b684a2d1a5` |
| Primary `lock_held` values | `true` |
| Secondary held contention-metric lock probes | 12/12 exit code 0 |
| Secondary elapsed wall time | 0.526167s |
| Secondary decisions | `{"skip": 12}` |
| Secondary reasons | `{"lock_contention": 12}` |
| Secondary terminal processes | 12 |
| Secondary contention metric behavior | 0 written + 12 explicit stderr drops |
| Secondary watchdog state writes | none |
| Secondary normal metric writes | none |
| Post-release probe | exit code 0, `observe_only/supervisor_healthy` |
| Post-release health | exit code 0, `healthy: true` |
| Post-release watchdog state SHA-256 | `828802f83336c2b63408a43c41fe73fc09e05e2b2c4d824347e7fcd37b50e7cb` |
| Post-release normal metric SHA-256 | `059528bfe428f3627dfa45b59e4e2a4bbb043a8a99c04df066ee98a7930549f4` |
| Post-release activity log SHA-256 | `27277fcd99cafdfe4a8cfa8cea7b8ce165a83495beb07d956c64108df62f2912` |

Sample contention JSON:

```json
{
  "decision": "skip",
  "reason": "lock_contention",
  "pid": 2664646,
  "new_pid": null,
  "heartbeat_age_seconds": 0.0,
  "lock_held": true,
  "restart_count_window": 0,
  "restart_count_hour": 0,
  "log_path": null
}
```

Sample secondary-lock stderr:

```text
watchdog contention metric write dropped due to lock contention
```

Fresh rerun at `2026-07-17T06:53:59Z` also passed:

| Check | Result |
|---|---|
| Fixture root | `/tmp/pantheon-watchdog-postmerge-sliyoh7s` |
| Repo head | `d38c25bcea4513a5e27095fe59582169b3845760` |
| Primary held `runtime-admission.lock` probes | 12/12 exit code 0 |
| Primary elapsed wall time | 1.020193s |
| Primary decisions | `{"skip": 12}` |
| Primary reasons | `{"lock_contention": 12}` |
| Primary contention metrics | 11 written + 1 explicit stderr drop = 12 |
| Secondary held contention-metric lock probes | 12/12 exit code 0 |
| Secondary elapsed wall time | 0.451967s |
| Secondary contention metric behavior | 0 written + 12 explicit stderr drops |
| Post-release probe | exit code 0, `observe_only/supervisor_healthy` |
| Post-release health | exit code 0, `healthy: true` |
| Post-release watchdog state SHA-256 | `e48664f05d45c93d66688697023ce9830eda16be63c73c146f02eb5cc082f426` |
| Post-release normal metric SHA-256 | `e0534b88a37003c7a84e386ec24809b88505915ece344a2bb5c5fe8d858e25b5` |
| Post-release activity log SHA-256 | `9b65fb1645b3c44ea7152da3222f91dd8f256f5be6abf18b55d8af4592c25134` |

## Live Dev-Root Readback

Read-only commands used:

```bash
python3 scripts/supervisor_runtime_health.py \
  --config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json \
  --require-watchdog --json
tail -n 40 /home/lupin/code/pantheon/.orchestrator/logs/supervisor-watchdog-cron.log
crontab -l
python3 - <<'PY'
from pathlib import Path
lock = Path('/home/lupin/code/pantheon/.orchestrator/runtime-admission.lock')
st = lock.stat()
needle = f'{st.st_dev:02x}:{st.st_ino}'
for line in Path('/proc/locks').read_text().splitlines():
    if needle in line or str(st.st_ino) in line:
        print(line)
PY
```

Live identity:

- Supervisor PID: `1802483`.
- Supervisor command:
  `python3 -u /home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/supervisor.py --config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json --verbose`.
- Supervisor cwd: `/home/lupin/pantheon-ci-deploy/dev-root`.
- Cron watchdog command:
  `cd /home/lupin/pantheon-ci-deploy/dev-root && ... bash scripts/run-supervisor-watchdog.sh --config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json --restart`.

Live health result at `2026-07-17T06:14:27Z`:

- `healthy: false`.
- `supervisor_process_alive: ok`, PID `1802483`, `pid_matches: true`,
  singleton lock held.
- `supervisor_heartbeat_fresh: ok`, heartbeat age `353.304552s` under
  `900s`.
- `watchdog_state_present: ok`, but watchdog state updated at
  `2026-07-17T05:07:02Z`.
- `watchdog_probe_fresh: false`, watchdog age `4045.304552s` over `180s`.

The cron log was still being updated (`mtime 2026-07-17 06:14:01 +0000`) and
the last 40 lines were all:

```text
watchdog decision=skip reason=lock_contention pid=1802483 new_pid=None
```

Lock holder proof:

```text
41: FLOCK  ADVISORY  WRITE 1802483 08:01:1117775 0 EOF
```

`fuser -v /home/lupin/code/pantheon/.orchestrator/runtime-admission.lock`
also identified PID `1802483` as the holder.

Follow-up readback at `2026-07-17T06:34:20Z` used the correct dev-root repo
argument:

```bash
python3 /home/lupin/pantheon-ci-deploy/dev-root/scripts/supervisor_runtime_health.py \
  --repo /home/lupin/pantheon-ci-deploy/dev-root \
  --config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json \
  --require-watchdog --json
```

That read was healthy with watchdog state updated at `2026-07-17T06:33:01Z`
and watchdog age `79.455415s`. The normal watchdog metrics did contain three
consecutive successful scheduler events at `2026-07-17T06:28:02Z`,
`2026-07-17T06:29:02Z`, and `2026-07-17T06:30:01Z`, each with
`decision=observe_only`, `reason=supervisor_healthy`, and PID `1802483`.

However, a fresh passive readback started at `2026-07-17T06:35:24Z` immediately
returned to the bounded contention path:

| Event time | Decision | Health result |
|---|---|---|
| `2026-07-17T06:36:01Z` | `skip/lock_contention` | false; watchdog age `181.528677s` |
| `2026-07-17T06:37:02Z` | `skip/lock_contention` | false; watchdog age `241.805455s` |
| `2026-07-17T06:38:01Z` | `skip/lock_contention` | false; watchdog age `303.2685s` |

The current installed implementation still avoids unbounded waiter buildup, but
the real scheduler did not sustain the required three consecutive healthy
cycles with per-cycle health readback. The task remains blocked at product
acceptance.

Fresh owner re-dispatch readback at `2026-07-17T06:53Z-06:57Z` confirmed the
same product gate failure without live repair:

Read-only commands used:

```bash
python3 /home/lupin/pantheon-ci-deploy/dev-root/scripts/supervisor_runtime_health.py \
  --repo /home/lupin/pantheon-ci-deploy/dev-root \
  --config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json \
  --require-watchdog --json
tail -n 1 /home/lupin/code/pantheon/.orchestrator/logs/supervisor-watchdog-cron.log
tail -n 1 /home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/metrics/supervisor-watchdog.jsonl
tail -n 1 /home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/metrics/supervisor-watchdog-contention.jsonl
fuser -v /home/lupin/code/pantheon/.orchestrator/runtime-admission.lock \
  /home/lupin/code/pantheon/.orchestrator/supervisor.lock
```

Current live identity:

- Dev-root HEAD: `1c9d32dddc89a1ac8513f536c0b36fd33f3f5811`.
- Implementation merge ancestor check exited `0`.
- Dev-root watchdog source files had no diff in
  `.orchestrator/supervisor_watchdog.py`,
  `scripts/run-supervisor-watchdog.sh`,
  `scripts/supervisor_runtime_health.py`, or
  `docs/operations/supervisor-watchdog-persistence.md`.
- Runtime and supervisor locks are held by PID `1802483`.

Fresh three-cycle passive observation:

| Read time | Health | Watchdog state | Cron / metric result |
|---|---|---|---|
| `2026-07-17T06:55:01Z` | `healthy: false` | updated `2026-07-17T06:49:01Z`, age `360.345425s` | cron `skip/lock_contention`; normal metric still `06:49:01Z`; contention metric latest `06:54:02Z` |
| `2026-07-17T06:56:06Z` | `healthy: false` | updated `2026-07-17T06:49:01Z`, age `425.517135s` | cron `skip/lock_contention`; contention metric latest `06:56:01Z` |
| `2026-07-17T06:57:11Z` | `healthy: false` | updated `2026-07-17T06:49:01Z`, age `490.691351s` | cron `skip/lock_contention`; contention metric latest `06:57:01Z` |

The last normal watchdog metric remained:

```json
{"at":"2026-07-17T06:49:01Z","decision":"observe_only","reason":"supervisor_healthy","pid":1802483,"heartbeat_age_seconds":20.0,"lock_held":true}
```

The latest contention metric at the end of the readback was:

```json
{"at":"2026-07-17T06:57:01Z","decision":"skip","reason":"lock_contention","pid":1802483,"heartbeat_age_seconds":245.0,"lock_held":true}
```

## Blocker

The task cannot claim product-level acceptance because the required three
consecutive real scheduler/watchdog cycles did not produce fresh healthy
readbacks. The installed implementation behaves correctly under isolated
contention, and the live cron no longer accumulates watchdog waiters, but the
current live supervisor-held runtime lock keeps real watchdog cycles on the
bounded skip path often enough that the watchdog sample becomes stale. Per the
post-merge plan, stale watchdog samples fail closed.

No live repair was attempted: no processes were killed, no lock files were
removed, no runtime state was edited, and `scripts/sync-dev-root.sh` was not
run.
