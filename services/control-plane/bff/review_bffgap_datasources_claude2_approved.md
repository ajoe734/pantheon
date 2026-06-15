# Review: BFFGAP-DATASOURCES — Claude2

Date: 2026-06-15
Reviewer: Claude2
Task: BFFGAP-DATASOURCES (GET /bff/management/data-sources)
Owner: Claude
Status: **APPROVED**

## Scope Reviewed

1. `services/control-plane/bff/console_gap/datasources.py` — factory router
2. `services/control-plane/bff/main.py` — 9-line `include_router` delta
3. `services/control-plane/bff/BFF_API_CONTRACT.md` §10.1.2 + surface count table
4. `services/control-plane/bff/tests/test_bff_management_data_sources_contract.py` — 5 contract tests

## Findings

### console_gap/datasources.py

- Factory pattern with full dependency injection (`get_read_store`, `extract_identity`, `require_read_role`, `snapshot_meta`, `utc_now`) — no circular imports, test-friendly.
- Canonical `{ data, items, page_info, meta }` envelope returned in all paths.
- `source in ("missing", "unavailable")` guard returns proper degraded envelope: `status:unavailable`, empty `items`, `page_info.total:0` — never a bare `[]`. Contract requirement satisfied.
- Empty-but-reachable case returns `status: degraded` (distinct from unavailable). Semantically correct.
- Optional extended fields (`policy_registry`, `financial_data_source_catalog`, `active_universe_policy`, `provider_examples`) forwarded when present.
- Auth guard via `require_read_role(identity)` consistent with existing BFF pattern.

### main.py delta

- Clean 9-line addition after `_include_assistant_routes()`.
- Isolated module import at the end of the file with `# noqa: E402` comment.
- Factory called with live singletons matching all required parameters.
- Negligible merge-conflict surface (end-of-file append).

### BFF_API_CONTRACT.md §10.1.2

- Route documented with correct composition source, response envelope, degraded behavior, and `operator` min-role.
- Degraded envelope JSON example matches implementation output exactly.
- Surface count table updated: `BFF Management (BFFGAP-CONSOLE) | DS-01 (/bff/management/data-sources) | 1`.

### Contract Tests — 5/5 PASSED

```
tests/test_bff_management_data_sources_contract.py::test_bff_management_data_sources_returns_canonical_envelope PASSED
tests/test_bff_management_data_sources_contract.py::test_bff_management_data_sources_includes_connector_fields PASSED
tests/test_bff_management_data_sources_contract.py::test_bff_management_data_sources_degraded_when_source_missing PASSED
tests/test_bff_management_data_sources_contract.py::test_bff_management_data_sources_degraded_when_source_unavailable PASSED
tests/test_bff_management_data_sources_contract.py::test_bff_management_data_sources_requires_auth PASSED
```

Verified with: `python3 -m pytest tests/test_bff_management_data_sources_contract.py -v`

## Acceptance Criteria Check

| Criterion | Status |
|---|---|
| GET /bff/management/data-sources returns 200 canonical envelope | ✅ |
| Degraded envelope (not bare []) when source missing/unavailable | ✅ |
| 5 contract tests all green | ✅ |
| Auth guard (401 without token) | ✅ |
| BFF_API_CONTRACT.md §10.1.2 documented | ✅ |
| Surface count table updated (DS-01, count 1) | ✅ |

## Decision

Approved. No required changes. Return to owner (Claude) for finalization and PR merge.
