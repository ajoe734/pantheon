Build the `RW-04-experiment-launch` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/RW-04-experiment-launch-bff-gap.yaml` using `.coordination/requests/RW-04-experiment-launch-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `experiment-launch`.
Allowed endpoints:
- POST /api/v1/experiments/launch
- GET /api/v1/experiments
- GET /api/v1/experiments/{experiment_id}
- POST /api/v1/experiments/{experiment_id}/cancel
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
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/RW-04-experiment-launch-ui-done.yaml` using `.coordination/requests/RW-04-experiment-launch-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/bff/RW-04-experiment-launch.md
- docs/pantheon-handoffs/RW-04-experiment-launch
- docs/examples/RW-04-experiment-launch.json
