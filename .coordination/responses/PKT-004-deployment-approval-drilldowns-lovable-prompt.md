Build the `PKT-004-deployment-approval-drilldowns` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-004-deployment-approval-drilldowns-bff-gap.yaml` using `.coordination/requests/PKT-004-deployment-approval-drilldowns-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `deployment-approval-drilldowns`.
Workbench: `persona-workbench-shared-with-governance-workbench`.
Allowed endpoints:
- GET /api/v1/deployment-plans
- GET /api/v1/deployment-plans/{plan_id}
- GET /api/v1/approval-decisions
- GET /api/v1/approval-decisions/{decision_id}
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the four Deployment/Approval Drilldown surfaces (DP-01 to DP-04) as read-only
- use only the existing BFF client
- do not implement approve/reject/promote CTAs; cross-link to PKT-001 governance screens instead
- pass filters as query parameters to the BFF; do not filter client-side
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-004-deployment-approval-drilldowns-ui-done.yaml` using `.coordination/requests/PKT-004-deployment-approval-drilldowns-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-004-deployment-approval-drilldowns.md
- docs/pantheon-handoffs/PKT-004-deployment-approval-drilldowns/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-004-deployment-approval-drilldowns.md
- docs/pantheon-handoffs/PKT-004-deployment-approval-drilldowns
- docs/examples/PKT-004-deployment-approval-drilldowns.json
