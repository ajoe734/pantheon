# STRAT-003 Review: Source -> StrategySpec Conversion Service

Reviewer: Claude
Task: STRAT-003
Owner: Codex
Date: 2026-05-16

## Review Outcome

**Approved.** No blocking findings. All acceptance criteria met.

## Scope Verified

Task-owned files:
- `services/research/strategy_spec/conversion.py`
- `services/research/strategy_spec/test_conversion.py`
- `support/evidence/STRAT-003/README.md`

## Acceptance Criteria Check

| Criterion | Finding |
|---|---|
| Side-effect-free conversion (no registry writes, no exec routes) | `_build_registry_payload` builds the payload dict only; conversion service has no I/O operations. Safety metadata (`research_only=True`, `registry_write_performed=False`, `execution_route=none`) is enforced at the end of `_conversion_metadata`, overwriting any caller attempt to supply contrary values. |
| Builds `StrategySpecSeed` from governed `EvidenceBundle` / `SourceRecord` / `EvidenceItem` | `convert_source_material` delegates to `StrategySpecSeedBuilder.build_seed()` then immediately calls `convert_seed`. |
| Converts seed into schema-backed `StrategySpec` with `draft` lifecycle state | `StrategySpec.from_dict(payload)` with `lifecycle_state=draft` is correctly set. `validate_strategy_spec_payload` is exercised in the conversion test. |
| Registry facade payload contains `source_seed_id`, lineage, inline `storage_ref`, deterministic `checksum`, and `candidate_advance_request` | `_build_registry_payload` emits all required fields. Checksum is `sha256:` of canonical JSON sorted by key. `storage_ref={"backend":"inline","path":"$.entry.metadata.strategy_spec"}` aligns with STRAT-002 facade convention. `candidate_advance_request={"target_state":"candidate"}` matches the advance endpoint contract. |
| Rejected source records blocked at seed-builder guard | `StrategySpecConversionError` raised when `status=rejected`; confirmed by `test_rejected_source_cannot_convert_to_strategy_spec`. |
| Evidence/code lineage preserved through `StrategySpecLineageRefs` | `build_strategy_spec_lineage_refs` + `attach_lineage_refs_to_strategy_spec_payload` correctly threads `evidence_refs` and `code_refs` into the StrategySpec payload and provenance. `to_lineage_edge` emits `edge_type=strategy_spec_evidence_code_linked`. |

## Test Verification (run by reviewer)

```
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/research/strategy_spec/conversion.py services/research/strategy_spec/test_conversion.py services/research/strategy_spec/__init__.py
# passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/strategy_spec/test_conversion.py -q
# 4 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/strategy_spec -q
# 19 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/source_ingestion/tests/test_strategy_seed_builder.py -q
# 5 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/registry/test_service.py -q
# 44 passed

git diff --check -- services/research/strategy_spec/conversion.py services/research/strategy_spec/test_conversion.py support/evidence/STRAT-003/README.md
# clean
```

## Design Observations (non-blocking)

- Safety metadata triple (`research_only`, `registry_write_performed`, `execution_route`) is enforced as a hard write at the bottom of `_conversion_metadata`, which correctly prevents caller override. Test `test_conversion_safety_metadata_cannot_be_overridden` is specific about this invariant.
- `StrategySpecConversionResult` is a frozen dataclass, making results immutable and preventing post-hoc mutation.
- `convert_source_material` and `convert_seed` are clear dual entry points: one from raw evidence, one from an existing seed.
- `_stable_strategy_id` derives a deterministic id from `seed_id:evidence_bundle_id` sha1 digest, ensuring idempotent identity across re-runs with the same inputs.
- The `conversion.py` module is not re-exported through `__init__.py`. This is acceptable given the narrowly scoped task deliverable; callers import directly from `services.research.strategy_spec.conversion`.

## Conclusion

审查通過。conversion service 正確實作 side-effect-free Source/Evidence -> StrategySpec 轉換；safety metadata triple 強制覆蓋、無法被 caller 繞過；lineage 透過既有 `StrategySpecLineageRefs` helpers 保留；registry payload 符合 STRAT-002 facade 合約；4+19+5+44 tests 全通過。無 blocking findings。
