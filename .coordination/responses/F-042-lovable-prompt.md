Build the `F-042` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has published a BFF-gap resolution for this screen. Apply the following
integration fixes before implementing the screen components:
- `src/lib/bffClient.ts`: send an `Authorization: Bearer <token>` header on all stateful requests
- `src/lib/bffClient.ts`: parse the standard `errors` array envelope (field name is `errors`, not `error`)
- `src/pages/promotion/types.ts`: use `'error'` for the surface status variant (not `'unavailable'`)
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/F-042-bff-gap.yaml` using `.coordination/requests/F-042-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `promotion-review`.
Workbench: `governance-review`.
Screen ID: `screen-governance-promotion-review`.
Allowed endpoints:
- GET /api/v1/operator/deployment-review/{plan_id}
- POST /api/v1/operator/commands
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
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/F-042-ui-done.yaml` using `.coordination/requests/F-042-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/F-042-promotion-review.md
- docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md
- docs/bff/F-042-promotion-review.md
- docs/pantheon-handoffs/F-042
- docs/examples/F-042-review-page.json
