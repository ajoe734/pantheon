# STRAT-002 Review: StrategySpec Registry Endpoints

Reviewer: Claude
Task Owner: Codex
Date: 2026-05-16
Commit under review: 6a1ee000

## Summary

Review passed. The StrategySpec registry facade is correctly layered on the existing generic registry service and does not introduce a second lifecycle or bypass the artifact-state machine.

## Artifacts Reviewed

- `services/registry/service.py` — new StrategySpec facade endpoints and helper functions
- `services/registry/main.py` — route listing updated
- `services/registry/test_service.py` — new STRAT-002 tests
- `services/registry/contract.md` — facade documented
- `support/evidence/STRAT-002/README.md` — acceptance mapping and verification

## Acceptance Criteria Check

| Criterion | Status |
|---|---|
| `artifact_type` always forced to `strategy_spec` | PASS — `_strategy_spec_register_payload` sets `artifact_type=ArtifactType.STRATEGY_SPEC` unconditionally |
| Lineage required | PASS — `lineage.is_empty()` raises `RegistryError` if no lineage or `source_seed_id` provided |
| `storage_ref` + `checksum` required or derived from inline payload | PASS — either explicit `storage_ref`/`checksum` or inline `strategy_spec` triggers deterministic SHA256 + inline storage ref |
| `draft -> candidate` delegates to existing artifact-state machine | PASS — `advance_strategy_spec_state` calls `RegistryService.advance_artifact_state()` unchanged |
| Facade filters to strategy_spec only on list/get/advance | PASS — `_ensure_strategy_spec_view` rejects non-strategy-spec artifacts on `GET /{registry_id}` and `POST /{registry_id}/advance`; list endpoint filters by `artifact_type == STRATEGY_SPEC` |
| `source_seed_id` threaded into lineage and metadata | PASS — appended to `lineage.source_run_ids` if not present; stored in `metadata.source_seed_id` |
| artifact_state / deployment_stage split unchanged | PASS — facade never touches deployment_stage; split semantics preserved |

## Code Observations

**`_strategy_spec_register_payload` (service.py:101)**
- Strategy ID empty-string guard is correct.
- Inline strategy_spec strategy_id mismatch check is correct; empty embedded ID is allowed (treated as unset).
- `source_seed_id` appended to `lineage.source_run_ids` only when not already present — idempotent.
- `producer_run_id` falls back to `source_seed_id` when no explicit run_id — correct derivation rule.

**`_ensure_strategy_spec_view` (service.py:169)**
- Raises `RegistryNotFoundError` (→ 404) when entry exists but is wrong type. This is semantically accurate: the entry is *not found* from the StrategySpec facade's perspective.

**`advance_strategy_spec_state` (service.py:332)**
- Fetches and validates before advancing — correct guard order.

**`list_strategy_spec_entries` (service.py:313)**
- `artifact_state` query parameter filter is correct Python; `Optional[ArtifactState] = None` handled cleanly.

## Test Coverage

New tests (`test_service.py` lines 336–432):
- `test_register_strategy_spec_from_source_seed_inline_payload` — full inline registration with lineage derivation, checksum, storage_ref, metadata
- `test_strategy_spec_facade_lists_gets_and_advances_only_strategy_specs` — isolation from other artifact_types, filtered list, full advance
- `test_strategy_spec_facade_rejects_missing_lineage` — 400 on empty lineage
- `test_strategy_spec_facade_rejects_mismatched_inline_strategy_id` — 400 on strategy_id conflict

All existing tests remain green (44 passed in test_service.py; 69 passed full registry suite).

## Findings

No blocking findings. Implementation is clean, correct, and appropriately narrow.

## Decision

APPROVED. Returning to Codex for closeout finalization.
