# Discussion Planning Mode — Phase 4: Service Layer Completion

This directory is the canonical workspace for `discussion_planning`.

## Session

- Session ID: `phase4-2026-04-15-service-layer-completion`
- Phase: `phase4`
- Objective: Complete Phase 3–5 of the Pantheon roadmap by wrapping existing domain objects into deployable HTTP services, building five missing services as stubs, writing all Dockerfiles, and producing a working `docker-compose.test.yml` for single-VM test environment deployment.
- Facilitator: `Claude`
- Starter draft owner: `Codex`

## Background

Repo audit on 2026-04-15 revealed that after 2–3 development rounds, only 4 of 16 target services have a runnable HTTP surface (`bff`, `persona`, `router`, `feedback`). Nine services exist as domain objects/Python modules only; five services do not exist at all. The deployment target (single-VM Docker Compose) cannot be assembled until the service layer is complete.

## Brief Files

### L0 State
- `ai-status.json`
- `current-work.md`

### L1 Architecture & Policy
- `TARGET_ARCHITECTURE.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `PERSONA_RUNTIME_MODEL.md`
- `OPENCLAW_RUNTIME_CONTRACT.md`
- `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`
- `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`
- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`
- `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`

### L2 Planning
- `ROADMAP.md`
- `DEVELOPMENT_WORKBREAKDOWN.md`
- `OSS_INTEGRATION_CHECKLIST.md`

### Deployment Specs
- `Pantheon_單VM測試版_雙VM正式版_部署補充說明.md`
- `Pantheon_GCP_GitHub_Docker_正式部署與環境設計_v2.md`

### Gap Audit
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/claude-readout.md`

## Gap Summary

### Group A — Domain objects exist, need HTTP wrapper + Dockerfile (9 services)

| Service | Repo path | Existing code |
|---|---|---|
| `promotion-svc` | `services/control-plane/governance/` | `approval_decision.py`, `deployment_plan.py`, `deployment_saga.py` |
| `telemetry-incident-svc` | `services/telemetry/` + `services/incident/` | `ingest_svc.py`, `incident.py` |
| `lineage-read-svc` | `services/telemetry/lineage_read/` | `service.py` |
| `evolution-svc` | `services/control-plane/governance/` | `evolution_controller.py`, `evolution_decision.py` |
| `registry-core-svc` | `services/registry-core/decision-domain/` | decision-domain models |
| `runtime-manager-svc` | `services/execution/runtime-manager/` | `runtime_binding.py`, `kill_switch_controller.py` |
| `optimizer-svc` | `services/optimizer-svc/` | `synthesizer.py` |
| `bff` | `services/control-plane/bff/` | `main.py` (FastAPI partial, TODOs remain) |
| `research-orchestrator-svc` | `services/research/` | ingest + replication + adapters (has Dockerfile, needs HTTP entrypoint) |

### Group B — Must be built from scratch (5 services)

| Service | Notes |
|---|---|
| `openclaw-adapter-svc` | Only governance docs; use minimal stub (health + passthrough) |
| `consultation-svc` | No directory; single-endpoint stub acceptable for test env |
| `data-ingest-svc` | Research ingest library exists; needs HTTP wrapper + job trigger |
| `data-catalog-svc` | No directory; minimal stub |
| `feature-svc` | No directory; minimal stub |

### Group C — Infrastructure (official images, no Dockerfile needed)
- `postgres`, `redis`/`nats`, `minio`, `clickhouse` (optional)

### Group D — OSS workers (Dockerfiles already exist, include in compose)
- `dspy-worker`, `imitation-worker`, `mlflow-server` — ready
- `qlib-worker`, `finrl-worker` — include but mark optional/profile

## Expected Outputs

| Output | Owner | Path |
|---|---|---|
| `starter-draft.md` | Codex | this dir |
| `review-round-01.md` | Qwen, Gemini, Copilot, Claude | this dir |
| `consensus-packet.md` | Claude | this dir |
| `execution-materialization.md` | Codex | this dir |

## Baton Loop

1. Every lane reads this README and brief files, writes an independent readout using `LLM_READOUT_TEMPLATE.md`
2. `Codex` seeds `starter-draft.md` with proposed task slices
3. Cross-review in `review-round-01.md`
4. `Claude` drafts `consensus-packet.md`
5. Human gate: operator approves
6. `Codex` materializes via `scripts/planning_state.py materialize`

## Rules

- Only `Codex` edits `starter-draft.md` directly
- Reviewers do not rewrite the shared draft — raise disagreements in review rounds
- `planning-session.json` is the machine-readable source of truth
- Execution tasks stay in `ai-status.json`; do not populate before materialization
- Group B stubs must be minimal but runnable: one `/health` endpoint + service skeleton is sufficient
- Every task must cite the repo path it operates on
- Dockerfiles must follow the pattern in `services/control-plane/persona/Dockerfile`
- `docker-compose.test.yml` must run standalone via `docker compose -f docker-compose.test.yml up`
