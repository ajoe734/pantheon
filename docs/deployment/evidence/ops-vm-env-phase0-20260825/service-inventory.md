# Compose Service Inventory by Profile (Phase 0 Baseline)

- **Measurement Date:** 2026-08-25
- **Source Configuration:** `docker-compose.yml` (67 services), `docker-compose.control.yml` (21 services), `docker-compose.exec.yml` (6 services), `docker-compose.staging-full.yml` (15 services)
- **Active Environment:** `pantheon-lupin-dev` (Dev VM, 50 running containers)

---

## Summary by Profile

| Profile | Total Services across Overlays | Running on Dev VM | Primary Role | Singleton / Ownership Constraint |
|---|---|---|---|---|
| `core` | 14 | 13 (1 init exited) | Core state, message bus, control plane APIs, BFF, ingress | Postgres / NATS / MinIO are shared singleton backends; BFF is single replica on host |
| `workers` | 17 | 16 (1 on-demand) | Background schedulers, queue consumers, drift listeners | **Strict Singleton**: exactly one active owner per consumer/scheduler to prevent duplicate event writes |
| `research` | 24 | 12 (12 dormant/smokes) | Ingest, search index, training sessions, evaluation, RL/LLM smoke tests | Research APIs are stateless; dormant smoke containers run on-demand only |
| `management-ai` | 8 | 6 (2 init/test) | OpenClaw gateway, adapter, consultation, persona, web-channel | Product runtime diagnostics only; no repository or deployment authority |
| `execution` | 7 | 3 (4 staging/live-only or disabled) | Runtime manager, broker/exchange adapters, signal store, execution loop | **Strict Isolation**: paper/sandbox only on Dev; real-capital execution isolated to Prod Execution VM |
| **Total Unique Services** | **70** | **50** | **Full Platform Baseline** | **Defined across all Compose overlays** |

---

## Profile: `CORE` (14 Services)

| Service | Dockerfile / Image | Ports | Depends On | Singleton / Lifecycle Note |
|---|---|---|---|---|
| `capital` | `services/capital/Dockerfile` | `${CAPITAL_PORT:-18092}:8092` | `postgres` | Stateless service; single replica in base VM |
| `deployment` | `services/deployment/Dockerfile` | `${DEPLOYMENT_PORT:-18095}:8095` | `governance` | Stateless service; single replica in base VM |
| `governance` | `services/governance/Dockerfile` | `${GOVERNANCE_PORT:-18082}:8082` | `postgres, minio, nats` | Stateless service; single replica in base VM |
| `incidents` | `services/incidents/Dockerfile` | `${INCIDENTS_PORT:-18090}:8090` | `runtime-manager, telemetry, postgres, nats` (base) / `telemetry, postgres, nats` (control) | Stateless service; single replica in base VM |
| `lineage-read` | `services/lineage-read/Dockerfile` | `${LINEAGE_READ_PORT:-18094}:8094` | `None` | Stateless service; single replica in base VM |
| `minio` | `minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e` | `${MINIO_API_PORT:-19000}:9000, ${MINIO_CONSOLE_PORT:-19001}:9001` | `None` | Stateful singleton data/message backend |
| `minio-init` | `minio/mc:RELEASE.2024-01-16T16-06-34Z` | `Internal only` | `minio` | One-shot initialization container (exits 0) |
| `nats` | `nats:2.11-alpine` | `${NATS_PORT:-14222}:4222, ${NATS_MONITOR_PORT:-18222}:8222` | `None` | Stateful singleton data/message backend |
| `operator-bff` | `services/control-plane/bff/Dockerfile` | `${OPERATOR_BFF_PORT:-${BFF_PORT:-18001}}:8001` | `registry, consultation-svc, source-ingest, search-svc, training-session-svc, policy-learning-svc, research-orchestrator-svc, research-worker-gateway-svc, memory, openclaw-gateway-adapter, paper-fleet-reconciler, governance, runtime-manager, deployment, capital, evolution, incidents, postmortems, telemetry, loop-run-projector-scheduler, lineage-read, postgres, nats` (base) / `telemetry, governance, deployment, capital, incidents, postmortems, registry, postgres, nats` (control) / `registry, consultation-svc, source-ingest, search-svc, training-session-svc, policy-learning-svc, research-orchestrator-svc, reconciliation-drift-svc, research-worker-gateway-svc, openclaw-gateway-adapter` (staging-full) | Request-facing API; blue/green candidate during deploy |
| `postgres` | `postgres:16-alpine` | `${POSTGRES_PORT:-15432}:5432` | `None` | Stateful singleton data/message backend |
| `postmortems` | `services/postmortems/Dockerfile` | `${POSTMORTEMS_PORT:-18091}:8091` | `incidents, postgres, minio` | Stateless service; single replica in base VM |
| `reconciliation-drift-svc` | `services/reconciliation-drift/Dockerfile` | `${RECONCILIATION_DRIFT_PORT:-18102}:8102` | `telemetry, lineage-read, runtime-manager, postgres, incidents` (base) / `telemetry, lineage-read, postgres` (control) / `telemetry, lineage-read, postgres, evolution` (staging-full) | Stateless service; single replica in base VM |
| `registry` | `services/registry/Dockerfile` | `${REGISTRY_PORT:-18087}:8087` | `postgres, minio, nats` | Stateless service; single replica in base VM |
| `telemetry` | `services/telemetry/Dockerfile` | `${TELEMETRY_PORT:-18083}:8083` | `runtime-manager, postgres, nats` (base) / `postgres, nats` (control) | Stateless service; single replica in base VM |

## Profile: `WORKERS` (17 Services)

| Service | Dockerfile / Image | Ports | Depends On | Singleton / Lifecycle Note |
|---|---|---|---|---|
| `alpha-replication-worker` | `services/research/Dockerfile` | `Internal only` | `research-orchestrator-svc, registry` | Singleton worker; exactly 1 replica required |
| `deployment-outbox-consumer` | `services/deployment/Dockerfile` | `Internal only` | `deployment, runtime-manager` | Singleton worker; exactly 1 replica required |
| `evolution-daily-sweep-scheduler` | `services/evolution/Dockerfile` | `Internal only` | `evolution` | Singleton worker; exactly 1 replica required |
| `evolution-dispatch-worker` | `services/evolution/Dockerfile` | `Internal only` | `evolution` | Singleton worker; exactly 1 replica required |
| `evolution-threshold-sweep-producer` | `services/evolution/Dockerfile` | `Internal only` | `telemetry, incidents` | Singleton worker; exactly 1 replica required |
| `loop-run-projector-scheduler` | `services/telemetry/Dockerfile` | `Internal only` | `postgres, telemetry` | Singleton worker; exactly 1 replica required |
| `paper-fleet-reconciler` | `services/execution/runtime-manager/Dockerfile` | `${PAPER_FLEET_RECONCILER_PORT:-18011}:8011` | `runtime-manager, signal-store, source-ingest` | Singleton worker; exactly 1 replica required |
| `paper-signal-producer` | `services/execution/lean_runtime/Dockerfile` | `Internal only` | `runtime-manager, signal-store, source-ingest` | Singleton worker; exactly 1 replica required |
| `policy-learning-shadow-eval-scheduler` | `services/policy-learning/Dockerfile` | `Internal only` | `policy-learning-svc, operator-bff` | Singleton worker; exactly 1 replica required |
| `reconciliation-drift-consumer` | `services/reconciliation-drift/Dockerfile` | `Internal only` | `reconciliation-drift-svc, telemetry` | Singleton worker; exactly 1 replica required |
| `reconciliation-drift-incident-listener` | `services/reconciliation-drift/Dockerfile` | `Internal only` | `reconciliation-drift-svc, incidents` | Singleton worker; exactly 1 replica required |
| `reconciliation-drift-scheduler` | `services/reconciliation-drift/Dockerfile` | `Internal only` | `reconciliation-drift-svc` | Singleton worker; exactly 1 replica required |
| `search-index-scheduler` | `services/search/Dockerfile` | `Internal only` | `search-svc` | Singleton worker; exactly 1 replica required |
| `source-ingest-agora-projector` | `services/source_ingestion/Dockerfile` | `Internal only` | `source-ingest, source-ingest-scheduler` | Singleton worker; exactly 1 replica required |
| `source-ingest-scheduler` | `services/source_ingestion/Dockerfile` | `Internal only` | `source-ingest, source-ingest-controller-migrate` | Singleton worker; exactly 1 replica required |
| `strategy-distillation-worker` | `services/source_ingestion/Dockerfile` | `Internal only` | `source-ingest, registry` | Singleton worker; exactly 1 replica required |
| `training-session-preview-worker` | `services/training-session/Dockerfile` | `Internal only` | `training-session-svc` | Singleton worker; exactly 1 replica required |

## Profile: `RESEARCH` (24 Services)

| Service | Dockerfile / Image | Ports | Depends On | Singleton / Lifecycle Note |
|---|---|---|---|---|
| `evaluation` | `services/evaluation/Dockerfile` | `${EVALUATION_PORT:-18084}:8084` | `postgres, minio, nats` | Stateless service; single replica in base VM |
| `evolution` | `services/evolution/Dockerfile` | `${EVOLUTION_PORT:-18093}:8093` | `runtime-manager, governance` (base) / `incidents, postgres` (control) | Stateless service; single replica in base VM |
| `experiments-dormant-smoke` | `services/registry/experiments/Dockerfile` | `Internal only` | `None` | Dormant / on-demand verification container |
| `feedback` | `services/control-plane/feedback/Dockerfile` | `${FEEDBACK_PORT:-18085}:8085` | `None` (base) / `postgres, minio, nats` (control) | Stateless service; single replica in base VM |
| `finrl-dormant-smoke` | `Dockerfile` | `Internal only` | `None` | Dormant / on-demand verification container |
| `lifecycle-projector-capacity-benchmark` | `services/telemetry/Dockerfile` | `Internal only` | `postgres, telemetry` | Dormant / on-demand verification container |
| `memory` | `services/memory/Dockerfile` | `${MEMORY_PORT:-18086}:8086` | `postgres, minio, nats` | Stateless service; single replica in base VM |
| `mlflow-dormant-smoke` | `Dockerfile` | `Internal only` | `None` | Dormant / on-demand verification container |
| `optimizer-svc` | `services/optimizer-svc/Dockerfile` | `${OPTIMIZER_PORT:-18088}:8088` | `postgres, minio, nats` | Stateless service; single replica in base VM |
| `oss-activation-ready-smoke-matrix` | `Dockerfile.smoke` | `Internal only` | `None` | Dormant / on-demand verification container |
| `policy-learning-svc` | `services/policy-learning/Dockerfile` | `${POLICY_LEARNING_PORT:-18100}:8100` | `postgres` | Stateless service; single replica in base VM |
| `promotion` | `services/promotion/Dockerfile` | `${PROMOTION_PORT:-18089}:8089` | `postgres` | Stateless service; single replica in base VM |
| `qlib-dormant-smoke` | `services/research/qlib/Dockerfile` | `Internal only` | `None` | Dormant / on-demand verification container |
| `ray-tune-dormant-smoke` | `services/research/rllib/Dockerfile` | `Internal only` | `None` | Dormant / on-demand verification container |
| `research-orchestrator-svc` | `services/research/Dockerfile` | `${RESEARCH_ORCHESTRATOR_PORT:-18101}:8101` | `postgres` | Stateless service; single replica in base VM |
| `research-worker-gateway-svc` | `services/research-worker-gateway/Dockerfile` | `${RESEARCH_WORKER_GATEWAY_PORT:-18103}:8103` | `postgres, research-orchestrator-svc` | Stateless service; single replica in base VM |
| `rllib-dormant-smoke` | `services/research/rllib/Dockerfile` | `Internal only` | `None` | Dormant / on-demand verification container |
| `search-svc` | `services/search/Dockerfile` | `${SEARCH_PORT:-18098}:8098` | `postgres, source-ingest` | Stateless service; single replica in base VM |
| `smoke-stack` | `Dockerfile.smoke` | `Internal only` | `consultation-svc, source-ingest, search-svc, training-session-svc, policy-learning-svc, research-orchestrator-svc, reconciliation-drift-svc, research-worker-gateway-svc, openclaw-gateway-adapter, web-channel, governance, runtime-manager, telemetry, incidents, postmortems, capital, deployment, evolution, lineage-read, operator-bff, persona, router, evaluation, feedback, memory, registry, optimizer-svc, promotion` | Dormant / on-demand verification container |
| `source-ingest` | `services/source_ingestion/Dockerfile` | `${SOURCE_INGEST_PORT:-18097}:8097` | `postgres` | Stateless service; single replica in base VM |
| `source-ingest-controller-migrate` | `services/source_ingestion/Dockerfile` | `Internal only` | `postgres` | One-shot initialization container (exits 0) |
| `source-search-bounded-smoke` | `Dockerfile.smoke` | `Internal only` | `source-ingest, search-svc` | Dormant / on-demand verification container |
| `training-session-svc` | `services/training-session/Dockerfile` | `${TRAINING_SESSION_PORT:-18099}:8099` | `postgres, source-ingest` | Stateless service; single replica in base VM |
| `trl-dormant-smoke` | `services/learning/trl/Dockerfile` | `Internal only` | `None` | Dormant / on-demand verification container |

## Profile: `MANAGEMENT-AI` (8 Services)

| Service | Dockerfile / Image | Ports | Depends On | Singleton / Lifecycle Note |
|---|---|---|---|---|
| `consultation-svc` | `services/consultation/Dockerfile` | `${CONSULTATION_PORT:-18096}:8096` | `postgres, openclaw-gateway-adapter, governance` | Product runtime diagnostics; read-only access |
| `openclaw-activation-ready-e2e` | `services/openclaw-gateway-adapter/Dockerfile` | `Internal only` | `None` | Product runtime diagnostics; read-only access |
| `openclaw-data-init` | `busybox:1.36` | `Internal only` | `None` | One-shot initialization container (exits 0) |
| `openclaw-gateway` | `integrations/openclaw/gateway/Dockerfile` | `${OPENCLAW_GATEWAY_PORT:-18789}:18789` | `openclaw-data-init` | Product runtime diagnostics; read-only access |
| `openclaw-gateway-adapter` | `services/openclaw-gateway-adapter/Dockerfile` | `127.0.0.1:${OPENCLAW_GATEWAY_ADAPTER_PORT:-18104}:8104` | `openclaw-data-init, openclaw-gateway` | Product runtime diagnostics; read-only access |
| `persona` | `services/control-plane/persona/Dockerfile` | `${PERSONA_PORT:-18002}:8002` | `operator-bff, registry, nats, minio` | Product runtime diagnostics; read-only access |
| `router` | `Dockerfile` | `${ROUTER_PORT:-18003}:8001` | `persona, operator-bff, governance` | Product runtime diagnostics; read-only access |
| `web-channel` | `services/channels/web/Dockerfile` | `${WEB_CHANNEL_PORT:-18105}:8000` | `router` | Product runtime diagnostics; read-only access |

## Profile: `EXECUTION` (7 Services)

| Service | Dockerfile / Image | Ports | Depends On | Singleton / Lifecycle Note |
|---|---|---|---|---|
| `broker` | `services/broker/Dockerfile` | `${BROKER_PORT:-18106}:8102` | `None` | Execution boundary; disabled live broker on dev (`PANTHEON_LIVE_BROKER_ENABLED=false`) |
| `broker-adapter` | `services/execution/lean_runtime/Dockerfile` | `${BROKER_ADAPTER_PORT:-28097}:8097` | `runtime-manager` | **Execution Sidecar (`docker-compose.exec.yml`)**: Mock/sandbox broker adapter for staging/prod execution plane (VM-2). Strict Singleton per active execution plane. |
| `exchange-adapter` | `services/execution/lean_runtime/Dockerfile` | `${EXCHANGE_ADAPTER_PORT:-28098}:8098` | `runtime-manager` | **Execution Sidecar (`docker-compose.exec.yml`)**: Mock/sandbox exchange adapter for market connectivity on staging/prod execution plane (VM-2). Strict Singleton per active execution plane. |
| `pantheon-lean-live` | `services/execution/lean_runtime/Dockerfile` | `${LEAN_LIVE_PORT:-28111}:8011` | `runtime-manager, broker-adapter, exchange-adapter` | **Live Execution Runtime (`docker-compose.exec.yml`, profile `live`)**: Live LEAN runtime with real capital binding. Requires 4 secret keys (`BROKER_API_KEY`, `BROKER_API_SECRET`, `EXCHANGE_API_KEY`, `EXCHANGE_API_SECRET`). Strict Isolation & Singleton: Gated by promotion gate and operator approval; only runnable under live profile on dedicated execution plane (VM-2). |
| `pantheon-paper-runtime` | `services/execution/lean_runtime/Dockerfile` | `${PAPER_RUNTIME_PORT:-18010}:8010` / `${LEAN_PAPER_PORT:-28110}:8010` | `signal-store, runtime-manager, telemetry, source-ingest, operator-bff` (base) / `signal-store, runtime-manager, broker-adapter, exchange-adapter` (exec) | Execution paper runtime; disabled live broker on dev |
| `runtime-manager` | `services/runtime-manager/Dockerfile` | `${RUNTIME_MANAGER_PORT:-${RUNTIME_PORT:-18081}}:8081` / `${RUNTIME_MANAGER_PORT:-28081}:8081` | `consultation-svc` (dev) / `None` (exec stack) | Execution lifecycle & binding enforcement. Single active runtime manager instance. |
| `signal-store` | `redis:7-alpine` | `${SIGNAL_STORE_PORT:-26379}:6379` | `None` | Stateful singleton data/message backend (Redis) |
