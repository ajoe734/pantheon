Build the `PKT-009-governance-audit-rail` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-009-governance-audit-rail-bff-gap.yaml` using `.coordination/requests/PKT-009-governance-audit-rail-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `governance-audit-rail`.
Workbench: `governance-workbench`.
Screen ID: `screen-governance-audit-rail`.
Allowed endpoints:
- GET /api/v1/operator/governance/audit
Published Pantheon dependencies:
- .coordination/responses/PKT-009-governance-audit-rail-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Governance Audit Rail list and entry detail drawer
- use only the existing BFF client
- do not add raw fetch calls in component files
- the audit trail is read-only; do not add write CTAs or command calls
- pass filters to the BFF as query parameters; do not filter or sort client-side
- render actor and action type labels as supplied by the BFF; do not invent display labels
- show delayed-data banner and available entries when meta.surfaces.audit_trail is degraded
- replace the list with unavailable-data message when meta.surfaces.audit_trail is unavailable
Required feedback bundle:
- docs/pantheon-feedback/PKT-009-governance-audit-rail/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-009-governance-audit-rail/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-009-governance-audit-rail/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-009-governance-audit-rail/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-009-governance-audit-rail-ui-done.yaml` using `.coordination/requests/PKT-009-governance-audit-rail-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-009-governance-audit-rail-frontend-feedback.yaml` using `.coordination/requests/PKT-009-governance-audit-rail-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-009-governance-audit-rail.md
- docs/pantheon-handoffs/PKT-009-governance-audit-rail/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-009-governance-audit-rail.md
- docs/pantheon-handoffs/PKT-009-governance-audit-rail
- docs/examples/PKT-009-governance-audit-rail.json
