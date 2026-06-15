# Review: BFFGAP-LINEAGE - Codex

Date: 2026-06-15
Reviewer: Codex
Task: BFFGAP-LINEAGE (GET /bff/lineage)
Owner: Claude2
Status: APPROVED

## Scope Reviewed

1. `services/control-plane/bff/console_gap/lineage.py` - new factory router.
2. `services/control-plane/bff/main.py` - single `include_router` delta.
3. `services/control-plane/bff/BFF_API_CONTRACT.md` - BFF management route table and endpoint count.
4. `services/control-plane/bff/tests/test_bff_lineage_contract.py` - 5 contract tests.
5. Existing legacy lineage route tests in `services/control-plane/bff/test_pkt003_lineage_view_contract.py`.

## Findings

No blocking findings.

- The new `/bff/lineage` route is isolated behind an APIRouter factory and injects the live read store, auth helpers, snapshot metadata, and clock dependency from `main.py`.
- The route enforces the existing BFF read auth path before returning lineage data.
- Successful responses use the required canonical list envelope: `data`, `items`, `page_info`, and `meta`.
- The response includes `data.nodes` and `data.edges`; `items` mirrors the edge list for list-envelope consumers.
- The missing/unavailable lineage store path returns an explicit `status: unavailable` envelope with empty `nodes`, `edges`, and `items`, not a bare `[]`.
- Query parameters `root_id`, `root_type`, `depth`, and `artifact_id` are documented and passed through to the existing lineage graph read path.
- Existing `/api/v1/lineage` behavior remains covered by the legacy contract suite.
- FastAPI-generated OpenAPI includes `/bff/lineage` with a GET operation.

## Verification

```text
python3 -m pytest services/control-plane/bff/tests/test_bff_lineage_contract.py -v
5 passed in 3.29s

python3 -m pytest services/control-plane/bff/tests/test_bff_lineage_contract.py services/control-plane/bff/test_pkt003_lineage_view_contract.py -q
10 passed in 5.25s

python3 - <<'PY'
import os, sys
sys.path.insert(0, 'services/control-plane/bff')
import main
paths = main.app.openapi().get('paths', {})
print('/bff/lineage' in paths)
print(paths.get('/bff/lineage', {}).keys())
PY
True
dict_keys(['get'])

git show --check HEAD
passed
```

## Acceptance Criteria Check

| Criterion | Status |
|---|---|
| `GET /bff/lineage` returns a canonical list envelope | PASS |
| Response carries lineage `nodes` and `edges` | PASS |
| Missing store returns explicit unavailable/degraded envelope, not bare `[]` | PASS |
| Existing auth/CORS surfaces remain unchanged | PASS |
| `BFF_API_CONTRACT.md` documents the new route and count | PASS |
| Focused contract tests pass | PASS |
| Existing legacy lineage route tests still pass | PASS |

## Decision

Approved. Return to owner Claude2 for final closeout.
