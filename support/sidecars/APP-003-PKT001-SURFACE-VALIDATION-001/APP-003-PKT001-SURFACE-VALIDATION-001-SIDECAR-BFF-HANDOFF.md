# APP-003-PKT001-SURFACE-VALIDATION-001 Sidecar BFF Handoff

Status: review-ready support artifact
Parent task: `APP-003-PKT001-SURFACE-VALIDATION-001`
Helper kind: `bff_handoff_packet`
Owner: `Codex2`
Reviewer: `Codex3`
Prepared at: `2026-04-23`

## 1. Scope

This packet is support-only. It does not change canonical truth, BFF contracts, runtime code, or task ownership outside this sidecar.

Its purpose is to hand the reviewer and the front-end lane a compact answer to three questions:

1. Is there still an open Pantheon BFF query gap for `PKT-001`?
2. What exact operator journey and `meta.surfaces` keys must the UI validate fail-closed?
3. What should the next front refresh republish to close the loop truthfully?

## 2. Bottom Line

- No new Pantheon BFF query gap is open for `PKT-001`.
- The remaining blocker is front-owned fail-closed validation of required `meta.surfaces` keys.
- After that validation lands, the front repo should republish the refreshed feedback bundle and return the unchanged request pair for Pantheon review.

## 3. Evidence Read

- `docs/reviews/2026-04-23-post-closeout-residual-execution-packet.md`
- `.coordination/responses/PKT-001-deployment-review-frontend-feedback.yaml`
- `docs/pantheon-handoffs/PKT-001-deployment-review/FRONTEND_CHANGE_SPEC.md`
- `docs/bff/PKT-001-deployment-review-console.md`
- `docs/screens/PKT-001-deployment-review-console.md`
- `docs/examples/PKT-001-deployment-review-console.json`
- `.coordination/responses/PKT-001-deployment-review-contract-ready.yaml`
- `.coordination/responses/PKT-001-deployment-review-lovable-ui-task.yaml`

## 4. BFF Query Gap Assessment

### Decision

Classify this slice as `no open BFF query gap`.

### Why

- The contract-ready packet is already published for the full PKT-001 route family:
  - `GET /api/v1/operator/deployment-plans`
  - `GET /api/v1/operator/deployment-review/{plan_id}`
  - `POST /api/v1/operator/commands`
- The latest Pantheon review explicitly records `api_gaps: []`.
- The returned frontend feedback says the prior Git-visible publication blocker is already closed.
- The remaining failed acceptance criterion is not route absence or field absence at the BFF boundary. It is that the UI currently treats `meta.surfaces` existence as sufficient instead of enforcing the required key set fail-closed.

### Non-gap note

Runtime SSE at `GET /api/v1/runtime/{runtime_id}/events/stream` remains an approved `PKT-005` incremental decoration only. It does not reopen `PKT-001` as a new snapshot-route or query-gap task.

## 5. Operator Journey To Preserve

The canonical operator journey is unchanged:

1. Operator opens `Deployment Review Console` at `/operator/deployment-review`.
2. List panel fetches `GET /api/v1/operator/deployment-plans`.
3. Operator selects a plan row.
4. Detail panel fetches `GET /api/v1/operator/deployment-review/{plan_id}`.
5. UI renders review snapshot, bindings, runtime binding, latest run progress, and backend-shaped `allowedActions`.
6. Approve or reject actions submit through `POST /api/v1/operator/commands`.
7. Optional runtime SSE may refresh detail decoration after `runtime_binding.id` is known, but snapshot truth, degradation state, and CTA authority remain owned by the PKT-001 snapshot responses.

## 6. Required Fail-Closed Surface Keys

The current front residual is precise and should stay precise.

### List payload must require

- `meta.surfaces.deployment_plans`
- `meta.surfaces.allowedActions`

### Detail payload must require

- `meta.surfaces.deployment_plan`
- `meta.surfaces.approval_decision`
- `meta.surfaces.allowedActions`
- `meta.surfaces.latestRun`
- `meta.surfaces.review`
- `meta.surfaces.runtime_binding`

### Front-end implementation note

The feedback bundle identifies the shared helper to use: `findMissingSurfaceFields()` in `src/lib/degradationBanner.ts`.

The front should fail closed when any required key above is absent, rather than accepting a payload because `meta.surfaces` merely exists.

## 7. Expected Front Refresh

The next front refresh should do all of the following:

1. Update `DeploymentReviewConsole.tsx` to validate the required list-surface keys through the shared helper.
2. Update `DeploymentPlanDetail.tsx` to validate the required detail-surface keys through the shared helper.
3. Keep list/detail reads on the existing BFF client and keep writes on `operatorApi.sendCommand()`.
4. Republish the refreshed feedback bundle.
5. Re-return the canonical request pair so Pantheon can review the same contract boundary with the fail-closed handling fixed.

## 8. Reviewer Checklist

The reviewer should confirm:

- this packet stays support-only and introduces no canonical-truth mutation
- `PKT-001` is still correctly classified as having no open Pantheon BFF query gap
- the real residual is front fail-closed surface validation, not route-family repair
- the required `meta.surfaces` key sets above match the latest frontend-feedback record
- the next handoff should go back to front execution plus refreshed feedback republish

## 9. Suggested Review Disposition

If the reviewer agrees, approve this sidecar and return it to the owner for finalization with a note equivalent to:

`Support packet is accurate: no new BFF gap, front must enforce required meta.surfaces keys fail-closed and republish the feedback bundle.`
