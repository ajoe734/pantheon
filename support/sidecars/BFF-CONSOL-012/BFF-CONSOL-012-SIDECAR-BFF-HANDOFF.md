# BFF-CONSOL-012 Sidecar: BFF and Frontend Handoff Packet

Task ID: BFF-CONSOL-012-SIDECAR-BFF-HANDOFF
Parent Task: BFF-CONSOL-012 - SSE backpressure & unbounded buffer test
Helper Kind: bff_handoff_packet
Prepared by: Codex
Reviewer: Codex2
Date: 2026-05-14
Mutates canonical truth: false
Provenance: refreshed from the tracked support packet after the sidecar was
auto-reassigned to Codex.

## Purpose

This packet gives downstream frontend and operator tooling work a support-only
reference for the SSE backpressure contract that BFF-CONSOL-012 verified and
locked. It does not change L1 canonical truth, core contracts, runtime code,
registry code, or governance implementation.

BFF-CONSOL-012 added three focused regression tests and a measurement evidence
file. The backend SSE infrastructure in `services/control-plane/bff/main.py` is
now covered by an explicit upper-bound contract that all future frontend SSE
consumers must respect.

## Parent Task Delivery Summary

BFF-CONSOL-012 is `done`. Commit `1b088e43` on `backend-dev-publish-20260429`
added:

- `services/control-plane/bff/tests/test_sse_backpressure.py` - 3 tests
- `support/evidence/BFF-CONSOL-012-sse-backpressure.json` - measurement evidence

Verification results from the parent closeout:

```
pytest services/control-plane/bff/tests/test_sse_backpressure.py -q
=> 3 passed in 11.72s

pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py \
       services/control-plane/bff/tests/test_sse_backpressure.py -q
=> 17 passed in 16.53s
```

## BFF SSE Backpressure Contract (Locked)

The following values are asserted by tests and must not be changed without a
corresponding test update:

| Contract point | Locked value | Source |
|---|---|---|
| Replay buffer per channel | `deque(maxlen=500)` | `_MAX_EVENTS = 500` |
| Subscriber queue per active connection | `asyncio.Queue(maxsize=1000)` | `_sse_stream` |
| Subscriber drop strategy | **newest dropped** (QueueFull silently ignored) | `_publish_event` |
| Replay buffer eviction strategy | **oldest evicted** (rolling deque) | deque auto-eviction |
| Heartbeat interval | 30 s comment `": heartbeat\n\n"` | `_sse_stream` timeout |
| Disconnect cleanup | subscriber removed in `finally` block | `_sse_stream` |
| Missing cursor error | HTTP 409, code `SSE_REPLAY_UNAVAILABLE` | `_handle_sse_stream` |
| Missing cursor resync suggestion | `GET` one of the 409 `resyncRoutes`; `X-SSE-Resync-Routes` mirrors the same route list for clients that can inspect headers | `_handle_sse_stream` |

## SSE Response Header Contract

Authenticated replay-capable SSE stream responses include these headers:

```
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
X-SSE-Channel: {channel}
X-SSE-Replay-Supported: true
X-SSE-Replay-Window-Events: 500
X-SSE-Buffer-Size: 500
X-SSE-Replay-Store: in-memory
X-SSE-Resync-Routes: {comma-separated routes, if defined for channel}
```

Native browser `EventSource` consumers cannot read response headers or the 409
status from `onerror`. Frontends that need header/status inspection must use a
fetch-based SSE client; native `EventSource` consumers should use the static
channel-to-resync-route mapping below or a REST/bootstrap config carrying the
same policy.

Channel-to-resync-route mapping (as of `main.py` `_SSE_RESYNC_ROUTES`):

| Channel | Resync routes |
|---|---|
| `approval` | `/bff/approvals`, `/bff/v5/interventions` |
| `ask` | `/bff/agora/ask/sessions/{id}` |
| All other `SSE_CHANNEL_CATALOG` channels, plus legacy incident streams | (none defined - use channel-specific REST bootstrap, full page reload, or reconnect policy) |

## SSE Event Envelope Format

Each SSE frame emitted by the BFF follows the `SseEventEnvelope` model:

```
id: evt-{unix_ts}-{random8hex}
event: {event_type}
data: {"id": "...", "type": "...", "timestamp": "...", "data": {...}}
```

Per-aggregate ordering fields present in `data`:

| Field | Purpose |
|---|---|
| `aggregate_type` | entity family (e.g., `"approval decision"`) |
| `aggregate_id` | stable entity id within the aggregate family |
| `sequence_no` | monotonically increasing per aggregate (not global) |
| `causal_parent_id` | event id of the preceding event in this aggregate chain |

Global total ordering is **not guaranteed** across aggregates. Frontend must
use `(aggregate_type, aggregate_id, sequence_no)` to order events within one
entity, not a global event stream cursor.

## BFF Query and SSE Gap

### Closed by BFF-CONSOL-011 and BFF-CONSOL-012

- Real SSE stream probe verified cookie-session and Bearer auth paths.
- `Last-Event-ID` reconnect replays correctly when the cursor is within the
  500-event window.
- 409 `SSE_REPLAY_UNAVAILABLE` is returned with `X-SSE-Resync-Routes` when the
  cursor has been evicted.
- Backpressure tests assert bounded replay buffer (500), bounded subscriber
  queue (1000), newest-drop strategy, and disconnect cleanup within 1 s.
- Evidence measurements are recorded in
  `support/evidence/BFF-CONSOL-012-sse-backpressure.json`.

### Still open for frontend integration

| Gap | Required handoff action |
|---|---|
| Resync flow | If a fetch-based SSE client receives 409 `SSE_REPLAY_UNAVAILABLE`, it must fetch one of the returned `resyncRoutes` before opening a fresh stream without `Last-Event-ID`. If using native `EventSource`, `onerror` cannot expose the 409 or headers, so the client must use channel metadata/static mapping and treat replay-failure error policy as a resync trigger. |
| Slow consumer awareness | EventSource reconnects automatically on disconnect. If the subscriber queue was saturated and newest events were dropped, the reconnect replay will surface missed events only within the 500-event window. Frontend must handle ordering gaps gracefully. |
| Per-aggregate ordering | Use `(aggregate_type, aggregate_id, sequence_no)` for in-UI ordering. Do not assume global monotonic ordering across channels or aggregates. |
| `causal_parent_id` validation | If the frontend detects a gap in `sequence_no` for the same aggregate, it should trigger a resync rather than silently displaying out-of-order state. |
| Heartbeat handling | `EventSource` ignores SSE comment lines by default. No frontend action is needed for `": heartbeat"` frames, but the frontend must not mistake a long silence for a dead stream if heartbeats are arriving. |
| Mock generator gate | BFF-CONSOL-011 acceptance requires mock generators to be off in live mode. Frontend env config must not enable mock SSE data in production or strict-staging environments. |

## Operator Journey

### Normal SSE connection

```text
Operator opens a page with a live event feed (e.g., approval queue)
  -> Frontend opens EventSource('/bff/events/stream?channel=approval',
       { withCredentials: true })
  -> BFF returns SSE stream with replay headers
  -> Fetch-based SSE clients may read X-SSE-Replay-Window-Events = 500;
     native EventSource clients must get this budget from bootstrap/static
     channel metadata
  -> Events arrive with id / event / data fields
  -> Frontend applies per-aggregate ordering using (aggregate_id, sequence_no)
  -> Heartbeat comment ": heartbeat" keeps the connection alive every 30 s
```

### Reconnect within the replay window

```text
EventSource disconnects (network blip or tab background)
  -> Browser sends reconnect request with Last-Event-ID: {last-seen-id}
  -> BFF replays all events after that cursor from the in-memory buffer
  -> Frontend resumes without a full page reload
  -> No gap appears if the disconnect was shorter than the event production rate
     needed to exceed 500 buffered events
```

### Reconnect outside the replay window (cursor evicted)

```text
EventSource reconnects with a stale Last-Event-ID
  -> BFF returns HTTP 409 with code SSE_REPLAY_UNAVAILABLE
  -> Error details include resyncRoutes and response headers mirror
     X-SSE-Resync-Routes for fetch-capable clients
  -> Native EventSource clients cannot read the 409 or headers from onerror,
     so they must stop retrying according to replay-failure policy and use the
     channel mapping/bootstrap metadata to select a resync route
  -> Frontend fetches GET /bff/approvals (or the appropriate resync route)
     to reconstruct the current canonical state snapshot
  -> Frontend discards stale in-memory event state
  -> Frontend opens a fresh EventSource without Last-Event-ID
  -> Stream resumes from the current live position
```

### Slow consumer / backpressure

```text
Operator leaves a tab open while BFF publishes events rapidly
  -> BFF subscriber queue for that connection fills to 1000 events
  -> Newest incoming events are silently dropped for that slow subscriber
  -> When the subscriber catches up (reads from queue), it sees only the
     events that were queued before the queue was full
  -> If the tab reconnects, BFF replays from Last-Event-ID (within 500-event window)
  -> Missed events between queue-full and reconnect are visible in replay only
     if still within the window; otherwise the resync flow applies
```

## Suggested Frontend Verification Commands

The parent BFF-CONSOL-011 SSE probe script supports manual verification:

```bash
# Compile check
python3 -m py_compile scripts/probe_bff_sse_stream.py

# Backend backpressure regression suite
pytest services/control-plane/bff/tests/test_sse_backpressure.py -q

# Full SSE substrate + backpressure suite
pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py \
       services/control-plane/bff/tests/test_sse_backpressure.py -q

# Evidence JSON validity
python3 -m json.tool support/evidence/BFF-CONSOL-012-sse-backpressure.json
```

Frontend unit test matrix recommended for consuming this contract:

| Test | Expected result |
|---|---|
| Native `EventSource` opens with `withCredentials: true` | 200 stream opens; header/status access is not assumed |
| Fetch-based SSE client reads `X-SSE-Replay-Window-Events` | Value `"500"` drives client-side gap detection |
| Native `EventSource` replay budget comes from metadata | Static/bootstrap channel metadata provides value `500` |
| Reconnect with valid `Last-Event-ID` | Events after cursor are replayed; no duplicate |
| Fetch-based reconnect with evicted `Last-Event-ID` | 409 received with `SSE_REPLAY_UNAVAILABLE`; frontend triggers resync flow |
| Native `EventSource` replay failure | `onerror` triggers replay-failure policy; client uses channel mapping/bootstrap metadata to resync |
| Resync flow fetches REST route | `GET /bff/approvals` called; stream re-opened without `Last-Event-ID` |
| Per-aggregate ordering applied | Events sorted by `(aggregate_id, sequence_no)` not global order |
| `sequence_no` gap detected | Resync triggered rather than silently displayed |
| Heartbeat comment ignored | No UI update; connection considered alive |
| Mock generator off in live mode | No seed/mock SSE data emitted to live frontend |

## Parent Absorption Risks and Gates

- BFF-CONSOL-012 backend tests run against `main.py` directly. If `_MAX_EVENTS`
  or subscriber `maxsize` values are changed, the test assertions and the
  evidence file measurements will fail, which is intentional as a regression
  guard.
- The 500-event in-memory replay window is **not persistent**. BFF process
  restarts wipe all replay history. Frontend must handle this case the same as
  an evicted cursor (resync flow).
- BFF-CONSOL-021 dual-write soak and BFF-CONSOL-022 staging strict cutover both
  depend on the SSE channel staying stable. Do not modify replay or subscriber
  queue limits until those soaks complete.
- The approval channel resync route `/bff/v5/interventions` is the canonical
  REST fetch for reconstructing current intervention state. The frontend must
  treat the JSON snapshot from this route as the ground truth when resync is
  triggered, not cached event projections.
- This sidecar is support-only. It does not modify `main.py`, test files,
  evidence files, or canonical documents.

## Handoff Checklist for Codex2 (Reviewer)

- Confirm the backpressure contract table matches the values asserted in
  `test_sse_backpressure.py` and measured in
  `support/evidence/BFF-CONSOL-012-sse-backpressure.json`.
- Confirm the SSE event envelope format matches `_sse_format` in `main.py`.
- Confirm the resync route table matches `_SSE_RESYNC_ROUTES` in `main.py`.
- Confirm no canonical truth, runtime, or registry files were modified.
- Confirm the operator journey sections are consistent with `_handle_sse_stream`
  behavior and the BFF-CONSOL-011 real-stream replay evidence.

## Verification for This Sidecar

Performed by Codex as read-only context checks plus a support-packet refresh:

- Read task-scoped context: `AI_COLLABORATION_GUIDE.md`,
  `.orchestrator/task-briefs/bff_consol_012_sidecar_bff_handoff.md`,
  `.orchestrator/skills/task-closeout-finalization.md`, and `ai-status.json`.
- Verified active task state with
  `AI_NAME=Codex ./scripts/ai-status.sh show BFF-CONSOL-012-SIDECAR-BFF-HANDOFF`.
- Read parent task archive: `ai-task-archive/tasks/BFF-CONSOL-012.json`.
- Read review artifact: `.orchestrator/reviews/BFF-CONSOL-012-review-codex.md`.
- Read evidence file: `support/evidence/BFF-CONSOL-012-sse-backpressure.json`.
- Read backpressure test file:
  `services/control-plane/bff/tests/test_sse_backpressure.py`.
- Read `main.py` SSE infrastructure sections to extract contract values:
  `_MAX_EVENTS`, `_SSE_RESYNC_ROUTES`, `_sse_stream`, `_publish_event`,
  `_replay_from`, `_handle_sse_stream`, `_sse_replay_headers`.
- Read BFF-CONSOL-011 archive for context on the preceding real stream replay
  work.
- Confirmed this sidecar refresh only updates
  `support/sidecars/BFF-CONSOL-012/BFF-CONSOL-012-SIDECAR-BFF-HANDOFF.md`.

Focused verification commands run for this refresh:

```bash
python3 -m json.tool support/evidence/BFF-CONSOL-012-sse-backpressure.json >/dev/null
pytest services/control-plane/bff/tests/test_sse_backpressure.py -q
# => 3 passed in 11.88s
git diff --check -- support/sidecars/BFF-CONSOL-012/BFF-CONSOL-012-SIDECAR-BFF-HANDOFF.md
```

No canonical truth, core contract truth, runtime implementation, registry code,
or governance implementation was modified by this sidecar.
