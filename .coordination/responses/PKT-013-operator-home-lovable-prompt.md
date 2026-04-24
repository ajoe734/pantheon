Build the `PKT-013-operator-home` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-013-operator-home-bff-gap.yaml` using `.coordination/requests/PKT-013-operator-home-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `operator-home-dashboard`.
Workbench: `operator-console`.
Screen ID: `screen-operator-home-dashboard`.
Allowed endpoints:
- GET /api/v1/operator/home
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Operator Home dashboard from the single operator-home route
- use only the existing BFF client
- keep cards and escalation shortcuts in backend-owned order
- distinguish unavailable or degraded state from a calm empty dashboard
- do not recreate this screen from alerts, health, incidents, governance, runtime, or kill-switch primitives
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-013-operator-home-ui-done.yaml` using `.coordination/requests/PKT-013-operator-home-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-013-operator-home.md
- docs/pantheon-handoffs/PKT-013-operator-home/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-013-operator-home.md
- docs/pantheon-handoffs/PKT-013-operator-home
- docs/examples/PKT-013-operator-home.json
