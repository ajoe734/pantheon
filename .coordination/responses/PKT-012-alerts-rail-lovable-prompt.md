Build the `PKT-012-alerts-rail` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-012-alerts-rail-bff-gap.yaml` using `.coordination/requests/PKT-012-alerts-rail-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `operator-alerts-rail`.
Workbench: `operator-console`.
Screen ID: `screen-operator-alerts-rail`.
Allowed endpoints:
- GET /api/v1/operator/alerts
Published Pantheon dependencies:
- .coordination/responses/PKT-012-alerts-rail-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Operator Alerts Rail from the single alerts route
- use only the existing BFF client
- keep alert severity, category, and ordering backend-owned
- render target refs exactly as supplied
- do not add acknowledgement or dismissal controls
Required feedback bundle:
- docs/pantheon-feedback/PKT-012-alerts-rail/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-012-alerts-rail/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-012-alerts-rail/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-012-alerts-rail/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-012-alerts-rail-ui-done.yaml` using `.coordination/requests/PKT-012-alerts-rail-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-012-alerts-rail-frontend-feedback.yaml` using `.coordination/requests/PKT-012-alerts-rail-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-012-alerts-rail.md
- docs/pantheon-handoffs/PKT-012-alerts-rail/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-012-alerts-rail.md
- docs/pantheon-handoffs/PKT-012-alerts-rail
- docs/examples/PKT-012-alerts-rail.json
