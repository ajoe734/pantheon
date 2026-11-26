# Dev container memory budget

Task: DEV-COMPOSE-MEMORY-LIMITS-PEAK-SIZED-20261117 (repairs DEV-COMPOSE-MEMORY-LIMITS-20261117). Scope: dev `docker-compose.yml` only, every service in the `root` profile (58). Staging and production definitions are untouched.

Source of numbers: hosted cgroup `memory.peak` and `memory.events` (max) on release 80d4cf2ca, 2026-11-17 15:15-15:20Z, containers up about 60 minutes (host 32086 MiB, no swap). Machine-readable copy: `evidence/DEV-COMPOSE-MEMORY-LIMITS-PEAK-SIZED-20261117/evidence.json`. The first budget (`evidence/DEV-COMPOSE-MEMORY-LIMITS-20261117/`) is kept as history only.

## Why the first budget was wrong

It used twice one `docker stats` sample taken at 02:10Z. `docker stats` leaves out inactive file cache, and one sample misses peaks and child processes. The kernel enforces the limit on anonymous memory plus page cache plus kernel memory of every process in the container. Result in the first hour: the OpenClaw adapter was OOM-killed 43 times (54 restarts), postgres reached its limit 1127463 times, paper-fleet-reconciler 220547 times, alpha-replication-worker 329 times.

## Rule

Each service gets `mem_limit: ${DEV_MEM_LIMIT_<SERVICE>:-<default>}`. Default >= 1.5 x the cgroup `memory.peak` recorded in the evidence for that service (and >= 128 MiB). `memory.peak` counts anonymous memory, page cache and child processes, so it is the number the kernel actually enforces. A root-profile service with no recorded peak must carry a stated not-measured reason in the evidence (`openclaw-data-init` and `source-ingest-controller-migrate` are one-shot jobs). `scripts/test_compose_memory_limits.py` enforces this.

**Start-up peak (DEV-MEMORY-BUDGET-STARTUP-PEAK-20261118).** The recorded peak of a service must include one start of that service, not only its steady state. Measure it by reading cgroup `memory.peak` after a fresh start (`docker compose up -d --force-recreate <service>`, wait until healthy, then read `memory.peak`); a restart resets `memory.peak`, `memory.events` and `oom_kill`, so a reading taken after a later restart no longer holds the start-up value. Services other than incidents are marked not measured at start in `evidence/DEV-MEMORY-BUDGET-STARTUP-PEAK-20261118/evidence.json`. Incidents: about 1045 MiB anonymous memory in the uvicorn process at start (steady peak 631 MiB); it was killed at the 1024 MiB limit on the releases of 2026-11-17 20:40:48Z and 2026-11-18 04:46:09Z (dmesg), the restart policy brought it back, so the limit is now 2g (1.5 x 1045 = 1568 MiB).

A peak recorded at the limit (limit reached many times) is a lower bound only; those services are exceptions below.

## Exceptions to the 1.5x rule

- `openclaw-gateway-adapter` 3g: idle about 200 MiB + 6 concurrent openclaw CLI calls x about 400 MiB each (two node processes: openclaw about 62 MiB and openclaw-gateway 170 to 345 MiB, for 5 to 7 seconds) = about 2600 MiB, rounded to 3g. The adapter has no concurrency cap today, so the limit is sized for six overlapping calls. Under a temporary 1 GiB the peak was 791 MiB with two overlapping calls. Callers in 10 minutes: 32 cron POSTs from persona, 17 status GETs from operator-bff, 1 structured call.
- `postgres` 8g: the limit bounds the database page cache. At 2g the file cache was 1984 of 2048 MiB (anon 22 MiB, shmem 143 MiB), the limit was reached over one million times in an hour and container IO pressure avg300 was 11.66 percent. The peak is capped, so 1.5x of it is meaningless.
- `paper-fleet-reconciler` 1280m: peak capped at 640 (anon 253 MiB, file 382 MiB). `alpha-replication-worker` 512m: peak capped at 256. Both set to 2x the capped peak; re-check on the next daily measurement. ALPHA-TEMPLATE-EVALUATION-SCHEDULED-20261125 then raised `alpha-replication-worker` to 768m because the scheduled AlphaTemplate evaluation runs in it (one full 171-template cycle measured at 395 MiB peak RSS, upper bound 452 MiB).
- `hindsight` 10g: peak 5784 MiB; 1.5x is 8676 MiB, 10g gives 1.77x (the incident floor was 8g).
- `search-svc` 13g: kept above the earlier 11.5 GiB peak so a leak restarts search-svc only (1.5x of the current 4578 MiB peak would be lower, 13g is not reduced).
- `loop-run-projector-scheduler`: keeps its pre-existing 16g limit (`LIFECYCLE_PROJECTOR_MEMORY_LIMIT`).
- `research-orchestrator-svc` 384m (steady peak 99): raised from 256m by DISCOVERY-GOVERNED-MARKET-DATASET-20261125 because template search builds the governed market boundary in this service (measured on a 1,950 symbol x 483 day store: about +50 MiB pyarrow import, +75 MiB boundary, +45 MiB for a 60 symbol dataset, 237 MiB peak for the process alone); 99 MiB steady + that exceeds 256m.
- `persona` 512m (peak 230) and `postmortems` 512m (peak 280) are not exceptions: they follow the 1.5x rule and were raised from 256m and 384m.

## Table

| Service | cgroup peak MiB | Old limit MiB | Limit (default) | Env override | Limit / peak |
|---|---|---|---|---|---|
| loop-run-projector-scheduler | 82 | 16384 | 16g | `DEV_MEM_LIMIT_LOOP_RUN_PROJECTOR_SCHEDULER` | 199.8x |
| search-svc | 4578 | 13312 | 13g | `DEV_MEM_LIMIT_SEARCH_SVC` | 2.9x |
| hindsight | 5784 | 6144 | 10g | `DEV_MEM_LIMIT_HINDSIGHT` | 1.8x |
| postgres | 2048 | 2048 | 8g | `DEV_MEM_LIMIT_POSTGRES` | 4.0x |
| source-ingest | 3469 | 6144 | 6g | `DEV_MEM_LIMIT_SOURCE_INGEST` | 1.8x |
| telemetry | 694 | 4096 | 4g | `DEV_MEM_LIMIT_TELEMETRY` | 5.9x |
| openclaw-gateway-adapter | 791 | 256 | 3g | `DEV_MEM_LIMIT_OPENCLAW_GATEWAY_ADAPTER` | 3.9x |
| operator-bff | 705 | 3072 | 3g | `DEV_MEM_LIMIT_OPERATOR_BFF` | 4.4x |
| openclaw-gateway | 658 | 1280 | 1280m | `DEV_MEM_LIMIT_OPENCLAW_GATEWAY` | 1.9x |
| paper-fleet-reconciler | 640 | 640 | 1280m | `DEV_MEM_LIMIT_PAPER_FLEET_RECONCILER` | 2.0x |
| retrieval-postgres | 139 | 1024 | 1g | `DEV_MEM_LIMIT_RETRIEVAL_POSTGRES` | 7.4x |
| incidents | 1045 (start-up; steady 631) | 1024 | 2g | `DEV_MEM_LIMIT_INCIDENTS` | 2.0x |
| signal-store | 305 | 640 | 640m | `DEV_MEM_LIMIT_SIGNAL_STORE` | 2.1x |
| strategy-distillation-worker | 423 | 640 | 640m | `DEV_MEM_LIMIT_STRATEGY_DISTILLATION_WORKER` | 1.5x |
| broker | 377 | 640 | 640m | `DEV_MEM_LIMIT_BROKER` | 1.7x |
| memory-index-postgres | 109 | 512 | 512m | `DEV_MEM_LIMIT_MEMORY_INDEX_POSTGRES` | 4.7x |
| nats | 25 | 512 | 512m | `DEV_MEM_LIMIT_NATS` | 20.5x |
| alpha-replication-worker | 256 | 256 | 768m | `DEV_MEM_LIMIT_ALPHA_REPLICATION_WORKER` | 3.0x |
| postmortems | 280 | 384 | 512m | `DEV_MEM_LIMIT_POSTMORTEMS` | 1.8x |
| persona | 230 | 256 | 512m | `DEV_MEM_LIMIT_PERSONA` | 2.2x |
| consultation-svc | 110 | 256 | 256m | `DEV_MEM_LIMIT_CONSULTATION_SVC` | 2.3x |
| source-ingest-controller-migrate | not measured | n/a | 256m | `DEV_MEM_LIMIT_SOURCE_INGEST_CONTROLLER_MIGRATE` | n/a |
| source-ingest-scheduler | 97 | 256 | 256m | `DEV_MEM_LIMIT_SOURCE_INGEST_SCHEDULER` | 2.6x |
| search-index-scheduler | 34 | 256 | 256m | `DEV_MEM_LIMIT_SEARCH_INDEX_SCHEDULER` | 7.5x |
| training-session-svc | 81 | 256 | 256m | `DEV_MEM_LIMIT_TRAINING_SESSION_SVC` | 3.2x |
| training-session-preview-worker | 32 | 256 | 256m | `DEV_MEM_LIMIT_TRAINING_SESSION_PREVIEW_WORKER` | 8.0x |
| policy-learning-svc | 85 | 256 | 256m | `DEV_MEM_LIMIT_POLICY_LEARNING_SVC` | 3.0x |
| policy-learning-shadow-eval-scheduler | 49 | 256 | 256m | `DEV_MEM_LIMIT_POLICY_LEARNING_SHADOW_EVAL_SCHEDULER` | 5.2x |
| research-orchestrator-svc | 99 | 384 | 384m | `DEV_MEM_LIMIT_RESEARCH_ORCHESTRATOR_SVC` | 3.9x |
| runtime-manager | 85 | 256 | 256m | `DEV_MEM_LIMIT_RUNTIME_MANAGER` | 3.0x |
| governance | 87 | 256 | 256m | `DEV_MEM_LIMIT_GOVERNANCE` | 2.9x |
| agora-interaction-worker | 80 | 256 | 256m | `DEV_MEM_LIMIT_AGORA_INTERACTION_WORKER` | 3.2x |
| router | 62 | 256 | 256m | `DEV_MEM_LIMIT_ROUTER` | 4.1x |
| web-channel | 72 | 256 | 256m | `DEV_MEM_LIMIT_WEB_CHANNEL` | 3.6x |
| evaluation | 61 | 256 | 256m | `DEV_MEM_LIMIT_EVALUATION` | 4.2x |
| feedback | 64 | 256 | 256m | `DEV_MEM_LIMIT_FEEDBACK` | 4.0x |
| learning-feedback-bridge | 25 | 256 | 256m | `DEV_MEM_LIMIT_LEARNING_FEEDBACK_BRIDGE` | 10.2x |
| memory | 63 | 256 | 256m | `DEV_MEM_LIMIT_MEMORY` | 4.1x |
| registry | 129 | 256 | 256m | `DEV_MEM_LIMIT_REGISTRY` | 2.0x |
| optimizer-svc | 58 | 256 | 256m | `DEV_MEM_LIMIT_OPTIMIZER_SVC` | 4.4x |
| promotion | 61 | 256 | 256m | `DEV_MEM_LIMIT_PROMOTION` | 4.2x |
| capital | 66 | 256 | 256m | `DEV_MEM_LIMIT_CAPITAL` | 3.9x |
| deployment | 77 | 256 | 256m | `DEV_MEM_LIMIT_DEPLOYMENT` | 3.3x |
| deployment-outbox-consumer | 87 | 256 | 256m | `DEV_MEM_LIMIT_DEPLOYMENT_OUTBOX_CONSUMER` | 2.9x |
| evolution | 127 | 256 | 256m | `DEV_MEM_LIMIT_EVOLUTION` | 2.0x |
| evolution-dispatch-worker | 64 | 256 | 256m | `DEV_MEM_LIMIT_EVOLUTION_DISPATCH_WORKER` | 4.0x |
| evolution-daily-sweep-scheduler | 33 | 256 | 256m | `DEV_MEM_LIMIT_EVOLUTION_DAILY_SWEEP_SCHEDULER` | 7.8x |
| evolution-threshold-sweep-producer | 40 | 256 | 256m | `DEV_MEM_LIMIT_EVOLUTION_THRESHOLD_SWEEP_PRODUCER` | 6.4x |
| lineage-read | 62 | 256 | 256m | `DEV_MEM_LIMIT_LINEAGE_READ` | 4.1x |
| paper-signal-producer | 71 | 256 | 256m | `DEV_MEM_LIMIT_PAPER_SIGNAL_PRODUCER` | 3.6x |
| reconciliation-drift-svc | 154 | 256 | 256m | `DEV_MEM_LIMIT_RECONCILIATION_DRIFT_SVC` | 1.7x |
| reconciliation-drift-consumer | 73 | 256 | 256m | `DEV_MEM_LIMIT_RECONCILIATION_DRIFT_CONSUMER` | 3.5x |
| reconciliation-drift-scheduler | 48 | 256 | 256m | `DEV_MEM_LIMIT_RECONCILIATION_DRIFT_SCHEDULER` | 5.3x |
| reconciliation-drift-incident-listener | 36 | 256 | 256m | `DEV_MEM_LIMIT_RECONCILIATION_DRIFT_INCIDENT_LISTENER` | 7.1x |
| research-worker-gateway-svc | 59 | 256 | 256m | `DEV_MEM_LIMIT_RESEARCH_WORKER_GATEWAY_SVC` | 4.3x |
| monitor-agent | 50 | 256 | 256m | `DEV_MEM_LIMIT_MONITOR_AGENT` | 5.1x |
| persona-evaluator-agent | 35 | 256 | 256m | `DEV_MEM_LIMIT_PERSONA_EVALUATOR_AGENT` | 7.3x |
| openclaw-data-init | not measured | n/a | 128m | `DEV_MEM_LIMIT_OPENCLAW_DATA_INIT` | n/a |

## Overcommit vs the 32 GiB host

- Sum of all limits: 84608 MiB (82.6 GiB) = 2.64x host RAM. Deliberately overcommitted: limits are ceilings, not reservations.
- Sum of recorded peaks: 24740 MiB (24.2 GiB), of which search-svc is 4578 MiB.
- Limits that mostly bound reclaimable page cache rather than process memory: postgres (8g; anon 22 MiB), paper-fleet-reconciler (file 382 of 640 MiB), hindsight and search-svc (large cached data). A page cache ceiling is reclaimed under pressure and does not OOM-kill, but a ceiling that is too low causes IO thrash, which is what happened to postgres.
- Limits that bound process memory: openclaw-gateway-adapter (3g, six concurrent node pairs), operator-bff, source-ingest, telemetry, loop-run-projector-scheduler (16g).
- The ceilings cannot all be reached at once at the recorded load; only runaway services or the adapter under many concurrent CLI calls approach theirs.

## Validity of the peaks

The recorded peaks cover about one hour (evening) on release 80d4cf2ca without a persona daily cycle (10:00Z). They are not a daily maximum. The daily hosted measurement is the place where limits are re-checked: after each daily cycle, read `memory.peak` and `memory.events` per container and update the evidence file; any service whose limit falls below 1.5x its peak, or whose `max` event count is above 0, is raised.

## Daily re-check and how to re-size from it

`.github/workflows/dev-storage-budget.yml` (daily 03:17Z, `scripts/measure_hosted_storage.py`) reads, for every running container, the cgroup `memory.max`, `memory.peak`, `memory.events` (`max`, `oom_kill`) and the docker restart count, and puts them in the `memory_table` of the `hosted-storage-report` artifact (columns: service, limit, peak, percent, max_events, oom_kill, restarts). To re-size a limit: download the latest report (or several days), take `peak` per service, and set the new limit to at least 1.5x the highest peak seen, except for cache-bound services (postgres) where the peak only shows page cache. Then update the table above and the `DEV_MEM_LIMIT_<SERVICE>` default.

| finding | severity | meaning |
|---|---|---|
| `container_oom_killed` | red (RC3) | `oom_kill` above zero: raise the limit or fix the leak |
| `container_restarts_grew` | red (RC3) | restart count grew since the previous report |
| `container_restarted_since_creation` | warning | docker restart count above zero since the container was created, reported on every report (also the first, and also when the count did not grow); names the service, the count and the container creation time |
| `memory_limit_reached` | warning | `max` counter grew since the previous report |
| `memory_peak_near_limit` | warning | peak is at or above 90 percent of the limit (a page-cache-bound container reaches this without harm) |
| `memory_limit_missing` | warning | container has no limit |

cgroup `oom_kill` and `memory.peak` are reset by a restart, so a zero `oom_kill` next to a restart count above zero is not evidence that no kill happened (check dmesg on the host). Counters reset when a container is recreated; a counter lower than in the previous report is treated as a reset and reports nothing. Collection is read-only: `docker ps`, `docker inspect` (needs docker socket access, which the transport user already has) and reads of world-readable files under `/sys/fs/cgroup/system.slice/docker-<id>.scope/`. Nothing is written on hosted.

## Rollback

Limits live only in the dev compose file. Revert the commit (or set `DEV_MEM_LIMIT_<SERVICE>` to a larger value) to roll back; the previous accepted release is unaffected. Staging and production compose files are untouched (tested).

## Residual findings

- The adapter has no concurrency cap on openclaw CLI calls; every cron poll or status poll spawns two node processes of about 400 MiB for 5 to 7 seconds. Owned by the persona-loop coordinator (PERSONA-CYCLE-CONTROLLER-CRON-POLL-BUDGET-20261117 reduces the cron polling).
- `pantheon-minio-1` and `pantheon-dev-paper-principal-issuer-1` run without a limit.
- Scheduled workflows on this repository start hours late or not at all (the daily measurement had never run by schedule before the manual run 44245690990 on 2026-11-18). Not fixed here.
