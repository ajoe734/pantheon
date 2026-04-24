Build the `PKT-004-capital-binding-drilldowns` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-004-capital-binding-drilldowns-bff-gap.yaml` using `.coordination/requests/PKT-004-capital-binding-drilldowns-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `capital-binding-drilldowns`.
Workbench: `persona-workbench`.
Allowed endpoints:
- GET /api/v1/capital-pools
- GET /api/v1/capital-pools/{pool_id}
- GET /api/v1/bindings
- GET /api/v1/bindings/{binding_id}
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the four Capital/Binding Drilldown surfaces (CP-01 to CP-04)
- use only the existing BFF client
- pass filters as query parameters to the BFF; do not filter client-side
- no write actions are defined in this module
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-004-capital-binding-drilldowns-ui-done.yaml` using `.coordination/requests/PKT-004-capital-binding-drilldowns-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-004-capital-binding-drilldowns.md
- docs/pantheon-handoffs/PKT-004-capital-binding-drilldowns/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-004-capital-binding-drilldowns.md
- docs/pantheon-handoffs/PKT-004-capital-binding-drilldowns
- docs/examples/PKT-004-capital-binding-drilldowns.json
