# ACG-BFF-MAIN-CUTOVER-20260828 BFF main.py Composition Cutover Evidence

Owner: Antigravity
Reviewer: Claude
Status: implementation complete; awaiting independent review

## Outcome

Cut over BFF `services/control-plane/bff/main.py` from monolithic route definitions (~2600 lines) down to clean composition root:
- Retained app construction, lifecycle startup/shutdown handlers, middleware, dependency wiring, and canonical domain router inclusion.
- Removed inline domain route bodies, overlays, duplicate route handlers, and runtime pruning hacks (`_prefer_latest_bff_gap004_routes`, `_BFF_GAP004_ROUTE_PATHS`).
- Mounted prepared canonical routers for command adapters, evolution, research, agora, etc.

## Validation

- Pytest passed 144 tests across 11 contract, router, port, and route uniqueness test suites:
  - `services/control-plane/bff/tests/test_actions_to_commands_adapter.py`
  - `services/control-plane/bff/research/test_router.py`
  - `services/control-plane/bff/evolution/test_router.py`
  - `services/control-plane/bff/tests/test_persona_capital_runtime_ports.py`
  - `services/control-plane/bff/tests/test_ranking_evolution_projection_ports.py`
  - `services/control-plane/bff/tests/test_ooda_management_ports.py`
  - `services/control-plane/bff/tests/test_lifecycle_telemetry_governance_ports.py`
  - `services/control-plane/bff/tests/test_persona_training_ports.py`
  - `services/control-plane/bff/tests/test_agora_route_ownership.py`
  - `services/control-plane/bff/tests/test_read_store_fixture_boundaries.py`
  - `services/control-plane/bff/test_normalized_route_uniqueness.py`
- `main.py` boots cleanly with AST parsing and FastAPI application initialization.
