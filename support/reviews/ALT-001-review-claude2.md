# ALT-001 Review: /bff/alerts endpoint

Reviewer: Claude2
Task: ALT-001
Owner: Codex
Date: 2026-05-16

## Review Outcome: APPROVED

## Scope Verified

- `GET /bff/alerts` — dedicated route at `main.py:22220`; calls `_build_operator_alerts_payload`; RBAC-gated via `_require_read_role`; returns `{ alerts, summary, meta }` with backend-owned severity, category, raised_at, target refs, and surface degradation metadata
- `GET /bff/alerts/{id}` — handled via `_sem_final_alert_detail` (main.py:24431); scans projected payload for matching `alert_id`; returns `{ data: AlertProjection, meta }`; returns 404 when alert not found and surfaces are healthy; returns degraded detail when alert feed is unavailable
- RBAC: both routes enforce operator read role
- Fail-closed surface semantics correct: when contributing sources are "missing", alert feed reports "unavailable" rather than false "no alerts"

## Contract Doc

`BFF_API_CONTRACT.md` section 10.1.1 added with correct route table:
- `/bff/alerts` → `/api/v1/operator/alerts` backing view
- `/bff/alerts/{id}` → single projected alert detail

## Test Verification

```
pytest -q services/control-plane/bff/test_pkt012_alerts_rail_contract.py
=> 3 passed
```

```
pytest -q services/control-plane/bff/test_pkt012_alerts_rail_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_live_probe_catalog_no_longer_404s_anonymously \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_seeded_detail_paths_use_read_model_dtos
=> 5 passed, 1 warning
```

Warning is pre-existing `datetime.utcnow()` deprecation in `read_store.py:73` — unrelated to this task.

## Notes

- Route ordering: dedicated `@app.get("/bff/alerts")` at line 22220 takes precedence over the catch-all alias at 24843 for the list path; correct.
- No write authority added; projection is purely derived from backend-owned operator alerts.
- Evidence file at `support/evidence/ALT-001/acceptance.md` is accurate.
