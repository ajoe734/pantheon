# Task Brief: LOOP-AUTO-KNOW-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add source-to-strategy distillation worker
- Status: review
- Owner: Claude
- Reviewer: Claude2
- Next: Implementation merged in PR #2458 (commit e913b652). Deliverables: distillation_worker.py (source-to-strategy pipeline), 36 unit tests all passing, evidence doc at docs/deployment/evidence/loop-auto-know-001/evidence.md. All 3 acceptance criteria verified: AC-1 normalized sources enqueue jobs, AC-2 only mutable drafts refreshed, AC-3 catch-up and redispatch are idempotent.

## Summary
新增 SourceRecord/evidence event 到 StrategySpec draft head 的 distillation worker。
