# Starter Draft — Phase 4: Service Layer Completion

Current rule: only `Codex` edits this file directly.

Last updated by: Claude (seed pass — Codex should refine)

## Objective

Produce a complete set of deployable Docker services (FastAPI HTTP wrappers + Dockerfiles) for all 16 Pantheon services, plus a working `docker-compose.test.yml` for single-VM test environment deployment, with bootstrap scripts and a smoke test suite.

## Scope Boundary

**In scope:**
- HTTP service wrappers for 9 existing domain-object services
- Minimal stubs for 5 missing services
- Dockerfiles for all 14 new services
- `docker-compose.test.yml` incorporating all 16 services + infra
- `.env.example`, `bootstrap.sh`, DB migration runner, healthcheck + smoke test script

**Out of scope for this phase:**
- Real OpenClaw upstream integration (OSS-001 continuation)
- Real LEAN runtime integration on runtime-manager
- OSS workers not in repo (vectorbt, statsmodels, quantlib)
- GCP/Cloud Run/GKE deployment manifests
- Production hardening (HA, TLS, secrets management)

## Proposed Wave Order

Wave 1 → Wave 2 → Wave 3 may run in parallel within each wave. Wave 4 may start once Wave 2 is done. Wave 5 blocked on Wave 4 BFF completion. Wave 6 blocked on all previous waves.

## Proposed Task Slices

### Wave 1 — Infrastructure + Compose Skeleton

| ID | Task | Owner lane | Reviewer lane | Acceptance criteria |
|---|---|---|---|---|
| `DEPLOY-001` | Write `docker-compose.test.yml` skeleton: infra services (postgres:16-alpine, redis:7-alpine, minio/minio), env vars from `.env.example`, placeholder `build:` entries for all 16 services; write `.env.example` with all required env var names | Gemini | Codex | `docker compose -f docker-compose.test.yml config` passes; all 16 service entries present; `.env.example` covers all vars referenced in compose file |
| `DEPLOY-002` | Write `bootstrap.sh`: pull/build images, wait-for-postgres, run DB migrations (Alembic or plain SQL), run healthchecks; write `migrations/` directory with initial schema SQL for all services that own DB tables | Gemini | Claude | `./bootstrap.sh` runs to completion on clean VM; all tables created; all services pass `/health` |

### Wave 2 — Core Governance Services (run in parallel)

| ID | Task | Owner lane | Reviewer lane | Acceptance criteria |
|---|---|---|---|---|
| `SVC-001` | Wrap `promotion-svc`: FastAPI app at `services/control-plane/governance/server.py`; expose `POST /approval-decisions`, `GET /approval-decisions/{id}`, `POST /deployment-plans`, `GET /deployment-plans/{id}`, `GET /health`; wire to postgres via env `DATABASE_URL`; write `Dockerfile` | Codex | Claude | service starts; `POST /approval-decisions` returns 201 with valid ApprovalDecision shape; `GET /health` returns 200; smoke test passes |
| `SVC-002` | Wrap `registry-core-svc`: FastAPI app at `services/registry-core/server.py`; expose `GET /artifacts`, `POST /artifacts`, `GET /artifacts/{id}`, `GET /health`; wire to postgres; write `Dockerfile` | Codex | Claude | service starts; `POST /artifacts` returns 201; artifact state enum enforced (`draft/candidate/approved/retired`); smoke test passes |
| `SVC-003` | Wrap `telemetry-incident-svc`: FastAPI app at `services/telemetry/server.py`; expose `POST /events`, `GET /events`, `GET /incidents`, `POST /incidents`, `GET /health`; wire `ingest_svc.py` + `incident.py`; postgres + optional redis queue; write `Dockerfile` | Qwen | Codex | service starts; `POST /events` returns 202; `POST /incidents` returns 201; shock-absorption buffer behaviour verified in smoke test |
| `SVC-004` | Wrap `lineage-read-svc`: FastAPI app at `services/telemetry/lineage_read/server.py`; expose `GET /lineage/{artifact_id}`, `GET /lineage/chain`, `GET /health`; read-only postgres queries via `service.py`; write `Dockerfile` | Qwen | Claude | service starts; `GET /lineage/{id}` returns chain with at least `artifact_id` field; read-only — no POST endpoints that mutate canonical truth; smoke test passes |

### Wave 3 — Execution and Evolution Services (run in parallel)

| ID | Task | Owner lane | Reviewer lane | Acceptance criteria |
|---|---|---|---|---|
| `SVC-005` | Wrap `runtime-manager-svc`: FastAPI app at `services/execution/runtime-manager/server.py`; expose `GET /bindings`, `POST /bindings`, `PATCH /bindings/{id}`, `POST /kill-switch`, `GET /health`; wire `runtime_binding.py` + `kill_switch_controller.py`; write `Dockerfile` | Claude | Gemini | service starts; `POST /bindings` returns 201 with RuntimeBinding shape; `POST /kill-switch` returns 200 and logs audit trail; write authority enforced — only runtime-manager creates binding records; smoke test passes |
| `SVC-006` | Wrap `evolution-svc`: FastAPI app at `services/control-plane/governance/evolution_server.py`; expose `POST /evolution-decisions`, `GET /evolution-decisions/{id}`, `POST /freeze`, `POST /rollback`, `GET /health`; wire `evolution_controller.py` + `evolution_decision.py`; write `Dockerfile` | Claude | Qwen | service starts; `POST /evolution-decisions` returns 201; freeze and rollback actions emit audit log; cooldown enforcement present; smoke test passes |
| `SVC-007` | Wrap `optimizer-svc`: FastAPI app at `services/optimizer-svc/server.py`; expose `POST /synthesize`, `GET /synthesis-results/{id}`, `GET /health`; wire `synthesizer.py`; write `Dockerfile` | Copilot | Codex | service starts; `POST /synthesize` returns 200 with synthesis artifact; conflict_resolution_log field present in response; smoke test passes |

### Wave 4 — Research and Data Services (run in parallel, Wave 2 must be done first)

| ID | Task | Owner lane | Reviewer lane | Acceptance criteria |
|---|---|---|---|---|
| `SVC-008` | Add HTTP entrypoint to `research-orchestrator-svc`: add `services/research/server.py`; expose `POST /jobs`, `GET /jobs/{id}`, `GET /health`; wire ingest + replication + strategy_spec modules; update existing `Dockerfile` to use `server.py` as entrypoint | Codex | Qwen | service starts; `POST /jobs` returns 202 with job_id; existing Dockerfile still builds; smoke test passes |
| `SVC-009` | Build `data-ingest-svc` stub: create `services/data/ingest/`; FastAPI app with `POST /ingest`, `GET /ingest/{job_id}`, `GET /health`; thin wrapper over existing `services/research/ingest/ingestion_manager.py`; write `Dockerfile` | Gemini | Codex | service starts; `POST /ingest` returns 202; job status queryable; smoke test passes |
| `SVC-010` | Build `data-catalog-svc` stub: create `services/data/catalog/`; FastAPI app with `GET /catalog`, `POST /catalog`, `GET /health`; in-memory or postgres-backed; write `Dockerfile` | Gemini | Qwen | service starts; `GET /catalog` returns empty list on fresh start; `POST /catalog` returns 201; smoke test passes |
| `SVC-011` | Build `feature-svc` stub: create `services/data/feature/`; FastAPI app with `POST /features/compute`, `GET /features/{id}`, `GET /health`; write `Dockerfile` | Gemini | Codex | service starts; `POST /features/compute` returns 202; smoke test passes |

### Wave 5 — App Surfaces (BFF and Persona, blocked on Wave 2+4)

| ID | Task | Owner lane | Reviewer lane | Acceptance criteria |
|---|---|---|---|---|
| `SVC-012` | Complete `bff`: replace TODO stubs in `services/control-plane/bff/main.py` with real HTTP client calls to backing services (URLs from env); add `PROMOTION_SVC_URL`, `REGISTRY_SVC_URL`, `TELEMETRY_SVC_URL`, `LINEAGE_SVC_URL`, `RUNTIME_MGR_URL` env vars; write `Dockerfile` | Codex | Claude | BFF starts; `GET /api/v1/operator/deployment-review/{plan_id}` returns real data from promotion-svc + telemetry-svc; all TODO markers removed from main.py; smoke test passes end-to-end |
| `SVC-013` | Complete `persona-hub-svc`: implement session registry and tool-bridge business logic in `services/control-plane/persona/main.py`; wire to registry-core and openclaw-adapter via env; update Dockerfile | Copilot | Claude | persona service starts; `POST /sessions` returns session_id; `GET /sessions/{id}` returns session state; smoke test passes |
| `SVC-014` | Build `consultation-svc` stub: create `services/control-plane/consultation/`; FastAPI with `POST /consult`, `GET /health`; stub returns mock committee response; write `Dockerfile` | Copilot | Qwen | service starts; `POST /consult` returns 200 with mock response; smoke test passes |
| `SVC-015` | Build `openclaw-adapter-svc` stub: create `services/integrations/openclaw/`; FastAPI with `POST /sessions`, `POST /tools/invoke`, `GET /health`; stub returns mock session and tool response; write `Dockerfile`; annotate that full OSS-001 integration is a follow-on task | Copilot | Codex | service starts; `POST /sessions` returns mock session_id; `POST /tools/invoke` returns mock tool response; smoke test passes; OSS-001 follow-on is documented in README |

### Wave 6 — Compose Assembly and Smoke Test (blocked on all above)

| ID | Task | Owner lane | Reviewer lane | Acceptance criteria |
|---|---|---|---|---|
| `DEPLOY-003` | Update `docker-compose.test.yml`: add all 16 service entries with correct `build:`, `ports:`, `environment:` (from `.env.example` defaults), `depends_on:`, `healthcheck:`; add OSS workers (dspy, imitation, mlflow) as optional profile | Gemini | Codex | `docker compose -f docker-compose.test.yml up --build` runs to completion; all services pass healthcheck; no orphan containers |
| `DEPLOY-004` | Write `scripts/smoke_test_single_vm.sh`: call each service's `/health`; call BFF's primary paths; call at least one `POST /approval-decisions → GET → POST /deployment-plans → POST /bindings` chain; output pass/fail per service | Gemini | Claude | script runs to completion; all 16 `/health` endpoints return 200; the DeploymentPlan → RuntimeBinding chain call returns expected shapes |
| `DEPLOY-005` | Write Golden Replay runbook (`docs/golden-replay-runbook.md`): step-by-step commands for running the Golden Replay acceptance scenario on the single-VM test env; cite §3.5 acceptance criteria from `Pantheon_單VM測試版_雙VM正式版_部署補充說明.md` | Codex | Claude | runbook covers all 10 acceptance criteria from §3.5; commands are copy-pasteable; expected outputs documented |

## Open Disagreements

None at seed time. Reviewers should raise disagreements in `review-round-01.md`.

## Key Decisions Embedded in This Draft

1. Group B services are minimum-viable stubs for test env — full implementation is post-test-env work
2. `openclaw-adapter-svc` stub is sufficient; OSS-001 real integration is a tracked follow-on, not a blocker
3. `runtime-manager-svc` does not integrate real LEAN in test env — mock RuntimeBinding is sufficient
4. DB migrations use plain SQL in `migrations/` directory (no Alembic framework dependency) unless Codex review finds a strong reason to prefer Alembic
5. Wave ordering is dependencies-driven; within each wave, tasks are parallelizable across agents
