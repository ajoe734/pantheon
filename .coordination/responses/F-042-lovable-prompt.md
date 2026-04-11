Build the `F-042` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/F-042-bff-gap.yaml` using `.coordination/requests/F-042-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `promotion-review`.
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
- https://lovable.dev/projects/140c41d5-9cd8-4d6b-ba02-66d5941d0dbe
