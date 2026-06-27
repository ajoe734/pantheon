# Task Brief: LOOP-AUTO-TEL-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add incident-triggered reconciliation listener
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude2
- Next: Review approved: all acceptance criteria met, PR #2439 already merged. Returned to owner Codex2 for closeout.

## Summary
新增 anomaly/incident-trigger listener，讓 heartbeat loss、order rejection spike 等事件立即觸發 reconciliation。

## Closeout
- Review approved by Claude2 with no required changes.
- Delivery PR: https://github.com/ajoe734/pantheon/pull/2439
- Merge commit: 5f3a9aa32329998d6e4e8d2d00685803fcb59385
- Verified:
  - `python3 -m pytest services/reconciliation-drift/tests/ -q`
  - `python3 -m pytest services/incidents/test_main_routes.py -q`
- Finalization scope: task artifact/status closeout only; no service behavior,
  incident authority, telemetry schema, or live-capital behavior changed.
