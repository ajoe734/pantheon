# Review: SVC-SEARCH-RETRIEVAL-AND-CUTOFF

Reviewer: Codex
Date: 2026-04-30
Decision: **approved**

## Scope Reviewed

Task: Harden search retrieval and cut off request document normal path
Owner: Claude
Reviewed owner commit: `b1cd89b655c0edc16852e6108a37852f9a7c56e2`

Artifacts reviewed:
- `services/search/main.py`
- `services/search/tests/test_retrieval_rank_filter_cutoff_contract.py`
- `services/control-plane/bff/test_search_retrieval_cutoff_contract.py`
- `services/control-plane/bff/read_store.py`
- `docker-compose.yml`
- `env/prod-control.env.example`

## Finding

No blocking findings remain.

The owner implementation hardens the search retrieval contract and quarantines request-document compatibility:
- durable-index-only mode rejects request documents on both the normal query endpoint and compat endpoint
- the compat endpoint is explicitly marked with `Deprecation: true` and `X-Search-Path: request_documents_compat`
- BFF normal search calls `/api/search/query` without embedding request documents
- ranking, `top_k`, ACL/license/environment/persona/workspace filters, and citation requirements are covered by contract tests

I applied one narrow reviewer support update before approval:
- `docker-compose.yml` now wires `SEARCH_DURABLE_INDEX_ONLY`
- `env/prod-control.env.example` sets `SEARCH_DURABLE_INDEX_ONLY=true`
- BFF cutoff tests now assert the deployment wiring

## Verification Run

```bash
python3 -m pytest services/search/tests/test_retrieval_rank_filter_cutoff_contract.py services/search/tests/test_http_service.py services/search/tests/test_service_activation_contract.py services/control-plane/bff/test_search_retrieval_cutoff_contract.py services/control-plane/bff/test_search_service_client.py services/control-plane/bff/test_staging_read_store_cutoff_contract.py -q
# 40 passed
```

```bash
python3 -m pytest services/search -q --ignore=services/search/tests/test_contracts.py
# 78 passed
```

Full `services/search` still has the pre-existing schema failure in `services/search/tests/test_contracts.py::test_sd03_contract_schemas_accept_model_payloads`; it is outside this task's retrieval/cutoff changes.

## Acceptance Assessment

Approved. Durable index is the only staging/prod configured normal path, request-document compatibility is explicitly deprecated/quarantined, BFF search uses the service-backed no-document path, and focused contract tests cover cutoff, ranking, filters, access control, citations, and compatibility quarantine.
