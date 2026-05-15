# Starter Draft — Phase 4: Service Layer Completion

Current rule: only `Codex` edits this file directly.

Last updated by: Codex (`SVC-DOCS-FUTURE-STATE-TRUTH-SYNC`, 2026-04-29)

## Shared Draft

- Objective: record the service-layer path from the original phase 4 planning wave to the current single-VM code truth. Earlier proposal text is historical; the active implementation source is the root `docker-compose.yml` plus service-local Dockerfiles and entrypoints.
- Cross-phase reading rule: use `phase2-phase6-gap-inventory.md` as the bridge between semantic baseline closure, delivered service wrappers, active hardening tasks, and future-deferred activation work. Do not reopen accepted L1 object semantics unless a current service boundary still depends on them.
- Scope boundary: the root compose baseline now exists. `web` and `cron` remain optional/out of default. Production research/learning adapters, upstream OpenClaw runtime execution, OpenClaw paper/live broker sessions, and multi-replica BFF HA remain explicitly deferred unless a new task reopens them.
- Proposed architecture:
  - `runtime-control` is the only side-effectful operator command API. Reuse/package `services/control_plane/internal_api.py` rather than inventing a second command surface during this wave.
  - `governance-api` exposes approval, capital pool, persona binding, deployment, saga, runtime-binding, and evolution objects from the existing domain modules.
  - `telemetry-ingest` owns event intake, buffering, retry, and DLQ. `lineage-read` owns projection/query endpoints only.
  - `bff` remains read-oriented and command-submitting. It should stop treating snapshot/default seed data as the long-term integration path.
  - `feedback` remains a separate ingestion service. `router` and `persona` stay as already-deployable dependencies, not redesign targets.
- SVC-BASELINE locked contract (2026-04-28):
  - Contract source: the implementation baseline is the root `docker-compose.yml` plus service-local Dockerfiles. This section supersedes earlier planning-only port proposals in this draft; the gap inventory remains a historical planning snapshot unless explicitly updated later.
  - Port map:

    | Service | Container port | Host port / env | Health path | Profile |
    |---|---:|---|---|---|
    | `postgres` | `5432` | `${POSTGRES_PORT:-15432}` | `pg_isready` | default |
    | `minio` | `9000`, `9001` | `${MINIO_API_PORT:-19000}`, `${MINIO_CONSOLE_PORT:-19001}` | `/minio/health/live` | default |
    | `nats` | `4222`, `8222` | `${NATS_PORT:-14222}`, `${NATS_MONITOR_PORT:-18222}` | `/healthz` | default |
    | `signal-store` | `6379` | not host-published by default | `redis-cli ping` | default |
    | `minio-init` | n/a | n/a | command exit status | default |
    | `consultation-svc` | `8096` | `${CONSULTATION_PORT:-18096}` | `/readyz` | default |
    | `source-ingest` | `8097` | `${SOURCE_INGEST_PORT:-18097}` | `/readyz` | default |
    | `search-svc` | `8098` | `${SEARCH_PORT:-18098}` | `/readyz` | default |
    | `training-session-svc` | `8099` | `${TRAINING_SESSION_PORT:-18099}` | `/readyz` | default |
    | `policy-learning-svc` | `8100` | `${POLICY_LEARNING_PORT:-18100}` | `/readyz` | default |
    | `research-orchestrator-svc` | `8101` | `${RESEARCH_ORCHESTRATOR_PORT:-18101}` | `/readyz` | default |
    | `openclaw-gateway-adapter` | `8104` | `${OPENCLAW_GATEWAY_ADAPTER_PORT:-18104}` | `/livez` | default |
    | `runtime-manager` | `8081` | `18081` | `/readyz` | default |
    | `governance` | `8082` | `18082` | `/readyz` | default |
    | `telemetry` | `8083` | `18083` | `/readyz` | default |
    | `evaluation` | `8084` | `18084` | `/readyz` | default |
    | `feedback` | `8085` | `18085` | `/readyz` | default |
    | `memory` | `8086` | `18086` | `/readyz` | default |
    | `registry` | `8087` | `18087` | `/readyz` | default |
    | `optimizer-svc` | `8088` | `18088` | `/readyz` | default |
    | `promotion` | `8089` | `18089` | `/readyz` | default |
    | `incidents` | `8090` | `18090` | `/readyz` | default |
    | `postmortems` | `8091` | `18091` | `/readyz` | default |
    | `capital` | `8092` | `18092` | `/readyz` | default |
    | `evolution` | `8093` | `18093` | `/readyz` | default |
    | `lineage-read` | `8094` | `18094` | `/readyz` | default |
    | `deployment` | `8095` | `18095` | `/readyz` | default |
    | `operator-bff` | `8001` | `18001` | `/readyz` | default |
    | `persona` | `8002` | `18002` | `/readyz` | default |
    | `router` | `8001` | `18003` | `/readyz` | default |
    | `reconciliation-drift-svc` | `8102` | `${RECONCILIATION_DRIFT_PORT:-18102}` | `/readyz` | default |
    | `research-worker-gateway-svc` | `8103` | `${RESEARCH_WORKER_GATEWAY_PORT:-18103}` | `/readyz` | default |
    | `openclaw-gateway` | `18789` | `${OPENCLAW_GATEWAY_PORT:-18789}` | `/readyz` | `openclaw` |
    | `smoke-stack` | n/a | n/a | command exit status | `smoke` |

  - Env naming contract:
    - Every Python HTTP service must accept `PORT` for its container listener when the service runner supports configurable ports.
    - Shared infrastructure env names stay canonical: `DATABASE_URL`, `PANTHEON_NATS_URL`, `PANTHEON_S3_ENDPOINT`, `PANTHEON_ARTIFACT_BUCKET`, `PANTHEON_RUNTIME_MANAGER_URL`, and service-to-service URLs such as `PANTHEON_BFF_URL`, `PANTHEON_REGISTRY_URL`, `PANTHEON_TELEMETRY_URL`, `PANTHEON_INTERNAL_API_URL`, `PANTHEON_CONSULTATION_API_URL`, and `PANTHEON_SEARCH_API_URL`.
    - Data-directory env names must match the owning service or canonical domain: `BFF_DATA_DIR`, `PANTHEON_GOVERNANCE_DATA_DIR`, `GOVERNANCE_DATA_DIR`, `PANTHEON_RUNTIME_DATA_DIR`, `PANTHEON_RUNTIME_BINDING_STORE_PATH`, `TELEMETRY_STORAGE_DIR`, `INCIDENTS_DATA_DIR`, `POSTMORTEMS_DATA_DIR`, `PROMOTION_DATA_DIR`, `CAPITAL_DATA_DIR`, `EVOLUTION_DATA_DIR`, `LINEAGE_DATA_DIR`, `DEPLOYMENT_DATA_DIR`, `CONSULTATION_DATA_DIR`, `SOURCE_INGEST_DATA_DIR`, `SEARCH_DATA_DIR`, `SEARCH_INDEX_STORE_PATH`, `TRAINING_SESSION_DATA_DIR`, `POLICY_LEARNING_DATA_DIR`, `RESEARCH_ORCHESTRATOR_DATA_DIR`, `RECONCILIATION_DRIFT_DATA_DIR`, and `RESEARCH_WORKER_GATEWAY_DATA_DIR`.
    - Production/research/OpenClaw activation flags remain explicit env gates with false defaults in root compose, including `POLICY_LEARNING_ENABLE_PRODUCTION_ADAPTERS`, `RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS`, `RESEARCH_WORKER_GATEWAY_ENABLE_PRODUCTION_ADAPTERS`, `OPENCLAW_PRODUCTION_BROKER_ENABLED`, and `OPENCLAW_PAPER_ADAPTER_ENABLED`.
    - Secrets and external credentials remain env-driven with local defaults only for the single-VM test profile, for example `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `OPENCLAW_GATEWAY_TOKEN`, `PANTHEON_S3_ACCESS_KEY`, and `PANTHEON_S3_SECRET_KEY`.
  - Volume contract:
    - Durable named volumes are `postgres-data`, `minio-data`, `nats-data`, `openclaw-data`, `runtime-data`, `governance-data`, `telemetry-data`, `incident-data`, `bff-data`, `feedback-data`, `promotion-data`, `capital-data`, `evolution-data`, `lineage-data`, `consultation-data`, `source-ingest-data`, `search-data`, `training-session-data`, `policy-learning-data`, `research-orchestrator-data`, `reconciliation-drift-data`, and `research-worker-gateway-data`. Deployment state is stored under `governance-data`; there is no separate deployment volume in root compose.
    - BFF is a read client for governance/runtime/incident state in the single-VM test stack: it mounts `governance-data`, `runtime-data`, and `incident-data` as read-only and owns only `bff-data`.
    - Runtime command state belongs under `/data/runtime`; governance state under `/data/governance`; telemetry under `/data/telemetry`; incident and postmortem records share `/data/incidents`.
  - Compose profile boundaries:
    - The default profile is the single-VM control/evidence/surface stack plus local infrastructure, safe research/learning service-boundary wrappers, and the Pantheon-owned OpenClaw adapter facade. It must boot without the optional upstream `openclaw-gateway`, `web`, `cron`, or production OSS/research adapters.
    - `openclaw` is optional and proves only upstream gateway reachability; it does not imply OpenClaw session execution, paper execution, production adapters, or broker activation.
    - `smoke` is a verification profile that runs `scripts/smoke_honest_stack.py` after the default core stack is healthy.
    - Research/learning production activation remains outside default SVC-BASELINE. The default `policy-learning-svc`, `research-orchestrator-svc`, and `research-worker-gateway-svc` are safe boundary wrappers that reject production adapters and paper/canary/live activation unless a separate governance task changes the env gates and smoke criteria.
  - Dockerfile conventions:
    - New service Dockerfiles should use repository-root build context when they import shared Pantheon modules; service-local contexts are allowed only when the service is self-contained, as with the current router.
    - Python service images use `python:3.11-slim`, set `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1`, install from the nearest service-specific `requirements.txt`, copy only the needed service/shared paths, expose the same container port used by compose, and run the HTTP app with the repo's existing Flask or FastAPI entrypoint.
    - FastAPI services should expose the shared health routes; root compose health checks use `/readyz` for most services and `/livez` for the OpenClaw adapter facade.
    - `runtime-manager` remains the non-BFF emergency/control path in this baseline. Its Docker packaging must preserve the kill-switch module path and expose the protected command route independently of `operator-bff`.
  - Explicit deferrals:
    - The single-VM test profile runs `operator-bff` as one replica. This intentionally defers the multi-replica BFF HA requirement in `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`; as of 2026-04-29 this is a product-scope defer, not a pending implementation task, because the operator frontend is expected to have low concurrent human usage. Reopen only if operator concurrency, availability SLOs, external customer access, or audit requirements make BFF outage a material risk.
    - The baseline locks deployability and smoke wiring only. It does not claim production-grade upstream OSS integration, full research-worker activation, paper/canary/live activation, or final BFF read-path convergence beyond the service contracts named here.
    - OpenClaw session creation remains explicitly fail-closed at the Pantheon adapter boundary: `POST /api/openclaw-adapter/sessions` may return non-retryable `CAPABILITY_DENIED` or retryable upstream `UPSTREAM_UNAVAILABLE`, and both `OPENCLAW_PRODUCTION_BROKER_ENABLED` and `OPENCLAW_PAPER_ADAPTER_ENABLED` are false in root compose.
    - Bounded external source fetch and search request-document quarantine are active hardening tasks, not already-complete production crawler/search claims. Until `SVC-SOURCE-INGEST-EXTERNAL-FETCH-BASELINE` lands, configured source fetch is static-record replay only. Until `SVC-SEARCH-DURABLE-COMPAT-QUARANTINE` is accepted, callers should still treat durable no-doc search as the normal path and request-document search as compatibility-only.
  - Current code-backed service disposition:
    - `consultation`, `source_ingestion`, and `search` are no longer outside the default single-VM compose baseline. Current truth comes from `docker-compose.yml` plus the service entrypoints:

      | Component | Repo state | Single-VM baseline disposition | Downstream implication |
      |---|---|---|---|
      | `consultation-svc` / `services/consultation/` | Root compose builds `services/consultation/Dockerfile`, sets `PORT=8096`, mounts `consultation-data`, maps `${CONSULTATION_PORT:-18096}:8096`, and checks `/readyz`. The FastAPI app also exposes legacy `/health` plus `/api/consult/requests`, `/api/consult/memos`, `/api/consult/handoffs`, and related consultation endpoints. | Activated in the default single-VM stack. `runtime-manager` and `operator-bff` use `PANTHEON_CONSULTATION_API_URL=http://consultation-svc:8096` and depend on the service health check. | Consultation is now an explicit network service dependency, not a hidden local-store fallback boundary. BFF degraded-mode rules still apply when the downstream service is unhealthy. |
      | `source-ingest` / `services/source_ingestion/` | Root compose builds `services/source_ingestion/Dockerfile`, sets `PORT=8097`, mounts `source-ingest-data`, maps `${SOURCE_INGEST_PORT:-18097}:8097`, and checks `/readyz`. The FastAPI wrapper exposes connector config, job trigger, watermark, DLQ replay, source record, evidence bundle, and audit endpoints. Current configured fetch validation accepts `fetch.mode == static_records`; the bounded HTTP/file feed baseline is owned by active task `SVC-SOURCE-INGEST-EXTERNAL-FETCH-BASELINE`. | Activated in the default single-VM stack as governed source ingest with inline records and configured static-record replay. It is not a production crawler and does not yet claim arbitrary external web scraping. | Downstream services use `SOURCE_INGEST_URL=http://source-ingest:8097`. Durable source evidence is the primary feed for the search durable index path. |
      | `search-svc` / `services/search/` | Root compose builds `services/search/Dockerfile`, sets `PORT=8098`, mounts `search-data`, maps `${SEARCH_PORT:-18098}:8098`, and checks `/readyz`. The FastAPI wrapper exposes `/api/search/index/reload`, `/api/search/index/status`, `/api/search/query`, the explicit `/api/search/query/request-documents-compat` compatibility route, and snapshots. `operator-bff` uses `PANTHEON_SEARCH_API_URL=http://search-svc:8098` and depends on the service health check. | Activated in the default single-VM stack with a durable evidence/index path. No-document queries use the durable JSONL evidence index seeded by `source-ingest` (`adapter_state=durable`). Request documents are compatibility-only and must be explicitly allowed by the compat flag or compat route. | Search-backed surfaces should use the no-doc durable path by default and rely on `source-ingest` for durable evidence. Explicit degraded state must be reported when the service is unavailable. |
- Historical wave seed and current work split:
  - `SVC-BASELINE`, `SVC-RUNTIME-CONTROL`, `SVC-GOVERNANCE-API`, `SVC-EVIDENCE`, `SVC-SURFACES`, and `SVC-COMPOSE` are retained below as the historical materialization slices that produced the current service baseline; do not read them as still-unstarted work.
  - Current active hardening tasks are narrower: `SVC-SOURCE-INGEST-EXTERNAL-FETCH-BASELINE` owns bounded allowlisted HTTP/file feed support for source ingest, `SVC-SEARCH-DURABLE-COMPAT-QUARANTINE` owns request-document search quarantine, and separate data/security tasks own Postgres migration mapping and BFF OIDC/JWKS.
  - Future-deferred work remains outside this session: BFF HA, OpenClaw runtime/paper/live broker session execution, research/learning production adapters, `web`, `cron`, and broader upstream OSS activation.
- Residual-gap interpretation by roadmap phase:
  - `Phase 2`: contracts and core service exposure are mostly done; remaining work is command-plane convergence, Postgres ownership migration, and tighter runtime/governance read/write split.
  - `Phase 3`: telemetry, lineage, incident, and postmortem service wrappers are in the default stack; remaining work is hardening, migration, and operator workbench coverage rather than missing Dockerfiles.
  - `Phase 4`: evolution semantics and service wrapper exist; remaining work is command/action convergence and workbench packet completion.
  - `Phase 5`: persona/app surface contracts and default BFF packaging exist; remaining work is product/workbench expansion, read-path hardening, and frontend loop closure.
  - `Phase 6`: OSS governance criteria and safe facades exist; production-grade upstream adapters and OpenClaw session execution remain future-deferred.
- Current evidence-backed decisions:
  - The repo has working HTTP apps and Dockerfiles for the locked single-VM baseline services listed above.
  - The root compose wires `operator-bff` to network services and sets local snapshot fallback off for the default stack; read-path hardening still continues under separate tasks.
  - Source ingest and search are default services, but source configured fetch is static-record replay today and search request-document mode is compatibility-only.
  - The current root compose resolves the earlier router/BFF `8001` collision by assigning distinct host ports while keeping each service's existing container listener.

## Historical Execution Slice Seed

This table is retained as the original materialization plan that produced the current service baseline. It is no longer the current task queue; use `ai-status.json` for active ownership and the sections above for current code truth, active hardening tasks, and future-deferred boundaries.

| Slice | Main closure target | Depends on |
|---|---|---|
| `SVC-BASELINE` | port/env/volume/compose contract across the stack | - |
| `SVC-RUNTIME-CONTROL` | real side-effectful operator command service | `SVC-BASELINE` |
| `SVC-GOVERNANCE-API` | service-backed approval/deployment/binding/evolution APIs | `SVC-BASELINE` |
| `SVC-EVIDENCE` | telemetry-ingest + lineage-read HTTP services | `SVC-BASELINE` |
| `SVC-SURFACES` | BFF + feedback packaging and BFF rewiring off snapshots/defaults | `SVC-RUNTIME-CONTROL`, `SVC-GOVERNANCE-API`, `SVC-EVIDENCE` |
| `SVC-COMPOSE` | single-VM compose assembly and smoke path | `SVC-RUNTIME-CONTROL`, `SVC-GOVERNANCE-API`, `SVC-EVIDENCE`, `SVC-SURFACES` |

## Citations

- [C1] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/planning-session.json`
- [C2] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/README.md`
- [C3] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md`
- [R1] `docker-compose.yml:81-220` — consultation, source-ingest, search, training-session, policy-learning, and research-orchestrator default services.
- [R2] `docker-compose.yml:257-278` — OpenClaw adapter facade default service and disabled broker/paper env gates.
- [R3] `docker-compose.yml:280-337` — runtime-manager and governance default services.
- [R4] `docker-compose.yml:720-841` — deployment, evolution, lineage-read, reconciliation-drift, and research-worker-gateway services.
- [R5] `docker-compose.yml:843-927` — smoke profile environment and dependency graph.
- [R6] `services/source_ingestion/configured.py:120-136` — configured fetch currently accepts `static_records` only.
- [R7] `services/search/main.py:278-326` — durable search path and explicit request-document compatibility path.
- [R8] `services/openclaw-gateway-adapter/main.py:180-220` — fail-closed OpenClaw sessions with deferred or upstream-unavailable session creation.
- [R9] `scripts/smoke_honest_stack.py:135-180` and `scripts/smoke_honest_stack.py:381-613` — smoke coverage for OpenClaw degraded semantics, source ingest, search, policy-learning, and research-orchestrator.
