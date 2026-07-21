Build the `RW-04-experiment-launch` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/RW-04-experiment-launch-bff-gap.yaml` using `.coordination/requests/RW-04-experiment-launch-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `experiment-launch`.
Workbench: `research-workbench`.
Screen ID: `screen-experiment-launch`.
Allowed endpoints:
- POST /api/v1/experiments/launch
- GET /api/v1/experiments
- GET /api/v1/experiments/{experiment_id}
- POST /api/v1/experiments/{experiment_id}/cancel
Published Pantheon dependencies:
- .coordination/responses/RW-04-experiment-launch-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- launch experiments via POST /api/v1/experiments/launch only
- poll async state through GET /api/v1/experiments/{experiment_id}
- render status badges from the published state machine vocabulary exactly
- surface cancel CTA only when allowedActions.canCancel is true
- do not synthesize experiment state from ticker data or local timers
- emit a bff-gap handoff if any required field is absent
Required feedback bundle:
- docs/pantheon-feedback/RW-04-experiment-launch/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/RW-04-experiment-launch/API_GAP_REQUESTS.json
- docs/pantheon-feedback/RW-04-experiment-launch/UI_DECISIONS.md
- docs/pantheon-feedback/RW-04-experiment-launch/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/RW-04-experiment-launch-ui-done.yaml` using `.coordination/requests/RW-04-experiment-launch-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml` using `.coordination/requests/RW-04-experiment-launch-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/pantheon-handoffs/RW-04-experiment-launch/FRONTEND_CHANGE_SPEC.md
- docs/bff/RW-04-experiment-launch.md
- docs/pantheon-handoffs/RW-04-experiment-launch
- docs/examples/RW-04-experiment-launch.json
