# AG-BE-RS-002 BFF and Frontend Handoff Packet (Followup-2)

| Field | Value |
|---|---|
| Task ID | `AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-RS-002` — Unified run/progress/result projection |
| Parent owner / reviewer | `Codex` / `Claude` |
| Prepared by | `Claude` |
| Reviewer | `Codex` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |
| Predecessor sidecar | `AG-BE-RS-002-SIDECAR-BFF-HANDOFF` (done 2026-06-21) |

This packet is a support artifact only. It supersedes certain findings of
`AG-BE-RS-002-SIDECAR-BFF-HANDOFF` with the ground-truth state observed in the
`review_approved` implementation, and provides concrete frontend guidance for
`AG-FE-RS-001`. No canonical docs, schemas, OpenAPI, BFF runtime, research
services, registry/governance, or frontend files are modified.

## Status Update (Changes From Predecessor Sidecar)

| Item | Predecessor state | Current state |
|---|---|---|
| `AG-BE-RS-002` | `todo` | `review_approved` — all routes implemented, 173 pytest passed |
| `AG-BE-RS-001` | `review_approved` | `done` (archived 2026-06-21) |
| `AG-XR-OPENAPI-004` | `done` | `done` (unchanged) |
| `AG-FE-RS-001` | `todo` | `todo` — unblocked once AG-BE-RS-002 reaches `done` |
| BFF run routes | Not implemented | Implemented and review-approved |
| SSE progress event | `publish_research_progress()` helper present; no caller | All dispatch/cancel paths call `publish_research_progress()` |

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-RS-002` | Status `review_approved`; owner `Codex`; reviewer `Claude`; 173 pytest; all run routes approved. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-RS-001` | Status `todo`; owner `Claude`; reviewer `Codex`; depends on `AG-BE-RS-002`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-RS-001` | Status `done` (archived); plan-first facade is complete. |
| `services/control-plane/bff/agora/research/router.py` | Full implementation with all plan and run routes, SSE publish calls, ETag locking, and idempotency enforcement. |
| `services/control-plane/bff/agora/research/store.py` | In-memory store (MemoryResearchPlanStore); Postgres backend deferred. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | 24 fields confirmed. |
| `execute-plans/src/lib/bff-v1/agora/` | Only `contract-snapshot.json`, `dashboard.ts`, `types.ts` present; `research.ts` is absent and must be created by AG-FE-RS-001. |
| `support/sidecars/AG-BE-RS-002/AG-BE-RS-002-SIDECAR-BFF-HANDOFF.md` | Predecessor sidecar for comparison. |

## Corrections To Predecessor Sidecar

These are factual differences between what the original sidecar assumed and what
was actually implemented. AG-FE-RS-001 must use the corrected shapes below.

### 1. SSE Event Type: `research.run.progress` (not `workshop.research.progress`)

The `publish_research_progress()` helper emits event type **`research.run.progress`**,
aligned with the v1.3 `research.*` naming convention. The predecessor sidecar
called this event `workshop.research.progress` — that name is incorrect.

Event payload:

```json
{
  "run_id": "<string>",
  "phase": "<string>",
  "percent": 0.0,
  "message": "<string>"
}
```

Full SSE event catalog from the implementation:

| Event type | Trigger |
|---|---|
| `research.plan.created` | `POST /bff/agora/workshops/{id}/research-plans` success |
| `research.plan.approved` | `POST /bff/agora/research-plans/{id}/approve` success |
| `research.plan.cancelled` | `POST /bff/agora/research-plans/{id}/cancel` success |
| `research.run.queued` | `POST /bff/agora/research-plans/{id}/runs` (dispatch) success |
| `research.run.progress` | Cancel path (via `publish_research_progress()`); any implementation path that advances run state |
| `workshop.openclaw.degraded` | `publish_openclaw_degraded()` when OpenClaw is unreachable |

### 2. Dispatch Response Is Not a Full `ResearchRunProjection`

`POST /bff/agora/research-plans/{plan_id}/runs` returns **HTTP 202** with a
queued-confirmation envelope, **not** a full `ResearchRunProjection`.

Actual response shape:

```json
{
  "status": "queued",
  "data": {
    "run_id": "<uuid>",
    "plan_id": "<uuid>",
    "stage_id": "<uuid>",
    "stage_type": "<string>"
  },
  "meta": {
    "snapshot_at": "<ISO-8601>",
    "capability": "agora.research.v1",
    "audience": "tenant:<id>:user:<id>"
  }
}
```

Frontend must call `GET /bff/agora/research-runs/{run_id}` to obtain the full
`ResearchRunProjection` after dispatch. The predecessor sidecar listed the
dispatch return as a `ResearchRunProjection` — this is incorrect.

### 3. Run Cancel Returns 409 for Terminal Statuses

`POST /bff/agora/research-runs/{run_id}/cancel` returns **HTTP 202** on success,
but **HTTP 409** (not a no-op) when the run is already in a terminal status
(`succeeded`, `failed`, `timed_out`, or `cancelled`). The predecessor sidecar
described this as idempotent — that description does not match the implementation.

Cancellable statuses: `queued`, `dispatching`, `running`.

Actual cancel response shape (HTTP 202):

```json
{
  "status": "accepted",
  "data": {
    "run_id": "<uuid>",
    "execution_status": "cancelled"
  },
  "meta": {
    "snapshot_at": "<ISO-8601>",
    "capability": "agora.research.v1",
    "audience": "tenant:<id>:user:<id>"
  }
}
```

### 4. Dispatch Requires `If-Match` and `Idempotency-Key` Headers

The dispatch endpoint (`POST /bff/agora/research-plans/{plan_id}/runs`) requires:
- `Idempotency-Key: <uuid>` — returns 400 if missing
- `If-Match: <plan-ETag>` — returns 428 if missing; returns 412 on version mismatch

The plan ETag is returned in `meta.etag` from `GET /bff/agora/research-plans/{plan_id}`.
Format: `W/"research-plan:<plan_id>:v<lock_version>"`.

The plan approve and cancel endpoints also require both headers.

### 5. Plan List Returns Plans Directly (Not Run Projections)

`GET /bff/agora/research-plans/{plan_id}/runs` returns `ResearchRunProjection`
objects in `items[]`. Each item is the full run projection with optional arrays
defaulted: `metrics`, `findings`, `warnings`, `blocking_reasons`, `artifact_refs`,
`evidence_refs`, `lineage_refs` all default to `[]` when absent.

### 6. Artifact List Response Shape

`GET /bff/agora/research-runs/{run_id}/artifacts` returns:

```json
{
  "items": [
    { "ref_type": "experiment_artifact", "ref_id": "<artifact_id>" },
    ...
  ],
  "page_info": {
    "next_page_token": null,
    "page_size": <int>,
    "has_more": false,
    "total": <int>
  },
  "meta": {
    "snapshot_at": "<ISO-8601>",
    "capability": "agora.research.v1",
    "audience": "tenant:<id>:user:<id>"
  }
}
```

`evidence_refs` from the run projection are appended directly as-is after the
`artifact_refs` items. Frontend should not assume a uniform shape for
`evidence_refs` entries — they are stored verbatim from the run record.

## Actual BFF Surface (As-Implemented)

All routes confirmed in `services/control-plane/bff/agora/research/router.py`.

### Plan Routes (AG-BE-RS-001, already `done`)

| Method | Path | Status | Auth | Required headers | Response |
|---|---|---|---|---|---|
| `GET` | `/bff/agora/workshops/{workshop_id}/research-plans` | 200 | Bearer + role | — | List envelope with `items[]`, `page_info`, `meta` |
| `POST` | `/bff/agora/workshops/{workshop_id}/research-plans` | 201 | Bearer + role | `Idempotency-Key` | Plan detail envelope |
| `GET` | `/bff/agora/research-plans/{plan_id}` | 200 | Bearer + role | — | Plan detail envelope |
| `POST` | `/bff/agora/research-plans/{plan_id}/approve` | 200 | Bearer + role | `Idempotency-Key`, `If-Match` | Action envelope |
| `POST` | `/bff/agora/research-plans/{plan_id}/cancel` | 200 | Bearer + role | `Idempotency-Key`, `If-Match` | Action envelope |

### Run Routes (AG-BE-RS-002, `review_approved`)

| Method | Path | Status | Auth | Required headers | Response |
|---|---|---|---|---|---|
| `GET` | `/bff/agora/research-plans/{plan_id}/runs` | 200 | Bearer + role | — | List envelope with `items[]` of `ResearchRunProjection` |
| `POST` | `/bff/agora/research-plans/{plan_id}/runs` | 202 | Bearer + role | `Idempotency-Key`, `If-Match` | Queued-confirmation envelope (not a `ResearchRunProjection`) |
| `GET` | `/bff/agora/research-runs/{run_id}` | 200 | Bearer + role | — | `ResearchRunProjection` directly (no envelope) |
| `POST` | `/bff/agora/research-runs/{run_id}/cancel` | 202 | Bearer + role | `Idempotency-Key` | Accepted-confirmation envelope |
| `GET` | `/bff/agora/research-runs/{run_id}/artifacts` | 200 | Bearer + role | — | List envelope with `items[]` |

### Plan Detail Envelope Shape

```json
{
  "object_ref": { "type": "research_plan", "id": "<plan_id>" },
  "status": "<plan_status>",
  "lifecycle_state": "<plan_status>",
  "allowedActions": {
    "approve": true,
    "cancel": true,
    "dispatch": false
  },
  "data": { /* ResearchPlanExecution fields */ },
  "meta": {
    "snapshot_at": "<ISO-8601>",
    "capability": "agora.research.v1",
    "audience": "tenant:<id>:user:<id>",
    "etag": "W/\"research-plan:<id>:v<n>\""
  },
  "links": {
    "self": "/bff/agora/research-plans/<id>",
    "runs": "/bff/agora/research-plans/<id>/runs"
  }
}
```

`allowedActions` controls which action buttons to render:

| Status | `approve` | `cancel` | `dispatch` |
|---|---|---|---|
| `draft` | true | true | false |
| `approved` | false | true | true |
| `running` | false | true | false |
| `completed` | false | false | false |
| `cancelled` | false | false | false |

### `ResearchRunProjection` Field Summary (24 fields)

| Field | Type | Notes |
|---|---|---|
| `spec_version` | string | Always `"1.0"` |
| `run_id` | string (uuid) | — |
| `plan_id` | string (uuid) | — |
| `workshop_id` | string | — |
| `strategy_id` | string | — |
| `strategy_spec_registry_id` | string | — |
| `stage_id` | string (uuid) | — |
| `stage_type` | string | One of 12 canonical stage types |
| `execution_status` | enum | `queued` / `dispatching` / `running` / `succeeded` / `failed` / `cancelled` / `timed_out` |
| `outcome` | enum | `pending` / `succeeded` / `failed` / `cancelled` |
| `progress` | object | `{phase, percent, message, updated_at}` |
| `backend` | object | `{requested, effective, mode}` — mode: `real` / `fixture` / `stub` |
| `metrics` | array | Default `[]`; 7 categories when present |
| `findings` | array | Default `[]` |
| `warnings` | array | Default `[]` |
| `blocking_reasons` | array | Default `[]` |
| `artifact_refs` | array | Default `[]` |
| `evidence_refs` | array | Default `[]` |
| `lineage_refs` | array | Default `[]` |
| `failure` | object | Optional; present when `execution_status` in `{failed, timed_out}` |
| `data_cutoff` | string | Optional ISO-8601 date |
| `no_order_route_proof` | string | Always `"research_only_not_direct_action"` |
| `created_at` | string | ISO-8601 |
| `updated_at` | string | ISO-8601 |
| `started_at` | string | Optional ISO-8601 |
| `completed_at` | string | Optional ISO-8601 |

### Error Response Shapes

| Code | Trigger | Frontend action |
|---|---|---|
| `400` | Missing `Idempotency-Key` header | Add UUID v4 in `Idempotency-Key` header |
| `404` | Plan or run not found | Clear stale view; do not retry |
| `409` | Idempotency conflict (duplicate key) | Refresh and re-read the resource |
| `409` | Plan/run state transition not allowed | Refresh to read current state; display state-machine error |
| `412` | ETag mismatch (`If-Match` check failed) | Re-GET plan to get fresh ETag, then retry |
| `422` | Validation failure (bad stage type, forbidden environment, bad spec_version) | Display governance/precondition failure message |
| `428` | Missing `If-Match` header | Add ETag from plan detail `meta.etag` |

## Store Backend Note

`MemoryResearchPlanStore` is the only backend wired up in this implementation
(`AGORA_RESEARCH_PLAN_STORE_BACKEND=off` default). State is **not persisted across
restarts**. A Postgres backend is explicitly deferred per the store module's doc
comment. AG-FE-RS-001 should be aware that the dev BFF resets state on restart;
this is expected behavior and not a bug.

## Updated Frontend Handoff (AG-FE-RS-001)

### What Must Be Created

`execute-plans/src/lib/bff-v1/agora/research.ts` does not exist and must be
created by AG-FE-RS-001. The `agora/` client dir currently has only
`contract-snapshot.json`, `dashboard.ts`, `types.ts`.

### Suggested Client Methods

All methods belong in `research.ts`. Methods must use `fetch` with `credentials: "include"` and live-strict behavior — no fixture fallback, no direct service calls.

```ts
// ──────────────────────────────────────────────────
// Plan methods (from AG-BE-RS-001, already live)
// ──────────────────────────────────────────────────

/** List research plans for a workshop. */
listWorkshopResearchPlans(
  workshopId: string,
  opts?: { cursor?: string; limit?: number }
): Promise<ResearchPlanListEnvelope>

/** Create a new research plan (draft). Requires Idempotency-Key. */
createWorkshopResearchPlan(
  workshopId: string,
  body: ResearchPlanCreateRequest,
  idempotencyKey: string
): Promise<ResearchPlanDetailEnvelope>

/** Get a research plan detail including allowedActions and ETag. */
getResearchPlan(planId: string): Promise<ResearchPlanDetailEnvelope>

/** Approve a draft plan. Requires Idempotency-Key + If-Match (plan ETag). */
approveResearchPlan(
  planId: string,
  idempotencyKey: string,
  ifMatch: string
): Promise<ActionEnvelope>

/** Cancel a plan. Requires Idempotency-Key + If-Match (plan ETag). */
cancelResearchPlan(
  planId: string,
  idempotencyKey: string,
  ifMatch: string
): Promise<ActionEnvelope>

// ──────────────────────────────────────────────────
// Run methods (from AG-BE-RS-002, review_approved)
// ──────────────────────────────────────────────────

/** List runs for a plan. Returns list envelope with ResearchRunProjection items. */
listResearchPlanRuns(
  planId: string
): Promise<ResearchRunListEnvelope>

/**
 * Dispatch an approved plan. Returns a queued-confirmation envelope (NOT a
 * ResearchRunProjection). Call getResearchRun(run_id) for the full projection.
 * Requires Idempotency-Key + If-Match (plan ETag).
 */
dispatchResearchPlan(
  planId: string,
  idempotencyKey: string,
  ifMatch: string
): Promise<DispatchConfirmationEnvelope>

/**
 * Get full ResearchRunProjection. Returns the projection directly (no envelope
 * wrapper). Use after dispatch and for polling progress.
 */
getResearchRun(runId: string): Promise<ResearchRunProjection>

/**
 * Cancel a queued/dispatching/running run. Returns 202 accepted-confirmation
 * envelope on success. Returns 409 when run is already in a terminal status —
 * this is NOT a no-op; catch the 409 and refresh the run state.
 * Requires Idempotency-Key.
 */
cancelResearchRun(
  runId: string,
  idempotencyKey: string
): Promise<CancelConfirmationEnvelope>

/** List artifact and evidence refs for a run. */
listResearchRunArtifacts(
  runId: string
): Promise<ArtifactListEnvelope>
```

### TypeScript Response Type Sketches

```ts
interface ResearchRunProjection {
  spec_version: string;
  run_id: string;
  plan_id: string;
  workshop_id: string;
  strategy_id: string;
  strategy_spec_registry_id: string;
  stage_id: string;
  stage_type: string;
  execution_status:
    | "queued" | "dispatching" | "running"
    | "succeeded" | "failed" | "cancelled" | "timed_out";
  outcome: "pending" | "succeeded" | "failed" | "cancelled";
  progress: {
    phase: string;
    percent: number;
    message: string;
    updated_at: string;
  };
  backend: {
    requested: string;
    effective: string;
    mode: "real" | "fixture" | "stub";
  };
  metrics: ResearchMetric[];
  findings: ResearchFinding[];
  warnings: string[];
  blocking_reasons: string[];
  artifact_refs: string[];
  evidence_refs: unknown[];
  lineage_refs: unknown[];
  failure?: unknown;
  data_cutoff?: string;
  no_order_route_proof: "research_only_not_direct_action";
  created_at: string;
  updated_at: string;
  started_at?: string;
  completed_at?: string;
}

interface ResearchMetric {
  category:
    | "performance" | "risk" | "cost" | "capacity"
    | "robustness" | "calibration" | "data_quality";
  name: string;
  value: unknown;
  unit?: string;
  direction?: "higher_better" | "lower_better" | "target";
  threshold?: unknown;
  gate_result?: "pass" | "fail" | "warn" | "skipped";
  baseline?: unknown;
  delta?: unknown;
}

interface ResearchFinding {
  severity: "info" | "watch" | "warning" | "high" | "critical";
  summary: string;
  detail?: string;
  evidence_refs?: string[];
}

interface DispatchConfirmationEnvelope {
  status: "queued";
  data: {
    run_id: string;
    plan_id: string;
    stage_id: string;
    stage_type: string;
  };
  meta: BffMeta;
}

interface CancelConfirmationEnvelope {
  status: "accepted";
  data: {
    run_id: string;
    execution_status: "cancelled";
  };
  meta: BffMeta;
}

interface BffMeta {
  snapshot_at: string;
  capability: "agora.research.v1";
  audience: string;
}
```

### SSE Subscription Guidance

Subscribe to the workshop SSE stream for `research.*` events. The correct event
type for run progress is **`research.run.progress`** (not
`workshop.research.progress`).

```ts
// In the workshop SSE event handler:
switch (event.type) {
  case "research.run.progress":
    // payload: { run_id, phase, percent, message }
    updateRunCard(event.data.run_id, event.data);
    break;
  case "research.run.queued":
    // payload: { run_id, plan_id, stage_id, stage_type, percent }
    showQueuedState(event.data.run_id);
    break;
  case "research.plan.approved":
    // payload: { plan_id, status: "approved" }
    refreshPlanState(event.data.plan_id);
    break;
  case "workshop.openclaw.degraded":
    // payload: { error_code: "OPENCLAW_UPSTREAM_DEGRADED", reason }
    showDegradedBanner(event.data.reason);
    break;
}
```

### Card Binding Rules (Unchanged From Predecessor Sidecar)

**`research_progress` card** — render when `execution_status ∈ {queued, dispatching, running}`:
- `progress.percent`, `progress.phase`, `progress.message`
- `backend.mode` (always display; `fixture`/`stub` must show a visible marker)
- `warnings`, `blocking_reasons`
- Do not render a result card from in-progress data.

**`research_result` card** — render only when `execution_status = succeeded`:
- `metrics` grouped by `category` (7 types: `performance`, `risk`, `cost`,
  `capacity`, `robustness`, `calibration`, `data_quality`)
- Each metric shows `value`, `unit`, `direction`, `threshold`, `gate_result`,
  and optional `baseline`/`delta`
- `findings` sorted by `severity` (info → watch → warning → high → critical)
- `data_cutoff`, `evidence_refs`, `artifact_refs`
- No candidate promotion, RuntimeBinding, or live trading controls.

**No-order guardrail**: `no_order_route_proof` is always
`"research_only_not_direct_action"`. No UI element may render order, capital, or
canary controls from any research response.

## Open Items For Parent Owner

1. **AG-BE-RS-002 closeout**: Task is `review_approved`; Codex (owner) must run
   closeout (`worker_commit.py` + `task_finalize.sh` + PR merge + `done` command)
   before AG-FE-RS-001 can formally unblock.

2. **Store persistence**: `MemoryResearchPlanStore` resets on restart. If the
   integration environment requires persistence across restarts, a Postgres backend
   should be added as a follow-up task (not part of AG-BE-RS-002 scope).

3. **`research.run.progress` event richness**: The current `publish_research_progress()`
   payload covers `run_id`, `phase`, `percent`, `message`. If the frontend needs
   richer fields (e.g. `blocking_reasons` or `backend.mode` in SSE events), the
   helper must be extended — raise a blocker before extending, as the event shape
   is part of the v1.3 capability surface.

4. **`evidence_refs` type**: The artifact list endpoint appends `evidence_refs`
   verbatim from the run record. The exact schema of `evidence_refs` items depends
   on what downstream services (research orchestrator, registry) store. If
   AG-FE-RS-001 needs typed access, Codex should clarify the shape before
   implementation.

## Reviewer Handoff

Codex review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status metadata changed. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, research service, registry/governance, or frontend files changed. |
| Factual alignment (status) | AG-BE-RS-002 is `review_approved`; AG-BE-RS-001 is `done`; AG-XR-OPENAPI-004 is `done`; AG-FE-RS-001 is `todo`. |
| Corrections are accurate | SSE event type `research.run.progress`; dispatch returns queued-confirmation not a full projection; cancel returns 409 for terminal statuses; dispatch requires `If-Match`. |
| Implementation-aligned | Route table, response shapes, allowed-actions matrix, and field list match `services/control-plane/bff/agora/research/router.py`. |
| Open items accurate | Store is in-memory only; AG-BE-RS-002 closeout is the immediate gate for AG-FE-RS-001. |

Recommended reviewer approval command:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/AG-BE-RS-002/AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
  REVIEW_NOTES_ZH="Followup-2 handoff packet approved: updated status (AG-BE-RS-002 review_approved), corrected SSE event name to research.run.progress, corrected dispatch response shape and cancel 409 behavior, documented ETag/Idempotency-Key header requirements, and provided concrete AG-FE-RS-001 client guidance — no canonical truth modified." \
  ./scripts/ai-status.sh approve AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Followup-2 handoff packet approved; parent owner may absorb updated BFF client guidance into AG-FE-RS-001."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Codex ./scripts/ai-status.sh reopen AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Describe the factual error, missing correction, or additional guidance required before approval."
```

## Validation Run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2

git status --short
# ?? .orchestrator/task-briefs/ag_be_rs_002_sidecar_bff_handoff_followup_2.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-RS-002
# status: review_approved; owner: Codex; 173 pytest

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-RS-001
# source: archive; terminal_status: done

AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-RS-001
# status: todo; depends_on: AG-FE-SW-002, AG-BE-RS-002, AG-XR-OPENAPI-004

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
# status: in_progress; owner: Claude; helper_kind: bff_handoff_packet

python3 -m json.tool services/control-plane/specs/agora/v4/research_run_projection.schema.json \
  | python3 -c "import sys, json; d=json.load(sys.stdin); print(list(d['properties'].keys()))"
# 24 fields confirmed

ls execute-plans/src/lib/bff-v1/agora/
# contract-snapshot.json  dashboard.ts  types.ts  (research.ts absent)
```
