Build the `PKT-011-health-status-board` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-011-health-status-board-bff-gap.yaml` using `.coordination/requests/PKT-011-health-status-board-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `operator-health-status-board`.
Workbench: `operator-console`.
Screen ID: `screen-operator-health-status-board`.
Allowed endpoints:
- GET /api/v1/operator/health-status
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Operator Health Status Board from the single health-status route
- use only the existing BFF client
- keep the published five-group taxonomy unchanged
- render the secondary control path panel only from backend-supplied fields
- do not assemble this board from PKT-010, IN-01, governance queues, or kill-switch calls in the browser
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-011-health-status-board-ui-done.yaml` using `.coordination/requests/PKT-011-health-status-board-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-011-health-status-board.md
- docs/pantheon-handoffs/PKT-011-health-status-board/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-011-health-status-board.md
- docs/pantheon-handoffs/PKT-011-health-status-board
- docs/examples/PKT-011-health-status-board.json
