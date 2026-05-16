# STRAT-003 Evidence: Source To StrategySpec Conversion Service

Task: STRAT-003 - Source -> StrategySpec conversion service
Owner: Codex
Reviewer: Claude
Date: 2026-05-16

## Scope

Task-owned files:

- `services/research/strategy_spec/conversion.py`
- `services/research/strategy_spec/lineage.py` (dependency helper imported by the conversion service)
- `services/research/strategy_spec/test_conversion.py`
- `support/evidence/STRAT-003/README.md`

Delivered behavior:

- Builds `StrategySpecSeed` from governed `EvidenceBundle`, `SourceRecord`, and `EvidenceItem` inputs.
- Converts the seed into a schema-backed `StrategySpec` with draft lifecycle state.
- Preserves source/evidence/code lineage through existing lineage helpers.
- Emits a registry facade payload for `POST /api/registry/strategy-specs` with:
  - `artifact_state=draft`
  - `source_seed_id`
  - source-seed lineage
  - inline `storage_ref`
  - deterministic `checksum`
  - inline `strategy_spec`
- Emits the narrow `{"target_state": "candidate"}` request for the existing registry advance endpoint.
- Rejects rejected source records through the seed-builder guard.
- Forces safety metadata to remain `research_only=true`,
  `registry_write_performed=false`, and `execution_route=none`.
- Does not write registry state, launch experiments, create deployment plans, or create execution routes.

Closeout boundary note:

- `conversion.py` imports the reviewed `StrategySpecLineageRefs` helper from
  `lineage.py`, so the STRAT-003 delivery commit includes that dependency to
  keep the conversion service usable from a clean checkout.
- STRAT-004-specific docs, exports, tests, and evidence remain outside this
  STRAT-003 closeout.

## Acceptance Mapping

- Source seed can produce a `strategy_spec` artifact: covered by `StrategySpecConversionService.convert_source_material()`.
- `strategy_spec` has lineage, `storage_ref`, and `checksum`: covered by `registry_payload` and conversion tests.
- `strategy_spec` can enter `draft -> candidate`: covered by registry facade integration in `test_conversion.py`.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/research/strategy_spec/conversion.py services/research/strategy_spec/test_conversion.py services/research/strategy_spec/lineage.py services/research/strategy_spec/__init__.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/strategy_spec/test_conversion.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/strategy_spec -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/source_ingestion/tests/test_strategy_seed_builder.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/registry/test_service.py -q
git diff --check -- services/research/strategy_spec/conversion.py services/research/strategy_spec/test_conversion.py services/research/strategy_spec/lineage.py support/evidence/STRAT-003/README.md support/reviews/STRAT-003-review-claude.md
```

Observed results:

- `py_compile`: passed
- `test_conversion.py`: 4 passed
- `services/research/strategy_spec`: 21 passed at closeout
- `test_strategy_seed_builder.py`: 5 passed
- `services/registry/test_service.py`: 44 passed
- `git diff --check`: passed
