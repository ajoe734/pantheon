# ASK-001 Review - Codex

Task: ASK-001 (`/bff/agora/ask/sessions`)
Owner: Claude2
Reviewer: Codex
Disposition: Changes requested

## Findings

1. `POST /bff/agora/ask/sessions` and `POST /bff/agora/ask/sessions/{sessionId}/close` do not actually persist idempotency records in the cache they read from. Both routes call `_agora_core_idempotency_check()`, which reads `_AGORA_CORE_BFF_IDEMPOTENCY` at `services/control-plane/bff/main.py:14723`, but the new routes store results in `_ASK_SESSIONS_IDEMPOTENCY` at `services/control-plane/bff/main.py:24438` and `services/control-plane/bff/main.py:24514`. The new dict is not read by the checker. Impact: retrying a create request without explicit `sessionId` creates a second session, and reusing the same key with a different payload returns success instead of `IDEMPOTENCY_CONFLICT`; close requests can also change `outcome` under the same key.

## Evidence

- `pytest services/control-plane/bff/test_ask_001_sessions_contract.py -q` -> 19 passed.
- Manual create retry probe with `PANTHEON_BFF_AUTH_STUB=true PANTHEON_BFF_AUTH_MODE=permissive`:
  - same `Idempotency-Key`, same payload, no explicit `sessionId`
  - first response: `201`, `sessionId=ask-f4c34299af`
  - second response: `201`, `sessionId=ask-883a9f1894`
  - same key with different payload: `201`, expected conflict.
- Manual close conflict probe with the same env:
  - same `Idempotency-Key`, first payload `{"outcome":"resolved"}` -> `200`
  - same key, second payload `{"outcome":"different"}` -> `200`, expected conflict.

## Required Fix

- Store ASK-001 POST idempotency records in `_AGORA_CORE_BFF_IDEMPOTENCY`, or update the checker to read/write a single route-specific cache consistently.
- Add regression coverage for generated `sessionId` create retry and create/close same-key different-payload conflict.
