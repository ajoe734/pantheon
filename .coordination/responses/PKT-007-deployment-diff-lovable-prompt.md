Build the `PKT-007-deployment-diff` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-007-deployment-diff-bff-gap.yaml` using `.coordination/requests/PKT-007-deployment-diff-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `governance-deployment-diff`.
Workbench: `governance-workbench`.
Screen ID: `screen-governance-deployment-diff`.
Allowed endpoints:
- GET /api/v1/operator/deployment-diff/{plan_id}
- POST /api/v1/operator/commands
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Governance Deployment Diff screen with plan identity header, change summary rail, and field diff table
- use only the existing BFF client
- do not add raw fetch calls in component files
- do not construct the diff from raw deployment plan fields; all diff entries must come from changes[] in the BFF response
- render all CTAs from backend-shaped allowedActions only
- render risk tier labels as supplied by the BFF; do not reclassify
- disable the approval CTA and show unavailable-data message when meta.surfaces.deployment_diff is unavailable
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-007-deployment-diff-ui-done.yaml` using `.coordination/requests/PKT-007-deployment-diff-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-007-deployment-diff.md
- docs/pantheon-handoffs/PKT-007-deployment-diff/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-007-deployment-diff.md
- docs/pantheon-handoffs/PKT-007-deployment-diff
- docs/examples/PKT-007-deployment-diff.json
