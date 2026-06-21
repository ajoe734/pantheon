# AG-FE-RS-001 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-FE-RS-001-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-RS-001` — Research plan/run/consult/backtest cards |
| Parent owner / reviewer | `Claude` / `Codex` |
| Prepared by | `Claude2` |
| Reviewer | `Claude` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, research services, registry/governance
implementation, or execute-plans frontend code. It summarizes the BFF surface
state, operator journeys, card field bindings, and frontend handoff boundaries for
`AG-FE-RS-001`; the parent owner decides whether and how to absorb it into the
main implementation.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff.md` | Sidecar is support-only: BFF query gap, operator journey, frontend handoff materials; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes must pass task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`, owner `Claude2`, reviewer `Claude`, parent `AG-FE-RS-001`, helper kind `bff_handoff_packet`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-RS-001` | Status `todo`; owner `Claude`; depends on `AG-FE-SW-002`, `AG-BE-RS-002`, `AG-XR-OPENAPI-004`; scope includes `ResearchPlanCard`, `ResearchRunCard`, `ConsultResultCard`, `BacktestResultCard`, and BFF client `research.ts`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-RS-001` | Status `done`; plan-first facade is fully closed — plan CRUD/approve/cancel/stage routing are implemented. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-RS-002` | Status `done`; unified run/progress/result/artifact/SSE projection is implemented; all run routes are live. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-XR-OPENAPI-004` | Status `done` (archived); v1.3 OpenAPI bundle and type generation merged. |
| `services/control-plane/bff/agora/research/router.py` | All plan-first and run/projection/artifact routes are implemented; `publish_research_progress()` is wired. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | `POST /bff/agora/workshops/{id}/consultations` is a `501 Not Implemented` stub. No GET for consultation detail. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | `ResearchRunProjection` v1: identity/lineage refs, `execution_status` enum, `outcome`, `progress`, `backend` (requested/effective/mode/version), `metrics[]` (7 categories), `findings[]`, `warnings[]`, `blocking_reasons[]`, `artifact_refs[]`, `evidence_refs[]`, `failure`, `data_cutoff`, `no_order_route_proof`, timestamps. |
| `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` | `ResearchPlanExecution`: `plan_id`, `workshop_id`, `strategy_id`, `status`, `stages[]`, `run_ids[]`, `budget`, `execution_constraints`, `no_order_route_proof`, `approval`. |
| `services/control-plane/specs/agora/v4/version_compare.schema.json` | `VersionCompare` schema exists but has no implementing BFF route. VersionCompareCard remains blocked on design gap A. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/05_workshop_card_contracts.md` | E7 `research_plan_proposal`, E8 `research_progress`, E9 `research_result`, E10 `consult_result` card field specs. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/05_execute_plans_agora_ui_ia_and_dependencies.md` | Strategy Workshop page composition: `ResearchPlanCard`, `ResearchRunCard`, `BacktestResultCard`, `VersionCompareCard` in the `WorkshopConversation (70%)` column. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` | AG-FE-RS-001 is gated on gaps A (strategy versioning), B (research facade), and E (workshop card field specs). Gap B is now resolved. Gap E is partially resolved by design-closure-round2. Gap A (VersionPatchProposal/VersionCompare) is still open. |
| `execute-plans/src/lib/bff-v1/agora/` | Contains `types.ts`, `dashboard.ts`, `contract-snapshot.json` only. No `research.ts` exists. |
| `execute-plans/src/agora/` | Contains `dashboard/`, `pages/`, `widgets/`. No research card components exist. |
| `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` | BFF is the sole frontend aggregation point; research routes must return typed degraded/blocked states, not synthetic success. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Current BFF State Observed In This Worktree

### Research Plan Routes (all implemented — AG-BE-RS-001 done)

| Surface | Observed state | Frontend meaning |
|---|---|---|
| `GET /bff/agora/workshops/{workshop_id}/research-plans` | Implemented; returns list envelope. | Frontend can fetch research plans for a workshop. |
| `POST /bff/agora/workshops/{workshop_id}/research-plans` | Implemented; requires `Idempotency-Key`; plan starts as `draft`. | Frontend creates a plan from a proposal card action. |
| `GET /bff/agora/research-plans/{plan_id}` | Implemented; returns `ResearchPlanExecution` envelope. | ResearchPlanCard can fetch plan detail. |
| `POST /bff/agora/research-plans/{plan_id}/approve` | Implemented; requires `If-Match` + `Idempotency-Key`; advances `draft → approved`. | ResearchPlanCard `approve` action. |
| `POST /bff/agora/research-plans/{plan_id}/cancel` | Implemented; requires `If-Match` + `Idempotency-Key`; cancellable from `draft`/`approved`/`running`. | ResearchPlanCard `cancel` action. |

### Research Run Routes (all implemented — AG-BE-RS-002 done)

| Surface | Observed state | Frontend meaning |
|---|---|---|
| `GET /bff/agora/research-plans/{plan_id}/runs` | Implemented; returns list envelope of `ResearchRunProjection`. | ResearchRunCard list by plan. |
| `POST /bff/agora/research-plans/{plan_id}/runs` | Implemented; requires `If-Match` + `Idempotency-Key`; plan must be `approved`; returns `202` with queued run. | Run dispatch action from an approved plan. |
| `GET /bff/agora/research-runs/{run_id}` | Implemented; returns schema-conformant `ResearchRunProjection`. | ResearchRunCard and BacktestResultCard data source. |
| `POST /bff/agora/research-runs/{run_id}/cancel` | Implemented; requires `Idempotency-Key`; idempotent cancel of `queued`/`dispatching`/`running` runs. | ResearchRunCard `cancel` action. |
| `GET /bff/agora/research-runs/{run_id}/artifacts` | Implemented; returns `artifact_refs[]` and `evidence_refs[]`. | Artifact list for a completed run. |
| Workshop SSE `research.run.progress` | `publish_research_progress()` called on dispatch and cancel transitions. | ResearchRunCard SSE binding. |

### Consultation Agora Routes (not implemented)

| Surface | Observed state | Frontend meaning |
|---|---|---|
| `POST /bff/agora/workshops/{workshop_id}/consultations` | `501 Not Implemented` stub in strategy_workshop router. | ConsultResultCard cannot bind consultation data from Agora BFF. This card is blocked pending a future task. |
| `GET /bff/agora/workshops/{workshop_id}/consultations/{consultation_id}` | Route does not exist. | No consultation projection available from Agora BFF. |

Consultation data lives in `/api/v1/consult/*` (internal control-plane routes), not in the Agora BFF. Wiring consultation into the Agora BFF is out of scope for AG-FE-RS-001.

---

## Scope Boundary Summary

| Layer | Owner | Status |
|---|---|---|
| Research plan CRUD / approve / cancel / stage routing | `AG-BE-RS-001` | `done` |
| Run dispatch / projection / artifact / SSE / cancel | `AG-BE-RS-002` | `done` |
| v1.3 OpenAPI bundle + type generation | `AG-XR-OPENAPI-004` | `done` |
| Frontend BFF client `research.ts` | `AG-FE-RS-001` | `todo` (unblocked) |
| `ResearchPlanCard.tsx` + `ResearchRunCard.tsx` | `AG-FE-RS-001` | `todo` (unblocked) |
| `BacktestResultCard.tsx` | `AG-FE-RS-001` | `todo` (unblocked for `prototype_backtest` stage type) |
| `ConsultResultCard.tsx` | `AG-FE-RS-001` | blocked — no Agora BFF consultation route |
| `VersionCompareCard.tsx` | `AG-FE-RS-001` / `AG-FE-SW-003` | blocked — design gap A not resolved |

AG-FE-RS-001 must not write `RuntimeBinding`, capital binding, broker order, or governance promotion from any research card or BFF client method.

---

## BFF Query Gap Matrix

| Gap | Needed BFF surface | Disposition |
|---|---|---|
| Research plan list for a workshop | `GET /bff/agora/workshops/{workshop_id}/research-plans` | **Resolved** — implemented in AG-BE-RS-001. |
| Research plan create from workshop | `POST /bff/agora/workshops/{workshop_id}/research-plans` | **Resolved** — implemented in AG-BE-RS-001. |
| Research plan detail | `GET /bff/agora/research-plans/{plan_id}` | **Resolved** — implemented in AG-BE-RS-001. |
| Research plan approve | `POST /bff/agora/research-plans/{plan_id}/approve` | **Resolved** — implemented in AG-BE-RS-001. |
| Research plan cancel | `POST /bff/agora/research-plans/{plan_id}/cancel` | **Resolved** — implemented in AG-BE-RS-001. |
| Run list by plan | `GET /bff/agora/research-plans/{plan_id}/runs` | **Resolved** — implemented in AG-BE-RS-002. |
| Run dispatch from approved plan | `POST /bff/agora/research-plans/{plan_id}/runs` | **Resolved** — implemented in AG-BE-RS-002. |
| Run projection detail | `GET /bff/agora/research-runs/{run_id}` | **Resolved** — implemented in AG-BE-RS-002. |
| Run cancel (idempotent) | `POST /bff/agora/research-runs/{run_id}/cancel` | **Resolved** — implemented in AG-BE-RS-002. |
| Artifact/evidence refs by run | `GET /bff/agora/research-runs/{run_id}/artifacts` | **Resolved** — implemented in AG-BE-RS-002. |
| SSE progress events | `workshop.research.progress` / `research.run.queued` | **Resolved** — `publish_research_progress()` wired in AG-BE-RS-002. |
| Consultation projection for `consult_result` card | `GET /bff/agora/workshops/{id}/consultations/{id}` | **Open** — no Agora BFF route; `ConsultResultCard` cannot be built until a future task wires consultation into Agora BFF. |
| Version compare for `VersionCompareCard` | `GET /bff/agora/research-plans/{plan_id}/version-compare` or similar | **Open** — design gap A unresolved; route does not exist. |

---

## Operator Journeys

### Journey A: View Research Plans For A Workshop

1. Operator opens the Strategy Workshop page for a given `workshop_id`.
2. Frontend calls `listWorkshopResearchPlans(workshopId)` through the BFF client.
3. BFF returns the list envelope; frontend renders each plan's `plan_id`, `status`, `stages[]` count, and `no_order_route_proof`.
4. Operator selects a plan to view detail; frontend calls `getResearchPlan(planId)`.
5. ResearchPlanCard renders plan objective, stages, budget, constraints, and available actions based on `status`.

### Journey B: Approve A Research Plan

1. Operator views a plan with `status=draft` in ResearchPlanCard.
2. Operator selects "Approve".
3. Frontend calls `approveResearchPlan(planId, { ifMatch: plan.etag, idempotencyKey })`.
4. BFF verifies `status=draft` and advances plan to `approved`; publishes `research.plan.approved` event.
5. ResearchPlanCard transitions to showing "Dispatch run" action.

### Journey C: Dispatch A Run From An Approved Plan

1. Operator views an `approved` plan in ResearchPlanCard.
2. Operator selects "Dispatch run".
3. Frontend calls `dispatchResearchRun(planId, { ifMatch: plan.etag, idempotencyKey })`.
4. BFF verifies `status=approved`; selects first `pending`/`ready` stage; creates queued run; advances plan to `running`.
5. BFF publishes `research.run.queued` SSE event.
6. UI transitions to show ResearchRunCard with `execution_status=queued`.
7. UI must not show `running`, `succeeded`, or any result card until the projection confirms it.

### Journey D: Monitor Run Progress

1. After dispatch, frontend subscribes to the workshop SSE stream for `research.run.progress` events.
2. Alternatively, frontend polls `getResearchRun(runId)`.
3. BFF publishes `research.run.progress` events via `publish_research_progress()` whenever `execution_status` or `progress.percent` changes.
4. ResearchRunCard renders: `execution_status`, `progress.percent`, `progress.phase`, `progress.message`, `backend.mode`, `warnings[]`, and `blocking_reasons[]`.
5. UI must always show `backend.mode`: `real`, `fixture`, or `stub`. A `fixture` or `stub` run cannot satisfy full-validation readiness.
6. If a stage is blocked, UI displays `blocking_reasons[]` without hiding the block or substituting a stub success.

### Journey E: View Backtest Run Results

1. Once `execution_status=succeeded` and `stage_type` is a backtest type (e.g., `prototype_backtest`), the BacktestResultCard becomes available.
2. Frontend calls `getResearchRun(runId)` to fetch the final `ResearchRunProjection`.
3. BacktestResultCard renders:
   - `outcome` (`pass` / `fail` / `inconclusive`)
   - `metrics[]` grouped by `category` (7 types: `performance`, `risk`, `cost`, `capacity`, `robustness`, `calibration`, `data_quality`); each metric shows `value`, `unit`, `direction`, `threshold`, `gate_result`, and optional `baseline`/`delta`.
   - `findings[]` sorted by `severity` (`info → watch → warning → high → critical`), each with `summary`, `detail`, `evidence_refs[]`.
   - `data_cutoff`, `artifact_refs[]`, `evidence_refs[]`.
   - `backend.mode` label (always visible).
4. BacktestResultCard must not suggest candidate promotion, RuntimeBinding, or live trading actions.
5. Result card must not be rendered from in-progress or failed runs.

### Journey F: View And Download Artifacts

1. Operator requests artifact evidence from a completed run.
2. Frontend calls `listResearchRunArtifacts(runId)`.
3. BFF returns `artifact_refs[]` and `evidence_refs[]`.
4. UI renders links; must not fetch blob content through Agora BFF; download links reference the appropriate storage service directly.

### Journey G: Cancel A Running Run

1. Operator decides to abort an in-flight run.
2. Frontend calls `cancelResearchRun(runId, { idempotencyKey })`.
3. BFF sets `execution_status=cancelled` for the target run; publishes `research.run.progress` with `phase=cancelled`.
4. BFF does NOT cancel already `succeeded`, `failed`, or `timed_out` runs — a second cancel on an already-cancelled run is a no-op `202`.
5. UI reflects `execution_status=cancelled` on the run card.

### Journey H: Backend Capability Blocked

1. Operator dispatches a run whose required stage backend is unavailable or not yet activated.
2. BFF returns the queued run immediately; the relevant stage transitions to a `blocked` state with non-empty `blocking_reasons[]`.
3. ResearchRunCard displays the blocked stage with `blocking_reasons[]` and next activation gate.
4. UI must not hide the block or substitute a stub run silently.

---

## Frontend Handoff

### research.ts BFF Client

Add all typed methods to `execute-plans/src/lib/bff-v1/agora/research.ts`. Pages and cards must not call research BFF routes or the research orchestrator directly.

Suggested client method signatures:

```ts
// Plan methods (AG-BE-RS-001 scope — include in the same module)
listWorkshopResearchPlans(workshopId: string): Promise<ResearchPlanList>
createWorkshopResearchPlan(workshopId: string, body: ResearchPlanCreateRequest, options: IdempotencyOptions): Promise<ResearchPlanExecution>
getResearchPlan(planId: string): Promise<ResearchPlanExecution>
approveResearchPlan(planId: string, options: IfMatchIdempotencyOptions): Promise<ResearchPlanActionResult>
cancelResearchPlan(planId: string, options: IfMatchIdempotencyOptions): Promise<ResearchPlanActionResult>

// Run methods (AG-BE-RS-002 scope)
listResearchPlanRuns(planId: string): Promise<ResearchRunProjectionList>
dispatchResearchRun(planId: string, options: IfMatchIdempotencyOptions): Promise<ResearchRunDispatchResult>
getResearchRun(runId: string): Promise<ResearchRunProjection>
cancelResearchRun(runId: string, options: IdempotencyOptions): Promise<void>
listResearchRunArtifacts(runId: string): Promise<ArtifactRefList>
```

No local fixture fallback, no synthetic run data, no direct service fanout. Live strict only.

### Card Binding Guide

| Card component | Card type | When to render | Key payload bindings |
|---|---|---|---|
| `ResearchPlanCard` | `research_plan_proposal` | Card in workshop conversation stream; also when user selects a plan | `plan_id`, `status`, `stages[]` (id/type/purpose/preferred_backend/dependencies), `objectives[]`, `evaluation_criteria`, `budget`, `approval_requirement`, `warnings[]` |
| `ResearchRunCard` | `research_progress` | When `execution_status ∈ {queued, dispatching, running}` | `run_id`, `plan_id`, `stage_id`, `stage_type`, `execution_status`, `progress.percent`, `progress.phase`, `progress.message`, `backend.mode`, `warnings[]`, `blocking_reasons[]` |
| `BacktestResultCard` | `research_result` (for backtest stage types) | When `execution_status=succeeded` | `run_id`, `outcome`, `metrics[]` by category, `findings[]` by severity, `evidence_refs[]`, `artifact_refs[]`, `data_cutoff`, `backend.mode` label |

**Note on `BacktestResultCard`:** The card renders the `research_result` card type for runs whose `stage_type` is a backtest variant (e.g., `prototype_backtest`, `full_backtest`). The underlying data source is `ResearchRunProjection`; no separate "backtest result" route exists. The parent owner should open a blocker if a distinct `BacktestResult` schema or route is needed beyond what `ResearchRunProjection` provides.

**Note on `ConsultResultCard`:** The `consult_result` card (E10 in design-closure-round2/05_workshop_card_contracts.md) requires a consultation projection from the Agora BFF. The current Agora BFF consultation route (`POST /bff/agora/workshops/{id}/consultations`) is a `501 Not Implemented` stub, and no GET route for a consultation detail exists in the Agora BFF path. ConsultResultCard **cannot be implemented** within AG-FE-RS-001's scope until a future backend task wires consultation into the Agora BFF. The parent owner must open a blocker for this component and proceed with the three implementable cards.

### Error and Degraded State Map

| HTTP status | Meaning | Frontend action |
|---|---|---|
| `501` | Feature not implemented (consultation stub) | Show "coming soon" or suppress if gated. |
| `403` | Missing scope or auth | Show auth error; do not retry silently. |
| `404` | Plan or run not found | Clear the stale card view; do not retry. |
| `409` | Status conflict (e.g., dispatch non-approved plan, double-cancel) | Map to refresh-required state; refetch the resource. |
| `422` | Governance or precondition failure | Show the failure message; do not silently re-route. |
| `503` / blocked stage | Capability unavailable | Show `blocking_reasons[]`; do not substitute stub. |

### Action Headers Required

| Action | Required headers |
|---|---|
| `createWorkshopResearchPlan` | `Idempotency-Key` |
| `approveResearchPlan` | `If-Match`, `Idempotency-Key` |
| `cancelResearchPlan` | `If-Match`, `Idempotency-Key` |
| `dispatchResearchRun` | `If-Match`, `Idempotency-Key` |
| `cancelResearchRun` | `Idempotency-Key` |

Map `409` to a refresh-required state; refetch the resource and let the user retry. Map `422` to a governance/precondition failure message.

### SSE Subscription

Subscribe to `GET /bff/agora/workshops/{workshop_id}/stream` for `research.run.progress` and `research.run.queued` events. Update the ResearchRunCard in place without a full page reload. The SSE progress event payload includes at minimum: `run_id`, `progress_pct`, `message`, and `phase`. Richer event shape must be sourced from the BFF implementation; do not invent fields.

### No-Order Guardrails

- UI must not render canary, live, order, or capital controls from any research response.
- `no_order_route_proof` is an invariant, not an optional display field.
- `backend.mode=fixture` or `stub` must show a visible marker; these runs cannot satisfy full-validation readiness.
- BacktestResultCard must not link to RuntimeBinding, candidate promotion, or live trading paths.

---

## Suggested Acceptance Checks

| Check | Expected result |
|---|---|
| Schema conformance | Every `ResearchRunProjection` response from `getResearchRun()` validates against `services/control-plane/specs/agora/v4/research_run_projection.schema.json`. |
| Required fields | `run_id`, `plan_id`, `workshop_id`, `strategy_id`, `strategy_spec_registry_id`, `stage_id`, `stage_type`, `execution_status`, `outcome`, `progress`, `backend`, `no_order_route_proof`, `created_at` are all present. |
| Plan gate | `dispatchResearchRun()` call when plan `status ≠ approved` surfaces a `409` to the UI. |
| Progress monotonicity | `progress.percent` does not decrease within a single run attempt. |
| Backend mode label | `backend.mode` is visible in both ResearchRunCard and BacktestResultCard; `fixture`/`stub` modes show a distinct marker. |
| No-order proof | No research card renders order, capital, or live-trading controls. |
| Cancel idempotency | Calling `cancelResearchRun()` on an already-cancelled run returns `202`; UI shows no error. |
| SSE card update | After dispatch, ResearchRunCard updates `execution_status` and `progress.percent` in place when SSE events arrive. |
| Result card gate | BacktestResultCard is not rendered when `execution_status ≠ succeeded`. |
| Metric categories | BacktestResultCard groups metrics into the 7 allowed categories only: `performance`, `risk`, `cost`, `capacity`, `robustness`, `calibration`, `data_quality`. |
| Consultation card blocked | ConsultResultCard is not implemented; no stub or placeholder mocks consultation data. |
| Scope boundary | `research.ts` methods do not call research orchestrator, registry, or any backend service directly. |

---

## Open Design Notes

### Gap A — VersionCompareCard remains blocked

Design gap A (`OPEN_DESIGN_GAPS_ROUND2` §A) is unresolved as of 2026-06-21. `VersionPatchProposal` envelope, patch grammar, `version_compare` semantics, and the readiness gate state machine have not been written. The `version_compare.schema.json` schema exists but has no implementing BFF route. `VersionCompareCard` is out of scope for AG-FE-RS-001 until gap A is resolved. The parent owner must not self-fill this gap; open a blocker if the reviewer requests it.

### Gap E (partial) — Card field specs provided for research cards

Design gap E is partially resolved for the research cards by `design-closure-round2/05_workshop_card_contracts.md` (E7–E9). The `research_plan_proposal`, `research_progress`, and `research_result` card payloads are specified there and may be used as the implementation source. Fields not present in E7–E9 must not be invented; open a blocker if any field is ambiguous.

### ConsultResultCard — Agora BFF gap

The `consult_result` card (E10) requires a consultation projection from the Agora BFF. This projection does not yet exist. The parent owner should explicitly scope `ConsultResultCard` OUT of the initial AG-FE-RS-001 delivery and track it as a follow-up after the Agora BFF consultation route is implemented.

### BacktestResultCard vs research_result

The canonical card type for a completed research run is `research_result` (E9). `BacktestResultCard` is the frontend component name for this card when `stage_type` is a backtest variant. No separate BFF route or schema exists for "backtest result"; the data source is `ResearchRunProjection`. If a distinct backtest result schema is needed, the parent owner must open a blocker before implementing.

---

## Reviewer Handoff

Claude (reviewer) should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope; no canonical docs, schemas, OpenAPI, BFF runtime, research service, or frontend files changed. |
| Canonical truth | AG-BE-RS-001 is `done`, AG-BE-RS-002 is `done`, AG-XR-OPENAPI-004 is `done`, AG-FE-RS-001 is `todo`. |
| BFF surface accuracy | All plan/run routes are implemented; consultation Agora BFF is `501`; no consultation GET route exists. |
| Card binding accuracy | E7/E8/E9 card fields correctly sourced from design-closure-round2/05_workshop_card_contracts.md; E10 (ConsultResultCard) correctly flagged as blocked. |
| Boundary clarity | VersionCompareCard correctly flagged as blocked on design gap A; BacktestResultCard correctly identified as the E9 `research_result` card for backtest stage types. |
| No-order guardrails | Packet consistently rejects RuntimeBinding, capital, broker order, or live trading from any research card. |
| Open design notes accuracy | Gap A (VersionCompare), Gap E partial (card specs), and ConsultResultCard (Agora BFF) are accurately described. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff packet approved: 確認 AG-BE-RS-001/AG-BE-RS-002 已 done、所有 research plan/run routes 已實作、card binding 對齊 E7/E8/E9 card contract、ConsultResultCard 正確標示為 blocked、VersionCompareCard 正確標示為 blocked on gap A、不修改 canonical truth 或 runtime 檔案。" \
  ./scripts/ai-status.sh approve AG-FE-RS-001-SIDECAR-BFF-HANDOFF \
  "Support-only AG-FE-RS-001 BFF/frontend handoff packet approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-RS-001-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

---

## Validation Run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# → task/AG-FE-RS-001-SIDECAR-BFF-HANDOFF

git status --short
# → ?? .orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff.md (untracked task brief only)

AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-RS-001
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-RS-001
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-RS-002
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-XR-OPENAPI-004
python3 -m json.tool services/control-plane/specs/agora/v4/research_run_projection.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/research_plan_execution.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/version_compare.schema.json > /dev/null
grep -n "def.*agora_research\|def.*workshop_research\|501\|Not Implemented" \
  services/control-plane/bff/agora/research/router.py \
  services/control-plane/bff/agora/strategy_workshop/router.py
```
