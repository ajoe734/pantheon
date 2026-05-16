# ALT-001 Acceptance

Task: ALT-001 - `/bff/alerts` endpoint
Owner: Codex
Reviewer: Claude2

## Delivered Behavior

- `GET /bff/alerts` exposes the backend-owned Management alerts projection using the existing operator alerts composition.
- The projection includes incident, governance review, approval, kill-switch, runtime, and telemetry-derived alerts with backend-owned severity, category, raised timestamp, summary, and target refs.
- `GET /bff/alerts/{id}` returns the matching projected alert in a `{ data, meta }` envelope.
- Surface metadata preserves fail-closed/degraded semantics: unavailable contributing sources produce an unavailable alert feed rather than false "no alerts" data.
- Read access remains RBAC-gated through the BFF read role check.

## Verification

```bash
pytest -q services/control-plane/bff/test_pkt012_alerts_rail_contract.py
```

Result: `3 passed`.

```bash
pytest -q services/control-plane/bff/test_pkt012_alerts_rail_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_live_probe_catalog_no_longer_404s_anonymously services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_seeded_detail_paths_use_read_model_dtos
```

Result: `5 passed, 1 warning` (existing `datetime.utcnow()` deprecation in `read_store.py`).
