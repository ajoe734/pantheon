Build the `PKT-001-governance-review-queue` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-001-governance-review-queue-bff-gap.yaml` using `.coordination/requests/PKT-001-governance-review-queue-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `governance-review-queue`.
Workbench: `governance-workbench`.
Screen ID: `screen-governance-review-queue`.
Allowed endpoints:
- GET /api/v1/operator/governance/review-queue
- POST /api/v1/operator/commands
Published Pantheon dependencies:
- .coordination/responses/PKT-001-governance-review-queue-contract-ready.yaml
- .coordination/responses/PKT-001-governance-review-queue-backend-delivery.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build or refresh the Governance Review Queue list and item detail drawer against the existing BFF client
- use only the existing BFF client
- do not add raw fetch calls in component files
- do not invent fields beyond this handoff packet
- render all routing CTAs from backend-shaped allowedActions only
- pass filters to the BFF as query parameters; do not filter client-side
- treat meta.surfaces.review_queue and meta.surfaces.allowedActions as required and fail closed if either is absent
- display the degradation banner when any meta.surfaces entry is degraded or unavailable
Required feedback bundle:
- docs/pantheon-feedback/PKT-001-governance-review-queue/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-001-governance-review-queue/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-001-governance-review-queue/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-001-governance-review-queue/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-001-governance-review-queue-ui-done.yaml` using `.coordination/requests/PKT-001-governance-review-queue-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-001-governance-review-queue-frontend-feedback.yaml` using `.coordination/requests/PKT-001-governance-review-queue-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-001-governance-review-queue.md
- docs/pantheon-handoffs/PKT-001-governance-review-queue/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-001-governance-review-queue.md
- docs/pantheon-handoffs/PKT-001-governance-review-queue
- docs/examples/PKT-001-governance-review-queue.json
