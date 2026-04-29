# Starter Draft — Phase 4: Service Layer Completion

Current rule: only `Codex` edits this file directly.

Last updated by: Codex (baton-owner seed for round 1 refresh)

## Shared Draft

- Objective: turn the existing phase 3-5 domain objects and operator surfaces into a coherent single-VM service stack with explicit write/read boundaries, Docker packaging, and a compose topology that can be cross-reviewed before execution work starts.
- Cross-phase reading rule: use `phase2-phase6-gap-inventory.md` as the canonical bridge between already-finished semantic baseline work and still-missing operational baseline work. This round should slice the residual operational gaps, not reopen already accepted L1 object semantics unless the service boundary still depends on them.
- Scope boundary: planning only. This session aligns service boundaries, port/env contracts, delivery order, and task slicing. It does not directly implement the wrappers or Dockerfiles yet. Default scope includes runtime-control, governance/evolution data APIs, telemetry ingest, lineage read, BFF, trader feedback, and the single-VM compose plan. `web` and `cron` stay optional until reviewers agree they belong in the default VM profile.
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
    | `runtime-manager` | `8081` | `18081` | `/__health__` | default |
    | `governance` | `8082` | `18082` | `/health` | default |
    | `telemetry` | `8083` | `18083` | `/__health__` | default |
    | `evaluation` | `8084` | `18084` | `/__health__` | default |
    | `feedback` | `8085` | `18085` | `/__health__` | default |
    | `memory` | `8086` | `18086` | `/__health__` | default |
    | `registry` | `8087` | `18087` | `/__health__` | default |
    | `optimizer-svc` | `8088` | `18088` | `/__health__` | default |
    | `promotion` | `8089` | `18089` | `/__health__` | default |
    | `incidents` | `8090` | `18090` | `/__health__` | default |
    | `postmortems` | `8091` | `18091` | `/__health__` | default |
    | `capital` | `8092` | `18092` | `/health` | default |
    | `evolution` | `8093` | `18093` | `/health` | default |
    | `lineage-read` | `8094` | `18094` | `/__health__` | default |
    | `operator-bff` | `8001` | `18001` | `/health` | default |
    | `persona` | `8002` | `18002` | `/health` | default |
    | `router` | `8001` | `18003` | `/health` | default |
    | `openclaw-gateway` | `18789` | `${OPENCLAW_GATEWAY_PORT:-18789}` | `/healthz` | `openclaw` |
    | `smoke-stack` | n/a | n/a | command exit status | `smoke` |

  - Env naming contract:
    - Every Python HTTP service must accept `PORT` for its container listener when the service runner supports configurable ports.
    - Shared infrastructure env names stay canonical: `DATABASE_URL`, `PANTHEON_NATS_URL`, `PANTHEON_S3_ENDPOINT`, `PANTHEON_ARTIFACT_BUCKET`, `PANTHEON_RUNTIME_MANAGER_URL`, and service-to-service URLs such as `PANTHEON_BFF_URL`, `PANTHEON_REGISTRY_URL`, `PANTHEON_TELEMETRY_URL`, and `PANTHEON_INTERNAL_API_URL`.
    - Data-directory env names must match the owning service or canonical domain: `BFF_DATA_DIR`, `PANTHEON_GOVERNANCE_DATA_DIR`, `GOVERNANCE_DATA_DIR`, `PANTHEON_RUNTIME_DATA_DIR`, `PANTHEON_RUNTIME_BINDING_STORE_PATH`, `TELEMETRY_STORAGE_DIR`, `INCIDENTS_DATA_DIR`, `POSTMORTEMS_DATA_DIR`, `PROMOTION_DATA_DIR`, `CAPITAL_DATA_DIR`, `EVOLUTION_DATA_DIR`, `LINEAGE_DATA_DIR`, `CONSULTATION_DATA_DIR`, `SOURCE_INGEST_DATA_DIR`, `SEARCH_DATA_DIR`, and `SEARCH_INDEX_STORE_PATH`.
    - Secrets and external credentials remain env-driven with local defaults only for the single-VM test profile, for example `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `OPENCLAW_GATEWAY_TOKEN`, `PANTHEON_S3_ACCESS_KEY`, and `PANTHEON_S3_SECRET_KEY`.
  - Volume contract:
    - Durable named volumes are `postgres-data`, `minio-data`, `nats-data`, `openclaw-data`, `runtime-data`, `governance-data`, `telemetry-data`, `incident-data`, `bff-data`, `promotion-data`, `capital-data`, `evolution-data`, `lineage-data`, `consultation-data`, `source-ingest-data`, and `search-data`.
    - BFF is a read client for governance/runtime/incident state in the single-VM test stack: it mounts `governance-data`, `runtime-data`, and `incident-data` as read-only and owns only `bff-data`.
    - Runtime command state belongs under `/data/runtime`; governance state under `/data/governance`; telemetry under `/data/telemetry`; incident and postmortem records share `/data/incidents`.
  - Compose profile boundaries:
    - The default profile is the single-VM control/evidence/surface stack plus local infrastructure. It must boot without OpenClaw, research workers, web, cron, or broader OSS adapters.
    - `openclaw` is optional and proves only gateway reachability for this wave; it does not imply full OpenClaw adapter integration.
    - `smoke` is a verification profile that runs `scripts/smoke_honest_stack.py` after the default core stack is healthy.
    - Research, learning, `web`, and `cron` remain outside default SVC-BASELINE. They may be introduced by later compose work only with explicit profile names and smoke criteria.
  - Dockerfile conventions:
    - New service Dockerfiles should use repository-root build context when they import shared Pantheon modules; service-local contexts are allowed only when the service is self-contained, as with the current router.
    - Python service images use `python:3.11-slim`, set `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1`, install from the nearest service-specific `requirements.txt`, copy only the needed service/shared paths, expose the same container port used by compose, and run the HTTP app with the repo's existing Flask or FastAPI entrypoint.
    - FastAPI services should prefer `/__health__`; legacy `/health` endpoints remain valid when already implemented and must be reflected exactly in compose health checks.
    - `runtime-manager` remains the non-BFF emergency/control path in this baseline. Its Docker packaging must preserve the kill-switch module path and expose the protected command route independently of `operator-bff`.
  - Explicit deferrals:
    - The single-VM test profile runs `operator-bff` as one replica. This intentionally defers the multi-replica BFF HA requirement in `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`; as of 2026-04-29 this is a product-scope defer, not a pending implementation task, because the operator frontend is expected to have low concurrent human usage. Reopen only if operator concurrency, availability SLOs, external customer access, or audit requirements make BFF outage a material risk.
    - The baseline locks deployability and smoke wiring only. It does not claim production-grade upstream OSS integration, full research-worker activation, or final BFF read-path convergence beyond the service contracts named here.
  - Current code-backed service disposition:
    - `consultation`, `source_ingestion`, and `search` are no longer outside the default single-VM compose baseline. Current truth comes from `docker-compose.yml` plus the service entrypoints:

      | Component | Repo state | Single-VM baseline disposition | Downstream implication |
      |---|---|---|---|
      | `consultation-svc` / `services/consultation/` | Root compose builds `services/consultation/Dockerfile`, sets `PORT=8096`, mounts `consultation-data`, maps `${CONSULTATION_PORT:-18096}:8096`, and checks `/readyz`. The FastAPI app also exposes legacy `/health` plus `/api/consult/requests`, `/api/consult/memos`, `/api/consult/handoffs`, and related consultation endpoints. | Activated in the default single-VM stack. `runtime-manager` and `operator-bff` use `PANTHEON_CONSULTATION_API_URL=http://consultation-svc:8096` and depend on the service health check. | Consultation is now an explicit network service dependency, not a hidden local-store fallback boundary. BFF degraded-mode rules still apply when the downstream service is unhealthy. |
      | `source-ingest` / `services/source_ingestion/` | Root compose builds `services/source_ingestion/Dockerfile`, sets `PORT=8097`, mounts `source-ingest-data`, maps `${SOURCE_INGEST_PORT:-18097}:8097`, and checks `/readyz`. The FastAPI wrapper exposes `/health`, `POST /api/source-ingest/jobs`, `GET /api/source-ingest/jobs`, `GET /api/source-ingest/watermarks/{connector_id}`, `GET /api/source-ingest/dlq`, and `GET /api/source-ingest/audit`. | Activated in the default single-VM stack as a bounded job-trigger wrapper. It intentionally accepts already-fetched records for this slice; external fetching remains outside the service process. | SVC-COMPOSE and smoke wiring may use `SOURCE_INGEST_URL=http://source-ingest:8097`. Later autonomous pipeline work should extend this service boundary instead of reclassifying it as absent. |
      | `search-svc` / `services/search/` | Root compose builds `services/search/Dockerfile`, sets `PORT=8098`, mounts `search-data`, maps `${SEARCH_PORT:-18098}:8098`, and checks `/readyz`. The FastAPI wrapper exposes `/health`, `POST /api/search/query`, and `GET /api/search/snapshots/{request_id}` over the governed search gateway and JSONL index store. | Activated in the default single-VM stack. `operator-bff` uses `PANTHEON_SEARCH_API_URL=http://search-svc:8098` and depends on the service health check. | Search-backed surfaces should use the configured service client path and report explicit degraded state if the service is unavailable. |
- Proposed wave order:
  1. Service baseline: finalize port map, env names, volume mounts, and compose profile boundaries.
  2. Runtime-control packaging under the locked baseline as `runtime-manager` on container port `8081` / host port `18081`, because BFF operator commands and emergency controls already depend on that interface.
  3. Governance API plus evidence services (`telemetry-ingest`, `lineage-read`).
  4. BFF and trader-feedback Dockerfiles plus BFF client rewiring.
  5. Single-VM compose assembly and smoke path. Only after that, decide whether `web` / `cron` belong in the default profile or an optional profile.
- Proposed task slices:
  - `SVC-BASELINE`: shared env/volume contract, service port allocation, Dockerfile conventions, and compose profile plan.
  - `SVC-RUNTIME-CONTROL`: package internal API, persist command state, and resolve the missing evolution command boundary.
  - `SVC-GOVERNANCE-API`: expose approval/capital/binding/deployment/evolution read/write APIs around current stores/controllers.
  - `SVC-EVIDENCE`: add HTTP wrappers + Dockerfiles for telemetry ingest and lineage read.
  - `SVC-SURFACES`: Dockerize BFF + trader-feedback and rewire BFF away from snapshot-seed mode.
  - `SVC-COMPOSE`: assemble the single-VM docker-compose file, storage mounts, dependency graph, and smoke commands.
- Residual-gap interpretation by roadmap phase:
  - `Phase 2`: contracts are mostly done; remaining work is service exposure for runtime-control and governance/runtime APIs.
  - `Phase 3`: schemas and service classes exist; remaining work is HTTP wrapping, packaging, and compose wiring for telemetry/lineage/incident evidence paths.
  - `Phase 4`: evolution semantics exist; remaining work is command-plane convergence plus service ownership split between runtime-control and governance-api.
  - `Phase 5`: persona/app surface contracts exist; remaining work is BFF rewiring away from snapshots/defaults and packet expansion for non-APP-002 workbenches.
  - `Phase 6`: OSS governance criteria exist; remaining work is mostly follow-on execution once the service stack is runnable, not part of the first compose-critical slice unless reviewers argue otherwise.
- Current evidence-backed decisions:
  - The repo already has working HTTP apps and Dockerfiles for the locked single-VM baseline services listed above.
  - BFF is not yet backed by canonical services; it reads JSON snapshots or seeded defaults for governance/runtime data and posts commands to the protected internal API.
  - Telemetry ingest and lineage read already exist as reusable service classes, so they should be wrapped instead of redesigned.
  - The current root compose resolves the earlier router/BFF `8001` collision by assigning distinct host ports while keeping each service's existing container listener.
- Open disagreements:
  - Resolved for SVC-BASELINE: `runtime-manager` is the locked non-BFF emergency/control path for the single-VM baseline. Any later Flask/FastAPI migration must preserve the same external contract.
  - Where should evolution approval/action endpoints live: `runtime-control`, `governance-api`, or a split between them?
  - Must BFF client rewiring to real services happen in the same wave as Dockerization, or can snapshot mode ship temporarily for an earlier smoke stack?
  - Should `web` and `cron` be part of the default single-VM profile, or remain optional profiles outside the phase 3-5 critical path?

## Execution Slice Seed

The current Codex recommendation is to keep the first materialization wave focused on the six service-layer slices below and treat broader workbench expansion plus phase 6 adapter realization as follow-on work after the stack is runnable:

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
- [R1] `docker-compose.yml:22-100`
- [R2] `services/control-plane/router/Dockerfile:13-15`
- [R3] `services/control-plane/bff/main.py:2124-2126`
- [R4] `services/control-plane/bff/read_store.py:43-175`
- [R5] `services/control-plane/bff/command_executor.py:21-25`
- [R6] `services/control-plane/bff/command_executor.py:64-183`
- [R7] `services/control_plane/internal_api.py:1-114`
- [R8] `services/control-plane/feedback/main.py:180-235`
- [R9] `services/telemetry/ingest_svc.py:1-120`
- [R10] `services/telemetry/lineage_read/service.py:1269-1295`
- [R11] `services/channels/web/main.py:1-68`
- [R12] shell observations from `find services -maxdepth 3 -name Dockerfile | sort` and `rg -n "class (LineageReadService|TelemetryIngestService|RuntimeBindingStore|KillSwitchController|StagePlanner|DeploymentSagaOrchestrator|EvolutionController|ApprovalDecisionStore|CapitalPoolStore|PersonaRegistry)" services/control-plane services/execution services/telemetry`
