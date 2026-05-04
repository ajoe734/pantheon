Build the `PKT-011-health-status-board` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-011-health-status-board-bff-gap.yaml` using `.coordination/requests/PKT-011-health-status-board-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `operator-health-status-board`.
Workbench: `operator-console`.
Screen ID: `screen-operator-health-status-board`.
Allowed endpoints:
- GET /api/v1/operator/health-status
Published Pantheon dependencies:
- .coordination/responses/PKT-011-health-status-board-contract-ready.yaml
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
Required feedback bundle:
- docs/pantheon-feedback/PKT-011-health-status-board/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-011-health-status-board/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-011-health-status-board/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-011-health-status-board/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-011-health-status-board-ui-done.yaml` using `.coordination/requests/PKT-011-health-status-board-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-011-health-status-board-frontend-feedback.yaml` using `.coordination/requests/PKT-011-health-status-board-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-011-health-status-board.md
- docs/pantheon-handoffs/PKT-011-health-status-board/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-011-health-status-board.md
- docs/pantheon-handoffs/PKT-011-health-status-board
- docs/examples/PKT-011-health-status-board.json
