# Task Brief: LOOP-PROD-RUNTIME-BOOT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Shared runtime/task/audit lock protocol bootstrap
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Owner fixed Codex2's exact-head findings (writer-scanner os.replace/os.rename blind spot; scripts/reap_stale_in_progress.py routed through the shared task-state lock; external-dependency and activity-audit-archive symlink following in the dispatcher and common.py; non-hermetic mixed-repo test in scripts/test_ai_status.py), added regression coverage, merged current dev, and refroze checks/evidence at commit a6e8116b5 / 98fc2c5af. Could not run `scripts/ai-status.sh` note/progress: the live canonical status root still fails closed with `RuntimeError: activity event_id duplicate across sources: worker-commit-25c0969133ec31f889e948398d2291c43440256c` (same defect Codex2 already reported), so this status transition is recorded here and on PR #3652 instead. `canonical_writer_guard.py`'s isolated-override finding is intentionally left open: the literal fix breaks the isolated-fixture testing convention used by other dispatcher tests.

## Summary
在 48 個 primary task materialization 前，讓 runtime admission、canonical task state 與 activity audit 的所有 writer 共用穩定 inode lock，並以 process/crash/recovery evidence 證明可安全 dry-run/apply。
