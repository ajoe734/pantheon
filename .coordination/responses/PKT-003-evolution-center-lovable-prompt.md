Build the `PKT-003-evolution-center` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-003-evolution-center-bff-gap.yaml` using `.coordination/requests/PKT-003-evolution-center-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `evolution-center`.
Workbench: `evolution-workbench`.
Screen ID: `screen-evolution-center`.
Allowed endpoints:
- GET /api/v1/evolution-decisions
- GET /api/v1/evolution-decisions/{decision_id}
- GET /api/v1/freeze-orders
- GET /api/v1/rollbacks
Published Pantheon dependencies:
- .coordination/responses/PKT-003-evolution-center-contract-ready.yaml
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
Required feedback bundle:
- docs/pantheon-feedback/PKT-003-evolution-center/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-003-evolution-center/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-003-evolution-center/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-003-evolution-center/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-003-evolution-center-ui-done.yaml` using `.coordination/requests/PKT-003-evolution-center-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-003-evolution-center-frontend-feedback.yaml` using `.coordination/requests/PKT-003-evolution-center-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-003-evolution-center.md
- docs/pantheon-handoffs/PKT-003-evolution-center/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-003-evolution-center.md
- docs/pantheon-handoffs/PKT-003-evolution-center
- docs/examples/PKT-003-evolution-center.json
