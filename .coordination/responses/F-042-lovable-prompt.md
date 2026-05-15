Build the `F-042` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/F-042-bff-gap.yaml` using `.coordination/requests/F-042-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `promotion-review`.
Workbench: `governance-review`.
Screen ID: `screen-governance-promotion-review`.
Allowed endpoints:
- GET /api/v1/operator/deployment-review/{plan_id}
- POST /api/v1/operator/commands
Published Pantheon dependencies:
- .coordination/responses/F-042-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Promotion Review page shell and state rendering
- use only the existing BFF client
- do not add raw fetch calls in component files
- do not invent endpoint fields beyond this handoff packet
- render approval decision and governance outcome from backend-shaped fields
Required feedback bundle:
- docs/pantheon-feedback/F-042/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/F-042/API_GAP_REQUESTS.json
- docs/pantheon-feedback/F-042/UI_DECISIONS.md
- docs/pantheon-feedback/F-042/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/F-042-ui-done.yaml` using `.coordination/requests/F-042-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/F-042-frontend-feedback.yaml` using `.coordination/requests/F-042-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/F-042-promotion-review.md
- docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md
- docs/bff/F-042-promotion-review.md
- docs/pantheon-handoffs/F-042
- docs/examples/F-042-review-page.json
