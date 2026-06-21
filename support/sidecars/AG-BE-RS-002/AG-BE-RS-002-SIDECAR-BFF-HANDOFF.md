# AG-BE-RS-002 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-RS-002-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-RS-002` — Unified run/progress/result projection |
| Parent owner / reviewer | `Codex` / `Claude` |
| Prepared by | `Claude` |
| Reviewer | `Codex` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, research services, registry/governance
implementation, or execute-plans frontend code. It summarizes the BFF query
gaps, operator journey, and frontend handoff boundaries for `AG-BE-RS-002`; the
parent owner decides whether and how to absorb it into the main implementation.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_be_rs_002_sidecar_bff_handoff.md` | Sidecar is support-only: BFF query gap, operator journey, frontend handoff materials; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes must pass task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-BE-RS-002-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`, owner `Claude`, reviewer `Codex`, helper parent `AG-BE-RS-002`, helper kind `bff_handoff_packet`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-RS-002` | Parent is `todo`; owner `Codex`, reviewer `Claude`; depends on `AG-BE-RS-001` and `AG-XR-OPENAPI-004`; scope is unified ResearchRunProjection (progress/metrics/findings/artifact refs/SSE). |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-RS-001` | Status `review_approved`; plan-first facade is approved and awaiting owner finalization. Routes for plan CRUD/approve/cancel/stage routing belong to AG-BE-RS-001. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-XR-OPENAPI-004` | Status `done` (archived); v1.3 OpenAPI bundle and capability manifest are merged. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-RS-001` | Status `todo`; owner `Claude`; depends on `AG-BE-RS-002`; needs BFF client `research.ts` and research plan/run/consult/backtest card components. |
| `support/sidecars/AG-BE-RS-001/AG-BE-RS-001-SIDECAR-BFF-HANDOFF.md` | Prior sidecar established the plan-first facade scope and explicitly deferred run/progress/result projection and artifact/evidence refs to AG-BE-RS-002. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/02_research_facade_run_projection.md` | B3 route catalog, B6 fallback rules, B7 DAG/concurrency, B8 run projection field set, B9 progress semantics, B10 no-order-route rules. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/05_workshop_card_contracts.md` | Frontend card contracts: `research_progress` and `research_result` card types. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` | Gap group B: §7.3 ResearchRunSummary projection contract and SSE progress event shape are still missing from SD; parent owner must treat the v4 schema as the authoritative shape and raise a blocker if any field is ambiguous. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | Defines `ResearchRunProjection` v1: identity/lineage refs, execution_status enum, outcome, progress object, backend (requested/effective/mode/version), metrics array (7 categories), findings, warnings, blocking_reasons, artifact_refs, evidence_refs, lineage_refs, failure, data_cutoff, no_order_route_proof, and timestamps. |
| `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` | Defines plan fields; `run_ids[]` is the list of run IDs attached to a plan — used when building the list route `GET /bff/agora/research-plans/{plan_id}/runs`. |
| `services/control-plane/specs/agora/v4/capability_manifest_v1_3.json` | Marks `agora.research.v1` as `execution_authority: research_only`; lists the v1.3 research path prefixes. |
| `services/control-plane/bff/agora/research/router.py` | Current research router exposes `publish_research_progress()` for SSE; it does not yet implement any run/projection/artifact routes. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Workshop SSE stream is the target for `workshop.research.progress` events via `_ws_publish`. The `POST /bff/agora/workshops/{workshop_id}/research-runs` legacy route is a `501` stub and must stay that way or reference an approved plan if revived. |
| `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` | BFF is the sole frontend aggregation point; research routes must return typed degraded/blocked states rather than synthetic success when downstream adapters are unavailable. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Current BFF State Observed In This Worktree

| Surface | Observed state | Handoff meaning |
|---|---|---|
| `GET /bff/agora/research-plans/{plan_id}/runs` | Not implemented. | AG-BE-RS-002 must add this route to list `ResearchRunProjection` objects for an approved plan. |
| `POST /bff/agora/research-plans/{plan_id}/runs` | Not implemented. | AG-BE-RS-002 must add run dispatch from an approved plan; must not create RuntimeBinding or broker order. |
| `GET /bff/agora/research-runs/{run_id}` | Not implemented. | AG-BE-RS-002 must return a schema-conformant `ResearchRunProjection`. |
| `POST /bff/agora/research-runs/{run_id}/cancel` | Not implemented. | AG-BE-RS-002 must add idempotent cancel that propagates only to queued/running descendants (B7 DAG rule). |
| `GET /bff/agora/research-runs/{run_id}/artifacts` | Not implemented. | AG-BE-RS-002 must return artifact refs linked from the run projection's `artifact_refs[]`. |
| Workshop SSE `workshop.research.progress` | `publish_research_progress()` helper present in `research/router.py`; no caller yet. | AG-BE-RS-002 must call `publish_research_progress()` from run dispatch and progress update handlers to fan events into the workshop stream. |
| `POST /bff/agora/workshops/{workshop_id}/research-runs` | Still `501` (legacy stub in strategy_workshop router). | Must not be revived without an approved plan reference; leave `501` unless parent owner explicitly wires it through a plan. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | Present and valid. | Implementation must validate responses against this schema; do not invent fields or omit required fields. |

## Parent Scope Boundary

`AG-BE-RS-001` owns the plan-first facade:
- Create/list/detail `ResearchPlanExecution` records.
- Approve or cancel a plan (`draft -> approved -> running -> completed/cancelled`).
- Stage routing: LLM-proposed intent → policy-resolved backend.
- No-order-route proof on plan creation.

`AG-BE-RS-002` owns the unified run/progress/result projection:
- Dispatch a run from an `approved` plan only.
- Return typed `ResearchRunProjection` projections (progress, metrics, findings, evidence refs, artifact refs, failures, data cutoff, no-order-route proof).
- Idempotent run cancellation with DAG-aware propagation.
- Artifact/evidence ref listing by run.
- SSE progress events into the workshop stream.
- Research result cards require completed run metrics and artifact links; UI may not render them from any in-progress or failed run.

Neither task may write `RuntimeBinding`, capital binding, broker order, or governance promotion.

## BFF Query Gap Matrix

| Gap | Needed BFF surface | Parent disposition |
|---|---|---|
| Run list by plan is missing | `GET /bff/agora/research-plans/{plan_id}/runs` returning list envelope of `ResearchRunProjection`. | `AG-BE-RS-002` primary. |
| Run dispatch is missing | `POST /bff/agora/research-plans/{plan_id}/runs` from an approved plan; returns queued `ResearchRunProjection`. | `AG-BE-RS-002` primary; plan must be `approved` before dispatch is allowed. |
| Run detail projection is missing | `GET /bff/agora/research-runs/{run_id}` with schema-conformant `ResearchRunProjection` including all metric categories, findings, evidence/artifact refs, failure, data_cutoff, and `no_order_route_proof`. | `AG-BE-RS-002` primary. |
| Run cancellation is missing | `POST /bff/agora/research-runs/{run_id}/cancel`; idempotent; propagates only to currently queued or running DAG descendants. | `AG-BE-RS-002` primary. |
| Artifact/evidence refs by run are missing | `GET /bff/agora/research-runs/{run_id}/artifacts` returning the run's `artifact_refs[]` and `evidence_refs[]`. | `AG-BE-RS-002` primary; depends on real run projection and artifact linkage in research orchestrator. |
| SSE research progress is missing | `workshop.research.progress` events must be published into the workshop SSE stream when a run transitions state or progress percent updates. | `AG-BE-RS-002`; use the existing `publish_research_progress()` helper. |
| Frontend run/result client is missing | `execute-plans/src/lib/bff-v1/agora/research.ts` must expose typed methods for run dispatch, detail, cancellation, and artifact listing. | `AG-FE-RS-001`, after `AG-BE-RS-002` routes land. |
| Research progress card binding is missing | `research_progress` workshop card must bind `ResearchRunProjection.progress`, `execution_status`, `backend.mode`, `warnings`, and `blocking_reasons`. | `AG-FE-RS-001`; gate on `AG-BE-RS-002`. |
| Research result card binding is missing | `research_result` card must bind `ResearchRunProjection.metrics` (all 7 categories), `findings`, `evidence_refs`, `artifact_refs`, `data_cutoff`, and `outcome` — only for `execution_status=succeeded`. | `AG-FE-RS-001`; gate on `AG-BE-RS-002`. |

## Operator Journey

### Journey A: Dispatch A Run From An Approved Plan

1. Operator opens the research plan detail view; plan must show `status=approved`.
2. Operator selects "Dispatch run".
3. Frontend calls `POST /bff/agora/research-plans/{plan_id}/runs` through the BFF
   client only; must not call the research orchestrator directly.
4. BFF verifies plan `status=approved` before creating any run record.
5. BFF creates a queued run, returns a `ResearchRunProjection` with
   `execution_status=queued` and `no_order_route_proof=research_only_not_direct_action`.
6. UI transitions the plan panel to show "Dispatching" status; it must not show
   `running`, `succeeded`, or any result card until the projection confirms it.

### Journey B: Monitor Run Progress

1. After dispatch, frontend polls `GET /bff/agora/research-runs/{run_id}` or
   listens to the workshop SSE stream for `workshop.research.progress` events.
2. BFF publishes a `workshop.research.progress` event through
   `publish_research_progress()` whenever `execution_status` or `progress.percent`
   changes.
3. UI renders the `research_progress` card showing: `execution_status`,
   `progress.percent`, `progress.phase`, `progress.message`,
   `backend.mode`, `warnings`, and `blocking_reasons`.
4. UI must always display `backend.mode`: `real`, `fixture`, or `stub`. A `fixture`
   or `stub` run cannot satisfy full-validation readiness.
5. If a stage is blocked, UI displays the typed blocked state with
   `blocking_reasons`; it must not silently re-route or show a synthetic success.

### Journey C: View Run Results

1. Once `execution_status=succeeded`, the `research_result` card becomes available.
2. Frontend calls `GET /bff/agora/research-runs/{run_id}` to fetch the final
   `ResearchRunProjection`.
3. UI renders the `research_result` card grouping `metrics` by `category` (7 types:
   `performance`, `risk`, `cost`, `capacity`, `robustness`, `calibration`,
   `data_quality`), each with `value`, `unit`, `direction`, `threshold`,
   `gate_result`, and optional `baseline`/`delta`/confidence interval.
4. UI displays `findings` sorted by `severity` (info → watch → warning → high →
   critical) with `summary`, `detail`, and `evidence_refs`.
5. UI shows `data_cutoff` and all `artifact_refs` and `evidence_refs`.
6. Result cards must not suggest candidate promotion, RuntimeBinding, or live trading
   actions. Research completion is research-only.

### Journey D: View And Download Artifacts

1. Operator requests artifact evidence from a completed run.
2. Frontend calls `GET /bff/agora/research-runs/{run_id}/artifacts` through the BFF
   client.
3. BFF returns the run's `artifact_refs[]` and `evidence_refs[]` as returned from the
   research orchestrator or registry.
4. UI renders links without pre-fetching artifact content through Agora BFF; direct
   blob/download links must reference the appropriate storage service.

### Journey E: Cancel A Running Run

1. Operator decides to abort an in-flight run.
2. Frontend calls `POST /bff/agora/research-runs/{run_id}/cancel` through the BFF
   client.
3. BFF sets `execution_status=cancelled` for the target run and propagates cancellation
   only to currently `queued` or `running` DAG descendants.
4. BFF must not cancel already `succeeded`, `failed`, or `timed_out` stage
   descendants; idempotency means a second cancel on an already-cancelled run is
   a no-op, not an error.
5. UI reflects the updated `execution_status=cancelled` on the run and affected stages.

### Journey F: Backend Capability Blocked

1. Operator dispatches a run whose required stage backend is unavailable or not yet
   activated.
2. BFF returns the queued run immediately; the relevant stage transitions to a
   `blocked` state with non-empty `blocking_reasons`.
3. UI displays the blocked stage with reasons and the next activation gate; it must
   not hide the block or substitute a stub run silently.

## Frontend Handoff

| UI / client need | Binding guidance |
|---|---|
| BFF client | Add typed methods to `execute-plans/src/lib/bff-v1/agora/research.ts`; page/components must not call research orchestrator or any backend service directly. |
| Fallback posture | Use live strict behavior. Do not add local fixture fallback, synthetic run data, or direct service fanout. |
| Run list | Bind `listResearchPlanRuns(planId)` to `ResearchRunProjection` list envelope; show `execution_status`, `backend.mode`, and plan ID on each row. |
| Run dispatch | `dispatchResearchPlan(planId, options)` should map to `POST /bff/agora/research-plans/{plan_id}/runs`; show queued state immediately after response. |
| Run detail | `getResearchRun(runId)` → bind `execution_status`, `progress`, `backend`, `metrics`, `findings`, `warnings`, `blocking_reasons`, `evidence_refs`, `artifact_refs`, `data_cutoff`. |
| Run cancel | `cancelResearchRun(runId, options)` with idempotency key; map `200` as no-op success if already cancelled. |
| Artifact list | `listResearchRunArtifacts(runId)` → render `artifact_refs[]` and `evidence_refs[]` without fetching blob content through Agora BFF. |
| Progress card | Bind `research_progress` card only when `execution_status ∈ {queued, dispatching, running}`; show `progress.percent`, `progress.phase`, `progress.message`, `backend.mode`, `warnings`, `blocking_reasons`. |
| Result card | Bind `research_result` card only when `execution_status=succeeded`; show metrics grouped by `category`, findings sorted by `severity`, `data_cutoff`, and `evidence_refs`/`artifact_refs`. Never render result card from in-progress or failed runs. |
| Backend label | Always display `backend.mode`: `real`, `fixture`, or `stub`. `fixture`/`stub` must show a visible marker and cannot satisfy full-validation readiness. |
| No-order guard | UI must not render canary/live/order/capital controls from any research response. `no_order_route_proof` is an invariant, not an optional label. |
| Write actions | Dispatch and cancel must use BFF action endpoints with idempotency keys; map `409` to a refresh-required state, `422` to a governance/precondition failure message. |
| SSE progress | Subscribe to the workshop SSE stream for `workshop.research.progress` events; update the run card in place without a full page reload. |
| Degraded state | `501`: feature not implemented (show coming-soon if gated). `403`: missing scope. `404`: run or plan not found (clear the stale view). `422`: governance or precondition failure. `503`/blocked stage: capability unavailable with `blocking_reasons`. |

Suggested frontend client methods (all to be added to `research.ts`):

```ts
listResearchPlanRuns(planId: string, options?: RequestOptions): Promise<ResearchRunProjectionList>
dispatchResearchRun(planId: string, body: DispatchRunRequest, options?: RequestOptions): Promise<ResearchRunProjection>
getResearchRun(runId: string): Promise<ResearchRunProjection>
cancelResearchRun(runId: string, options?: RequestOptions): Promise<void>
listResearchRunArtifacts(runId: string): Promise<ArtifactRefList>
```

Note: the plan-first methods (`listWorkshopResearchPlans`, `createWorkshopResearchPlan`,
`getResearchPlan`, `approveResearchPlan`, `cancelResearchPlan`) belong to the same
`research.ts` client module but are part of `AG-BE-RS-001`'s scope, not this task.
The complete client will combine both sets of methods.

## Suggested Backend Acceptance Checks

| Check | Expected result |
|---|---|
| Schema conformance | Every `ResearchRunProjection` response validates against `services/control-plane/specs/agora/v4/research_run_projection.schema.json`. |
| Required fields | `run_id`, `plan_id`, `workshop_id`, `strategy_id`, `strategy_spec_registry_id`, `stage_id`, `stage_type`, `execution_status`, `outcome`, `progress`, `backend`, `no_order_route_proof`, `created_at` are all present. |
| Plan gate | `POST /bff/agora/research-plans/{plan_id}/runs` returns `422` or similar when plan `status ≠ approved`. |
| Progress monotonicity | `progress.percent` must not decrease within a single run attempt; a new attempt must have a new attempt ID. |
| Fallback rule | `backend_mode=fixture\|stub` is only accepted when explicitly requested; missing real backend returns a typed blocked stage, not a synthetic success. |
| No-order proof | Every run response includes `no_order_route_proof=research_only_not_direct_action`. |
| Runtime boundary | No code path writes `RuntimeBinding`, capital binding, broker order, or governance promotion from any research run route. |
| Cancel idempotency | `POST /bff/agora/research-runs/{run_id}/cancel` is a no-op (not an error) when run is already `cancelled`, `succeeded`, `failed`, or `timed_out`. |
| DAG propagation | Cancel propagates only to `queued` or `running` stage descendants; terminal descendants are unaffected. |
| SSE publication | When run state transitions or `progress.percent` changes, a `workshop.research.progress` event is emitted via `publish_research_progress()`. |
| Metric categories | Metrics present in the result projection use only the 7 allowed categories: `performance`, `risk`, `cost`, `capacity`, `robustness`, `calibration`, `data_quality`. |
| Frontend readiness | `AG-FE-RS-001` can consume `ResearchRunProjection` fields from `research.ts` without direct fetch or local fallback. |

## Open Design Note

The `OPEN_DESIGN_GAPS_ROUND2` document (gap group B) notes that §7.3
`ResearchRunSummary` projection contract and the SSE `progress` event shape were
not yet in the SD as of 2026-06-21. The v4 `research_run_projection.schema.json`
and `design-closure-round2/02_research_facade_run_projection.md` (B8–B10) are
treated as the authoritative shape for this implementation wave. If the parent
owner (`Codex`) encounters any field ambiguity, missing detail, or contradiction
between the v4 schema and the design docs, they must open a blocker immediately
rather than self-filling the gap.

The SSE progress event shape (event type `workshop.research.progress`, payload
fields beyond `run_id`, `progress_pct`, and `message`) is not fully specified in
the canonical docs as of this date. The existing `publish_research_progress()`
helper uses those three fields. Parent owner should use that shape and raise a
blocker if a richer event envelope is required.

## Reviewer Handoff

Codex review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, research service, registry/governance, or frontend files changed by this sidecar. |
| Factual alignment | AG-BE-RS-001 is `review_approved`; AG-XR-OPENAPI-004 is `done`; AG-BE-RS-002 is still `todo`; AG-FE-RS-001 is still `todo`. |
| Current-state accuracy | Run/projection/artifact routes are all absent in the current worktree; only `publish_research_progress()` helper exists for SSE. |
| Boundary clarity | Packet does not claim plan-first facade routes (those belong to AG-BE-RS-001); it focuses on run/projection/result/artifact/SSE surfaces. |
| Open design note accuracy | Gap B in OPEN_DESIGN_GAPS_ROUND2 is accurately described; v4 schema and design-closure-round2 doc are the current authoritative shape. |

Recommended reviewer approval command:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/AG-BE-RS-002/AG-BE-RS-002-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff packet approved: it records the ResearchRunProjection run/progress/result/artifact/SSE gap surfaces, operator journeys, frontend client/card boundaries, no-order-route guardrails, and AG-BE-RS-002 versus AG-BE-RS-001 ownership boundary without modifying canonical truth or runtime files." \
  ./scripts/ai-status.sh approve AG-BE-RS-002-SIDECAR-BFF-HANDOFF \
  "Support-only AG-BE-RS-002 BFF/frontend handoff packet approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Codex ./scripts/ai-status.sh reopen AG-BE-RS-002-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

## Validation Run

Commands run from this sidecar worktree:

```bash
git branch --show-current
git status --short
AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-RS-002-SIDECAR-BFF-HANDOFF
AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-RS-002
AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-RS-001
AI_NAME=Claude python3 scripts/ai_status.py show AG-XR-OPENAPI-004
AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-RS-001
python3 -m json.tool services/control-plane/specs/agora/v4/research_run_projection.schema.json >/tmp/ag-be-rs-002-run-schema.json
python3 -m json.tool services/control-plane/specs/agora/v4/research_plan_execution.schema.json >/tmp/ag-be-rs-002-plan-schema.json
```
