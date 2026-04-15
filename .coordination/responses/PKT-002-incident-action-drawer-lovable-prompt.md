Build the `PKT-002-incident-action-drawer` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-002-incident-action-drawer-bff-gap.yaml` using `.coordination/requests/PKT-002-incident-action-drawer-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `incident-action-drawer`.
Allowed endpoints:
- GET /api/v1/kill-switch/status
- POST /api/v1/operator/commands
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
- render all CTAs from backend-shaped allowedActions only; do not derive eligibility locally
- render command receipts inline after each POST /api/v1/operator/commands call
- render the secondary control path panel when meta.surfaces.kill_switch is degraded or unavailable
- require a non-empty audit_context.reason before enabling any action submit button
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-002-incident-action-drawer-ui-done.yaml` using `.coordination/requests/PKT-002-incident-action-drawer-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
