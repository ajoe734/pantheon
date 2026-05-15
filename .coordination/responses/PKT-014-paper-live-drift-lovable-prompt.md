Build the `PKT-014-paper-live-drift` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-014-paper-live-drift-bff-gap.yaml` using `.coordination/requests/PKT-014-paper-live-drift-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `operator-paper-live-drift`.
Workbench: `operator-console`.
Screen ID: `screen-operator-paper-live-drift`.
Allowed endpoints:
- GET /api/v1/operator/paper-live-drift/{runtime_id}
Published Pantheon dependencies:
- .coordination/responses/PKT-014-paper-live-drift-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Operator Paper / Live Drift view from the single drift route
- use only the existing BFF client
- keep drift groups, threshold evaluation, and recommended actions backend-owned
- render evidence refs and target refs exactly as supplied
- do not derive follow-up actions from raw metric values
Required feedback bundle:
- docs/pantheon-feedback/PKT-014-paper-live-drift/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-014-paper-live-drift/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-014-paper-live-drift/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-014-paper-live-drift/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-014-paper-live-drift-ui-done.yaml` using `.coordination/requests/PKT-014-paper-live-drift-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-014-paper-live-drift-frontend-feedback.yaml` using `.coordination/requests/PKT-014-paper-live-drift-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-014-paper-live-drift.md
- docs/pantheon-handoffs/PKT-014-paper-live-drift/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-014-paper-live-drift.md
- docs/pantheon-handoffs/PKT-014-paper-live-drift
- docs/examples/PKT-014-paper-live-drift.json
