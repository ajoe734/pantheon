# APP-002-W5-SSE-LIVE BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-002-W5-SSE-LIVE` - Implement live SSE transports and reconciliation semantics
**Parent Owner**: Qwen
**Parent Reviewer**: Codex
**Parent Status**: `done` (review approved; completed)
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Qwen
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-12
**Last Updated**: 2026-04-12
**Review Status**: APPROVED (Qwen, 2026-04-12)

> Support artifact only. Does not modify canonical truth, L1 policy, or runtime/registry/governance implementations. Summarizes the Wave 5 SSE transport + reconciliation work and provides frontend handoff notes.

---

## 1. Parent Task Summary

Wave 5 introduces BFF-backed SSE streams and a frontend reconciliation layer to keep live UI state aligned.

**Acceptance criteria (from ai-status)**:
- `three_sse_streams_live`
- `reconnect_semantics_defined`
- `frontend_reconciliation_aligned`

**Primary artifacts in scope**:
- `services/control-plane/bff/main.py`
- `services/frontend/sse_reconciler.py`
- `services/frontend/adapter.py`
- `services/control-plane/bff/BFF_API_CONTRACT.md`

---

## 2. BFF SSE Transport (Implementation Snapshot)

### 2.1 SSE Endpoints (BFF)

| Stream | Endpoint | Notes |
|---|---|---|
| Runtime events | `GET /api/v1/runtime/{runtime_id}/events/stream` | Emits runtime-state changes. `last_event_id` replay supported. |
| Incident events | `GET /api/v1/incidents/stream` | Emits incident lifecycle changes. `last_event_id` replay supported. |
| Kill-switch updates | `GET /api/v1/kill-switch/updates` | Emits kill-switch activations/deactivations. `last_event_id` replay supported. |

**Headers** (BFF_API_CONTRACT §11.4, implemented in `main.py`):
- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- `Connection: keep-alive`
- `X-Accel-Buffering: no`

### 2.2 Event Buffer + Replay

- Per-stream in-memory buffers (`deque`, maxlen = 500) for runtime, incident, kill-switch events.
- `_replay_from()` replays events after `last_event_id`.
- If `last_event_id` is missing or no longer in buffer, the **full buffer** is replayed.
- Heartbeat comment (`: heartbeat`) is emitted every 30s to keep connections alive.

### 2.3 Event Format (Wire Shape)

BFF emits a **full event JSON** inside the SSE `data:` line (aligned to BFF_API_CONTRACT §11.3):

```json
{
  "id": "evt-20260410T030000Z-abc123",
  "type": "runtime_state_changed",
  "timestamp": "2026-04-10T03:00:00Z",
  "data": {
    "runtime_id": "r-001",
    "previous_state": "paper",
    "current_state": "canary",
    "surface_id": "RT-03"
  }
}
```

Event types:
- `runtime_state_changed`
- `incident_created`
- `incident_updated`
- `kill_switch_activated`
- `kill_switch_deactivated`

### 2.4 Internal Publish Helper (Testing / Admin Injection)

`POST /api/v1/internal/sse/publish` allows internal injection for smoke tests.
- Chooses stream based on `event_type` prefix (`runtime*`, `incident*`, `kill_switch*`).
- Not a production ingress; intended as a convenience hook.

---

## 3. Frontend Reconciliation Layer

### 3.1 Adapter (`services/frontend/adapter.py`)

- `transform_bff_event()` normalizes SSE payloads into `{id, type, timestamp, data}`.
- Legacy `ts/event/payload` shapes are still accepted for backward compatibility.
- URL helpers:
  - `runtime_sse_url(runtime_id, last_event_id=None)`
  - `incidents_sse_url(last_event_id=None)`
  - `kill_switch_sse_url(last_event_id=None)`

### 3.2 Reconciler (`services/frontend/sse_reconciler.py`)

Core reconciliation is deterministic + idempotent:
- `reconcile_ui_state()` applies one event, skips duplicates by `last_event_id`.
- `reconcile_event_sequence()` replays an ordered batch on reconnect.

Stream-specific handlers:
- `runtime_state_changed` → updates `state.runtimes[runtime_id]`
- `incident_created` / `incident_updated` → updates `state.incidents[incident_id]`
- `kill_switch_activated` / `kill_switch_deactivated` → updates `state.kill_switch`

### 3.3 Reconnect Manager

`SSEReconnectManager` provides:
- exponential backoff (`1s` → `30s`) + jitter
- `last_event_id` tracking
- `build_url()` appends `?last_event_id=` on reconnect

---

## 4. Frontend Integration Notes

### 4.1 Recommended Subscription Flow

1. Start EventSource with stream URL from `adapter.py`.
2. Parse `event.data` as JSON; feed into `transform_bff_event()`.
3. Apply to `SseReconciler` (or equivalent TS state machine).
4. On disconnect, use `SSEReconnectManager.build_url()` with stored `last_event_id`.

### 4.2 Example Runtime Stream (JS-style pseudo)

```js
const mgr = new SSEReconnectManager(runtimeUrl)
let source = new EventSource(mgr.build_url())

source.onopen = () => mgr.on_connect()
source.onmessage = (evt) => {
  const payload = JSON.parse(evt.data)
  const event = transform_bff_event(payload)
  mgr.record_event_id(event.id)
  reconciler.apply_event(event)
}

source.onerror = async () => {
  source.close()
  const delay = mgr.on_disconnect()
  await sleep(delay)
  source = new EventSource(mgr.build_url())
}
```

### 4.3 UI Gating

- Treat SSE as **incremental state**, not a replacement for initial reads.
- On reconnect with replay, apply all events in order; reconciler is idempotent.
- If replay includes unknown events, they are merged into the root state to avoid data loss.

---

## 5. Non-blocking Notes / Follow-Ups

1. **Runtime stream path includes `{runtime_id}` but the current generator does not filter by runtime_id**. Client should filter on `data.runtime_id` if multiple runtimes share a stream. Future update may enforce server-side filtering.
2. **Event buffers are in-memory**. Process restarts drop replay history; reconnect will only return recent buffer if available.
3. **Replay fallback returns full buffer** when `last_event_id` is missing. Reconciler idempotency handles duplicates, but UI should expect replays.
4. **Internal publish endpoint** is for testing/injection only; production bus integration remains out-of-scope for this slice.

---

## 6. Verification Checklist (Self-check)

| Check | Status | Evidence |
|---|---|---|
| Support-only artifact | PASS | File under `support/sidecars/` only |
| Canonical truth untouched | PASS | No edits to L1 / core runtime code |
| 3 SSE endpoints wired | PASS | `main.py` routes for runtime/incidents/kill-switch |
| Replay semantics present | PASS | `_replay_from()` + `last_event_id` query |
| SSE event shape aligned | PASS | `_sse_format()` emits full event JSON | 
| Frontend reconciliation aligned | PASS | `sse_reconciler.py` handlers + idempotency |
| Adapter URL helpers present | PASS | `adapter.py` stream URL builders |

---

## 7. Handoff Status

Review completed. Packet approved and ready for parent owner consumption.

---

## 8. Review Notes (Qwen)

**Review Decision**: APPROVED

**Verification Method**: All claims in sections 2–4 verified against actual implementation files:
- `services/control-plane/bff/main.py` — 3 SSE endpoints, deque buffers (maxlen=500), `_replay_from()`, `_sse_format()`, internal publish endpoint, SSE headers all confirmed.
- `services/frontend/sse_reconciler.py` — `reconcile_ui_state()` idempotency, `reconcile_event_sequence()`, stream-specific handlers, `SSEReconnectManager` with exponential backoff (1s→30s+jitter) all confirmed.
- `services/frontend/adapter.py` — `transform_bff_event()` legacy compatibility, 3 URL helper functions all confirmed.
- `services/control-plane/bff/BFF_API_CONTRACT.md` §11.3/§11.4 — event shape and headers aligned.

**Minor Notes** (non-blocking):
1. Buffers store `(event_id, event_dict)` tuples, not bare event dicts — descriptive gap only, no behavioral impact.
2. Runtime endpoint path includes `{runtime_id}` but no server-side filtering — already documented in §5 note 1.
3. Internal publish endpoint requires only read role — acceptable for testing-only scope.

**Status**: Packet approved. Handed to owner (Codex) for finalization to `done`.
