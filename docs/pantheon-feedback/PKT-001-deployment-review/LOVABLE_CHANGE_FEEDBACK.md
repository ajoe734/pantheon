# PKT-001 Lovable Change Feedback

Reviewed the current `ajoe734/front-ai-trading-system` working tree on top of commit `faa1bc2d1bd02e0a3d9fc1e1e5c35bc510182ea7`.

## Outcome

Pantheon review result: accepted for follow-up handoff.

The Deployment Review Console is implemented against the published PKT-001
snapshot contract and example payload, including the full degradation banner
path and backend-shaped CTA visibility. The detail surface also reuses the
shared `PKT-005` runtime SSE substrate for incremental runtime decoration; this
is an acknowledged cross-cut dependency, not a new PKT-001 snapshot API gap.

## Verified Against Pantheon

- `GET /api/v1/operator/deployment-plans` is consumed through `operatorApi.listDeploymentPlans()` in `bffClient.ts`.
- `GET /api/v1/operator/deployment-review/{plan_id}` is consumed through `operatorApi.getDeploymentReview()`.
- When `runtime_binding.id` is present, `DeploymentReviewConsole.tsx` opens `/api/v1/runtime/{runtimeBindingId}/events/stream` through the shared `PKT-005` `SseClient` substrate. This live feed is incremental only and does not replace the BFF snapshot as source of truth.
- `POST /api/v1/operator/commands` is submitted through `operatorApi.sendCommand()` with the full `ApproveDeployment` command payload structure specified in the BFF contract.
- CTA visibility (`canApprove`, `canReject`, `canPromoteToPaper`) is driven exclusively from backend-shaped `allowedActions` fields. The UI does not derive approval eligibility locally.
- The `GlobalDegradationBanner` renders at the top of the console when any `meta.surfaces` entry is `"degraded"` or `"unavailable"` — both at list level and detail level.
- All action CTAs are disabled when any surface is degraded; degraded-surface badges are rendered in the detail panel header.
- Verification notes are required on reject and enforced client-side before the command POST fires.
- Audit reason is required for all actions and validated before submission.
- Status filter is passed as a query parameter to the BFF — no client-side filter logic.
- Pagination via `page_info.next_page_token` is implemented through a "Load more" button — not infinite scroll.
- Contract-gap state is rendered as an explicit BFF-gap alert in both the list panel (missing required list fields) and the detail panel (missing required detail fields), not as a silent fallback.
- Both `DeploymentReviewConsole.tsx` and `DeploymentPlanDetail.tsx` handle loading, empty, degraded, error, and missing-field states as distinct visual states.

## Notes

- The status filter toolbar uses query params (`?status=...`) to drive the BFF call. Clicking a row appends `?plan=...` to the URL — detail state is URL-addressable.
- `DeploymentPlanDetail` accepts `planId` as a prop, forwarding parent-side status filter changes back to the list via `onActionCompleted`.
- The `toDetailResponse` helper normalizes two documented BFF payload shapes (envelope with `data` key, and flat layout) — mirrors the same tolerance pattern used for the incident-action-drawer surface.
- The runtime event stream belongs to `PKT-005`, not to the PKT-001 snapshot route family. Its role on this screen is read-only enhancement after the detail snapshot has already loaded.
- This review included static code verification and cross-check against `docs/bff/PKT-001-deployment-review-console.md`, `docs/screens/PKT-001-deployment-review-console.md`, and `docs/examples/PKT-001-deployment-review-console.json`.

## Pantheon Follow-up

- No new PKT-001 snapshot API gap remains in this cycle. Pantheon now serves `GET /api/v1/operator/deployment-plans` in the current workspace.
- The remaining follow-up is coordination truth: the front-owned request pair and published feedback bundle must stay explicit that runtime SSE is inherited from `PKT-005` as a cross-cut substrate.
