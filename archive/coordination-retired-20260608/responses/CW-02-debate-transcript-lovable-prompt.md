Build the `CW-02-debate-transcript` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/CW-02-debate-transcript-bff-gap.yaml` using `.coordination/requests/CW-02-debate-transcript-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `consultation-debate-transcript`.
Workbench: `consultation-workbench`.
Screen ID: `screen-consultation-debate-transcript`.
Allowed endpoints:
- GET /api/v1/consultations/{session_id}/transcript
Published Pantheon dependencies:
- .coordination/responses/CW-02-debate-transcript-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Debate Transcript surface in front-ai-trading-system
- use only the existing BFF client
- do not add raw fetch calls in component files
- preserve backend ordering exactly by sequence_no
- render actor identity, redaction, and inline evidence refs from the BFF payload only
- do not turn the transcript into a local chat-style state machine
- emit a bff-gap handoff if any required field is absent
- publish ui-done and frontend-feedback from one Git-visible commit
Required feedback bundle:
- docs/pantheon-feedback/CW-02-debate-transcript/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/CW-02-debate-transcript/API_GAP_REQUESTS.json
- docs/pantheon-feedback/CW-02-debate-transcript/UI_DECISIONS.md
- docs/pantheon-feedback/CW-02-debate-transcript/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/CW-02-debate-transcript-ui-done.yaml` using `.coordination/requests/CW-02-debate-transcript-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/CW-02-debate-transcript-frontend-feedback.yaml` using `.coordination/requests/CW-02-debate-transcript-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/pantheon-handoffs/CW-02-debate-transcript/FRONTEND_CHANGE_SPEC.md
- docs/bff/CW-02-debate-transcript.md
- docs/pantheon-handoffs/CW-02-debate-transcript
- docs/examples/CW-02-debate-transcript.json
