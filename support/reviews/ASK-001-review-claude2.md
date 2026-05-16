# ASK-001 Review: /bff/agora/ask/sessions (idempotency fix)

**Task:** ASK-001
**Reviewer:** Claude2
**Owner:** Codex
**Review date:** 2026-05-16

## Decision: APPROVED

## Scope reviewed

Codex's follow-up fix addressing the reviewer-requested idempotency cache mismatch in the ASK-001 session lifecycle routes.

## Key findings

### Idempotency fix — correct

`_ASK_SESSIONS_IDEMPOTENCY = _AGORA_CORE_BFF_IDEMPOTENCY` (main.py line 24398) makes the alias point to the exact same dict object that `_agora_core_idempotency_check()` reads. Both `POST /bff/agora/ask/sessions` (create) and `POST /bff/agora/ask/sessions/{sessionId}/close` now:

1. Check via `_agora_core_idempotency_check()` → reads `_AGORA_CORE_BFF_IDEMPOTENCY`
2. Write result via `_AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {...}`

The original bug (writing to a separate per-route dict while the checker read a different shared dict) is resolved.

### Route implementation — correct

- `POST /bff/agora/ask/sessions`: forces `mode=quick_ask`, generates stable sessionId if omitted, enforces Idempotency-Key, writes to shared cache.
- `GET /bff/agora/ask/sessions/{sessionId}`: returns `data + meta` envelope with `snapshot_at`; 404 for unknown sessions.
- `POST /bff/agora/ask/sessions/{sessionId}/close`: calls `read_store.close_agora_session()`, publishes `ask.session.completed` SSE event, accepts optional `outcome`. Returns 404 for unknown sessions.

### `close_agora_session()` — correct

Sets `status=closed`, `closedAt`, `updatedAt`, and optional `outcome`. Returns `None` for unknown session IDs (caller maps to 404).

### Tests — 22 passed

Regression tests for reviewer-requested scenarios are present and correct:
- Generated session ID replay: same key + same payload → same sessionId returned; only 1 entry in list (no double-create).
- Create idempotency conflict: same key + different payload → 409 IDEMPOTENCY_CONFLICT with correct error structure.
- Close idempotency conflict: same key + different payload → 409 IDEMPOTENCY_CONFLICT with correct error structure.

### One failing test in combined run — unrelated

`test_approval_and_ask_stream_routes_publish_replay_metadata_headers` fails only in the combined Agora extended + SSE substrate suite. The failure is caused by an unrelated ASK-003 dirty hunk that adds `/bff/agora/committee/sessions/{id}` to the `ask` channel's `X-SSE-Resync-Routes` header. This is not part of ASK-001 scope and does not affect any ASK-001 contract tests.

### No isolated commit — justified

`main.py` contains interleaved ASK-003 dirty hunks in the same worktree. Non-interactive background worker cannot use `git add -p`. Exception is appropriately noted by Codex.

### Scope boundary — clean

- `mode=quick_ask` filter preserved in list route.
- No broker route, live capital binding, or governance approval authority added.

## Verification commands confirmed

```
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/test_ask_001_sessions_contract.py -> OK
pytest services/control-plane/bff/test_ask_001_sessions_contract.py -q -> 22 passed
pytest services/control-plane/bff/test_bff_agora_extended_contract.py -q -> 8 passed
```
