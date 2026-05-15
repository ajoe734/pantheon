# BFF-CONSOL-012 Sidecar: BFF and Frontend Handoff Packet

Task ID: BFF-CONSOL-012-SIDECAR-BFF-HANDOFF
Parent Task: BFF-CONSOL-012 - SSE real stream replay test
Helper Kind: bff_handoff_packet
Prepared by: Claude
Reviewer: Codex
Date: 2026-05-14
Mutates canonical truth: false

## Purpose

This packet gives downstream frontend and operator tooling work a support-only
reference for the SSE substrate contract that BFF-CONSOL-012 delivered. It does
not change L1 canonical truth, core contracts, runtime code, registry code, or
governance implementation.

BFF-CONSOL-012 added bounded-buffer backpressure regression tests for the BFF
SSE substrate. The tests verify replay window bounds, per-subscriber drop policy,
disconnect cleanup, per-aggregate ordering, and the `X-SSE-*` header policy
published to connecting clients.

## Parent Task Delivery Summary

BFF-CONSOL-012 is `done`. The delivery is durable in commits recorded in the
`feat/bff-consol-022-staging-strict-cutover` branch.

| Artifact | Path | Purpose |
|---|---|---|
| SSE backpressure tests | `services/control-plane/bff/tests/test_sse_backpressure.py` | 3-test regression suite for BFF-CONSOL-012 |
| Evidence file | `support/evidence/BFF-CONSOL-012-sse-backpressure.json` | Measured buffer bounds, drop counts, replay cursors |
| SSE substrate | `services/control-plane/bff/main.py` (lines ~18438–18700) | `_sse_stream`, `_publish_event`, `_replay_from`, `_handle_sse_stream`, `_sse_replay_headers` |

Verification from the parent closeout (from evidence file):

```
pytest services/control-plane/bff/tests/test_sse_backpressure.py -q
=> 3 passed in 5.61s

pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py \
       services/control-plane/bff/tests/test_sse_backpressure.py -q
=> 17 passed in 7.05s
```

## SSE Substrate Contract (Locked)

The following behavior is asserted by 17 tests and must not change without
corresponding test updates:

### Buffer Bounds

| Parameter | Value | Source |
|---|---|---|
| Replay buffer size (`_MAX_EVENTS`) | 500 events | `main.py:18438` |
| Per-subscriber queue maxsize | 1000 events | `main.py:18581` (`asyncio.Queue(maxsize=1000)`) |

### Drop Strategies

| Layer | Strategy | Behavior |
|---|---|---|
| Subscriber queue | **Newest-drop** | When `asyncio.Queue(maxsize=1000)` is full, `_publish_event` catches `QueueFull` and silently drops the incoming event for that slow subscriber. Oldest queued events are preserved. |
| Replay window | **Oldest-drop** | The buffer is `deque(maxlen=500)`, so publishing beyond the window evicts the oldest event IDs. |

### Replay Semantics

`_replay_from(buffer, last_event_id)` behavior:

- If `last_event_id` is `None` → replay the entire buffer.
- If `last_event_id` is found → replay all events **after** that ID.
- If `last_event_id` is no longer in the buffer (evicted) → raise `SseReplayUnavailableError`.

`_handle_sse_stream` converts `SseReplayUnavailableError` into HTTP 409 with error
code `SSE_REPLAY_UNAVAILABLE`:

```json
{
  "code": "SSE_REPLAY_UNAVAILABLE",
  "reason": "SSE_REPLAY_HISTORY_MISSING",
  "suggestion": "Resync canonical state via GET routes before reconnecting to the stream",
  "details": {
    "channel": "<channel>",
    "lastEventId": "<requested-id>",
    "replaySupported": true,
    "replayWindowEvents": 500,
    "replayStore": "in-memory",
    "resyncRoutes": ["<route-a>", "<route-b>"]
  }
}
```

### Disconnect Cleanup

`_sse_stream` appends the per-subscriber `asyncio.Queue` to `subscribers` on
enter and removes it in the `finally` block on client disconnect. The
backpressure test verifies `len(subscribers) == 0` within 1 second of stream
closure.

### Heartbeat

When no event arrives within 30 seconds, `_sse_stream` yields `: heartbeat\n\n`
(an SSE comment) to keep the HTTP connection alive. This is not a named event
and does not appear in the replay buffer.

### SseEventEnvelope Shape

All domain events published through `_publish_event` are serialized as
`SseEventEnvelope[dict]` (defined in `services/control-plane/bff/models.py`):

```python
class SseEventEnvelope(BaseModel, Generic[T]):
    id: str          # evt-<epoch>-<uuid8>
    type: str        # e.g. "approval.created"
    timestamp: str   # UTC ISO-8601, e.g. "2026-05-14T06:00:00Z"
    data: T          # domain payload dict
```

The serialized event is what appears in the SSE body (`data:` line) AND in the
replay buffer. Frontend clients must parse the full envelope from the `data:`
field.

### Per-Aggregate Ordering

The replay window preserves insertion order for all events. Within a single
aggregate, the `causal_parent_id` chain allows clients to detect gaps.
Replay guarantees sequence continuity only within the 500-event window; a 409
response means the client must fall back to a full resync rather than partial
replay.

### Response Headers

Every SSE stream response (except the frontend liveness BFF endpoint) includes:

| Header | Value | Purpose |
|---|---|---|
| `X-SSE-Channel` | `<channel-name>` | Which channel this stream belongs to |
| `X-SSE-Replay-Supported` | `"true"` | Client may send `Last-Event-ID` to replay |
| `X-SSE-Replay-Window-Events` | `"500"` | Replay buffer depth |
| `X-SSE-Buffer-Size` | `"500"` | Same as replay window |
| `X-SSE-Replay-Store` | `"in-memory"` | No durable event log behind this buffer |
| `X-SSE-Resync-Routes` | Comma-separated REST routes | Where to GET canonical state before reconnecting |

Resync routes per channel:

| Channel | Resync routes |
|---|---|
| `approval` | `/bff/approvals`, `/bff/v5/interventions` |
| `ask` | `/bff/agora/ask/sessions/{id}` |
| others | (none configured; resync route header omitted) |

### SSE Channel Catalog

Channels with replay-backed buffers (all use `_MAX_EVENTS = 500`):

```
approval, ask, artifact, runtime, mcp, skill, channel, tool, ranking,
rebalance, evolution, research, signal, inbox, journal, postmortem, loop,
sentinel, intervention, audit, system
```

## Frontend Integration Guidance

### Native EventSource vs fetch-based SSE clients

**Critical difference for reconnect/resync logic:**

| Client type | Can read response headers on reconnect? | Can detect 409 from `onerror`? |
|---|---|---|
| Native browser `EventSource` | No — `onerror` fires but headers and status code are not accessible | No — the browser hides HTTP status from `onerror` callbacks |
| `fetch`-based SSE client | Yes — read `X-SSE-*` headers from the initial response | Yes — `response.status === 409` is readable |

**Implications for frontend engineers:**

1. If you use native `EventSource` and the server returns 409
   (`SSE_REPLAY_UNAVAILABLE`), the `onerror` callback fires but you cannot
   inspect the status code or headers. You must rely on channel metadata or a
   bootstrap REST call to detect that a resync is needed.

2. If you use a `fetch`-based SSE wrapper (e.g., a custom `ReadableStream`
   decoder), you CAN read the `X-SSE-*` headers from the initial response and
   detect 409 before starting to consume the stream. This is the recommended
   approach for clients that need to handle replay gaps gracefully.

3. For native `EventSource` clients, implement a **bootstrap mapping**: on page
   load (or after any `onerror`), call the channel's resync route (listed in
   `X-SSE-Resync-Routes` on the previous connection, or hard-coded from the
   table above) to pull canonical state before reconnecting to the stream.

### Reconnect Flow (Recommended)

```
1. GET /bff/approvals (or channel-specific resync route)
   → capture current canonical state
2. Connect to SSE endpoint with Last-Event-ID header
   → if server returns 409 (fetch client) or onerror fires (EventSource):
     a. Re-run step 1 to re-hydrate canonical state
     b. Reconnect without Last-Event-ID to start from current buffer head
```

### Operator Journey Integration

BFF-CONSOL-012 does not add new operator-visible routes or UI surfaces. The
SSE substrate is a shared transport layer. Frontend and operator tooling should
treat the backpressure limits as runtime constants:

- Do not assume the subscriber queue is unbounded.
- Do not retry a 409 with the same `Last-Event-ID` — the event is gone.
- Treat `: heartbeat` comments as no-ops; do not parse them as events.

## Query Gap Inventory

No new BFF query gaps were opened by BFF-CONSOL-012. The gaps that exist for
the SSE substrate are upstream delivery concerns:

| Gap | Status | Notes |
|---|---|---|
| Durable event log behind SSE channels | Not delivered; out of scope for BFF-CONSOL-012 | In-memory only; process restart loses buffer |
| SSE auth for native EventSource clients | Out of scope | Browser cannot attach `Authorization` header to EventSource; cookie-backed path not yet built |
| Resync for channels without a configured resync route | Out of scope | Channels without `_SSE_RESYNC_ROUTES` entries have no structured fallback |

## Evidence Reference

`support/evidence/BFF-CONSOL-012-sse-backpressure.json` contains:

- Measured buffer bounds and drop counts
- Replay cursor sequence numbers used in tests
- Drop strategy evidence strings
- Full `X-SSE-*` header set
- Ordering policy alignment reference (`EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`)

## Sidecar Boundary

This packet is a support-only artifact. It does not:

- Modify `main.py`, `models.py`, or any runtime code.
- Change L1 policy documents.
- Supersede the parent task BFF-CONSOL-012.
- Introduce new canonical contracts.

The parent task owner decides whether to absorb any part of this packet into
the main-line canonical docs.
