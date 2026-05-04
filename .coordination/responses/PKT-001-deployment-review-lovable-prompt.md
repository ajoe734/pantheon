Build the `PKT-001-deployment-review` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-001-deployment-review-bff-gap.yaml` using `.coordination/requests/PKT-001-deployment-review-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `deployment-review-console`.
Workbench: `operator-console`.
Screen ID: `screen-operator-deployment-review`.
Allowed endpoints:
- GET /api/v1/operator/deployment-plans
- GET /api/v1/operator/deployment-review/{plan_id}
- POST /api/v1/operator/commands
Published Pantheon dependencies:
- .coordination/responses/PKT-001-deployment-review-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Deployment Review Console list and detail panels
- use only the existing BFF client
- do not add raw fetch calls in component files
- do not invent fields beyond this handoff packet
- render all CTAs from backend-shaped allowedActions only
- display the degradation banner when any meta.surfaces entry is degraded or unavailable
Required feedback bundle:
- docs/pantheon-feedback/PKT-001-deployment-review/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-001-deployment-review/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-001-deployment-review/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-001-deployment-review/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-001-deployment-review-ui-done.yaml` using `.coordination/requests/PKT-001-deployment-review-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-001-deployment-review-frontend-feedback.yaml` using `.coordination/requests/PKT-001-deployment-review-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-001-deployment-review-console.md
- docs/pantheon-handoffs/PKT-001-deployment-review/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-001-deployment-review-console.md
- docs/pantheon-handoffs/PKT-001-deployment-review
- docs/examples/PKT-001-deployment-review-console.json
