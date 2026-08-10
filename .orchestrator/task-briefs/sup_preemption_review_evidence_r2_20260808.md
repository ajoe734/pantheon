# Task Brief: SUP-PREEMPTION-REVIEW-EVIDENCE-R2-20260808

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Redeliver exact-head rejection evidence without rewriting PR #4402
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Fresh supervisor run restarted the task; validate the preserved evidence cut, publish the replacement PR, and hand its exact head to Codex2 for independent review.

## Summary
Publish the exact rejection-evidence net delta from failed PR #4402 through a fresh governed task branch and independent exact-head review, without rewriting stale history or touching PR #4399.

## Replacement Boundary
- Source evidence cut: PR #4402 commit `17d5665f57c302d71f522f9a6e9b0f95c77473e3`.
- Replacement anchor: `59b18f79115305ac541e41207369608e2ef5064b`.
- Preserve PR #4402 and its branch history; do not amend, force-push, delete, or recreate its ref.
- Keep PR #4399 implementation, `.orchestrator/config.json`, runtime, and generated canonical state unchanged.
- Merge the independently approved replacement before closing PR #4402 as superseded. Rollback is a revert of the replacement merge.

## Owner Verification
- Isolated `a924a6f3c0c54982d7efe145750cc99c57bc7f2e` suite: `486 passed, 4 subtests passed`; the four reviewed Python files also passed `py_compile`.
- The checked-in counterexample exited `1` as expected with `dispatch_block_reason="codex local CLI worker is not ready"`, `preemption_decision=true`, and the asserted provider-readiness mismatch.
- JSON parsing, `git diff --check`, the runtime-mirror generated-file guard, and the embedded-frontend rejection check passed.
- README, JSON, Markdown, and reproducer blobs match source evidence commit `17d5665f57c302d71f522f9a6e9b0f95c77473e3`; the source-task brief additionally records the authorized non-rewrite replacement dispatch.
- `PATH="$PWD/.venv-pantheon/bin:$PATH" ./scripts/run-acceptance.sh smoke` passed.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
