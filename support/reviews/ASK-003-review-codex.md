# ASK-003 Review - Codex

Task: ASK-003 (`ask / committee session lifecycle`)
Owner: Claude2
Reviewer: Codex
Review date: 2026-05-16
Disposition: APPROVED after reviewer patch

## Scope Reviewed

Reviewed commit `a24a9b49` plus the ASK-003 evidence packet at `support/evidence/ASK-003/README.md`.

The reviewed implementation adds:

- `GET /bff/agora/committee/sessions`
- `POST /bff/agora/committee/sessions`
- `GET /bff/agora/committee/sessions/{sessionId}`
- `POST /bff/agora/committee/sessions/{sessionId}/open`
- `POST /bff/agora/committee/sessions/{sessionId}/close`
- `ReadSurfaceStore.open_committee_session()`
- `ReadSurfaceStore.close_committee_session()`

## Review Findings And Fixes

### SSE resync contract drift - fixed

The initial review run found `test_approval_and_ask_stream_routes_publish_replay_metadata_headers` failing because `_SSE_RESYNC_ROUTES["ask"]` now advertises both ask and committee detail routes, while `BFF_API_CONTRACT.md` and the SSE substrate test still expected only `/bff/agora/ask/sessions/{id}`.

Reviewer patch:

- Updated `BFF_API_CONTRACT.md` section 11.4 to list both ask-channel resync routes.
- Updated `test_pkt005_sse_substrate_contract.py` to expect `/bff/agora/ask/sessions/{id},/bff/agora/committee/sessions/{id}`.

### Cross-mode session isolation gap - fixed

Committee routes initially read from the shared `agora_sessions` store without rejecting non-committee records. That meant `/bff/agora/committee/sessions/{id}` and the committee open/close routes could operate on a quick-ask session ID.

Reviewer patch:

- `GET /bff/agora/committee/sessions/{sessionId}` now returns 404 for non-committee sessions.
- `open_committee_session()` and `close_committee_session()` now no-op with `None` for non-committee sessions, which the routes map to 404.
- ASK routes now return 404 for committee session IDs on detail/close, preserving the quick-ask vs committee lifecycle boundary.
- Added ASK-001 and ASK-003 regression tests for cross-mode detail and close/open protection.

## Result

ASK-003 is approved with the reviewer patch applied. The lifecycle routes are present, authenticated, idempotency-key guarded, use the shared Agora idempotency store, publish ask SSE lifecycle events, and keep quick-ask and committee sessions isolated by mode.

## Verification

```bash
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/read_store.py services/control-plane/bff/test_ask_001_sessions_contract.py services/control-plane/bff/test_ask_003_committee_lifecycle.py services/control-plane/bff/test_pkt005_sse_substrate_contract.py
python3 -m pytest services/control-plane/bff/test_ask_003_committee_lifecycle.py -q
# 29 passed
python3 -m pytest services/control-plane/bff/test_ask_001_sessions_contract.py -q
# 24 passed
python3 -m pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py -q
# 14 passed
python3 -m pytest services/control-plane/bff/test_bff_agora_extended_contract.py -q
# 8 passed
python3 -m pytest services/control-plane/bff/test_bff_agora_extended_contract.py services/control-plane/bff/test_pkt005_sse_substrate_contract.py -q
# 22 passed
git diff --check -- services/control-plane/bff/main.py services/control-plane/bff/read_store.py services/control-plane/bff/test_ask_001_sessions_contract.py services/control-plane/bff/test_ask_003_committee_lifecycle.py services/control-plane/bff/test_pkt005_sse_substrate_contract.py services/control-plane/bff/BFF_API_CONTRACT.md
```
