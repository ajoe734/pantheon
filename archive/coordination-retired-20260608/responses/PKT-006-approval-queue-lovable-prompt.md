Build the `PKT-006-approval-queue` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-006-approval-queue-bff-gap.yaml` using `.coordination/requests/PKT-006-approval-queue-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `governance-approval-queue`.
Workbench: `governance-workbench`.
Screen ID: `screen-governance-approval-queue`.
Allowed endpoints:
- GET /api/v1/operator/governance/approval-queue
- POST /api/v1/operator/commands
Published Pantheon dependencies:
- .coordination/responses/PKT-006-approval-queue-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Governance Approval Queue list and decision detail drawer
- use only the existing BFF client
- do not add raw fetch calls in component files
- do not invent fields beyond this handoff packet
- render all approval CTAs from backend-shaped allowedActions only
- pass filters to the BFF as query parameters; do not filter client-side
- display the degradation banner when any meta.surfaces entry is degraded or unavailable
- inherit the queue model and pagination pattern from PKT-001 Governance Review Queue
Required feedback bundle:
- docs/pantheon-feedback/PKT-006-approval-queue/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-006-approval-queue/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-006-approval-queue/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-006-approval-queue/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-006-approval-queue-ui-done.yaml` using `.coordination/requests/PKT-006-approval-queue-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-006-approval-queue-frontend-feedback.yaml` using `.coordination/requests/PKT-006-approval-queue-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-006-approval-queue.md
- docs/pantheon-handoffs/PKT-006-approval-queue/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-006-approval-queue.md
- docs/pantheon-handoffs/PKT-006-approval-queue
- docs/examples/PKT-006-approval-queue.json
