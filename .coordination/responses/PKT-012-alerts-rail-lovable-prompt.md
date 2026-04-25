Build the `PKT-012-alerts-rail` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-012-alerts-rail-bff-gap.yaml` using `.coordination/requests/PKT-012-alerts-rail-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `operator-alerts-rail`.
Workbench: `operator-console`.
Screen ID: `screen-operator-alerts-rail`.
Allowed endpoints:
- GET /api/v1/operator/alerts
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
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-012-alerts-rail-ui-done.yaml` using `.coordination/requests/PKT-012-alerts-rail-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-012-alerts-rail.md
- docs/pantheon-handoffs/PKT-012-alerts-rail/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-012-alerts-rail.md
- docs/pantheon-handoffs/PKT-012-alerts-rail
- docs/examples/PKT-012-alerts-rail.json
