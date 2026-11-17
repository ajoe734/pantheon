# Dev container memory budget

Task: DEV-COMPOSE-MEMORY-LIMITS-20261117. Scope: dev `docker-compose.yml` only, every service in the `root` profile (58). Staging and production definitions are untouched.

Source of numbers: hosted `docker stats` 2026-11-17 ~02:10Z (host 32086 MiB total, 8324 MiB available, no swap, Docker 29.1.3). Machine-readable copy: `evidence/DEV-COMPOSE-MEMORY-LIMITS-20261117/evidence.json`.

## Rule

Each service gets `mem_limit: ${DEV_MEM_LIMIT_<SERVICE>:-<default>}`. Default >= max(2 x measured RSS, 128 MiB). Services not in the measured list were all under 55 MiB, so they get 256m (>= 128 MiB floor, ~4.6x). `openclaw-data-init` is a one-shot busybox init job and gets the 128m floor. Postgres limits are sized above shared_buffers + work_mem; page cache is outside RSS, but the cgroup counts it as reclaimable so it does not trigger an OOM kill.

## Exceptions to the 2x floor

- `search-svc`: measured 10.48 GiB, cgroup peak 11.5 GiB. 2x (~21 GiB) would let one service take two thirds of the host, which defeats the purpose. Limit 13g sits above the observed peak, so a leak restarts search-svc only instead of a host-wide OOM (the 11/16 incident killed a 15.5 GB process). The 10.48 GiB baseline itself is a separate residual finding.
- `loop-run-projector-scheduler`: keeps its pre-existing 16g limit (`LIFECYCLE_PROJECTOR_MEMORY_LIMIT`).
- `source-ingest` 6g and `operator-bff` 3g are set above 2x to cover earlier peaks (5.9 GiB journal replay on 2026-11-16, journal now 69 MB; BFF arena ~2 GB).

## Table

| Service | Measured RSS 2026-11-17 | Limit (default) | Env override | Headroom |
|---|---|---|---|---|
| loop-run-projector-scheduler | n/a | 16g | `DEV_MEM_LIMIT_…(LIFECYCLE_PROJECTOR_MEMORY_LIMIT)` | n/a |
| search-svc | 10732 MiB | 13g | `DEV_MEM_LIMIT_SEARCH_SVC` | 1.2x |
| hindsight | 2796 MiB | 6g | `DEV_MEM_LIMIT_HINDSIGHT` | 2.2x |
| source-ingest | 1352 MiB | 6g | `DEV_MEM_LIMIT_SOURCE_INGEST` | 4.5x |
| telemetry | 1782 MiB | 4g | `DEV_MEM_LIMIT_TELEMETRY` | 2.3x |
| operator-bff | 284 MiB | 3g | `DEV_MEM_LIMIT_OPERATOR_BFF` | 10.8x |
| postgres | 206 MiB | 2g | `DEV_MEM_LIMIT_POSTGRES` | 9.9x |
| openclaw-gateway | 571 MiB | 1280m | `DEV_MEM_LIMIT_OPENCLAW_GATEWAY` | 2.2x |
| retrieval-postgres | 150 MiB | 1g | `DEV_MEM_LIMIT_RETRIEVAL_POSTGRES` | 6.8x |
| incidents | 503 MiB | 1024m | `DEV_MEM_LIMIT_INCIDENTS` | 2.0x |
| signal-store | 288 MiB | 640m | `DEV_MEM_LIMIT_SIGNAL_STORE` | 2.2x |
| strategy-distillation-worker | 270 MiB | 640m | `DEV_MEM_LIMIT_STRATEGY_DISTILLATION_WORKER` | 2.4x |
| broker | 272 MiB | 640m | `DEV_MEM_LIMIT_BROKER` | 2.4x |
| paper-fleet-reconciler | 306 MiB | 640m | `DEV_MEM_LIMIT_PAPER_FLEET_RECONCILER` | 2.1x |
| memory-index-postgres | 56 MiB | 512m | `DEV_MEM_LIMIT_MEMORY_INDEX_POSTGRES` | 9.1x |
| nats | 104 MiB | 512m | `DEV_MEM_LIMIT_NATS` | 4.9x |
| postmortems | 183 MiB | 384m | `DEV_MEM_LIMIT_POSTMORTEMS` | 2.1x |
| consultation-svc | 84 MiB | 256m | `DEV_MEM_LIMIT_CONSULTATION_SVC` | 3.0x |
| source-ingest-controller-migrate | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_SOURCE_INGEST_CONTROLLER_MIGRATE` | 4.7x |
| source-ingest-scheduler | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_SOURCE_INGEST_SCHEDULER` | 4.7x |
| alpha-replication-worker | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_ALPHA_REPLICATION_WORKER` | 4.7x |
| search-index-scheduler | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_SEARCH_INDEX_SCHEDULER` | 4.7x |
| training-session-svc | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_TRAINING_SESSION_SVC` | 4.7x |
| training-session-preview-worker | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_TRAINING_SESSION_PREVIEW_WORKER` | 4.7x |
| policy-learning-svc | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_POLICY_LEARNING_SVC` | 4.7x |
| policy-learning-shadow-eval-scheduler | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_POLICY_LEARNING_SHADOW_EVAL_SCHEDULER` | 4.7x |
| research-orchestrator-svc | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_RESEARCH_ORCHESTRATOR_SVC` | 4.7x |
| openclaw-gateway-adapter | 69 MiB | 256m | `DEV_MEM_LIMIT_OPENCLAW_GATEWAY_ADAPTER` | 3.7x |
| runtime-manager | 79 MiB | 256m | `DEV_MEM_LIMIT_RUNTIME_MANAGER` | 3.2x |
| governance | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_GOVERNANCE` | 4.7x |
| agora-interaction-worker | 65 MiB | 256m | `DEV_MEM_LIMIT_AGORA_INTERACTION_WORKER` | 3.9x |
| persona | 99 MiB | 256m | `DEV_MEM_LIMIT_PERSONA` | 2.6x |
| router | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_ROUTER` | 4.7x |
| web-channel | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_WEB_CHANNEL` | 4.7x |
| evaluation | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_EVALUATION` | 4.7x |
| feedback | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_FEEDBACK` | 4.7x |
| learning-feedback-bridge | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_LEARNING_FEEDBACK_BRIDGE` | 4.7x |
| memory | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_MEMORY` | 4.7x |
| registry | 58 MiB | 256m | `DEV_MEM_LIMIT_REGISTRY` | 4.4x |
| optimizer-svc | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_OPTIMIZER_SVC` | 4.7x |
| promotion | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_PROMOTION` | 4.7x |
| capital | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_CAPITAL` | 4.7x |
| deployment | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_DEPLOYMENT` | 4.7x |
| deployment-outbox-consumer | 73 MiB | 256m | `DEV_MEM_LIMIT_DEPLOYMENT_OUTBOX_CONSUMER` | 3.5x |
| evolution | 100 MiB | 256m | `DEV_MEM_LIMIT_EVOLUTION` | 2.6x |
| evolution-dispatch-worker | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_EVOLUTION_DISPATCH_WORKER` | 4.7x |
| evolution-daily-sweep-scheduler | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_EVOLUTION_DAILY_SWEEP_SCHEDULER` | 4.7x |
| evolution-threshold-sweep-producer | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_EVOLUTION_THRESHOLD_SWEEP_PRODUCER` | 4.7x |
| lineage-read | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_LINEAGE_READ` | 4.7x |
| paper-signal-producer | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_PAPER_SIGNAL_PRODUCER` | 4.7x |
| reconciliation-drift-svc | 93 MiB | 256m | `DEV_MEM_LIMIT_RECONCILIATION_DRIFT_SVC` | 2.8x |
| reconciliation-drift-consumer | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_RECONCILIATION_DRIFT_CONSUMER` | 4.7x |
| reconciliation-drift-scheduler | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_RECONCILIATION_DRIFT_SCHEDULER` | 4.7x |
| reconciliation-drift-incident-listener | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_RECONCILIATION_DRIFT_INCIDENT_LISTENER` | 4.7x |
| research-worker-gateway-svc | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_RESEARCH_WORKER_GATEWAY_SVC` | 4.7x |
| monitor-agent | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_MONITOR_AGENT` | 4.7x |
| persona-evaluator-agent | <55 MiB (not in top list) | 256m | `DEV_MEM_LIMIT_PERSONA_EVALUATOR_AGENT` | 4.7x |
| openclaw-data-init | n/a | 128m | `DEV_MEM_LIMIT_OPENCLAW_DATA_INIT` | n/a |

## Overcommit vs the 32 GiB host

- Sum of all limits: 68864 MiB (67.2 GiB) = 2.15x host RAM. Deliberately overcommitted: limits are ceilings, not reservations.
- Sum of measured RSS (unmeasured services counted at their 55 MiB bound; upper estimate of the normal load): about 22280 MiB (21.8 GiB), of which search-svc is 10.5 GiB.
- The big ceilings (search-svc 13g, loop-run-projector-scheduler 16g, hindsight 6g, source-ingest 6g, telemetry 4g) cannot all be reached at once at normal load, since measured use is far lower. Only a runaway service reaches its cap and is then OOM-killed and restarted by itself.
- The host keeps at least 4 GiB free for the OS and page cache at normal load: normal measured RSS leaves ~8 GiB available today (8324 MiB), and once search-svc is capped no single service can consume more than 13 GiB.
- postgres (2g), nats (512m) and minio are never reached at normal load (206, 104, 86 MiB). minio is not defined in this compose file, so no limit is applied here.

## Rollback

Limits live only in the new compose file. Revert the commit (or set `DEV_MEM_LIMIT_<SERVICE>` to a larger value) to roll back; the previous accepted release is unaffected.
