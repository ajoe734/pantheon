# STRAT-002 Evidence: StrategySpec Registry Endpoints

Task: STRAT-002 - StrategySpec registry endpoints
Owner: Codex
Reviewer: Claude
Date: 2026-05-16

## Scope

Added a StrategySpec-specific facade on the registry service while preserving the existing generic registry lifecycle:

- `POST /api/registry/strategy-specs`
- `GET /api/registry/strategy-specs/{registry_id}`
- `GET /api/registry/strategies/{strategy_id}/strategy-specs`
- `POST /api/registry/strategy-specs/{registry_id}/advance`

The facade forces `artifact_type=strategy_spec`, requires lineage, requires `storage_ref` plus `checksum` or derives them from an inline `strategy_spec`, and uses the existing artifact-state machine for `draft -> candidate`.

## Acceptance Mapping

- Source seed can register a `strategy_spec` artifact: covered by `source_seed_id` support in `POST /api/registry/strategy-specs`.
- `strategy_spec` has lineage, `storage_ref`, and `checksum`: endpoint rejects missing lineage and derives inline storage/checksum when needed.
- `strategy_spec` can enter `draft -> candidate`: covered by `POST /api/registry/strategy-specs/{registry_id}/advance`.
- Existing artifact-state and deployment-stage split remains unchanged: facade delegates to `RegistryService.register()` and `advance_artifact_state()`.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/registry/service.py services/registry/main.py services/registry/test_service.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/registry/test_service.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/registry -q
git diff --check -- services/registry/service.py services/registry/test_service.py services/registry/contract.md services/registry/main.py
```

Results:

- `services/registry/test_service.py`: 44 passed
- `services/registry`: 69 passed
- `git diff --check`: passed
