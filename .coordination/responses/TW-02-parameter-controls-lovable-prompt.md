Build the `TW-02-parameter-controls` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/TW-02-parameter-controls-bff-gap.yaml` using `.coordination/requests/TW-02-parameter-controls-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `parameter-controls`.
Workbench: `trainer-workbench`.
Screen ID: `screen-parameter-controls`.
Allowed endpoints:
- GET /api/v1/trainer/sessions/{session_id}/controls
- POST /api/v1/trainer/sessions/{session_id}/patch
Published Pantheon dependencies:
- .coordination/responses/TW-02-parameter-controls-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Parameter Controls surface in front-ai-trading-system
- use only the existing BFF client
- do not add raw fetch calls in component files
- render control metadata, patch acceptance, rejection, and diff responses from Pantheon BFF only
- respect allowedActions.canPatchControls and degraded-state gating exactly as published
- do not invent control ranges, clipping, or local diff summaries
- emit a bff-gap handoff if any required field is absent
- publish ui-done and frontend-feedback from one Git-visible commit
Required feedback bundle:
- docs/pantheon-feedback/TW-02-parameter-controls/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/TW-02-parameter-controls/API_GAP_REQUESTS.json
- docs/pantheon-feedback/TW-02-parameter-controls/UI_DECISIONS.md
- docs/pantheon-feedback/TW-02-parameter-controls/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/TW-02-parameter-controls-ui-done.yaml` using `.coordination/requests/TW-02-parameter-controls-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/TW-02-parameter-controls-frontend-feedback.yaml` using `.coordination/requests/TW-02-parameter-controls-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/TW-02-parameter-controls.md
- docs/pantheon-handoffs/TW-02-parameter-controls/FRONTEND_CHANGE_SPEC.md
- docs/bff/TW-02-parameter-controls.md
- docs/pantheon-handoffs/TW-02-parameter-controls
- docs/examples/TW-02-parameter-controls.json
