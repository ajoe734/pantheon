Build the `PKT-010-runtime-state-board` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-010-runtime-state-board-bff-gap.yaml` using `.coordination/requests/PKT-010-runtime-state-board-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `operator-runtime-state-board`.
Workbench: `operator-console`.
Screen ID: `screen-operator-runtime-state-board`.
Allowed endpoints:
- GET /api/v1/operator/runtime-state
Published Pantheon dependencies:
- .coordination/responses/PKT-010-runtime-state-board-contract-ready.yaml
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
Required feedback bundle:
- docs/pantheon-feedback/PKT-010-runtime-state-board/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-010-runtime-state-board/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-010-runtime-state-board/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-010-runtime-state-board/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-010-runtime-state-board-ui-done.yaml` using `.coordination/requests/PKT-010-runtime-state-board-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-010-runtime-state-board-frontend-feedback.yaml` using `.coordination/requests/PKT-010-runtime-state-board-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-010-runtime-state-board.md
- docs/pantheon-handoffs/PKT-010-runtime-state-board/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-010-runtime-state-board.md
- docs/pantheon-handoffs/PKT-010-runtime-state-board
- docs/examples/PKT-010-runtime-state-board.json
