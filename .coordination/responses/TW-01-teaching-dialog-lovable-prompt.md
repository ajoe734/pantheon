Build the `TW-01-teaching-dialog` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/TW-01-teaching-dialog-bff-gap.yaml` using `.coordination/requests/TW-01-teaching-dialog-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `teaching-dialog`.
Workbench: `trainer-workbench`.
Screen ID: `screen-teaching-dialog`.
Allowed endpoints:
- POST /api/v1/trainer/sessions
- GET /api/v1/trainer/sessions
- GET /api/v1/trainer/sessions/{session_id}
- POST /api/v1/trainer/sessions/{session_id}/message
Published Pantheon dependencies:
- .coordination/responses/TW-01-teaching-dialog-contract-ready.yaml
- .coordination/responses/TW-01-teaching-dialog-backend-delivery.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- remove the pending-BFF placeholder from /trainer/sessions and /trainer/sessions/:session_id now that Pantheon has confirmed the TW-01 routes live
- create sessions through POST /api/v1/trainer/sessions only
- fetch trainer-session list from GET /api/v1/trainer/sessions only
- fetch trainer-session detail and transcript from GET /api/v1/trainer/sessions/{session_id} only
- send coaching messages through POST /api/v1/trainer/sessions/{session_id}/message only
- preserve the full published create shape, including optional context_refs[] when the operator supplies them
- render transcript rows from backend TeachingEvent objects only; do not create local optimistic transcript state
- use links.workbench_detail from the BFF for navigation when present
- gate the message composer by allowedActions.canSendMessage only
- do not substitute Persona teaching-history responses for Trainer dialog routes
- show the non-dismissable PKT-005 degradation banner when meta.surfaces.trainer_dialog is degraded or unavailable
- do not expose pause, complete, or abandon CTAs until a later Trainer packet publishes those write routes
- publish ui-done and frontend-feedback from one Git-visible commit that contains the TW-01 request pair, feedback bundle, and final UI files
- emit a bff-gap handoff if any required field is absent
Required feedback bundle:
- docs/pantheon-feedback/TW-01-teaching-dialog/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/TW-01-teaching-dialog/API_GAP_REQUESTS.json
- docs/pantheon-feedback/TW-01-teaching-dialog/UI_DECISIONS.md
- docs/pantheon-feedback/TW-01-teaching-dialog/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/TW-01-teaching-dialog-ui-done.yaml` using `.coordination/requests/TW-01-teaching-dialog-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/TW-01-teaching-dialog-frontend-feedback.yaml` using `.coordination/requests/TW-01-teaching-dialog-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/TW-01-teaching-dialog.md
- docs/pantheon-handoffs/TW-01-teaching-dialog/FRONTEND_CHANGE_SPEC.md
- docs/bff/TW-01-teaching-dialog.md
- docs/pantheon-handoffs/TW-01-teaching-dialog
- docs/examples/TW-01-teaching-dialog.json
