Build the `PKT-006-approval-queue` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-006-approval-queue-bff-gap.yaml` using `.coordination/requests/PKT-006-approval-queue-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `governance-approval-queue`.
Workbench: `governance-workbench`.
Screen ID: `screen-governance-approval-queue`.
Allowed endpoints:
- GET /api/v1/operator/governance/approval-queue
- POST /api/v1/operator/commands
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
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-006-approval-queue-ui-done.yaml` using `.coordination/requests/PKT-006-approval-queue-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-006-approval-queue.md
- docs/pantheon-handoffs/PKT-006-approval-queue/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-006-approval-queue.md
- docs/pantheon-handoffs/PKT-006-approval-queue
- docs/examples/PKT-006-approval-queue.json
