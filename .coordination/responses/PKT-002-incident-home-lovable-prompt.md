Build the `PKT-002-incident-home` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-002-incident-home-bff-gap.yaml` using `.coordination/requests/PKT-002-incident-home-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `incident-home`.
Workbench: `operator-console`.
Screen ID: `screen-operator-incident-home`.
Allowed endpoints:
- GET /api/v1/incidents
- GET /api/v1/kill-switch/status
Published Pantheon dependencies:
- .coordination/responses/PKT-002-incident-home-contract-ready.yaml
- .coordination/responses/PKT-002-incident-home-backend-delivery.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Incident Home list panel and kill switch control rail badge
- use only the existing BFF client
- do not add raw fetch calls in component files
- do not invent fields beyond this handoff packet
- render kill switch badge from GET /api/v1/kill-switch/status only; do not derive state locally
- render the non-dismissable warning banner when meta.surfaces.kill_switch is degraded or unavailable
- display the degradation banner when any meta.surfaces entry is degraded or unavailable
Required feedback bundle:
- docs/pantheon-feedback/PKT-002-incident-home/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-002-incident-home/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-002-incident-home/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-002-incident-home/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-002-incident-home-ui-done.yaml` using `.coordination/requests/PKT-002-incident-home-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-002-incident-home-frontend-feedback.yaml` using `.coordination/requests/PKT-002-incident-home-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-002-incident-home.md
- docs/pantheon-handoffs/PKT-002-incident-home/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-002-incident-home.md
- docs/pantheon-handoffs/PKT-002-incident-home
- docs/examples/PKT-002-incident-home.json
