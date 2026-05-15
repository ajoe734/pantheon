# Search Retrieval and Cutoff — Acceptance Record

Task: SVC-SEARCH-RETRIEVAL-AND-CUTOFF
Owner: Claude
Reviewer: Codex
Date: 2026-04-30
Decision: approved → done

## Delivery Summary

Hardens the search retrieval contract and quarantines request-document
compatibility to dev/test-only paths.  Staging and production deployments now
require `SEARCH_DURABLE_INDEX_ONLY=true`, which causes the search service to
reject all request-document paths regardless of the compat flag.

## Acceptance Criteria Satisfied

| Criterion | Evidence |
|-----------|----------|
| Durable index is the only staging/prod query path | `SEARCH_DURABLE_INDEX_ONLY=true` in `env/prod-control.env.example`; `docker-compose.yml` wires the var to the search service |
| Request-document compat endpoint is deprecated/quarantined | `/api/search/query/request-documents-compat` returns `Deprecation: true` and `X-Search-Path: request_documents_compat`; durable-index-only mode rejects it unconditionally |
| Ranking, top_k, filters, citations are deterministic | Covered by `test_retrieval_rank_filter_cutoff_contract.py` |
| BFF uses service-backed search client only | `test_search_retrieval_cutoff_contract.py` asserts `/api/search/query` without embedded documents |
| Tests cover cutoff and compatibility quarantine | 40 focused tests pass; 78 broader search tests pass |

## Verification Commands

```bash
python3 -m pytest \
  services/search/tests/test_retrieval_rank_filter_cutoff_contract.py \
  services/search/tests/test_http_service.py \
  services/search/tests/test_service_activation_contract.py \
  services/control-plane/bff/test_search_retrieval_cutoff_contract.py \
  services/control-plane/bff/test_search_service_client.py \
  services/control-plane/bff/test_staging_read_store_cutoff_contract.py \
  -q
# 40 passed

python3 -m pytest services/search -q \
  --ignore=services/search/tests/test_contracts.py
# 78 passed (pre-existing schema contract failure in test_contracts.py excluded)
```

## Commits

- `b1cd89b` — owner: harden retrieval contract and request-document cutoff
- `31c76b3` — reviewer support: wire durable cutoff review (docker-compose, env, BFF tests)
- `32eb0ef` — sidecar: BFF handoff packet

## Pre-existing Failure Note

`services/search/tests/test_contracts.py::test_sd03_contract_schemas_accept_model_payloads`
fails due to a pre-existing schema contract issue unrelated to this task's
retrieval/cutoff changes.  Tracked separately under the search production
hardening track (`SVC-SOURCE-SEARCH-PROD-HARDENING`).
