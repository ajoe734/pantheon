Build the `PKT-010-runtime-state-board` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-010-runtime-state-board-bff-gap.yaml` using `.coordination/requests/PKT-010-runtime-state-board-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `operator-runtime-state-board`.
Workbench: `operator-console`.
Screen ID: `screen-operator-runtime-state-board`.
Allowed endpoints:
- GET /api/v1/operator/runtime-state
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Operator Runtime State Board screen with one server-backed runtime roster
- use only the existing BFF client
- keep sorting, filtering, and pagination server-backed
- do not join RT-03, RT-04, or TL-02 per row in the browser
- do not add rollback, pause, or promotion CTAs to this packet
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-010-runtime-state-board-ui-done.yaml` using `.coordination/requests/PKT-010-runtime-state-board-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-010-runtime-state-board.md
- docs/pantheon-handoffs/PKT-010-runtime-state-board/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-010-runtime-state-board.md
- docs/pantheon-handoffs/PKT-010-runtime-state-board
- docs/examples/PKT-010-runtime-state-board.json
