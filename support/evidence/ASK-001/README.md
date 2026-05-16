# ASK-001 Evidence: /bff/agora/ask/sessions

**Task:** ASK-001
**Owner:** Codex (follow-up fix; initial implementation by Claude2)
**Reviewer:** Claude2
**Commit:** 4f3bc011 initial implementation; follow-up fix pending review
**Branch:** bff-luv-fe-006-dev-deploy

## Scope

Implemented the full `/bff/agora/ask/sessions` session lifecycle in the BFF:

| Route | Method | Purpose |
|---|---|---|
| `/bff/agora/ask/sessions` | GET | List ask sessions (existing stub — unchanged) |
| `/bff/agora/ask/sessions` | POST | Explicit session creation (new) |
| `/bff/agora/ask/sessions/{sessionId}` | GET | Session detail / SSE resync route (new) |
| `/bff/agora/ask/sessions/{sessionId}/close` | POST | Close session with optional outcome (new) |

## Implementation Notes

- `POST /bff/agora/ask/sessions` — forces `mode=quick_ask`, generates a stable `sessionId` if omitted, enforces `Idempotency-Key` header per final-contract pattern.
- `GET /bff/agora/ask/sessions/{sessionId}` — mirrors the SSE resync route registered in `_SSE_RESYNC_ROUTES["ask"]`. Returns `data + meta` envelope.
- `POST /bff/agora/ask/sessions/{sessionId}/close` — calls `read_store.close_agora_session()`, publishes `ask.session.completed` SSE event, accepts optional `outcome` field.
- `close_agora_session()` added to `ReadSurfaceStore` — sets `status=closed`, `closedAt`, `updatedAt`, optional `outcome`.
- All POST routes use `_agora_core_idempotency_check` for idempotency and persist ASK-001 create/close idempotency results in the shared `_AGORA_CORE_BFF_IDEMPOTENCY` store read by that checker.

## Files Changed

- `services/control-plane/bff/main.py` — 3 new routes + shared Agora idempotency cache wiring
- `services/control-plane/bff/read_store.py` — `close_agora_session()` method
- `services/control-plane/bff/test_ask_001_sessions_contract.py` — 22 contract tests, including reviewer-requested idempotency regressions

## Verification

```
python3 -m py_compile services/control-plane/bff/main.py -> OK
python3 -m py_compile services/control-plane/bff/read_store.py -> OK
pytest services/control-plane/bff/test_ask_001_sessions_contract.py -q -> 19 passed
pytest services/control-plane/bff/test_bff_agora_extended_contract.py -> 14 passed
pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py -> 8 passed
```

## Follow-up Fix: 2026-05-16

Reviewer-requested idempotency fix:

- `POST /bff/agora/ask/sessions` now writes replay records to `_AGORA_CORE_BFF_IDEMPOTENCY`, the cache read by `_agora_core_idempotency_check()`.
- `POST /bff/agora/ask/sessions/{sessionId}/close` now writes replay records to the same shared cache.
- `_ASK_SESSIONS_IDEMPOTENCY` remains only as a compatibility alias to the shared cache for existing probes/tests.
- Added regressions for generated `sessionId` replay, create same-key/different-payload conflict, and close same-key/different-payload conflict.

Follow-up verification:

```
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/test_ask_001_sessions_contract.py -> OK
pytest services/control-plane/bff/test_ask_001_sessions_contract.py -q -> 22 passed
pytest services/control-plane/bff/test_bff_agora_extended_contract.py -q -> 8 passed
pytest services/control-plane/bff/test_bff_agora_extended_contract.py services/control-plane/bff/test_pkt005_sse_substrate_contract.py -q -> 21 passed, 1 failed
```

The combined Agora extended + SSE substrate run fails only in `test_approval_and_ask_stream_routes_publish_replay_metadata_headers`: the dirty worktree has an unrelated ASK-003 change that adds `/bff/agora/committee/sessions/{id}` to the ask channel `X-SSE-Resync-Routes` header while the SSE test still expects only `/bff/agora/ask/sessions/{id}`.

## Invariants

- Only sessions with `mode=quick_ask` appear in `GET /bff/agora/ask/sessions` (filter preserved).
- `GET /bff/agora/ask/sessions/{sessionId}` returns 404 for unknown sessions (SSE resync clients must handle this).
- Close is idempotent; re-sending same idempotency key returns cached result.
- No broker route, live capital binding, or governance approval authority is added.
