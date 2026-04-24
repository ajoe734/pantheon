Build the `PKT-001-deployment-review` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-001-deployment-review-bff-gap.yaml` using `.coordination/requests/PKT-001-deployment-review-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `deployment-review-console`.
Workbench: `operator-console`.
Screen ID: `screen-operator-deployment-review`.
Allowed endpoints:
- GET /api/v1/operator/deployment-plans
- GET /api/v1/operator/deployment-review/{plan_id}
- POST /api/v1/operator/commands
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
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-001-deployment-review-ui-done.yaml` using `.coordination/requests/PKT-001-deployment-review-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-001-deployment-review-console.md
- docs/pantheon-handoffs/PKT-001-deployment-review/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-001-deployment-review-console.md
- docs/pantheon-handoffs/PKT-001-deployment-review
- docs/examples/PKT-001-deployment-review-console.json
