# PKT-009 Governance Audit Rail — Frontend Change Spec

## Feature

- Feature ID: `PKT-009-governance-audit-rail`
- Screen ID: `screen-governance-audit-rail`
- Workbench: Governance Workbench
- Packet status: ready

## Summary

Build the **Governance Audit Rail** inside `front-ai-trading-system`. This screen gives governance operators a filterable, paginated chronological audit trail of all governance actions so they can trace decisions, verify actor authority, and review compliance evidence without reconstructing event history in the browser. The audit trail is read-only — no write actions originate from this screen. All data comes from Pantheon BFF; actor labels, action type labels, and evidence refs must not be invented client-side.

## Files to Create or Modify

```
src/pages/governance/GovernanceAuditRail.tsx   — new audit trail list page
src/pages/governance/AuditEntryDetail.tsx      — new entry detail drawer
src/pages/governance/types.ts                  — add audit trail types
src/lib/bffClient.ts                           — add audit trail fetch helpers
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch audit trail

```
GET /api/v1/operator/governance/audit
Query params: actor (string),
              action_type (comma-separated: ApproveDecision | RejectDecision | ApproveRollback |
                           RejectRollback | EscalateDiff | ForwardToApprovalQueue |
                           RequestGovernanceChanges | EscalateGovernanceItem |
                           RequestApprovalRevision | ApproveDeployment | RejectDeployment),
              target_type (DeploymentPlan | ApprovalDecision | Rollback | GovernanceReviewItem),
              from (RFC3339), to (RFC3339), page_token, page_size
```

Expected response shape (see `docs/examples/PKT-009-governance-audit-rail.json` for a full example):

```typescript
interface GovernanceAuditResponse {
  entries: AuditEntry[];
  page_info: { next_page_token: string | null };
  meta: {
    snapshot_at: string;
    surfaces: Record<string, { status: "ok" | "degraded" | "unavailable" }>;
  };
}
interface AuditEntry {
  entry_id: string;
  actor: string;
  action_type: string;
  target_type: "DeploymentPlan" | "ApprovalDecision" | "Rollback" | "GovernanceReviewItem";
  target_id: string;
  timestamp: string;
  outcome: "success" | "rejected" | "escalated";
  audit_context: {
    reason: string | null;
  };
  evidence_refs: Array<{ ref_id: string; type: string; url: string | null }>;
}
```

## Component Structure

### `GovernanceAuditRail.tsx`

- Renders the paginated audit trail list.
- Fetches from `GET /api/v1/operator/governance/audit` on mount and on filter change.
- Renders one row per entry showing `actor`, `action_type`, `target_type`, `target_id`, `timestamp`, `outcome`, and an evidence indicator.
- Provides filter rail for `actor`, `action_type`, `target_type`, and date range (`from` / `to`) — all filters passed as query params to the BFF. No client-side filtering or sorting.
- Clicking a row opens the `AuditEntryDetail` drawer.
- When `meta.surfaces.audit_trail` is `"degraded"`, shows the delayed-data banner alongside available entries in read-only mode.
- When `meta.surfaces.audit_trail` is `"unavailable"`, replaces the list with the unavailable-data message. Does not show a blank state.
- When any other `meta.surfaces` entry is `"degraded"` or `"unavailable"`, shows a non-dismissable degradation banner at the top of the screen.
- Renders loading, empty, and error states as distinct visual states.

### `AuditEntryDetail.tsx`

- Receives `entry_id` (and the already-fetched `AuditEntry`) as props.
- Renders `entry_id`, `actor`, `action_type`, `target_type`, `target_id`, `timestamp`, `outcome`, `audit_context.reason`, and `evidence_refs[]`.
- When `evidence_refs` is empty, renders `no evidence attached`.
- When `evidence_refs` is non-empty, renders each ref as a linked entry (`ref_id`, `type`, `url` if present).
- No write actions or CTAs in this drawer.
- Renders loading and error states as distinct visual states.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- Actor labels and action type labels are supplied by the BFF and must be rendered as-is. Do not invent display labels.
- All filter selections must be sent as query parameters. Do not implement any client-side filtering or sorting.
- The audit trail is read-only. Do not add any write CTAs or command calls.
- Do not invent fields or supplement the BFF response with client-derived values.
- The audit rail inherits `meta.surfaces.*` degradation semantics from `PKT-005 Degradation Banner`; do not invent new degradation variants.

## Degradation Handling

When `meta.surfaces.audit_trail` is `"degraded"`:

- Show the delayed-data banner explaining that audit data may be delayed.
- Render available entries in read-only mode alongside the banner.
- Do not blank the list.

When `meta.surfaces.audit_trail` is `"unavailable"`:

- Replace the list with the unavailable-data message.
- Do not show a blank state or empty list.

When any other `meta.surfaces` entry is `"degraded"` or `"unavailable"`:

- Show a non-dismissable degradation banner at the top of the screen.
- Keep the list visible in read-only mode.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-009-governance-audit-rail-ui-done.yaml`. Sync the file back to GitHub so the Pantheon supervisor can pick up the next integration step.

## References

- BFF contract: `docs/bff/PKT-009-governance-audit-rail.md`
- Screen spec: `docs/screens/PKT-009-governance-audit-rail.md`
- Example payload: `docs/examples/PKT-009-governance-audit-rail.json`
- Contract-ready: `.coordination/responses/PKT-009-governance-audit-rail-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-009-governance-audit-rail-lovable-ui-task.yaml`
- Degradation substrate: `docs/pantheon-handoffs/PKT-005-degradation-banner/FRONTEND_CHANGE_SPEC.md`
