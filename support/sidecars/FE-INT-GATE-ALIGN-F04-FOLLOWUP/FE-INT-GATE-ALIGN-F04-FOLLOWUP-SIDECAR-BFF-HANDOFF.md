# BFF Handoff Packet

**Task ID:** FE-INT-GATE-ALIGN-F04-FOLLOWUP-SIDECAR-BFF-HANDOFF
**Parent Task:** FE-INT-GATE-ALIGN-F04-FOLLOWUP
**Helper Kind:** bff_handoff_packet
**Owner:** Claude
**Reviewer:** Gemini2
**Date:** 2026-05-14

---

## 1. Parent Task Status (Verified)

**FE-INT-GATE-ALIGN-F04-FOLLOWUP is DONE.**

- FE source commit: `8c7606cf6904e63eb265427cef25f8d226e10cbf`
- Repo: `/home/lupin/code/execute-plans`, branch `bff-luv-fe-006-dev-deploy`
- Hosted Lovable/dev BFF F04 Playwright run: **3/3 passed**
- Closeout recorded in: `support/sidecars/FE-INT-GATE-ALIGN-F04-FOLLOWUP/FE-INT-GATE-ALIGN-F04-FOLLOWUP-CLOSEOUT.md`

---

## 2. What Changed in Commit 8c7606cf

The commit restores row-scoped optimization approval control via three coordinated changes:

1. **Preserve `nextAction.href` from live BFF** — `adaptBffLoopRun()` in `src/lib/bff/v5.ts` now resolves `nextAction.href` through a waterfall (see §4) rather than dropping it.
2. **Derive approval evidence from both top-level and nested BFF fields** — `adaptLoopEvidence()` consolidates `item.evidence`, `item.evidence_refs`, `item.approval.approval_id`, and approval-typed stage `entity_id` into a single deduplicated `evidence[]` array.
3. **Render row-level approval link** — `OptimizationLoop.tsx` constructs `nextActionHref` from `nextAction.href`, falling back to `approvalHref(approvalEv.id)` only when `nextAction.href` is absent. The F04 spec now fails if the approval/HIQ control appears outside the rebalance row.

---

## 3. BFF Query Gap: Live `/bff/v5/loop-runs` Data Requirements

The frontend calls:
- **List**: `GET /bff/v5/loop-runs` (optionally `?kind=optimization` for the Optimization Loop page)
- **Single**: `GET /bff/v5/loop-runs/{id}`

Response envelope format (strict — frontend uses `strictItemsFrom` / `strictDataFrom`):

```json
{
  "items": [ /* LoopRun objects */ ],
  "meta": { "snapshot_at": "ISO8601" },
  "pageSize": 1,
  "totalCount": 1
}
```

For a single item, wrap in `{ "data": { ... }, "meta": { ... } }`.

### Required fields per LoopRun item

| Field | Accepted aliases | Purpose |
|---|---|---|
| `loop_run_id` | `loopRunId`, `id` | Canonical run identifier |
| `loop_family` | `loopKind`, `loop_kind`, `kind`, `title` | Controls `loopKind` classification (`"optimization"` triggers the optimization bucket) |
| `status` | `runStatus`, `run_status` | `running` / `blocked` / `succeeded` / `failed` / `cancelled` |
| `started_at` | `startedAt`, `activePeriod.start`, `created_at` | Timeline anchor |
| `stages` | `timeline` | Array of stage objects (see below) |
| `next_action` | `nextAction` | Object with `kind`, `label`, `href` |
| `approval` | — | Nested approval object |
| `evidence` | `evidence_refs` | Array of `{ kind, id }` refs |
| `subject_id` | `subjectId`, `binding_id` | Links row to rebalance page |
| `subject_name` | `subjectName`, `title`, `name` | Display name in row |

### Required fields per stage item

| Field | Accepted aliases | Purpose |
|---|---|---|
| `id` | `stage_id`, `stageId` | Stage identifier |
| `name` | `kind`, `stage`, `stage_name`, `stageName`, `label` | Stage display label |
| `status` | — | `running` / `blocked` / `succeeded` / `failed` / `skipped` / `pending` |
| `entity_type` | `entityType` | Used to detect approval stage (`"approval"` triggers approval-stage logic) |
| `entity_id` | `entityId`, `approval_id`, `approvalId` | Approval ID extracted from the stage |
| `action_href` | `actionHref` | Deep link surfaced via waterfall (see §4) |

---

## 4. Operator Row Journey for Approval/HIQ Control

The operator uses `/management/loops/optimization` to monitor optimization loop runs and navigate to pending approvals.

### Journey steps

1. Page loads → frontend calls `GET /bff/v5/loop-runs?kind=optimization`
2. Each row renders: **Subject** → **Status badge** → **Stage pipeline** → **Next** → **Evidence** → **Updated**
3. For a `blocked` run awaiting approval, the **Next** column renders a clickable link using `nextActionHref`
4. The **Evidence** column renders `approvalEv.id` as a direct link to `/management/approvals?approval={id}`

### `nextAction.href` waterfall (in priority order)

The adapter tries each in sequence, stopping at the first `/management/...` path found:

1. `item.nextAction.href` / `item.next_action.href`
2. `item.action_href` / `item.actionHref`
3. `item.links.approval` / `item.links.approvals`
4. `item.approval.links.approval` / `item.approval.links.approvals`
5. `approvalStage.action_href` / `approvalStage.actionHref`
6. Constructed: `/management/approvals?approval={encodeURIComponent(approvalId)}`

**Note:** Non-`/management/` hrefs are silently dropped by `asManagementHref()`. The BFF must emit paths starting with `/management/` for the link to render.

### Approval evidence extraction waterfall

The adapter aggregates a deduplicated `evidence[]` array from:

1. `item.evidence[]` (each `{ kind, id }`)
2. `item.evidence_refs[]` (each `{ kind, id }`)
3. `item.approval.approval_id` / `item.approval.approvalId` / `item.approval.id` → pushed as `{ kind: "approval", id }`
4. Approval-typed stage (`entity_type === "approval"`) `entity_id` / `approval_id` → pushed as `{ kind: "approval", id }`
5. `item.derived_from_incident_id` / `item.incident_id` → pushed as `{ kind: "incident", id }`

The first `{ kind: "approval" }` entry wins as `approvalId` for the fallback href.

### F04 spec acceptance criteria (row-scoped)

The F04 spec (`e2e/04b-optimization-loop.spec.ts`) asserts:

- The rebalance row (`tbody tr` containing `REBALANCE_ID`) must expose a clickable link/button matching `approval-c01-rebalance` or `hiq-c01-rebalance` or status patterns
- Clicking that control must navigate to `/management/approvals` or `/management/interventions`
- The approval/HIQ control must be **inside the rebalance row** — generic shell navigation from outside the row fails the test

---

## 5. Frontend Adapter Handoff Fields Summary

These are the BFF fields the frontend adapter actively uses for the approval/HIQ control surface:

| UI Surface | Adapter Field | BFF Source Field(s) |
|---|---|---|
| Next column link href | `nextAction.href` | `nextAction.href`, `next_action.href`, `links.approval`, approval stage `action_href`, or constructed from `approvalId` |
| Next column link label | `nextAction.label` | `nextAction.label`, `next_action.label` |
| Next column kind | `nextAction.kind` | `nextAction.kind` — must be `"awaiting_approval"` to trigger approval logic |
| Evidence column link | `evidence[kind=approval].id` | `evidence[]`, `evidence_refs[]`, `approval.approval_id`, approval stage `entity_id` |
| HIQ link (via href) | `nextAction.href` or evidence | `links.hiq`, `links.intervention`, approval stage `hiq_href`, or intervention query param |
| Stage pipeline dots | `stages[].status` | `stages[].status` / `timeline[].status` |
| Stage names | `stages[].name` | `stages[].name`, `kind`, `stage`, `label` — canonical fields only (mock-only fields like `displayLabel` are prohibited by the spec) |

---

## 6. Verified BFF Contract (from F04 Spec Fixture)

The F04 spec uses a Playwright route mock that defines the authoritative contract shape. Key verified fields:

**Loop run top-level object:**
```json
{
  "loop_run_id": "loop-c01-optimization",
  "loop_family": "optimization",
  "status": "blocked",
  "next_action": { "kind": "awaiting_approval", "label": "Review approval", "href": "/management/approvals?approval=approval-c01-rebalance" },
  "nextAction":  { "kind": "awaiting_approval", "label": "Review approval", "href": "/management/approvals?approval=approval-c01-rebalance" },
  "stages": [ /* timelineStages */ ],
  "timeline": [ /* timelineStages */ ],
  "evidence": [{ "kind": "approval", "id": "approval-c01-rebalance" }],
  "evidence_refs": [{ "kind": "approval", "id": "approval-c01-rebalance" }],
  "approval": {
    "approval_id": "approval-c01-rebalance",
    "links": {
      "approval": "/management/approvals?approval=approval-c01-rebalance",
      "hiq": "/management/interventions?intervention=hiq-c01-rebalance"
    }
  }
}
```

**Approval stage (within `stages`/`timeline`):**
```json
{
  "stage": "approval",
  "stage_id": "stage-c01-approval",
  "kind": "awaiting_approval",
  "status": "blocked",
  "entity_type": "approval",
  "entity_id": "approval-c01-rebalance",
  "action_href": "/management/approvals?approval=approval-c01-rebalance",
  "hiq_href": "/management/interventions?intervention=hiq-c01-rebalance"
}
```

**Stage pipeline field constraint:** The spec enforces that `stages[]` must not contain display-only mock fields (`display_label`, `displayLabel`, `label`, `mock_label`, `mockLabel`, `mock_stage`, `mockStage`, `seed_label`, `seedLabel`, `stage_display_name`, `stageDisplayName`, `title`). Names must derive from `name`, `kind`, `stage`, or `stage_name`.

---

## 7. Instructions for Reviewer (Gemini2)

Review this packet against the verified facts:

1. Parent task FE-INT-GATE-ALIGN-F04-FOLLOWUP is `done` — confirm this matches `ai-status.json` or the archive.
2. Commit `8c7606cf6904e63eb265427cef25f8d226e10cbf` in `/home/lupin/code/execute-plans` on branch `bff-luv-fe-006-dev-deploy` is the confirmed FE source.
3. The BFF endpoints, field names, and waterfall logic described in §3–§4 are derived directly from `src/lib/bff/v5.ts` (functions `adaptBffLoopRun`, `adaptLoopNextAction`, `adaptLoopEvidence`).
4. The operator journey in §4 and UI surface table in §5 are derived directly from `src/management/pages/v5/OptimizationLoop.tsx` (lines 64, 96–131).
5. The verified contract in §6 is derived directly from `e2e/04b-optimization-loop.spec.ts` fixture data.
6. No canonical truth files were modified. This packet is a support artifact only.

---

## 8. Dependencies

- **FE-INT-GATE-ALIGN-F04** (`done`): Align 04b-optimization-loop.spec.ts to hosted Lovable DOM
- **FE-INT-GATE-ALIGN-F04-FOLLOWUP** (`done`): Row-level optimization approval/HIQ control restored
