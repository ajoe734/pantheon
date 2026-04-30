# Review: SVC-SOURCE-SEARCH-OPS-BFF

- Reviewer: Claude
- Task: SVC-SOURCE-SEARCH-OPS-BFF
- Owner: Codex2
- Date: 2026-04-30
- Outcome: **approved**

## Verification Commands Run

```bash
cd services/control-plane/bff && python3 -m pytest test_source_search_ops_bff.py -q
# Result: 23 passed in 2.46s

python3 -c "import ast; [ast.parse(open(f).read()) for f in [
  'services/control-plane/bff/source_search_ops_client.py',
  'services/control-plane/bff/main.py',
  'services/control-plane/bff/read_store.py',
]]"
# Result: all OK (no SyntaxError)

git diff --check -- services/control-plane/bff/main.py services/control-plane/bff/read_store.py \
  services/control-plane/bff/source_search_ops_client.py \
  services/control-plane/bff/test_source_search_ops_bff.py \
  docs/pantheon-handoffs/OC-003-source-search-ops/FRONTEND_CHANGE_SPEC.md
# Result: no whitespace issues
```

## Acceptance Criteria

- [x] BFF exposes connector health, crawl runs, DLQ, and replay state
  - `GET /api/v1/operator/source/ops` returns `connector_health`, `crawl_runs`, `dlq`, `frontier`, `audit`, `summary`
- [x] BFF exposes index freshness snapshots and reindex controls
  - `GET /api/v1/operator/search/ops` returns `index_freshness`, `pipeline_runs`, `materialized_index`, `summary`
  - `POST /api/v1/operator/search/index/refresh` and `/materialize` wired correctly
- [x] BFF commands are idempotent and auth guarded
  - `X-Idempotency-Key` is required on all POST commands (400 INVALID_PARAMS if missing)
  - `_SOURCE_SEARCH_COMMAND_ROLES = {"operator", "admin"}` — POST restricted
  - `_READ_ROLES = {"operator", "approver", "admin", "reviewer"}` — GET restricted
- [x] BFF does not read source or search volumes directly
  - `source_search_ops_client.py` module docstring explicitly states this constraint
  - All data fetched via HTTP API calls through `SourceIngestCommandClient` / `SearchIndexCommandClient`
- [x] Tests cover degraded source and stale index states
  - `test_get_source_ops_snapshot_degraded_source` — missing URL → `source: missing`
  - `test_get_source_ops_snapshot_service_unavailable` — all calls fail → `source: unavailable`
  - `test_get_search_ops_snapshot_stale_index` — `within_sla: False` → `freshness_status: stale`
  - `test_get_search_ops_snapshot_missing_url` — missing URL → `source: missing`

## Frontend Handoff Spec

`docs/pantheon-handoffs/OC-003-source-search-ops/FRONTEND_CHANGE_SPEC.md` is complete and includes:
- all 6 routes with request/response shapes
- auth and idempotency rules matching implementation
- degradation rules for missing/unavailable/stale states
- verification curl commands

## Notes

Implementation is clean. Auth roles match the handoff spec exactly. Error handling
correctly maps `SourceSearchOpsClientError` to BFF error codes (400/403/502/503).
The `service_available = any([avail_*])` partial-availability pattern is correct for
a composite read surface.
