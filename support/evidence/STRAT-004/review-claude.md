# STRAT-004 Review: Evidence / Code Refs Lineage

Reviewer: Claude
Date: 2026-05-16
Status: APPROVED

## Scope Reviewed

Commit d9c834f4 — 5 files changed:
- `services/research/strategy_spec/__init__.py` — exports for lineage helpers
- `services/research/strategy_spec/test_lineage.py` — 6 new lineage regression tests
- `services/control-plane/specs/contract.md` — section 2.4 lineage surface doc
- `services/research/strategy_spec/README.md` — lineage surface documentation
- `support/evidence/STRAT-004/README.md` — evidence and worktree boundary

## Acceptance Criteria Check

**evidence_refs carries EvidenceBundle + EvidenceItem refs from seed lineage**: PASS.
`build_strategy_spec_lineage_refs()` prepends an `evidence_bundle` ref then appends per-item `evidence_item` refs from `seed.evidence_item_ids`. Verified in `test_builds_strategy_spec_lineage_refs_from_seed_evidence_and_repo_code`.

**code_refs carries allowlisted repo/path/commit/symbol/line refs**: PASS.
`_collect_code_refs()` reads from seed metadata, SourceRecord `code_refs` metadata, EvidenceItem `code_ref`/`code_refs` metadata, and repo-source fallback fields. The `test_builds_code_refs_from_repo_fallback_and_evidence_item_metadata` test covers both paths.

**Lineage safety — rejects inputs outside seed lineage**: PASS.
`_validate_seed_inputs()` checks `source_id` membership in `seed.source_ids` and `evidence_item_id` in `seed.evidence_item_ids`. Both rejection paths are tested and raise `StrategySpecLineageError` with the "outside StrategySpecSeed" message.

**Non-execution boundary respected**: PASS.
No registry write, experiment launch, deployment-plan creation, broker route, or order-routing call is present. contract.md section 2.4 explicitly states this.

**Package exports published**: PASS.
`StrategySpecLineageError`, `StrategySpecLineageRefs`, `build_strategy_spec_lineage_refs`, and `attach_lineage_refs_to_strategy_spec_payload` are added to both `__init__.py` imports and `__all__`. Verified by `test_lineage_helpers_are_exported_from_strategy_spec_package`.

**provenance.source_refs preserved on attach**: PASS.
`attach_lineage_refs_to_strategy_spec_payload()` merges existing `source_refs` with `refs.source_ids` using `_unique_strings`, so prior refs are not overwritten. Tested in `test_attach_lineage_refs_to_strategy_spec_payload_round_trips_contract`.

## Test Verification

- `pytest services/research/strategy_spec/test_models.py services/research/strategy_spec/test_lineage.py -q`: 14 passed
- `pytest services/research/strategy_spec -q`: 22 passed

## Non-blocking Observations

1. `StrategySpecLineageRefs.to_lineage_edge()` silently omits the `trace_id` field when `trace_refs` is empty (falls back to `""`). Callers relying on a non-empty `trace_id` in the lineage edge should supply `trace_id` explicitly — not a blocker as the field is optional.

2. Duplicate deduplication in `StrategySpecLineageRefs.__post_init__` checks `(ref_type, ref_id)` uniqueness for evidence_refs but the seed's `evidence_item_ids` could list the same item id twice without being caught by `_validate_seed_inputs`. Deduplication in `_unique_strings` would then collapse them, producing a single ref — acceptable behavior for now.

## Conclusion

All acceptance criteria met. Evidence clear, tests comprehensive, execution boundary respected. Approved and returned to Codex for finalization.
