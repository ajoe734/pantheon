# Review: DATASTRAT-IDS-007 — Negative-Memory Matcher (Safety)

Reviewer: Claude2
Date: 2026-06-12
PR: #1346 (merged)

## Verdict: APPROVED

All acceptance criteria met. Tests pass. Blocking guard correctly implemented at two layers.

## Acceptance Criteria Check

| Criteria | Status |
|---|---|
| Blocking match prevents seed acceptance | PASS — `_assert_no_blocking_negative_memory` in `StrategySpecSeedStore.save` refuses writes; persona discovery adds `negative_memory_blocking_match` hard blocker |
| Warning surfaces on seed card | PASS — warning-level matches persisted in `negative_memory_match` dict on `StrategySpecSeed` and in store |
| Deterministic/keyword match v1 | PASS — Jaccard scoring over 5 weighted term groups; no embedding call |
| `StrategySpecSeed` carries `negative_memory_match` | PASS — field added to dataclass, schema contract updated |
| Materializer compares against store records + supplied records | PASS — `list_negative_memory_records()` auto-loads rejected/retired/failed seeds; combined with caller-supplied records before build |
| `blocking` refused by store | PASS — `_assert_no_blocking_negative_memory` raises `StrategySpecSeedStoreError` |
| `warning` retained for read-model display | PASS — test `test_warning_negative_memory_match_is_persisted_for_seed_card` confirms round-trip |

## Test Verification

```
python3 -m pytest services/source_ingestion/tests/test_negative_memory_matcher.py \
  services/source_ingestion/tests/test_strategy_seed_builder.py \
  services/source_ingestion/tests/test_strategy_seed_store.py -q
# 26 passed in 2.92s

python3 -m pytest services/control-plane/persona/test_persona_strategy_discovery.py -q
# 6 passed in 0.81s
```

## Design Notes

- The dual-layer blocking guard (store write + persona discovery hard-blocker) is the correct safety pattern: write boundary and read boundary both refuse the blocked seed.
- `negative_memory_record_from_seed` correctly projects only seeds whose status is in `_BLOCKING_STATUSES` or have `metadata.negative_memory=true` — avoids feeding draft/promoted seeds back as negative signals.
- `_score` skips records with fewer than 2 matched terms even if similarity crosses the threshold, preventing spurious single-token matches.
- Schema contract `strategy_spec_seed.schema.json` correctly uses `additionalProperties: false` on `negative_memory_match` to prevent schema drift.
- EPIC doc correctly notes IDS-007 as SAFETY and "must land with or before IDS-004."

## No Required Changes

Implementation matches the v1 contract described in the EPIC. Embedding-based similarity is explicitly deferred per the EPIC's deferred list.
