# ASK-001 Evidence: /bff/agora/ask/sessions

**Task:** ASK-001  
**Owner:** Claude2  
**Reviewer:** Codex2  
**Commit:** 4f3bc011  
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
- All POST routes use `_agora_core_idempotency_check` for idempotency and `_ASK_SESSIONS_IDEMPOTENCY` dict for caching results.

## Files Changed

- `services/control-plane/bff/main.py` — 3 new routes + `_ASK_SESSIONS_IDEMPOTENCY` dict
- `services/control-plane/bff/read_store.py` — `close_agora_session()` method
- `services/control-plane/bff/test_ask_001_sessions_contract.py` — 19 contract tests (new)

## Verification

```
python3 -m py_compile services/control-plane/bff/main.py -> OK
python3 -m py_compile services/control-plane/bff/read_store.py -> OK
pytest services/control-plane/bff/test_ask_001_sessions_contract.py -q -> 19 passed
pytest services/control-plane/bff/test_bff_agora_extended_contract.py -> 14 passed
pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py -> 8 passed
```

## Invariants

- Only sessions with `mode=quick_ask` appear in `GET /bff/agora/ask/sessions` (filter preserved).
- `GET /bff/agora/ask/sessions/{sessionId}` returns 404 for unknown sessions (SSE resync clients must handle this).
- Close is idempotent; re-sending same idempotency key returns cached result.
- No broker route, live capital binding, or governance approval authority is added.
