# Claude Readout — Phase 4: Service Layer Completion

## Lane

- Agent: Claude
- Capability focus: Execution and control-plane architecture; governance-layer semantics; cross-service consistency; deployment readiness audit

## Canonical Sources Read

- L0: `ai-status.json`, `current-work.md`
- L1: `TARGET_ARCHITECTURE.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `PERSONA_RUNTIME_MODEL.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`
- L2: `ROADMAP.md`, `DEVELOPMENT_WORKBREAKDOWN.md`, `OSS_INTEGRATION_CHECKLIST.md`, `Pantheon_單VM測試版_雙VM正式版_部署補充說明.md`, `Pantheon_GCP_GitHub_Docker_正式部署與環境設計_v2.md`

## Working Interpretation

### Architecture summary

Pantheon's ROADMAP defines a strictly ordered phase sequence. Phases 0–2 (canonical docs, governance contracts, domain objects) are complete. The gap is that Phases 3–5 produced Python-level implementations but stopped short of wrapping them into deployable HTTP services. The jump to single-VM deployment requires closing this gap first.

The 16-service inventory from `Pantheon_單VM測試版_雙VM正式版_部署補充說明.md` is the deployment target. Current repo state maps to:

- **4 services**: have FastAPI HTTP surface (bff, persona, router, feedback)
- **9 services**: have domain-object Python code only (promotion, telemetry-incident, lineage-read, evolution, registry-core, runtime-manager, optimizer, research-orchestrator, bff-completion)
- **5 services**: do not exist in repo at all (openclaw-adapter, consultation, data-ingest, data-catalog, feature-svc)

### Delivery order

For single-VM test environment, the dependency graph forces this wrapping order:

1. **Infrastructure first**: postgres, redis/nats, minio (use official images — no code needed)
2. **Core governance services** (promotion-svc, registry-core-svc): BFF depends on these for artifact metadata and approval state
3. **Telemetry and lineage** (telemetry-incident-svc, lineage-read-svc): needed for operator surfaces
4. **Execution services** (runtime-manager-svc, evolution-svc): needed for deployment flow validation
5. **Optimizer** (optimizer-svc): depends on promotion and registry
6. **Research** (research-orchestrator-svc, data-ingest-svc, data-catalog-svc, feature-svc): semi-independent; can be stubbed
7. **BFF completion**: depends on all above services being reachable
8. **Persona and consultation**: depends on BFF being stable
9. **OpenClaw adapter**: stub is sufficient for test env
10. **OSS workers** (dspy, imitation, mlflow): independent; add to compose
11. **Docker-compose.test.yml + bootstrap**: final assembly

### Ownership boundaries

Per `TARGET_ARCHITECTURE.md` and `BINDING_AND_DEPLOYMENT_SEMANTICS.md`:
- `promotion-svc` owns `ApprovalDecision` and `DeploymentPlan` write authority
- `runtime-manager-svc` owns `RuntimeBinding` write authority — must be the only service that creates/updates runtime binding records
- `telemetry-incident-svc` owns `TelemetryEvent` and `IncidentCase` writes
- `lineage-read-svc` is read-only derived view — must not own any canonical truth
- `evolution-svc` owns `EvolutionDecision`
- `bff` is read-aggregation only — must not shadow-copy canonical objects

These ownership rules constrain the HTTP API surface for each service.

## Risks / Contradictions

### Risk 1: BFF TODOs hide real missing functionality
`services/control-plane/bff/main.py` has a FastAPI skeleton but several read surfaces depend on backend services that have no running instances. Until services like `promotion-svc`, `registry-core-svc`, and `telemetry-incident-svc` have HTTP endpoints, BFF completion is blocked. BFF completion should be sequenced AFTER the backing services are wrapped.

### Risk 2: Group B stubs block test env acceptance criteria
The single-VM acceptance criteria (from `Pantheon_單VM測試版_雙VM正式版_部署補充說明.md` §3.5) require BFF to reach `registry / promotion / telemetry / persona`. However three of the five Group B services (`consultation`, `data-catalog`, `feature-svc`) are not on the critical path for the §3.5 smoke tests. They can be minimal stubs that return 200 on `/health` and placeholder responses on primary endpoints — this unblocks the BFF integration path without requiring full implementation.

### Risk 3: openclaw-adapter is deeply incomplete
`OSS_INTEGRATION_CHECKLIST.md` shows `OpenClaw` status as `adapter-started` — the `integrations/openclaw/` directory has only governance docs. For the test environment, `openclaw-adapter-svc` should be a stub that returns a mock session/tool-bridge response so that `persona-hub-svc` does not hard-fail. Real integration is a follow-on task (OSS-001 continuation).

### Risk 4: runtime-manager-svc in test env
`runtime-manager-svc` (`services/execution/runtime-manager/`) has `runtime_binding.py` and `kill_switch_controller.py` as domain objects, not a server. For the test environment, a minimal FastAPI wrapper exposing `GET /bindings`, `POST /bindings`, `POST /kill-switch` is sufficient to validate the `DeploymentPlan → RuntimeBinding` flow. Real LEAN integration is out of scope for single-VM test env.

### Risk 5: No database migration system
None of the existing services have a migration runner. This must be addressed in the bootstrap script — either via Alembic or plain SQL migration files. This is a deployment-gate item.

## Suggested Task Slices

The following is a proposed grouping. Codex should refine IDs and acceptance criteria in `starter-draft.md`.

### Wave 1 — Infra + compose skeleton
- `DEPLOY-001`: Write `docker-compose.test.yml` skeleton with infra services (postgres, redis, minio) and placeholder service entries; write `.env.example`
- `DEPLOY-002`: Write `bootstrap.sh` and DB migration runner

### Wave 2 — Core governance services wrapped
- `SVC-001`: Wrap `promotion-svc` — FastAPI over `approval_decision.py`, `deployment_plan.py`; endpoints: `POST /approval-decisions`, `GET /approval-decisions/{id}`, `POST /deployment-plans`, `GET /deployment-plans/{id}`; Dockerfile
- `SVC-002`: Wrap `registry-core-svc` — FastAPI over decision-domain; endpoints: `GET /artifacts`, `POST /artifacts`, `GET /artifacts/{id}`; Dockerfile
- `SVC-003`: Wrap `telemetry-incident-svc` — FastAPI over `ingest_svc.py` + `incident.py`; endpoints: `POST /events`, `GET /incidents`, `POST /incidents`; Dockerfile
- `SVC-004`: Wrap `lineage-read-svc` — FastAPI over `service.py`; read-only endpoints; Dockerfile

### Wave 3 — Execution and evolution
- `SVC-005`: Wrap `runtime-manager-svc` — FastAPI over `runtime_binding.py` + `kill_switch_controller.py`; endpoints: `GET /bindings`, `POST /bindings`, `POST /kill-switch`; Dockerfile
- `SVC-006`: Wrap `evolution-svc` — FastAPI over `evolution_controller.py` + `evolution_decision.py`; Dockerfile
- `SVC-007`: Wrap `optimizer-svc` — FastAPI over `synthesizer.py`; Dockerfile

### Wave 4 — Research and data services
- `SVC-008`: Wrap `research-orchestrator-svc` — add HTTP entrypoint to `services/research/`; existing Dockerfile needs update
- `SVC-009`: Build `data-ingest-svc` stub — thin FastAPI wrapper over existing research ingest adapters; Dockerfile
- `SVC-010`: Build `data-catalog-svc` stub — minimal FastAPI with `/health` + catalog list endpoint; Dockerfile
- `SVC-011`: Build `feature-svc` stub — minimal FastAPI; Dockerfile

### Wave 5 — App surfaces
- `SVC-012`: Complete `bff` — wire real service URLs from env; replace TODO stubs with real HTTP client calls to backing services; complete smoke tests
- `SVC-013`: Complete `persona-hub-svc` — add real business logic for session/registry flows; update Dockerfile
- `SVC-014`: Build `consultation-svc` stub — minimal FastAPI; Dockerfile
- `SVC-015`: Build `openclaw-adapter-svc` stub — mock session/tool-bridge responses; Dockerfile

### Wave 6 — Compose assembly and smoke test
- `DEPLOY-003`: Update `docker-compose.test.yml` with all services from Waves 2–5; wire service URLs via env
- `DEPLOY-004`: Write healthcheck + smoke test script; run against full compose stack
- `DEPLOY-005`: Write Golden Replay runbook (per §3.5 of `Pantheon_單VM測試版_雙VM正式版_部署補充說明.md`)

## Verified Repo Evidence (2026-04-15 scan)

The following facts are drawn from a live scan of the repo; they ground the abstractions above.

### docker-compose.yml — actual current services
`docker-compose.yml` has **9 entries**:
- `lean` (LEAN execution engine, build from `./lean`)
- `signal-store` (Redis, port 6379)
- `control-plane-router` (FastAPI, port 8001, Dockerfile present)
- `control-plane-persona` (FastAPI, port 8002, Dockerfile present)
- `dspy-worker` (learning, no port)
- `qlib-worker` (research, no port)
- `finrl-worker` (research, no port)
- `imitation-worker` (learning, no port)
- `mlflow-server` (port 5000, Dockerfile present)

**Postgres is absent.** No DB service of any kind is in the compose file.

### Services with main.py + Dockerfile (ready to deploy today)
- `services/control-plane/router` ✓
- `services/control-plane/persona` ✓
- `services/research/` (has a Dockerfile; also sub-Dockerfiles for dspy, qlib, finrl, imitation, mlflow)
- `services/learning/dspy`, `services/learning/imitation`

### Services with main.py but NO Dockerfile (Dockerfile needed, then add to compose)
- `services/control-plane/bff` — full FastAPI app with `CommandStore`, `ReadSurfaceStore`, operator endpoints
- `services/control-plane/feedback`
- `services/channels/web`, `services/channels/telegram`, `services/channels/discord`

### Services with domain objects but NO HTTP entry point (main.py + Dockerfile both needed)
- `services/telemetry` — `ingest_svc.py` is an in-process class; no `main.py`
- `services/execution/runtime-manager` — `runtime_binding.py`, `kill_switch_controller.py`
- `services/registry` — `gate.py`, `cli.py`, promotion pipeline
- `services/registry-core/decision-domain`
- `services/optimizer-svc` — `portfolio_synthesis` module
- `services/incident` — `incident.py` domain object + schemas

### Services not found in `services/` directory
- `decision-engine-svc` — not found; may be inside registry-core decision-domain
- `openclaw-adapter-svc` — only governance docs in `integrations/openclaw/`
- `consultation-svc` — schema/contract docs in BFF area but no service dir
- `data-ingest-svc`, `data-catalog-svc`, `feature-svc` — not present

### Planning session state (Round 0)
- `starter-draft.md` — completely unpopulated (all fields are blank placeholders)
- All other readouts (Codex, Gemini, Qwen, Copilot) — template stubs only, no content
- `baton-log.md` — only bootstrap entry; Codex has not yet logged receipt of baton
- `review-round-01.md` — pending, no comments

## Citations

- [TARGET_ARCHITECTURE.md §3] Responsibility split: ownership boundaries for each plane
- [BINDING_AND_DEPLOYMENT_SEMANTICS.md] ApprovalDecision → DeploymentPlan → RuntimeBinding chain
- [BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md] BFF is read-aggregation, not canonical truth owner
- [Pantheon_單VM測試版_雙VM正式版_部署補充說明.md §3.2] 16-service single-VM target inventory
- [Pantheon_單VM測試版_雙VM正式版_部署補充說明.md §3.5] Single-VM acceptance criteria (smoke tests)
- [ROADMAP.md Phase 3–5] TEL/LIN/INC (Phase 3), EVO (Phase 4), PER/APP (Phase 5)
- [OSS_INTEGRATION_CHECKLIST.md] OpenClaw is `adapter-started` only — stub is the correct test-env approach
- [docker-compose.yml] 9 services listed; Postgres absent; BFF absent; telemetry-ingest absent
- [services/control-plane/bff/main.py:1-60] Complete FastAPI BFF app — deployable after Dockerfile is added
- [services/telemetry/ingest_svc.py:1-40] `TelemetryIngestService` is an in-process class; HTTP wrapper required
- [starter-draft.md] Blank placeholders — Codex has not yet seeded the shared draft
- [planning-session.json:14-19] Baton sequence: Codex → Qwen → Gemini → Copilot → Claude
