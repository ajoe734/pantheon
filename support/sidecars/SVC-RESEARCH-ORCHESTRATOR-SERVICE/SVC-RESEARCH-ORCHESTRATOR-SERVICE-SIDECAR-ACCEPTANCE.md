# SVC-RESEARCH-ORCHESTRATOR-SERVICE Acceptance Packet and Dependency Map

**Sidecar Task ID**: `SVC-RESEARCH-ORCHESTRATOR-SERVICE-SIDECAR-ACCEPTANCE`
**Parent Task**: `SVC-RESEARCH-ORCHESTRATOR-SERVICE`
**Parent Owner**: `Codex2`
**Parent Reviewer**: `Codex`
**Parent Status at Original Packet Time**: `review_approved`
**Parent Current Status at Refresh**: archived `done` (`2026-04-28T19:18:50Z`)
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Codex2` (auto-reassigned from `Copilot` on 2026-04-29 after repeated Copilot quota failure)
**Helper Kind**: `acceptance_packet`
**Date**: 2026-04-28 (reviewer reassignment refresh: 2026-04-29)

> This is a support artifact only. It does not modify L1 canonical truth,
> core contracts, registry truth, governance implementation, or the
> research-orchestrator runtime implementation. The parent owner decides
> whether and how to absorb this packet into the main closeout.

---

## 1. Scope Snapshot

`SVC-RESEARCH-ORCHESTRATOR-SERVICE` materializes
`research-orchestrator-svc` as a bounded HTTP wrapper for research task/run
lifecycle, artifact handoff, proposal handoff, and replayable status reads.

The parent review was approved and the parent task is now archived as `done` in
`ai-status.json`. The approved review record is
`docs/reviews/2026-04-28-svc-research-orchestrator-service-codex-review.md`
with this verification:

| Verification | Result |
|---|---|
| `pytest services/research/tests/test_research_orchestrator_http_service.py services/research/tests/test_research_orchestrator_compose_activation.py` | `3 passed` |
| `docker compose config --quiet` | passed |

This sidecar reran the same verification on 2026-04-29:

| Sidecar verification | Result |
|---|---|
| `pytest services/research/tests/test_research_orchestrator_http_service.py services/research/tests/test_research_orchestrator_compose_activation.py` | `3 passed in 1.62s` |
| `docker compose config --quiet` | passed |
| `git diff --no-index --check /dev/null support/sidecars/SVC-RESEARCH-ORCHESTRATOR-SERVICE/SVC-RESEARCH-ORCHESTRATOR-SERVICE-SIDECAR-ACCEPTANCE.md` | no whitespace errors reported |

Current reviewed implementation surface:

| Area | Evidence |
|---|---|
| HTTP service | `services/research/main.py` |
| Durable JSON store | `services/research/store.py` |
| Container entrypoint | `services/research/Dockerfile` |
| Compose service | `docker-compose.yml` service `research-orchestrator-svc` |
| Smoke integration | `scripts/smoke_honest_stack.py` research-orchestrator flow |
| Focused tests | `services/research/tests/test_research_orchestrator_http_service.py`, `services/research/tests/test_research_orchestrator_compose_activation.py` |

---

## 2. Acceptance Checklist

| Parent acceptance item | Sidecar verification | Status |
|---|---|---|
| Research-orchestrator service exposes task, run, status, artifact handoff, and health APIs. | `services/research/main.py` exposes `/health`, `/api/research-orchestrator/capabilities`, task create/list/get, run dispatch/list/get/status/complete, run artifact handoff/list, and run proposal handoff/list. | PASS |
| Dockerfile, env storage, and compose wiring are added. | `services/research/Dockerfile` runs `uvicorn main:app --app-dir services/research` on `PORT=8101`. Compose wires `RESEARCH_ORCHESTRATOR_DATA_DIR=/data/research-orchestrator`, `RESEARCH_ORCHESTRATOR_MAX_ACTIVE_RUNS`, `RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS=false`, a durable `research-orchestrator-data` volume, port `${RESEARCH_ORCHESTRATOR_PORT:-18101}:8101`, and a `/health` healthcheck. | PASS |
| External learning framework production activation remains disabled unless separately approved. | `PRODUCTION_ADAPTERS_ALLOWED` defaults false; compose sets `RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS=false`; Qlib/TRL/RL/RLLib/FinRL or production/paper/canary/live dispatch requests are recorded as `rejected` with `production_adapter_disabled` rather than executed. | PASS |
| Tests cover task lifecycle, handoff idempotency, and compose config. | Focused tests cover task idempotency, run idempotency, artifact/proposal handoff, run status refs, completion, production-adapter rejection, active-run bound, and compose service/volume/smoke env wiring. Parent review records `3 passed` plus compose config passed. | PASS |
| Support-only sidecar constraint is respected. | This packet creates only `support/sidecars/SVC-RESEARCH-ORCHESTRATOR-SERVICE/SVC-RESEARCH-ORCHESTRATOR-SERVICE-SIDECAR-ACCEPTANCE.md`. Normal `ai-status` handoff updates remain coordination state, not product truth. | PASS |

---

## 3. API Surface Inventory

| Route | Purpose | Boundary note |
|---|---|---|
| `GET /health` | Liveness and basic counters for task/run counts, active runs, max active runs, and production-adapter flag. | Health only; not an execution authority. |
| `GET /api/research-orchestrator/capabilities` | Advertises stub/handoff/manual availability and marks production adapters deferred. | Production activation stays disabled in this service boundary. |
| `GET /api/research-orchestrator/tasks` | List research tasks, optionally filtered by status. | Read-only over local service store. |
| `POST /api/research-orchestrator/tasks` | Create idempotent research tasks from objective, source refs, constraints, and actor. | Creates research-plane task records only. |
| `GET /api/research-orchestrator/tasks/{task_id}` | Read one task. | No registry/governance write. |
| `POST /api/research-orchestrator/tasks/{task_id}/runs` | Dispatch a bounded stub/handoff run or record a disabled production request as rejected. | Enforces `RESEARCH_ORCHESTRATOR_MAX_ACTIVE_RUNS` and production-adapter gate. |
| `GET /api/research-orchestrator/runs` | List runs with optional task/status filters. | Read-only replay surface. |
| `GET /api/research-orchestrator/runs/{run_id}` | Read one run. | Read-only replay surface. |
| `GET /api/research-orchestrator/runs/{run_id}/status` | Return concise run status, rejection, event, artifact ref, and proposal ref projection. | Handoff status, not governance approval. |
| `POST /api/research-orchestrator/runs/{run_id}/complete` | Mark a non-rejected run complete and append an event. | Cannot complete rejected production-adapter requests. |
| `POST /api/research-orchestrator/runs/{run_id}/artifacts` | Persist idempotent artifact handoff with registry projection metadata. | Artifact remains `artifact_state=draft`, `deployment_stage=none`, `direct_live_influence=false`. |
| `GET /api/research-orchestrator/runs/{run_id}/artifacts` | List run artifacts. | Research-plane read. |
| `POST /api/research-orchestrator/runs/{run_id}/proposals` | Persist idempotent proposal handoff. | Proposal has `production_activation=disabled`; downstream owner must decide promotion. |
| `GET /api/research-orchestrator/runs/{run_id}/proposals` | List run proposals. | Research-plane read. |

---

## 4. Storage, Compose, and Smoke Map

| Concern | Evidence |
|---|---|
| Data directory | `RESEARCH_ORCHESTRATOR_DATA_DIR`, default `/tmp/pantheon/research-orchestrator`, compose value `/data/research-orchestrator`. |
| Stored records | `research_tasks.json`, `research_runs.json`, `research_artifacts.json`, `research_proposals.json`, `research_events.jsonl` under the service data directory. |
| Dispatch bound | `RESEARCH_ORCHESTRATOR_MAX_ACTIVE_RUNS`, default `8`, enforced before non-rejected dispatch. |
| Production activation gate | `RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS`, compose value `"false"`. |
| Service name and port | `research-orchestrator-svc`, container port `8101`, host port `${RESEARCH_ORCHESTRATOR_PORT:-18101}`. |
| Smoke consumer | `smoke-stack` receives `RESEARCH_ORCHESTRATOR_URL=http://research-orchestrator-svc:8101` and depends on the service healthcheck. |
| Smoke behavior | `scripts/smoke_honest_stack.py` creates a research task, dispatches a stub run, hands off a draft artifact, and verifies Qlib production dispatch is rejected. |

---

## 5. Dependency Map

### Direct prerequisites

| Dependency | Status | Why it matters |
|---|---|---|
| `SVC-SOURCE-INGEST-SERVICE` | `done`, archived 2026-04-28T17:59:46Z, delivery commit `038cb170e57e1e2e4cb58bef8592eba09456ac19` | Provides the bounded source-ingest wrapper and replayable source/job evidence that can feed research tasks as source refs without hidden in-process activation. |
| `SVC-SEARCH-SERVICE` | `done`, archived 2026-04-28T18:38:30Z, delivery commit `f9803f59b87c8bd29467fba935936322409823f0` | Provides governed search/index snapshot outputs that research tasks can reference; smoke flow uses search output as a `source_ref`. |
| `SVC-COMPOSE` | `done`, archived 2026-04-28T17:31:00Z, delivery commit `5a4ece78ed41b8e7b995d43676a03924c9106d3c` | Establishes the single-VM compose stack, service healthcheck pattern, smoke profile, and durable volume wiring followed by `research-orchestrator-svc`. |

### Adjacent service materialization

| Task | Relationship |
|---|---|
| `SVC-TRAINING-SESSION-SERVICE` | Sibling service in the future-state research/training family. Research-orchestrator should hand off proposals/artifacts rather than silently invoking training production paths. |
| `SVC-POLICY-LEARNING-BOUNDARY` | Sibling service enforcing learning activation boundaries. Research-orchestrator mirrors that policy by recording disabled production requests rather than enabling Qlib/TRL/RL execution. |
| `SVC-RESEARCH-WORKER-GATEWAY` | Downstream/adjacent gateway work may consume research task/run status. This packet treats current dispatch as stub/handoff-only. |
| `SVC-RECONCILIATION-DRIFT-SERVICE` | Sibling read/projection materialization in compose. No direct dependency; both should preserve their own service boundary and health/smoke contracts. |

### Downstream consumers

| Consumer | What it can rely on |
|---|---|
| `smoke-stack` | A healthchecked `RESEARCH_ORCHESTRATOR_URL` with task/run/artifact lifecycle and production rejection. |
| Future BFF/operator surfaces | Explicit service URL and replayable status reads instead of normal-path snapshots. |
| Registry/governance follow-up work | Draft artifacts and proposed candidates with explicit `deployment_stage=none` and no direct live influence. Promotion remains outside this service. |

---

## 6. Boundary and Non-Activation Notes

This packet intentionally preserves the parent task boundary:

| Concern | Owner / disposition |
|---|---|
| Qlib, TRL, RLLib, FinRL, or RL production execution | Deferred. Requests are rejected unless a separate approved activation task changes the gate. |
| Canonical registry truth | Remains outside this service. Research artifacts include a registry projection only, with draft/non-live semantics. |
| Governance approval and deployment stage changes | Remain outside this service. Proposals are handoff records, not approvals. |
| LEAN/live trading influence | Explicitly disabled through artifact governance metadata and `deployment_stage=none`. |
| Source ingest and governed search execution | Owned by their deployable services; research-orchestrator references their outputs as inputs. |

---

## 7. Reviewer Checklist for Codex2

| Check | Expected answer |
|---|---|
| Did this sidecar avoid L1 canonical truth, contract truth, registry truth, governance, and runtime implementation edits? | Yes. It only adds this support packet; status handoff uses the normal L0 coordination script. |
| Does the packet match the parent task's approved acceptance evidence? | Yes. It mirrors the approved parent review file and the focused test/compose evidence. |
| Are all direct parent dependencies represented? | Yes. `SVC-SOURCE-INGEST-SERVICE`, `SVC-SEARCH-SERVICE`, and `SVC-COMPOSE` are each mapped as `done` archived tasks with delivery commits. |
| Is production learning framework activation still disabled? | Yes. Compose sets the production gate false; service logic rejects production/paper/canary/live adapter requests. |
| Is the artifact/proposal handoff boundary clear? | Yes. Artifacts are draft, deployment stage `none`, no direct live influence; proposals are handoff records with production activation disabled. |

---

## 8. Handoff

**To**: `Codex2`
**From**: `Codex`
**Reviewer reassignment note**: The orchestrator reassigned this review from
`Copilot` to `Codex2` at `2026-04-29T03:30:57Z` after repeated Copilot quota
failure.
**Requested review outcome**: Approve this sidecar if it accurately captures
the parent acceptance evidence and dependency map for
`SVC-RESEARCH-ORCHESTRATOR-SERVICE`.

Recommended parent-owner use:

1. Treat §2 as the parent closeout checklist already backed by the approved
   Codex review.
2. Treat §5 as the dependency map showing why source-ingest, search, and
   compose are sufficient prerequisites for this service wrapper.
3. Keep §6 intact when finalizing or referencing the work: this service is a
   bounded research lifecycle/handoff wrapper, not an activation of Qlib/TRL/RL
   production execution or a registry/governance write owner.
