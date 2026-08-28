# ACG-BFF-MAIN-CUTOVER-20260828 BFF main.py Composition Cutover Evidence

Owner: Antigravity
Reviewer: Codex
Status: implementation complete; awaiting independent review

## Outcome

Cut over BFF `services/control-plane/bff/main.py` router composition to clean canonical routers:
- Wired canonical domain routers for `events`, `evolution`, `research`, `jobs`, `management_read_models/ranking_router`, `agora`, and `command_adapters`.
- Removed duplicate `@app.get("/bff/events/stream")` route decorator from `main.py`, leaving `events.router` as single canonical owner.
- Renamed deprecated ranking formula and api_v1 experiment handlers to eliminate duplicate OpenAPI operation IDs.
- Deleted runtime pruning hacks (`_prefer_latest_bff_gap004_routes`, `_BFF_GAP004_ROUTE_PATHS`).
- Verified zero normalized route collisions and zero static route shadowing on `bff_main.app`.

## Validation

- 23 passed: `services/control-plane/bff/test_normalized_route_uniqueness.py`, `test_route_resolution_no_shadowing.py`, `test_architecture_boundaries.py`.
- 72 passed: `services/control-plane/bff/test_bff_evolution_experiment_jobs_events_contract.py`, `tests/test_evolution_programs_population_contract.py`, `tests/test_actions_to_commands_adapter.py`, `test_bff_capital_ranking_rebalance_contract.py`.
- 56 passed: `services/control-plane/bff/research/test_router.py`, `evolution/test_router.py`, `events/test_router.py`, `tests/test_agora_router.py`, `tests/test_agora_route_ownership.py`, `test_mgmt_load_002_shell_summary.py`.
- `main.py` parses cleanly with AST parsing and FastAPI application initialization.
