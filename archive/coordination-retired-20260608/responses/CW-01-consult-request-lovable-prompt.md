Build the `CW-01-consult-request` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/CW-01-consult-request-bff-gap.yaml` using `.coordination/requests/CW-01-consult-request-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `consult-request`.
Workbench: `consultation-workbench`.
Screen ID: `screen-consult-request`.
Allowed endpoints:
- POST /api/v1/consult/requests
- GET /api/v1/consult/requests
- GET /api/v1/consult/requests/{request_id}
- POST /api/v1/consult/requests/{request_id}/cancel
Published Pantheon dependencies:
- .coordination/responses/CW-01-consult-request-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the request composer from the published create contract only
- build the request list and detail pages from the CW-01 BFF routes only
- render request-to-session status from linked_session_id plus request_to_session_status exactly as supplied
- render the cancel CTA only when allowedActions.canCancel is true
- emit a bff-gap handoff if any required field is absent
Required feedback bundle:
- docs/pantheon-feedback/CW-01-consult-request/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/CW-01-consult-request/API_GAP_REQUESTS.json
- docs/pantheon-feedback/CW-01-consult-request/UI_DECISIONS.md
- docs/pantheon-feedback/CW-01-consult-request/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/CW-01-consult-request-ui-done.yaml` using `.coordination/requests/CW-01-consult-request-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/CW-01-consult-request-frontend-feedback.yaml` using `.coordination/requests/CW-01-consult-request-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/CW-01-consult-request.md
- docs/pantheon-handoffs/CW-01-consult-request/FRONTEND_CHANGE_SPEC.md
- docs/bff/CW-01-consult-request.md
- docs/pantheon-handoffs/CW-01-consult-request
- docs/examples/CW-01-consult-request.json
