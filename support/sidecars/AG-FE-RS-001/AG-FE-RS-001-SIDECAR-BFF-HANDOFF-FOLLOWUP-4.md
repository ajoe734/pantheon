# AG-FE-RS-001 BFF and Frontend Handoff Follow-up 4

| Field | Value |
|---|---|
| Task ID | `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-RS-001` - Research plan/run/consult/backtest cards |
| Owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff after task PR |

This is a support artifact only. It does not edit L1 canonical truth, OpenAPI,
JSON schemas, BFF runtime, registry/governance code, or execute-plans frontend
code. It complements the prior AG-FE-RS-001 sidecar packets and narrows them
into a parent-owner intake checklist: what the frontend owner can safely absorb
first, which BFF shapes must be handled exactly, and where the owner must stop
instead of filling gaps locally.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates task lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_4.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | Active task is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001` | Parent remains `todo`; depends on `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004`; frontend must stop on unclear spec/design gaps. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-002` | Still `todo`; owns conversation/result cards and completeness rail placement, including shared card surfaces. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-RS-002` | Archived `done`; unified run/progress/result projection was implemented and reviewed. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-XR-OPENAPI-004` | Archived `done`; v1.3 OpenAPI/schema/capability bundle is merged. |
| Prior AG-FE-RS-001 handoff packets | Existing packets document initial BFF surface, envelope corrections, SSE runtime shape, cancel behavior, absorption order, and stop lines. |
| `services/control-plane/bff/agora/research/router.py` | Implemented plan/run route family; plan detail uses envelopes, run detail returns raw projection, actions require idempotency and often ETag. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Workshop-level versions, research-runs, consultations, and conclude routes are explicit `501` stubs; `/stream` uses runtime SSE `id/type/timestamp/data`. |
| `services/control-plane/bff/tests/test_agora_research_run_projection.py` | Tests assert raw run projection, schema validation, list/artifact envelopes, and queued research SSE events. |
| `services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py` | Tests assert SSE formatting and helper payload shape with `type` and `data`. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | v1.3 route catalog lists research plan/run routes, `/cards`, and version comparison surfaces; runtime does not implement every listed workshop/card/version route yet. |
| `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` | Plan schema requires plan identity, status, stages, and `research_plan_no_order_route`. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | Run schema requires execution status, outcome, progress, backend, no-order proof, and optional metrics/findings/warnings/blockers/artifacts/evidence. |
| `services/control-plane/specs/agora/v4/workshop_card.schema.json` | Workshop cards are typed by `card_type`; pages must not infer card type from free LLM text. |
| `services/control-plane/specs/agora/v4/capability_manifest_v1_3.json` | `agora.research.v1` has `execution_authority: research_only`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/05_workshop_card_contracts.md` | Frontend source map binds research plan to plan detail and research progress/result to run projection; consult and version cards need their own projections/routes. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/05_execute_plans_agora_ui_ia_and_dependencies.md` | Strategy Workshop page includes research cards; all BFF access must go through `src/lib/bff-v1/agora/*`, not direct page fetches. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Parent Intake Summary

`AG-FE-RS-001` should absorb this support work as a staged frontend delivery,
not as permission to fill missing backend/card-projection gaps.

| Intake decision | Parent-owner action |
|---|---|
| The research plan/run BFF route family is usable. | Start with `execute-plans/src/lib/bff-v1/agora/research.ts` and route-backed adapters/tests. |
| `AG-FE-SW-002` is still `todo`. | Do not claim full conversation card or completeness rail integration until that task lands or hands off a concrete card source. |
| `GET /bff/agora/workshops/{workshop_id}/cards` exists in OpenAPI but no matching runtime route was found in the inspected BFF routers. | Do not fabricate `WorkshopCard` payloads; either render route-backed research surfaces explicitly or open a backend card-projection blocker. |
| Consultation and version surfaces are not available as implemented Agora BFF projections. | Keep `ConsultResultCard` and `VersionCompareCard` blocked for AG-FE-RS-001. |
| The research capability is `research_only`. | No card or client method may create orders, bind capital, promote canary/live, or write `RuntimeBinding`. |

---

## Minimum Safe Absorption Sequence

1. Create a clean `execute-plans` task branch from the current delivery base.
2. Refresh or explicitly mirror the v1.3 Agora research types before writing frontend code.
3. Implement `src/lib/bff-v1/agora/research.ts` first, using only implemented BFF routes.
4. Add client/adapter tests for envelope unwrapping, ETag/idempotency headers, raw run projection, terminal cancel conflict, and current SSE runtime shape.
5. Add route-backed `ResearchPlanCard`, `ResearchRunCard`, and backtest-result rendering only where the data source is explicit.
6. Stop and open a blocker before workshop-card projection integration, `ConsultResultCard`, `VersionCompareCard`, or any route that is only present in OpenAPI but not runtime.

This sequence is the narrowest useful slice for the parent owner. It preserves
progress while keeping the broader Strategy Workshop card stream and completeness
rail under the task that owns them.

---

## BFF Client Contract To Carry Into `research.ts`

| Client method | Implemented route | Required headers | Response handling |
|---|---|---|---|
| `listWorkshopResearchPlans(workshopId)` | `GET /bff/agora/workshops/{workshop_id}/research-plans` | auth/session only | List envelope; read `items[]`. |
| `createWorkshopResearchPlan(workshopId, body, options)` | `POST /bff/agora/workshops/{workshop_id}/research-plans` | `Idempotency-Key`; runtime currently does not require `If-Match` | Detail envelope; read `data` and `meta.etag`. |
| `getResearchPlan(planId)` | `GET /bff/agora/research-plans/{plan_id}` | auth/session only | Detail envelope; read `data`, `allowedActions`, and `meta.etag`. |
| `approveResearchPlan(planId, options)` | `POST /bff/agora/research-plans/{plan_id}/approve` | `If-Match`, `Idempotency-Key` | Command response; refetch plan to get new ETag before dispatch. |
| `cancelResearchPlan(planId, options)` | `POST /bff/agora/research-plans/{plan_id}/cancel` | `If-Match`, `Idempotency-Key` | Command response; disable for terminal plan statuses. |
| `listResearchPlanRuns(planId)` | `GET /bff/agora/research-plans/{plan_id}/runs` | auth/session only | List envelope; items are `ResearchRunProjection` objects. |
| `dispatchResearchRun(planId, options)` | `POST /bff/agora/research-plans/{plan_id}/runs` | `If-Match`, `Idempotency-Key` | Command response with queued ids only; call `getResearchRun(run_id)` for the full projection. |
| `getResearchRun(runId)` | `GET /bff/agora/research-runs/{run_id}` | auth/session only | Raw `ResearchRunProjection`; do not unwrap `data`. |
| `cancelResearchRun(runId, options)` | `POST /bff/agora/research-runs/{run_id}/cancel` | `Idempotency-Key` | Accepted command response only for `queued`, `dispatching`, or `running`; terminal statuses return conflict. |
| `listResearchRunArtifacts(runId)` | `GET /bff/agora/research-runs/{run_id}/artifacts` | auth/session only | List envelope; items can include artifact refs and evidence-ref objects. |

Error handling requirements:

- Map `409` to refresh-required/conflict and refetch the relevant plan or run.
- Map missing/precondition failures (`412`/`428` where surfaced) to stale ETag or missing ETag UI states.
- Preserve backend-degraded or blocked data exactly; do not replace it with fixture success.
- Keep live strict mode: no local fixture fallback and no direct service fanout.

---

## Card Source Matrix

| Component | Safe data source | Disposition for parent |
|---|---|---|
| `ResearchPlanCard` | `ResearchPlanExecution` from plan detail envelope, plus `allowedActions` and `meta.etag`. | Implementable as route-backed rendering. Stop if the intended source must be `WorkshopCard` projection before the cards route exists. |
| `ResearchRunCard` | Raw `ResearchRunProjection` plus current runtime SSE `research.run.*` events shaped as `id/type/timestamp/data`. | Implementable as route-backed rendering and polling/SSE adapter. Preserve `backend.mode`, `warnings[]`, and `blocking_reasons[]`. |
| `BacktestResultCard` | Succeeded `ResearchRunProjection` where `stage_type` is a backtest-like stage such as `prototype_backtest`. | Implementable as a research-result rendering of the run projection; do not invent a distinct backtest result route. |
| `ConsultResultCard` | No implemented Agora BFF consultation projection was found; workshop consultation route is a `501` stub. | Blocked. Do not call internal `/api/v1/consult/*` from the frontend. |
| `VersionCompareCard` | Version-comparison routes are in v1.3 OpenAPI, but inspected workshop version routes remain `501` stubs. | Blocked for AG-FE-RS-001 unless a dedicated versioning/card-projection task lands first. |
| Workshop conversation card integration | `GET /bff/agora/workshops/{workshop_id}/cards` or stream card references, once runtime exists. | Blocked/coordination item with `AG-FE-SW-002`; do not synthesize `WorkshopCard.payload`. |

---

## Frontend Test Expectations For Parent

Before AG-FE-RS-001 asks for review, it should prove at least:

| Test target | Expected proof |
|---|---|
| Plan list/detail unwrapping | Lists read `items[]`; detail reads `data` and `meta.etag`. |
| Create/approve/dispatch flow | `Idempotency-Key` is sent for commands; approve and dispatch send fresh `If-Match`. |
| Dispatch response | Queued command response is not treated as a full run projection; client follows with `getResearchRun`. |
| Run detail | `getResearchRun` handles a raw `ResearchRunProjection` with no `data` wrapper. |
| Run cancel | Cancel action is hidden/disabled outside `queued`, `dispatching`, and `running`; `409` triggers refetch. |
| SSE adapter | Reads runtime `event.type` and `event.data`, including `research.run.queued` and `research.run.progress`. |
| Backend mode and blockers | Card rendering always preserves `backend.mode`, `warnings[]`, and `blocking_reasons[]`. |
| No-order guardrail | No UI action or client method routes to broker order, capital binding, canary/live promotion, or `RuntimeBinding` writes. |

---

## Stop-Line Blockers Parent May Need

Use blocker text like this instead of guessing in frontend code:

```text
AG-FE-RS-001 blocked on card projection source: v1.3 OpenAPI lists
GET /bff/agora/workshops/{workshop_id}/cards, but no matching runtime route was
found in the inspected BFF routers. Need backend/card-projection owner to land
or explicitly hand off the route before WorkshopCard-backed rendering.
```

```text
AG-FE-RS-001 ConsultResultCard blocked: Agora workshop consultation route is a
501 stub and no consultation detail/projection GET route was found. Frontend
must not fan out to internal consult routes or mock consultation payloads.
```

```text
AG-FE-RS-001 VersionCompareCard blocked: version comparison/card semantics are
not backed by an implemented inspected runtime route. Need the versioning/card
projection task to land before UI implementation.
```

---

## Reviewer Checklist

Claude should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-scoped brief/status artifacts are intentionally changed. No runtime/frontend/schema/canonical docs are modified. |
| Non-duplication | Follow-up 4 does not replace Follow-up 2 or 3; it turns them into a parent-owner intake checklist and concrete stop lines. |
| Runtime accuracy | Route/envelope/header table matches `research/router.py` and BFF tests. |
| Dependency honesty | `AG-FE-SW-002` remains a coordination gate for full conversation-card and completeness-rail integration. |
| Blocker clarity | Consult, version compare, and workshop card projection are blocked instead of guessed. |
| No-order guardrail | `agora.research.v1` remains research-only; no order, capital, canary/live, or `RuntimeBinding` action is suggested. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: parent-owner intake checklist, BFF route/envelope/header mapping, card source matrix, and stop-line blockers are documented for AG-FE-RS-001; no canonical truth, runtime, schema, or frontend files changed." \
  ./scripts/ai-status.sh approve AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4 \
  "Support-only AG-FE-RS-001 BFF/frontend follow-up packet approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4 \
  "Describe the factual correction, missing blocker, or parent handoff detail needed before approval."
```

---

## Validation

Focused validation run from this task worktree:

```bash
git status --short
# -> ?? .orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_4.md
# -> ?? support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md

AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
# -> source: active; status: in_progress; owner: Codex; reviewer: Claude

AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001
# -> source: active; status: todo; depends_on includes AG-FE-SW-002, AG-BE-RS-002, AG-XR-OPENAPI-004

AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-002
# -> source: active; status: todo

AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-RS-002
# -> source: archive; terminal_status: done

AI_NAME=Codex python3 scripts/ai_status.py show AG-XR-OPENAPI-004
# -> source: archive; terminal_status: done

python3 -m json.tool services/control-plane/specs/agora/v4/research_plan_execution.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/research_run_projection.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/workshop_card.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/capability_manifest_v1_3.json > /dev/null
# -> all passed

python3 -m pytest services/control-plane/bff/tests/test_agora_research_run_projection.py services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py -q
# -> 27 passed in 21.53s

git diff --check
# -> passed
```
