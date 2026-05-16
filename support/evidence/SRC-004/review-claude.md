# SRC-004 Review — Claude

Reviewer: Claude
Date: 2026-05-16

## Verdict

**Approved.**

## Scope Verified

Task-owned files (commit 5a872693):
- `services/source_ingestion/strategy_seed_builder.py`
- `services/source_ingestion/tests/test_strategy_seed_builder.py`
- `docs/contracts/strategy_spec_seed.schema.json`
- `support/evidence/SRC-004/README.md`

No files outside SRC-004 scope were touched.

## Research Boundary — No Blocking Findings

- `lineage.registry_write_performed` is hardcoded `False` and schema enforces `const: false`.
- `lineage.execution_route` is hardcoded `"none"` and schema enforces `enum: ["none"]`.
- `metadata.research_only` is hardcoded `True` and schema enforces `const: true`.
- `build_strategy_spec_seed_lineage_edge()` emits a plain dict with `registry_write_authority: "registry_service_only"` — no registry call is made by the builder.
- No imports of broker, execution, deployment, or runtime modules.

## Rejection Guard

`_validate_lineage_inputs()` raises `StrategySpecSeedError` for any source record with `status == REJECTED` before any hint extraction. Tested in `test_rejected_source_record_cannot_build_seed`.

## Lineage Preservation

- `lineage` dict carries `evidence_bundle_id`, `source_ids`, `evidence_item_ids`, `citation_refs`, `trace_refs`, and optionally `source_record_refs` (content_ref list when source records are supplied).
- `promotion_requires` includes `["evidence_bundle_id", "strategy_spec_review"]` — both gates are explicit.
- `StrategySpecSeed` validates non-empty `source_ids`, `evidence_bundle_id`, `hypothesis`, `asset_class`, `market_scope`, `required_data`.

## Schema Validity

`strategy_spec_seed.schema.json` is JSON Schema Draft-7 with `additionalProperties: false` at root. `to_dict()` output validated by `Draft7Validator` in `test_builds_strategy_spec_seed_from_evidence_bundle_with_lineage` — passes.

## Deterministic Seed ID

`_stable_seed_id()` produces a SHA-256 prefix from `evidence_bundle_id + source_ids + hypothesis` via JSON-serialized canonical form — stable and collision-resistant for the research scope.

## Inference Fallbacks

`_infer_*` functions are text-pattern based and deterministic for a given input. Explicit `strategy_seed` metadata in source record contexts takes priority over inference. `_extract_list()` cascades: strategy_metadata → context dict → fallback — no randomness.

## Minor Note (Non-blocking)

`StrategySpecSeed.__post_init__` uses `_strings()` (allows empty) for `evidence_item_ids`, but the schema requires `lineage.evidence_item_ids` to have `minItems: 1`. A caller passing a bundle with empty `evidence_item_ids` would produce a seed that fails schema validation at the lineage level. This gap does not affect the delivered tests (all bundles include evidence_item_ids). Recommend a follow-up guard in STRAT-003 or a later hardening task when the builder is wired to production paths.

## Verification Reproduced

```
python3 -m py_compile services/source_ingestion/strategy_seed_builder.py services/source_ingestion/tests/test_strategy_seed_builder.py
→ OK

python3 -m pytest services/source_ingestion/tests/test_strategy_seed_builder.py -q
→ 5 passed

python3 -m pytest services/source_ingestion/tests -q
→ 46 passed

python3 -m pytest services/knowledge/evidence/tests/test_bundle.py -q
→ 4 passed

python3 -m pytest services/source_ingestion -q
→ 73 passed
```
