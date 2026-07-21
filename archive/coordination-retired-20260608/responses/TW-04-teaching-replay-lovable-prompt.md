Build the `TW-04-teaching-replay` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/TW-04-teaching-replay-bff-gap.yaml` using `.coordination/requests/TW-04-teaching-replay-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `teaching-replay`.
Workbench: `trainer-workbench`.
Screen ID: `screen-teaching-replay`.
Allowed endpoints:
- GET /api/v1/trainer/replay
- GET /api/v1/trainer/replay/{session_id}
- POST /api/v1/trainer/sessions/{session_id}/commit
- POST /api/v1/trainer/sessions/{session_id}/discard
Published Pantheon dependencies:
- .coordination/responses/TW-04-teaching-replay-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- list replayable sessions from GET /api/v1/trainer/replay with persona_id (required), status, pagination params
- render replay detail from GET /api/v1/trainer/replay/{session_id}
- render ordered TeachingEvent[] timeline from the detail response events[] array
- submit commit through POST /api/v1/trainer/sessions/{session_id}/commit via the existing BFF client action layer only
- submit discard through POST /api/v1/trainer/sessions/{session_id}/discard via the existing BFF client action layer only
- surface commit/discard CTAs only when allowedActions.canCommit / allowedActions.canDiscard is true
- resolve evidence links from BFF-provided `event.evidence_ref` objects — do not construct them client-side
- render replay_resolution.state correctly (pending_decision / committed / discarded / not_applicable)
- emit a bff-gap handoff if any required field is absent
Required feedback bundle:
- docs/pantheon-feedback/TW-04-teaching-replay/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/TW-04-teaching-replay/API_GAP_REQUESTS.json
- docs/pantheon-feedback/TW-04-teaching-replay/UI_DECISIONS.md
- docs/pantheon-feedback/TW-04-teaching-replay/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/TW-04-teaching-replay-ui-done.yaml` using `.coordination/requests/TW-04-teaching-replay-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/TW-04-teaching-replay-frontend-feedback.yaml` using `.coordination/requests/TW-04-teaching-replay-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/TW-04-teaching-replay.md
- docs/pantheon-handoffs/TW-04-teaching-replay/FRONTEND_CHANGE_SPEC.md
- docs/bff/TW-04-teaching-replay.md
- docs/pantheon-handoffs/TW-04-teaching-replay
- docs/examples/TW-04-teaching-replay.json
