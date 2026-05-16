# STRAT-003 Review — Claude

Task: STRAT-003 Source -> StrategySpec conversion service
Reviewer: Claude
Owner: Codex
Date: 2026-05-16

## Verdict: APPROVED

No blocking findings. Side-effect-free conversion service is correct, safety invariants hold, and STRAT-002/STRAT-003 integration is verified end-to-end.

## Scope reviewed

- `services/research/strategy_spec/conversion.py` — StrategySpecConversionService and helpers
- `services/research/strategy_spec/lineage.py` — StrategySpecLineageRefs and lineage builders
- `services/research/strategy_spec/test_conversion.py` — 4 conversion tests (21 pass across suite)

## Findings

### Correctness

- `convert_source_material` delegates to `_seed_builder.build_seed()` (which enforces rejected-source guard) then calls `convert_seed`. ValueError chain is re-raised as `StrategySpecConversionError`. ✓
- `_build_registry_payload()` hardcodes `artifact_state="draft"` — no caller bypass possible. ✓
- `lineage.source_run_ids` = `[seed.seed_id]`, `source_dataset_refs` = `list(seed.source_ids)` — full provenance traceable. ✓
- `storage_ref` is `inline / $.entry.metadata.strategy_spec` matching STRAT-002 facade conventions. ✓
- Checksum uses same `json.dumps(sort_keys=True)` SHA-256 approach as STRAT-002. ✓
- `candidate_advance_request = {"target_state": "candidate"}` — narrow and correct.

### Safety invariants

- `research_only=True`, `registry_write_performed=False`, `execution_route="none"` are re-applied AFTER user-provided metadata in `_conversion_metadata()` — callers cannot override them. ✓
- `test_conversion_safety_metadata_cannot_be_overridden` explicitly confirms these three fields hold even when caller passes opposing values. ✓
- No registry writes, experiment launches, deployment plans, or execution routes occur anywhere in the service. ✓

### Lineage correctness (`lineage.py`)

- `_validate_seed_inputs` rejects source_records or evidence_items outside the seed's declared lineage. Out-of-scope evidence cannot be attached. ✓
- `StrategySpecLineageRefs` validates `source_ids` and `evidence_refs` are non-empty, and deduplicates evidence_refs and code_refs using keyed sets. ✓
- `attach_lineage_refs_to_strategy_spec_payload` deduplicates `provenance.source_refs` correctly. ✓

### End-to-end integration with STRAT-002

- `test_converted_registry_payload_registers_and_advances_to_candidate`: conversion result posts to `POST /api/registry/strategy-specs`, entry is created as `strategy_spec/draft`, advance to `candidate` succeeds, deployment_stage remains `none`. STRAT-002/STRAT-003 contract is verified. ✓

### Test coverage

- `test_conversion_service_builds_strategy_spec_and_registry_payload_from_source_material`: registry payload shape, lineage, storage_ref, checksum, candidate_advance_request, and lineage edges all verified.
- `test_converted_registry_payload_registers_and_advances_to_candidate`: end-to-end integration with STRAT-002 facade.
- `test_rejected_source_cannot_convert_to_strategy_spec`: seed guard catches rejected sources.
- `test_conversion_safety_metadata_cannot_be_overridden`: safety invariants confirmed.
- 21 tests pass across `services/research/strategy_spec`; 5 seed builder tests pass; 44 registry tests pass — no regressions.

### Minor observations (non-blocking)

- `_conversion_metadata()` hardcodes `"task_id": "STRAT-003"`. Harmless metadata annotation but would require update if refactored.
- `_stable_strategy_id` uses SHA-1 suffix for ID generation — acceptable for non-cryptographic uniqueness.
- `_frequency` defaults to `"research"` when no frequency signals detected — sensible fallback for research context.

## Verification

```
python3 -m pytest services/research/strategy_spec/test_conversion.py -q  → 4 passed
python3 -m pytest services/research/strategy_spec -q  → 21 passed
python3 -m pytest services/source_ingestion/tests/test_strategy_seed_builder.py -q  → 5 passed
python3 -m pytest services/registry/test_service.py -q  → 44 passed
```
