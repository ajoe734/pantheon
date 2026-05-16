# ASK-004 Review: memo publish to registry / review

**Reviewer:** Claude2
**Owner:** Codex
**Date:** 2026-05-16
**Decision:** APPROVED

## Scope Confirmed

Reviewed the committee session memo publish surface as described in the task brief and evidence packet.

Routes implemented:
- `GET /bff/agora/committee/sessions/{sessionId}/memos` — list memos linked to a session
- `POST /bff/agora/committee/sessions/{sessionId}/memos` — submit a draft memo
- `GET /bff/agora/committee/sessions/{sessionId}/memos/{memoId}` — get memo detail for review
- `POST /bff/agora/committee/sessions/{sessionId}/memos/{memoId}/publish` — publish to consult memo registry

## Review Findings

### Implementation Quality

- Routes are clean and follow established BFF patterns.
- `_reject_body_idempotency_key` applied consistently on both submit and publish.
- `Idempotency-Key` and `X-Idempotency-Key` headers both resolved via `_resolve_final_idempotency_key`.
- Deep copies via `json.loads(json.dumps(...))` prevent memo state mutation.
- `read_store.py` methods (`submit_committee_session_memo`, `list_committee_session_memos`, `get_committee_session_memo`, `publish_committee_session_memo`) are well-scoped.

### Key Behaviors Verified

| Behavior | Status |
|---|---|
| Auth required on all routes | ✅ |
| 404 for missing session | ✅ |
| 404 for non-committee session (quick_ask rejected) | ✅ |
| 409 conflict on duplicate explicit memoId | ✅ |
| Auto-generated memoId when not provided | ✅ |
| Idempotency replay returns same memo | ✅ |
| Body idempotency key rejected (camelCase + snake_case) | ✅ |
| Publish transitions status → `published`, `lifecycle_state → published` | ✅ |
| `published_at` is stable on repeat publish (idempotent) | ✅ |
| `session_to_memo_mapping.mapping_status` → `active` after publish | ✅ |
| `ask.memo.published` emitted only on first publish | ✅ |
| Published memo visible via `GET /api/v1/consult/memos?status=published` | ✅ |
| Cross-session memo isolation (wrong session → 404) | ✅ |

### Test Execution (run by reviewer)

```
pytest services/control-plane/bff/test_ask_004_memo_publish_contract.py -q
# 31 passed

pytest services/control-plane/bff/test_ask_003_committee_lifecycle.py \
       services/control-plane/bff/test_cw04_redteam_memo_contract.py \
       services/control-plane/bff/test_pkt015_consultation_workbench_contract.py -q
# 38 passed (no regressions)
```

### Advisory Boundary

Confirmed: implementation is BFF/local-registry scoped only. No deployment, broker, capital, or runtime side effects.

### Minor Observations (non-blocking)

- The `publish` route uses `_require_read_role` rather than a dedicated write role; this is consistent with the rest of the BFF agora surface and acceptable within the advisory boundary.
- No OpenAPI duplicate issue (different from SENT-001) — these routes are distinct path patterns.

## Decision

All acceptance criteria met. Implementation is correct, well-tested, and regression-safe. Returning to Codex for final closeout.
