# EXEC-OSS-VECTORBT-001 Review

Review date: 2026-04-21
Reviewer: Codex
Status: changes requested

## Findings

1. The governed input adapter still accepts records with missing or invalid `date` values, so the claimed date-governance boundary is not actually enforced.

- `GovernedVectorbtInputAdapter.prepare()` reads `rec.get("date", "")`, coerces it to a string, and sorts on that value without validating presence or format in [services/research/vectorbt/adapter/vectorbt_adapter.py](/home/lupin/code/pantheon/services/research/vectorbt/adapter/vectorbt_adapter.py:135) and [services/research/vectorbt/adapter/vectorbt_adapter.py](/home/lupin/code/pantheon/services/research/vectorbt/adapter/vectorbt_adapter.py:159).
- The implementation therefore accepts both missing dates and arbitrary strings such as `not-a-date`; reproduced locally with:
  `python3 -c 'from services.research.vectorbt.adapter.vectorbt_adapter import GovernedVectorbtInputAdapter; from services.research.vectorbt.test_adapter import MINIMAL_DATASET; import copy; bad=copy.deepcopy(MINIMAL_DATASET); bad["records"][0].pop("date", None); print(GovernedVectorbtInputAdapter().prepare(bad).bars_per_instrument["AAA"])'`
  and
  `python3 -c 'from services.research.vectorbt.adapter.vectorbt_adapter import GovernedVectorbtInputAdapter; from services.research.vectorbt.test_adapter import MINIMAL_DATASET; import copy; bad=copy.deepcopy(MINIMAL_DATASET); bad["records"][0]["date"]="not-a-date"; print(GovernedVectorbtInputAdapter().prepare(bad).bars_per_instrument["AAA"])'`
- This contradicts the governed-surface claim that the adapter rejects an invalid date index in [integrations/vectorbt/integration.md](/home/lupin/code/pantheon/integrations/vectorbt/integration.md:69), and there is no regression test covering the rejection path in [services/research/vectorbt/test_adapter.py](/home/lupin/code/pantheon/services/research/vectorbt/test_adapter.py:128).
- Required fix: reject missing or malformed dates in the adapter and add tests that fail on absent/invalid date values.

2. The evidence pack still documents an obsolete artifact and registry contract, so the governed I/O boundary is not actually synced to the implemented baseline.

- The evidence doc still says `artifact_bundle` exposes top-level `strategy_id` and `metrics`, and that `registry_entry.lineage` carries `strategy_spec_ref`, `framework`, and `framework_version` in [integrations/vectorbt/integration.md](/home/lupin/code/pantheon/integrations/vectorbt/integration.md:91) through [integrations/vectorbt/integration.md](/home/lupin/code/pantheon/integrations/vectorbt/integration.md:124).
- The implementation now emits `dataset_summary`, `backtest_config`, `per_instrument_metrics`, `aggregate_metrics`, `registry_hints`, and a different lineage/metadata split in [services/research/vectorbt/adapter/vectorbt_adapter.py](/home/lupin/code/pantheon/services/research/vectorbt/adapter/vectorbt_adapter.py:429) through [services/research/vectorbt/adapter/vectorbt_adapter.py](/home/lupin/code/pantheon/services/research/vectorbt/adapter/vectorbt_adapter.py:506); the tests also assert against the newer shape in [services/research/vectorbt/test_adapter.py](/home/lupin/code/pantheon/services/research/vectorbt/test_adapter.py:186) through [services/research/vectorbt/test_adapter.py](/home/lupin/code/pantheon/services/research/vectorbt/test_adapter.py:230).
- Result: the task's "adapter / smoke-test / governed I/O boundary clear" acceptance is not satisfied yet, because the canonical evidence still describes a different contract than the one the code actually emits.
- Required fix: update `integrations/vectorbt/integration.md` so the documented governed surface and output contract match the current implementation.

## Verification

- Re-ran `python3 services/research/vectorbt/smoke_test.py` with `assertions: OK`.
- Re-ran `python3 -m pytest services/research/vectorbt/test_adapter.py -q` with `28 passed, 5 subtests passed`.
- Re-ran `python3 services/research/vectorbt/worker.py` and confirmed the sample-dataset fallback produces a draft registry entry.
