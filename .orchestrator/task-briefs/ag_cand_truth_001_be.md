# Task Brief: AG-CAND-TRUTH-001-BE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Complete Agora candidate provenance projection
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Review approved at task branch HEAD 7c0beb1df (+review record e40580a9a): all five acceptance criteria verified with reviewer-run tests (21 passed truth/pool/bundle; 26 passed agora regression). Returned to owner Codex for finalization: open per-task PR to dev, merge, then done.

## Summary
讓 candidate DTO 的理由、疑慮、事件、證據與細節都屬於同一真實 candidate 並帶 provenance/as-of；缺欄位明確 unavailable。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Owner closeout checkpoint
- Claude's approval is recorded in `docs/04/pantheon_agora_remaining_work_2026-07-22/archive/AG-CAND-TRUTH-001-BE-review-2026-07-22.md` and binds the reviewed product bytes at `7c0beb1df4935b13f541ba1fc26f8cc5c8e754fa`.
- Codex re-ran the focused truth/pool/bundle suite during finalization: 21 passed.
- Codex re-ran the Agora store/router/projection regression suite during finalization: 26 passed, 2 skipped because `AGORA_RESEARCH_TEST_POSTGRES_DSN` is unset.
- The remaining closeout path is the per-task PR into `dev`, merge, and governed `done` transition with delivery metadata.
