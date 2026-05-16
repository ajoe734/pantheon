# ASK-004 Evidence: committee memo publish to registry / review

**Task:** ASK-004
**Owner:** Codex
**Reviewer:** Claude2

## Scope

Implemented the committee memo publish/review surface on top of the ASK-003 committee session lifecycle:

- `GET /bff/agora/committee/sessions/{sessionId}/memos` lists memos linked to a committee session.
- `POST /bff/agora/committee/sessions/{sessionId}/memos` creates a draft `ConsultMemo` in the BFF local consult memo registry overlay.
- `GET /bff/agora/committee/sessions/{sessionId}/memos/{memoId}` returns the memo detail for review.
- `POST /bff/agora/committee/sessions/{sessionId}/memos/{memoId}/publish` transitions a draft memo to `published`, makes it visible through `GET /api/v1/consult/memos`, and emits `ask.memo.published` only on the first publish.

The implementation preserves the advisory boundary: it publishes memo registry/read-model state only. It does not create deployment, broker, capital, or runtime side effects.

## Files Changed

- `services/control-plane/bff/main.py` - ASK-004 routes, idempotent publish handling, duplicate memo-id protection, and body idempotency-key rejection for both camelCase and snake_case.
- `services/control-plane/bff/read_store.py` - committee memo draft/publish helpers, explicit `session_to_memo_mapping`, registry overlay projection, published memo stability, and `memo_id` overlay de-duplication.
- `services/control-plane/bff/test_ask_004_memo_publish_contract.py` - 31 contract tests covering list/submit/detail/publish, auth, idempotency, duplicate protection, registry visibility, and published timestamp stability.

## Verification

```bash
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/read_store.py services/control-plane/bff/test_ask_004_memo_publish_contract.py
# OK

python3 -m pytest services/control-plane/bff/test_ask_004_memo_publish_contract.py -q
# 31 passed

python3 -m pytest services/control-plane/bff/test_ask_003_committee_lifecycle.py -q
# 29 passed

python3 -m pytest services/control-plane/bff/test_cw04_redteam_memo_contract.py -q
# 7 passed

python3 -m pytest services/control-plane/bff/test_pkt015_consultation_workbench_contract.py -q
# 2 passed
```

## Publication Note

The implementation files were captured in upstream commit `77ce6b8d` while an ASK-002 review/finalization commit was created in the shared worktree. This evidence packet records the ASK-004 scope and verification so the reviewer can evaluate the current tree without treating the ASK-002 review note as the ASK-004 task boundary.

## Reviewer Notes

- Publish is stable after the first successful publish: later calls with a new idempotency key return the existing published memo without changing `published_at`.
- Draft submission rejects explicit duplicate `memoId` values unless the client is replaying the same request with the same idempotency key.
- The memo projection includes `session_to_memo_mapping.source_session_id`, `memo_id`, `memo_type`, `created_by`, evidence ref ids, and `mapping_status`.
- This patch is intentionally BFF/local-registry scoped. Downstream governance approval routing remains a separate ASK-005/SSE and governance workflow concern.

## Closeout

Reviewer approval is recorded in `support/reviews/ASK-004-review-claude2.md`.

Closeout verification on 2026-05-16:

```bash
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/read_store.py services/control-plane/bff/test_ask_004_memo_publish_contract.py
# OK

python3 -m pytest services/control-plane/bff/test_ask_004_memo_publish_contract.py -q
# 31 passed in 24.41s

python3 -m pytest services/control-plane/bff/test_ask_003_committee_lifecycle.py services/control-plane/bff/test_cw04_redteam_memo_contract.py services/control-plane/bff/test_pkt015_consultation_workbench_contract.py -q
# 38 passed in 35.68s
```
