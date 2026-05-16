# Review: RT-003 /bff/runtimes list/detail

Reviewer: Claude
Task: RT-003
Owner: Codex
Date: 2026-05-16

## Decision

**Approved**

## Scope Verified

Implemented `GET /bff/runtimes` (list) and `GET /bff/runtimes/{runtime_id}` (detail) as the canonical RuntimeBinding read surface under the BFF layer.

## Checklist

### RBAC
- `_require_read_role` applied to both endpoints. Correct.

### List endpoint (`GET /bff/runtimes`)
- `status` filter: multi-value comma-split, lowercased. Correct.
- `deployment_stage` filter: checks `deployment_stage` first, falls back to `deployment_mode`. Matches canonical binding projection.
- `surface.status == "unavailable"`: returns empty items list with `total=0` and surface metadata surfaced to caller. Does NOT raise 503 for the list case — correct behavior; the caller sees the unavailability signal rather than a hard failure.
- Pagination via `_page_slice` with `page_info.next_page_token`. Correct.
- `meta.surfaces.runtimes` populated from `_dataset_surface_status`. Correct.

### Detail endpoint (`GET /bff/runtimes/{runtime_id}`)
- Dual lookup: first `get_runtime_binding_by_runtime_id`, fallback to `get_runtime_binding` (binding_id). Both paths covered and test-verified.
- When not found AND store unavailable: `_raise_if_read_surface_unavailable` → 503 `DOWNSTREAM_UNAVAILABLE`. Correct.
- When not found AND store available: 404 `OBJECT_NOT_FOUND`. Correct.
- `meta.surfaces.runtime` populated. Correct.

### `_project_canonical_runtime_binding`
- `id` = `binding_id` (correct — UI can use `id` as stable key)
- `binding_id`, `runtime_binding_id`, `runtime_id` all correctly resolved with fallback chains
- `deployment_stage` derived from `deployment_stage` or `deployment_mode` field
- `metadata` deep-copied via `json.loads(json.dumps(...))`. Correct.
- Pure read projection, no write operations.

### No-write authority
- Both endpoints are `GET` only.
- `read_store.list_runtime_bindings()` and `read_store.get_runtime_binding*()` are read-only. Correct.

## Test Evidence

```
pytest -q services/control-plane/bff/test_bff_runtimes_contract.py
# 3 passed

pytest -q services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py::test_bff_deployment_runtime_and_risk_action_routes_return_final_envelopes
# 1 passed

pytest -q services/control-plane/bff/test_bff_consol_016_detail_smoke_a.py
# 2 passed
```

All 6 targeted tests pass in reviewer environment.

## Cross-task Note

Broader `execute-plans`/governance suite has unrelated failures (incident fixture ordering, capital-pool missing-store behavior) as noted by owner. These are pre-existing and not introduced by this task.

## Outcome

RT-003 approved. Returning to Codex for closeout finalization.
