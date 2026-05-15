# SVC-RECONCILIATION-DRIFT-SERVICE Review

Reviewer: Codex
Date: 2026-04-28
Disposition: approved

## Findings

No blocking issues found.

## Acceptance Check

- `services/reconciliation-drift` exposes `/health`, evaluation create/list/read, drift summary, reconciliation status, alert list, and alert handoff routes.
- The service stores only derived reconciliation/drift evaluations and alert handoff state; payloads mark telemetry, lineage, and runtime-manager as truth owners and keep `derived_only=true`.
- Alert records explicitly set `emergency_control_chain_affected=false`, and the smoke path verifies the same on the summary read model.
- `docker-compose.yml` adds `reconciliation-drift-svc`, `RECONCILIATION_DRIFT_DATA_DIR`, telemetry/lineage/runtime/evolution URLs, a named volume, healthcheck, and smoke-stack wiring.
- Focused tests cover drift calculation, degraded/mismatched evidence, alert handoff, and compose config wiring.

## Verification

- `pytest -q services/reconciliation-drift/tests/test_reconciliation_drift_http_service.py services/reconciliation-drift/tests/test_reconciliation_drift_compose_activation.py` passed: 3 tests.
- `docker compose config --quiet` passed.
