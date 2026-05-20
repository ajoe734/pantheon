# Review: HA-008-V2 — SSE Last-Event-ID Replay Test

Reviewer: Claude
Date: 2026-05-20
Status: APPROVED

## Owner Closeout Verification

Date: 2026-05-20
Owner: Codex

```
python3 -m pytest -q tests/bff/test_sse_replay.py
2 passed in 4.09s
```

Delivery PR: #287
Merge commit: 845d9c7192d8d0f36b7393701f294d9ae69d8855
Closeout evidence PR: #322
Closeout evidence merge commit: 2301ce0de444e89b735a40790bd4b4579b724db8

## Verification

```
python3 -m pytest -q tests/bff/test_sse_replay.py
2 passed in 3.63s
```

## Scope Reviewed

- `services/control-plane/bff/main.py` — SSE shared replay store implementation
- `tests/bff/test_sse_replay.py` — two-test suite covering failover replay and fail-closed behavior

## Implementation Assessment

### Shared Replay Store

- `PANTHEON_BFF_SSE_REPLAY_STORE=file` activates a JSONL-backed replay store per channel under `{BFF_DATA_DIR}/sse_replay/{channel}.jsonl`.
- `_publish_event` appends to the shared file on every publish, so replicas sharing `BFF_DATA_DIR` can replay events that were published by a different replica.
- `_replay_from_events` correctly yields events *after* (not including) the cursor event, producing no-gap no-duplicate replay.
- `_trim_shared_sse_events` caps file size at `_MAX_EVENTS` lines to prevent unbounded growth.
- Fail-closed path: unknown cursor → `SseReplayUnavailableError` → HTTP 409 `SSE_REPLAY_UNAVAILABLE` / `SSE_REPLAY_HISTORY_MISSING`.

### Minor Observation (non-blocking)

`_handle_sse_stream` hardcodes `"replayStore": "in-memory"` in the 409 error `details_extra` even when the actual store is file-backed. The response *headers* use `_sse_replay_headers(channel)` which is accurate. The test correctly verifies the header value (`X-SSE-Replay-Store: file`). This cosmetic inconsistency in the JSON body is not a blocker.

## Test Coverage

| Test | Scenario | Result |
|---|---|---|
| `test_last_event_id_replay_survives_replica_failover_without_gap_or_duplicate` | Primary publishes 3 events; passive (empty buffer) replays from cursor 1 → receives 2,3 only | PASS |
| `test_shared_sse_replay_fails_closed_when_cursor_is_unavailable` | Unknown cursor → 409 with correct error code, reason, channel, lastEventId | PASS |

## Acceptance Criteria Verdict

All criteria met: no-gap, no-duplicate replay after replica failover; fail-closed on unknown cursor; correct SSE headers.
