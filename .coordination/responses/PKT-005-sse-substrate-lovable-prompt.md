Build the `PKT-005-sse-substrate` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-005-sse-substrate-bff-gap.yaml` using `.coordination/requests/PKT-005-sse-substrate-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `sse-reconciliation-substrate`.
Workbench: `operator-console`.
Screen ID: `surface-operator-sse-reconciliation`.
Allowed endpoints:
- GET /api/v1/runtime/{runtime_id}/events/stream
- GET /api/v1/incidents/stream
- GET /api/v1/kill-switch/updates
Constraints:
- use existing bff client only; add SSE handling inside the shared client layer, not in component files
- do not add raw EventSource calls in component files
- do not import demo providers
- if any required event data field is absent, emit a bff-gap handoff instead of silently discarding the event
- SSE must be additive on top of the initial composed view read; never use SSE as the sole data source on mount
- reconnect must use exponential backoff (1s to 30s with jitter) with last_event_id replay
- reconciler must be idempotent and must skip already-applied events by event.id
Acceptance:
- shared SSE client is implemented with reconnect manager and last_event_id replay
- runtime stream is subscribed only after the host screen's initial composed view read resolves, and runtime events are filtered client-side by event.data.runtime_id
- incident stream is subscribed on Incident Response and Post-Incident Review screens after the initial composed view load
- kill-switch stream is subscribed on Incident Response surfaces after the initial composed view load
- events received before the initial composed view is ready are buffered and applied in order once the screen state is hydrated
- replayed events do not produce duplicate UI updates
- kill_switch_activated immediately disables runtime action buttons on Incident Response without re-deriving degradation banner state from SSE payloads
- SSE connection state is shown in the host screen footer as connected, reconnecting, or disconnected
- no raw EventSource or raw fetch calls appear in component files
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-005-sse-substrate-ui-done.yaml` using `.coordination/requests/PKT-005-sse-substrate-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-005-sse-substrate.md
- docs/pantheon-handoffs/PKT-005-sse-substrate/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-005-sse-substrate.md
- docs/pantheon-handoffs/PKT-005-sse-substrate
- docs/examples/PKT-005-sse-substrate.json
