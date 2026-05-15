Build the `PKT-002-incident-action-drawer` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-002-incident-action-drawer-bff-gap.yaml` using `.coordination/requests/PKT-002-incident-action-drawer-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `incident-action-drawer`.
Workbench: `operator-console`.
Screen ID: `screen-operator-incident-action-drawer`.
Allowed endpoints:
- GET /api/v1/kill-switch/status
- POST /api/v1/operator/commands
Published Pantheon dependencies:
- .coordination/responses/PKT-002-incident-action-drawer-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Incident Action Drawer with kill switch status header and emergency action buttons
- use only the existing BFF client
- do not add raw fetch calls in component files
- do not invent fields beyond this handoff packet
- send the published PKT-002 command names and params directly; do not translate to the legacy PauseRuntime/ExecuteRollback/ActivateKillSwitch envelope
- render all CTAs from backend-shaped allowedActions only; do not derive eligibility locally
- render command receipts inline after each POST /api/v1/operator/commands call
- render the secondary control path panel when meta.surfaces.kill_switch is degraded or unavailable
- require a non-empty audit_context.reason before enabling any action submit button
Required feedback bundle:
- docs/pantheon-feedback/PKT-002-incident-action-drawer/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-002-incident-action-drawer/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-002-incident-action-drawer/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-002-incident-action-drawer/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-002-incident-action-drawer-ui-done.yaml` using `.coordination/requests/PKT-002-incident-action-drawer-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-002-incident-action-drawer-frontend-feedback.yaml` using `.coordination/requests/PKT-002-incident-action-drawer-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-002-incident-action-drawer.md
- docs/pantheon-handoffs/PKT-002-incident-action-drawer/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-002-incident-action-drawer.md
- docs/pantheon-handoffs/PKT-002-incident-action-drawer
- docs/examples/PKT-002-incident-action-drawer.json
