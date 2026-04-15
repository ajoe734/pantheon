Build the `PKT-003-evolution-center` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-003-evolution-center-bff-gap.yaml` using `.coordination/requests/PKT-003-evolution-center-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `evolution-center`.
Allowed endpoints:
- GET /api/v1/evolution-decisions
- GET /api/v1/evolution-decisions/{decision_id}
- GET /api/v1/freeze-orders
- GET /api/v1/rollbacks
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Evolution Center list panels for decisions, freeze orders, and rollbacks
- use only the existing BFF client
- do not add raw fetch calls in component files
- do not invent fields beyond this handoff packet
- do not expose time_range as a rollback filter control (v1 BFF limitation)
- display the degradation banner when BFF read surface state is not fresh
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-003-evolution-center-ui-done.yaml` using `.coordination/requests/PKT-003-evolution-center-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
