# ASK-003 Evidence: ask / committee session lifecycle

**Task:** ASK-003
**Owner:** Claude2
**Reviewer:** Codex
**Branch:** bff-luv-fe-006-dev-deploy

## Scope

Implemented the full `/bff/agora/committee/sessions` committee session lifecycle in the BFF:

- `GET /bff/agora/committee/sessions` — list sessions filtered to `mode=committee`
- `POST /bff/agora/committee/sessions` — create committee session; initializes `quorumState`, `consensusState`, `participantRoster`, `linkedRequestId`; status starts as `pending`
- `GET /bff/agora/committee/sessions/{sessionId}` — detail/SSE resync route (mirrors `_SSE_RESYNC_ROUTES["ask"]` which now includes `/bff/agora/committee/sessions/{id}`)
- `POST /bff/agora/committee/sessions/{sessionId}/open` — transitions `pending → open`, sets `openedAt`, publishes `ask.session.started` SSE event
- `POST /bff/agora/committee/sessions/{sessionId}/close` — transitions to `closed`, accepts optional `outcome` and `memoIds`, publishes `ask.session.completed` SSE event

All mutating routes use `_AGORA_CORE_BFF_IDEMPOTENCY` (shared agora idempotency store) with `Idempotency-Key` header enforcement.

## Files Changed

- `services/control-plane/bff/main.py` — 5 new committee routes + SSE resync route update
- `services/control-plane/bff/read_store.py` — `open_committee_session()`, `close_committee_session()`, committee-field handling in `create_agora_session()`
- `services/control-plane/bff/test_ask_003_committee_lifecycle.py` — 26 contract tests (new)

Also bundled: ASK-001 idempotency fix — `_ASK_SESSIONS_IDEMPOTENCY` aliased to `_AGORA_CORE_BFF_IDEMPOTENCY` so create/close results are visible to the checker; updated `test_ask_001_sessions_contract.py` and `support/evidence/ASK-001/README.md`.

## Verification

```
python3 -m pytest services/control-plane/bff/test_ask_003_committee_lifecycle.py -v
# 26 passed

python3 -m pytest services/control-plane/bff/test_ask_001_sessions_contract.py -q
# 22 passed (regression clean)
```
