Build the `PKT-008-rollback-review` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-008-rollback-review-bff-gap.yaml` using `.coordination/requests/PKT-008-rollback-review-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `governance-rollback-review`.
Workbench: `governance-workbench`.
Screen ID: `screen-governance-rollback-review`.
Allowed endpoints:
- GET /api/v1/operator/rollback-review/{rollback_id}
- POST /api/v1/operator/commands
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Governance Rollback Review screen with rollback identity header, scope summary, position impact table, affected bindings panel, and trigger evidence drawer
- use only the existing BFF client
- do not add raw fetch calls in component files
- do not derive position impact from raw binding or telemetry data; all position impact fields come from position_impact[] in the BFF response
- render all CTAs from backend-shaped allowedActions only
- disable the Approve CTA when meta.surfaces.position_data is degraded or unavailable regardless of allowedActions.canApproveRollback
- show stale-data badge and unknown impact message for position_impact rows where position_data_stale is true
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-008-rollback-review-ui-done.yaml` using `.coordination/requests/PKT-008-rollback-review-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-008-rollback-review.md
- docs/pantheon-handoffs/PKT-008-rollback-review/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-008-rollback-review.md
- docs/pantheon-handoffs/PKT-008-rollback-review
- docs/examples/PKT-008-rollback-review.json
