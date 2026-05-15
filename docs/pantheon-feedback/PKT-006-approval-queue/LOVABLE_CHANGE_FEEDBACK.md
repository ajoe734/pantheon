# PKT-006 Governance Approval Queue — Lovable Change Feedback

## Feature

- Feature ID: `PKT-006-approval-queue`
- Screen ID: `screen-governance-approval-queue`
- Workbench: Governance Workbench

## Summary of Changes

Built the Governance Approval Queue screen and the embedded decision detail drawer inside `front-ai-trading-system`.

### New files

- `src/pages/governance/GovernanceApprovalQueue.tsx` — paginated queue list with filter rail, degradation banner, loading/empty/error states
- `src/pages/governance/ApprovalDecisionDetail.tsx` — decision detail drawer with Approve, Reject, and Request Revision CTAs

### Modified files

- `src/pages/governance/types.ts` — added PKT-006 approval queue types
- `src/lib/bffClient.ts` — added `listGovernanceApprovalQueue` fetch helper and PKT-006 command payloads
- `src/App.tsx` — registered `/governance-approval-queue` route
- `src/components/AppSidebar.tsx` — added Approval Queue nav entry

## Contract Adherence

- All data sourced from `GET /api/v1/operator/governance/approval-queue` via the shared BFF client.
- Write actions use `POST /api/v1/operator/commands` with `ApproveDecision`, `RejectDecision`, `RequestApprovalRevision` command envelopes.
- CTA visibility (`canApprove`, `canReject`, `canRequestRevision`) is backend-shaped via `allowedActions` — no local derivation.
- Filters (`decision_type`, `risk_level`, `decision_state`) pass through as query parameters to the BFF. No client-side filtering.
- Degradation banner is non-dismissable and disables all approval CTAs when any `meta.surfaces` entry is `degraded` or `unavailable`.
- BFF gap detection: if required `allowedActions` fields are absent, the screen reports a BFF contract gap with the missing field list.
- Drawer content populated from the embedded `decision_context` sub-object — no additional fetch needed.
- Queue model and pagination pattern inherited from PKT-001 Governance Review Queue.

## Known Limitations

- Live browser QA against a running Pantheon BFF not yet performed.
- Live command verification depends on backend being deployed.
