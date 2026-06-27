# Task Brief: LOOP-AUTO-KNOW-001

## Task
- Title: Add source-to-strategy distillation worker
- Status: review_approved → closing as done
- Owner: Claude
- Reviewer: Claude2
- Next: Owner finalization — all checks passed, PR opened for merge

## Summary
新增 SourceRecord/evidence event 到 StrategySpec draft head 的 distillation worker。

## Deliverables

- `services/source_ingestion/distillation_worker.py` — production module
- `services/source_ingestion/tests/test_distillation_worker.py` — 36 unit tests
- `docs/deployment/evidence/loop-auto-know-001/evidence.md` — evidence packet
- `docs/deployment/evidence/loop-auto-know-001/review-claude2.md` — reviewer approval

## Acceptance Criteria

- [x] New normalized sources enqueue distillation jobs
- [x] Distillation updates mutable draft only
- [x] Manual re-distill and catch-up paths are idempotent

## Verification (closeout)

```
$ python3 -m pytest services/source_ingestion/tests/test_distillation_worker.py -v
36 passed in 2.83s
```

All 36 tests pass. Approved by Claude2 (review-claude2.md). Closeout commit created.
