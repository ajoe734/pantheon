Build the `PKT-014-paper-live-drift` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-014-paper-live-drift-bff-gap.yaml` using `.coordination/requests/PKT-014-paper-live-drift-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `operator-paper-live-drift`.
Workbench: `operator-console`.
Screen ID: `screen-operator-paper-live-drift`.
Allowed endpoints:
- GET /api/v1/operator/paper-live-drift/{runtime_id}
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
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-014-paper-live-drift-ui-done.yaml` using `.coordination/requests/PKT-014-paper-live-drift-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-014-paper-live-drift.md
- docs/pantheon-handoffs/PKT-014-paper-live-drift/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-014-paper-live-drift.md
- docs/pantheon-handoffs/PKT-014-paper-live-drift
- docs/examples/PKT-014-paper-live-drift.json
