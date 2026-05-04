Build the `PKT-004-capital-binding-drilldowns` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-004-capital-binding-drilldowns-bff-gap.yaml` using `.coordination/requests/PKT-004-capital-binding-drilldowns-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `capital-binding-drilldowns`.
Workbench: `persona-workbench`.
Allowed endpoints:
- GET /api/v1/capital-pools
- GET /api/v1/capital-pools/{pool_id}
- GET /api/v1/bindings
- GET /api/v1/bindings/{binding_id}
Published Pantheon dependencies:
- .coordination/responses/PKT-004-capital-binding-drilldowns-contract-ready.yaml
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
Required feedback bundle:
- docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-004-capital-binding-drilldowns-ui-done.yaml` using `.coordination/requests/PKT-004-capital-binding-drilldowns-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-004-capital-binding-drilldowns-frontend-feedback.yaml` using `.coordination/requests/PKT-004-capital-binding-drilldowns-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-004-capital-binding-drilldowns.md
- docs/pantheon-handoffs/PKT-004-capital-binding-drilldowns/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-004-capital-binding-drilldowns.md
- docs/pantheon-handoffs/PKT-004-capital-binding-drilldowns
- docs/examples/PKT-004-capital-binding-drilldowns.json
