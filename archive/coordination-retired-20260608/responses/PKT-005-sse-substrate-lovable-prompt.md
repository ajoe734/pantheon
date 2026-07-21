Build the `PKT-005-sse-substrate` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-005-sse-substrate-bff-gap.yaml` using `.coordination/requests/PKT-005-sse-substrate-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `sse-reconciliation-substrate`.
Workbench: `operator-console`.
Screen ID: `surface-operator-sse-reconciliation`.
Allowed endpoints:
- GET /api/v1/runtime/{runtime_id}/events/stream
- GET /api/v1/incidents/stream
- GET /api/v1/kill-switch/updates
Published Pantheon dependencies:
- .coordination/responses/PKT-005-sse-substrate-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- implement a shared SSE client layer with reconnect manager (exponential backoff 1s-30s, last_event_id tracking)
- implement idempotent event reconciler that skips already-applied events by id
- subscribe to relevant stream(s) on screen mount; unsubscribe on unmount
- do not use SSE as the initial data source; always fetch the composed view first, then apply SSE events on top
- filter runtime events by data.runtime_id client-side (server-side filtering not yet active)
- show SSE connection state (connected / reconnecting / disconnected) in the screen footer
- do not add raw EventSource calls in component files; all stream wiring must go through the shared client layer
- if any required event data field is missing, emit a bff-gap handoff
Required feedback bundle:
- docs/pantheon-feedback/PKT-005-sse-substrate/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-005-sse-substrate/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-005-sse-substrate/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-005-sse-substrate/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-005-sse-substrate-ui-done.yaml` using `.coordination/requests/PKT-005-sse-substrate-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-005-sse-substrate-frontend-feedback.yaml` using `.coordination/requests/PKT-005-sse-substrate-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-005-sse-substrate.md
- docs/pantheon-handoffs/PKT-005-sse-substrate/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-005-sse-substrate.md
- docs/pantheon-handoffs/PKT-005-sse-substrate
- docs/examples/PKT-005-sse-substrate.json
