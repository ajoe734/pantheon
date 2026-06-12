# Task Brief: DATASTRAT-SEEDFLOW-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Seed-to-Replication bridge
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Review approved: PR #1335 is merged into dev at 788018f1; reviewer verified seed replication bridge, BFF submit route, idempotency, wrong-status refusal, lineage writeback, and no registry/execution/approved-artifact authority. Local checks: pytest services/source_ingestion/tests/test_replication_bridge.py -q (5 passed); pytest services/control-plane/bff/test_datastrat_seed_replication_bff.py -q (3 passed); py_compile targeted files; git diff --check. Owner Codex should finalize closeout to done.

## Summary
把 promoted StrategySpecSeed 接到 research replication / ExperimentTask 提交，回寫 replication_ref 到 seed lineage；不得建立 execution route 或 approved artifact。
