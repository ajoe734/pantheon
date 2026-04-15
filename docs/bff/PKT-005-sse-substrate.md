# PKT-005 SSE Reconciliation Substrate BFF Contract

## Purpose

Document the three live BFF SSE endpoints, their event wire format, replay semantics, and heartbeat behaviour so that the Operator Console front end can implement a shared SSE client without re-deriving these invariants from the BFF implementation.

## SSE Endpoints

### Runtime events stream

```
GET /api/v1/runtime/{runtime_id}/events/stream
```

- Path parameter: `runtime_id` — the runtime to watch. Note: server-side filtering by `runtime_id` is not yet implemented; the stream returns all runtime events. Clients must filter on `event.data.runtime_id`.
- Query parameter: `last_event_id` (optional) — resume replay from this event ID.

Required response headers:

| Header | Value |
|---|---|
| `Content-Type` | `text/event-stream` |
| `Cache-Control` | `no-cache` |
| `Connection` | `keep-alive` |
| `X-Accel-Buffering` | `no` |

### Incident events stream

```
GET /api/v1/incidents/stream
```

- Query parameter: `last_event_id` (optional)

Headers: same as above.

### Kill-switch updates stream

```
GET /api/v1/kill-switch/updates
```

- Query parameter: `last_event_id` (optional)

Headers: same as above.

---

## Event Wire Format

Each event is emitted as a single SSE `data:` line containing a JSON string:

```
id: evt-20260414T120000Z-abc123
data: {"id":"evt-20260414T120000Z-abc123","type":"runtime_state_changed","timestamp":"2026-04-14T12:00:00Z","data":{"runtime_id":"r-001","previous_state":"paper","current_state":"canary","surface_id":"RT-03"}}
```

Top-level event fields:

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Unique event ID used for `last_event_id` replay |
| `type` | string | yes | One of the event type values below |
| `timestamp` | ISO 8601 string | yes | Wall-clock time the event was generated |
| `data` | object | yes | Event-type-specific payload |

### `runtime_state_changed`

| Field | Type |
|---|---|
| `runtime_id` | string |
| `previous_state` | string (`paper`, `canary`, `live`, `paused`, `halted`) |
| `current_state` | string (same enum) |
| `surface_id` | string |

### `incident_created`

| Field | Type |
|---|---|
| `incident_id` | string |
| `title` | string |
| `severity` | string (`critical`, `high`, `medium`, `low`) |
| `artifact_id` | string |

### `incident_updated`

| Field | Type |
|---|---|
| `incident_id` | string |
| `status` | string (`active`, `investigating`, `mitigated`, `resolved`) |
| `updated_at` | ISO 8601 string |

### `kill_switch_activated`

| Field | Type |
|---|---|
| `scope` | string (`all`, `persona:{id}`, `pool:{id}`) |
| `activated_by` | string (operator ID) |
| `activated_at` | ISO 8601 string |

### `kill_switch_deactivated`

| Field | Type |
|---|---|
| `scope` | string |
| `deactivated_by` | string |
| `deactivated_at` | ISO 8601 string |

---

## Heartbeat

The BFF emits a heartbeat comment every 30 seconds to keep the TCP connection alive through proxies:

```
: heartbeat
```

This is an SSE comment line (begins with `:`). It carries no `id`, no `event` type, and no `data`. Clients must ignore it.

---

## Replay and Buffer Semantics

- Each stream maintains an in-memory circular buffer of 500 events (`deque(maxlen=500)`).
- When `last_event_id` is provided and found in the buffer, all events after that ID are replayed in order.
- When `last_event_id` is not provided, or is not found in the buffer (e.g., after a BFF process restart), the full buffer contents are replayed.
- Replay events use the same wire format as live events. The client reconciler must be idempotent (skip events already applied by `last_event_id`).

---

## Internal Test Endpoint (Non-Production)

```
POST /api/v1/internal/sse/publish
```

Injects a synthetic event into the correct stream buffer based on `event_type` prefix:
- `runtime*` → runtime stream
- `incident*` → incident stream
- `kill_switch*` → kill-switch stream

This endpoint is for smoke tests and admin injection only. It must not appear in production UI code or be called from the front-end client.

---

## BFF Gap Conditions

Emit a `bff-gap` handoff if:
- A required top-level field (`id`, `type`, `timestamp`, `data`) is absent from an event.
- A required `data` sub-field for the received `type` is absent.
- The stream returns a non-2xx status on initial connection (not a transient network error).

## Write Actions

None. All SSE streams are read-only event feeds.
