# RT-003 Runtime BFF Read Surface Evidence

Task: RT-003 - /bff/runtimes list/detail
Owner: Codex
Reviewer: Claude
Date: 2026-05-16 UTC

## Scope

`GET /bff/runtimes` exposes the canonical RuntimeBinding read surface with
`status` and `deployment_stage` filters, pagination, snapshot metadata, and
runtime surface availability metadata.

`GET /bff/runtimes/{runtime_id}` resolves by `runtime_id` first, falls back to
`binding_id`, returns 404 for missing available records, and returns 503
`DOWNSTREAM_UNAVAILABLE` when the canonical runtime binding store is missing.

## Touched Contracts

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_bff_runtimes_contract.py`
- `support/reviews/RT-003-review-claude.md`

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q services/control-plane/bff/test_bff_runtimes_contract.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py::test_bff_deployment_runtime_and_risk_action_routes_return_final_envelopes
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q services/control-plane/bff/test_bff_consol_016_detail_smoke_a.py
```

Observed results:

- Runtime contract: `3 passed`.
- Governance runtime envelope route: `1 passed`.
- CONSOL-016 detail smoke: `2 passed`.
