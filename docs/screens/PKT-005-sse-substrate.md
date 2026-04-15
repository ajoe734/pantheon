# PKT-005 SSE Reconciliation Substrate

## Classification

- Workbench: Operator Console (cross-cutting)
- Surface ID: `surface-operator-sse-reconciliation`
- Feature ID: `PKT-005-sse-substrate`
- Packet status: ready

## User Goal

Keep all live Operator Console screens in sync with Pantheon's runtime, incident, and kill-switch state without full-page refreshes. The SSE substrate is a shared client-side primitive that every Operator Console screen uses — it is not a standalone page.

## SSE Streams

Three BFF-backed SSE streams are live:

| Stream name | Endpoint | Primary consumers |
|---|---|---|
| Runtime events | `GET /api/v1/runtime/{runtime_id}/events/stream` | Deployment Review (`PKT-001`), Incident Response (`PKT-002`) |
| Incident events | `GET /api/v1/incidents/stream` | Incident Response (`PKT-002`), Post-Incident Review (`PKT-003`) |
| Kill-switch events | `GET /api/v1/kill-switch/updates` | Incident Response (`PKT-002`) |

## Event Wire Format

Every event is a JSON object emitted as a single SSE `data:` line:

```json
{
  "id": "evt-20260414T120000Z-abc123",
  "type": "<event_type>",
  "timestamp": "<ISO 8601>",
  "data": { ... }
}
```

Event types and their `data` shape:

| Event type | Data fields |
|---|---|
| `runtime_state_changed` | `runtime_id`, `previous_state`, `current_state`, `surface_id` |
| `incident_created` | `incident_id`, `title`, `severity`, `artifact_id` |
| `incident_updated` | `incident_id`, `status`, `updated_at` |
| `kill_switch_activated` | `scope`, `activated_by`, `activated_at` |
| `kill_switch_deactivated` | `scope`, `deactivated_by`, `deactivated_at` |

## Connection and Reconnect Rules

### Initial connection

1. Start an `EventSource` with the stream URL.
2. On `open`, record connection state and reset the backoff timer.
3. Parse each `message` event `data` field as JSON.
4. Feed the parsed event into the reconciler.

### Reconnect with replay

On disconnect (`error` event):

1. Close the current `EventSource`.
2. Wait for the current backoff duration (start at 1 s, double on each failure, cap at 30 s, add ±20 % jitter).
3. Re-open `EventSource` with `?last_event_id=<stored_event_id>` appended to the URL.
4. On reconnect, the BFF replays all buffered events after `last_event_id`. Apply them in order via the reconciler. The reconciler is idempotent — duplicate events are skipped.
5. If `last_event_id` is absent or no longer in the BFF buffer (process restart), the full 500-event buffer is replayed. The reconciler handles duplicates transparently.

### Heartbeat handling

The BFF emits `: heartbeat` comments every 30 seconds. These are SSE comment lines and must not be treated as data events. `EventSource` receives them as empty events with no `data`; ignore them.

## Reconciliation Rules

- SSE events are **incremental updates**, not full state replacements. The screen must initialise state from the BFF composed view read on mount, then apply SSE events on top.
- **Banner state is backend authority.** SSE events do not carry `meta` snapshots and must not be used to re-derive or update the degradation banner. The banner reflects the most recently received `meta` from an initial BFF read or a subsequent full fetch. If an SSE event indicates a significant state change that may affect data freshness, request a fresh composed view explicitly — do not infer a new banner state from event payload fields.
- If a `kill_switch_activated` event arrives, disable all runtime action buttons immediately, regardless of the current composed view staleness state. This is CTA gating only — it does not change the degradation banner.
- If the screen is not yet mounted (race: event arrives before initial read), buffer the event and apply it once the composed view response is received.

## Caveats

1. **Server-side runtime filtering is not yet implemented.** The runtime stream at `GET /api/v1/runtime/{runtime_id}/events/stream` does not filter events by `runtime_id` on the BFF side. Clients must filter on `event.data.runtime_id` when more than one runtime is relevant.
2. **In-memory buffers only.** BFF process restarts drop replay history. Clients must handle receiving only a partial or empty replay buffer on reconnect.
3. **Internal publish endpoint is testing-only.** `POST /api/v1/internal/sse/publish` is for smoke tests and internal injection only; it must not appear in production UI code.

## Interaction Rules

- Subscribe on screen mount; unsubscribe (close `EventSource`) on unmount.
- Do not use SSE as the sole data source on initial load. Always fetch the composed view first.
- If no SSE event has been received for 60 seconds on a screen where events are expected (e.g., an active incident), show a "Real-time updates may be delayed" note in the screen footer. This does not trigger the degradation banner.
- If `EventSource` emits `error` with `readyState = 2` (closed), begin reconnect logic. Do not retry immediately.

## Acceptance

- Runtime, incident, and kill-switch SSE streams each connect successfully on screen mount.
- Events are applied to UI state without a full-page refresh.
- `last_event_id` is stored and passed on reconnect.
- Exponential backoff (1 s – 30 s with jitter) is implemented for reconnect.
- Replayed events do not produce duplicate UI updates (reconciler idempotency).
- `kill_switch_activated` event immediately disables runtime action buttons on the Incident Response screen.
- SSE connection state (connected / reconnecting / disconnected) is shown in the screen footer.
- No raw `fetch` or `EventSource` calls appear in component files — all stream wiring uses the shared SSE client layer.
- If any required `event.data` field is missing, emit a `bff-gap` handoff instead of silently ignoring the event.
