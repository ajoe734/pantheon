# SRC-004 Evidence: StrategySpecSeed Builder

## Scope

Implemented a governed `StrategySpecSeed` builder for SD-03 source/evidence lineage.

Task-owned files:

- `services/source_ingestion/strategy_seed_builder.py`
- `services/source_ingestion/tests/test_strategy_seed_builder.py`
- `docs/contracts/strategy_spec_seed.schema.json`
- `support/evidence/SRC-004/README.md`

## Delivered Behavior

- Builds `StrategySpecSeed` from an `EvidenceBundle`.
- Requires `evidence_bundle_id`, source ids, evidence item ids, and citation refs.
- Preserves source/evidence/citation/trace lineage on the seed.
- Distills hypothesis, asset class, market scope, holding period, required data, backend hint, feature hints, label hints, risk notes, and confidence from explicit `strategy_seed` metadata when available.
- Falls back to deterministic text/metadata inference when explicit hints are sparse.
- Rejects rejected source records.
- Emits a review-only promotion lineage edge from seed to StrategySpec without writing registry state.
- Keeps safety metadata explicit: no registry write, no execution route, research-only.

## Verification

```bash
python3 -m py_compile services/source_ingestion/strategy_seed_builder.py services/source_ingestion/tests/test_strategy_seed_builder.py
python3 -m pytest services/source_ingestion/tests/test_strategy_seed_builder.py -q
python3 -m pytest services/source_ingestion/tests -q
python3 -m pytest services/knowledge/evidence/tests/test_bundle.py -q
python3 -m pytest services/source_ingestion -q
git diff --check -- services/source_ingestion/strategy_seed_builder.py services/source_ingestion/tests/test_strategy_seed_builder.py docs/contracts/strategy_spec_seed.schema.json
```

Observed results:

- `test_strategy_seed_builder.py`: 5 passed.
- `services/source_ingestion/tests`: 46 passed.
- `services/knowledge/evidence/tests/test_bundle.py`: 4 passed.
- `services/source_ingestion`: 73 passed.
- `git diff --check`: pass.
