# PKT-009 Governance Audit Rail

## Classification

- Workbench: Governance Workbench
- Screen ID: `screen-governance-audit-rail`
- Feature ID: `PKT-009-governance-audit-rail`
- Packet status: ready

## User Goal

Give a governance operator a chronological, filterable audit trail of all governance actions so they can trace decisions, verify actor authority, and surface evidence for compliance review without reconstructing event history in the browser.

## Page Sections

- **Audit trail list**: paginated chronological list of governance audit entries. Each row shows `entry_id`, `actor`, `action_type`, `target_type`, `target_id`, `timestamp`, `outcome` (`success` | `rejected` | `escalated`), and an evidence indicator.
- **Entry detail drawer**: opens on row selection. Shows full `entry_id`, `actor`, `action_type`, `target_type`, `target_id`, `timestamp`, `outcome`, `audit_context` (operator note), and `evidence_refs[]`.
- **Filter rail**: filter by `actor`, `action_type` (comma-separated), `target_type`, and `date_range` (`from` + `to` in RFC3339). Filters are passed as query parameters; no client-side filtering.
- **Delayed-data banner**: when `meta.surfaces.audit_trail` is `degraded` or `unavailable`, a non-dismissable banner explains that audit data may be delayed; the list renders any available entries in read-only mode.
- **Degradation banner**: when any BFF surface is degraded, a non-dismissable banner is shown.
- **Loading, empty, and error states**: explicit and visually distinct with no mock fallback.

## Interaction Rules

- All production data comes from `GET /api/v1/operator/governance/audit`.
- The audit trail is read-only; no write actions originate from this screen.
- Filters are sent as query parameters to the BFF. No client-side filtering or sorting.
- When `meta.surfaces.audit_trail` is `degraded`, the list renders available entries with the delayed-data banner. It does not show a blank state.
- When `meta.surfaces.audit_trail` is `unavailable`, the list is replaced with the unavailable-data message.
- Evidence drawer opens from the entry row. Evidence refs come from the BFF response; the UI does not synthesize evidence from other sources.

## Acceptance

- Audit list renders from BFF-supplied `entries[]` only; no client-side derivation of actor or action labels.
- Filter parameters pass through to the BFF query; no client-side filter logic.
- Degraded audit surface shows the delayed-data banner and renders available entries in read-only mode.
- Unavailable audit surface replaces the list with the unavailable-data message.
- Entry detail drawer opens from row selection and renders all required BFF fields.
- `evidence_refs` renders as a list of linked refs when present; shows `no evidence attached` when the array is empty.
- Loading, empty, degraded, and error states are explicit and visually distinct.
