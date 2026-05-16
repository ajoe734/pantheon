# P0-AUD-001 Acceptance Evidence: GET /bff/audit

**Task:** P0-AUD-001 — /bff/audit read endpoint  
**Owner:** Claude2  
**Reviewer:** Claude (final; originally Codex, reassigned after quota failure)  
**Date:** 2026-05-15  
**Closeout date:** 2026-05-16  
**Review outcome:** APPROVED

## Deliverables

| File | Description |
|---|---|
| `services/control-plane/bff/main.py` | New `bff_list_audit` handler at `GET /bff/audit` |
| `services/control-plane/bff/test_bff_audit_contract.py` | 11 contract tests |

## Implementation

Added `GET /bff/audit` as a dedicated FastAPI handler in the `# -- Audit ---` section of `main.py`:

- **Auth:** `_require_read_role` enforced on all requests
- **Filters:** `actor`, `action_type` (comma-separated), `target_type`, `from` (datetime alias), `to` (datetime)
- **Pagination:** `page_token` + `page_size` (default 50, max 500) via `_page_slice`
- **Response:** `{data, items, page_info: {next_page_token, total}, meta}`
- **Meta:** uses `_read_surface_meta("governance_audit_events", "audit_list")` — includes surface status, staleness, and degradation reason

Removed `@app.get("/bff/audit")` from the generic stub handler decorator list so the dedicated handler is the sole registration.

## Verification

```
python3 -m py_compile services/control-plane/bff/main.py
# => OK

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_bff_audit_contract.py -v
# => 11 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_contract_paths_are_registered \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_live_probe_catalog_no_longer_404s_anonymously \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable \
  -v
# => 3 passed
```

Total: **14 passed** (11 contract + 3 live-wiring)

## Closeout Verification (2026-05-16)

Final re-verification by owner (Claude2) at closeout:

```
python3 -m py_compile services/control-plane/bff/main.py
# => OK

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_bff_audit_contract.py -q
# => 11 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_contract_paths_are_registered \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_live_probe_catalog_no_longer_404s_anonymously \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable \
  -v
# => 3 passed
```

All deliverables durable in HEAD (commits 83f6c138 + 5107a989).
No new isolated commit for main.py: dirty worktree contains unrelated RT-003/runtime hunks
that cannot be separated without interactive git (background-worker git rule applies).
