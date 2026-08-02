# Task Brief: SUP-WORKER-FAILURE-ENVELOPE-GATE-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate worker failures on authoritative terminal envelopes
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Assignment created

## Summary
停止從任意 transcript 與原始碼片段誤判 provider quota；只消費 runner/provider 的權威終止證據。

## Owned Boundary
- Owns worker-failure evidence admission in `.orchestrator/supervisor.py`, its
  focused/full supervisor regressions, and task-scoped review evidence.
- Does not change provider credentials, account/quota grouping, retry
  thresholds, reviewer policy, runtime-state JSON, live process signals, or
  product code.

## Delivered Contract
- Plain-text provider errors are eligible only after `worker_runner.py` has
  published a terminal failure status.
- Claude/Qwen-style top-level stream JSON is eligible without a runner marker
  only when it is an explicit terminal result/error/failure or rejected
  rate-limit control envelope.
- User/tool-result JSON, assistant prose, source/diff lines, search results,
  captured orchestrator records, and non-throttling rate-limit notices remain
  outside failure classification even if the runner later fails.
- A missing process with quota-like transcript text but no terminal envelope
  records `failure_kind: missing_process`; it never pauses a provider as quota.

## Verification
- Focused envelope, pause, and boot reconciliation suite: 88 passed, 392
  deselected, 10 subtests passed.
- Full `.orchestrator/test_supervisor.py`: 480 passed, 74 subtests passed.
- Python compile and `git diff --check`: passed.
- Review evidence manifest:
  `docs/deployment/evidence/supervisor/SUP-WORKER-FAILURE-ENVELOPE-GATE-20260802/evidence.json`.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
