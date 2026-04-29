# Review: SVC-SEARCH-DURABLE-COMPAT-QUARANTINE

Reviewer: Claude  
Date: 2026-04-29

## Scope Verified

- `services/search/main.py` — compat quarantine logic
- `services/search/tests/test_http_service.py` — 5 focused tests
- `services/control-plane/bff/read_store.py` — BFF normal-path payload
- `services/control-plane/bff/test_search_service_client.py` — BFF client test
- `scripts/smoke_honest_stack.py` — smoke search step

## Acceptance Criteria Result

| Criterion | Result |
|---|---|
| durable no-document query is default normal path | PASS |
| request-document mode requires explicit compat signal or compat route | PASS |
| BFF search client never sends documents in normal path | PASS |
| smoke keeps durable index reload and snapshot replay | PASS |
| focused search and BFF client tests pass | PASS (6/6) |

## Implementation Notes

- `/api/search/query` now raises HTTP 400 when caller sends documents without `allow_request_documents_compat=true` in the body.
- `/api/search/query/request-documents-compat` provides an explicit compat route.
- `_query_search` helper accepts both the body flag and the route-level override cleanly.
- `_rw02_search_service_payload` in BFF omits `documents` key entirely; asserted in `test_rw02_search_uses_explicit_search_service_url_for_normal_path`.
- Smoke calls `/api/search/query` without documents — correct durable path.
- `adapter_state` field on the index adapter correctly reflects `"durable"` vs `"request_documents_compat"` for observability.

## Verification

```
python3 -m pytest services/search/tests/test_http_service.py services/control-plane/bff/test_search_service_client.py -v
# 6 passed in 1.71s

git diff --check -- services/search/main.py services/search/tests/test_http_service.py
# exit 0 (no whitespace issues)
```

## Decision

**Approved.** Implementation is clean and all acceptance criteria are met. Returning to Codex2 for closeout.
