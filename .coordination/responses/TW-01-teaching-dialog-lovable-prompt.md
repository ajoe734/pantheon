Build the `TW-01-teaching-dialog` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/TW-01-teaching-dialog-bff-gap.yaml` using `.coordination/requests/TW-01-teaching-dialog-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `teaching-dialog`.
Workbench: `trainer-workbench`.
Screen ID: `screen-teaching-dialog`.
Allowed endpoints:
- POST /api/v1/trainer/sessions
- GET /api/v1/trainer/sessions
- GET /api/v1/trainer/sessions/{session_id}
- POST /api/v1/trainer/sessions/{session_id}/message
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
Completion handoff:
- When the refreshed UI cycle is ready, publish both `.coordination/requests/TW-01-teaching-dialog-ui-done.yaml` and `.coordination/requests/TW-01-teaching-dialog-frontend-feedback.yaml` from the same final front commit as the TW-01 feedback bundle and refreshed UI files. Keep both `source_commit` values aligned to that immutable publication commit before syncing the handoff back to GitHub.
References:
- docs/screens/TW-01-teaching-dialog.md
- docs/pantheon-handoffs/TW-01-teaching-dialog/FRONTEND_CHANGE_SPEC.md
- docs/bff/TW-01-teaching-dialog.md
- docs/pantheon-handoffs/TW-01-teaching-dialog
- docs/examples/TW-01-teaching-dialog.json
