# Task Brief: OPS-RECONCILE-REVIEWER-SELF-SERVICE-20260806

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Allow the current reviewer (not just Human/Ops) to execute reconcile_merged_done once verification passes (PR #4589)
- Status: in_progress
- Owner: Codex
- Reviewer: Antigravity
- Next: Re-dispatched to Codex while Claude is dispatch-paused; Antigravity remains the independent reviewer. The reviewer must approve the current branch tip because the prior approval was bound to a rewritten SHA.

## Summary
讓 command_reconcile_merged_done 也接受任務目前的 reviewer 執行,不再只認 Human/Ops。跟已合併的 #4573（owner 轉派自動驗證)互補：#4573 讓判斷自動化,這筆讓已驗證通過後的執行動作不用等人工。使用者明確選擇「reviewer 本人可執行」這個折衷方案,不是完全開放給任何 agent、也不是維持現狀。

## Delivery
- Repository: ajoe734/pantheon
- PR: #4589 (`task/OPS-RECONCILE-REVIEWER-SELF-SERVICE-20260806` -> `dev`)
- Implementation commit: `5ca39995fdf87a1b5e7cbb2aee0c5786f74729bb`
- Changed surface: the actor gate in `command_reconcile_merged_done`
  (`scripts/ai_status.py`) plus its unit coverage in `scripts/test_ai_status.py`.
- Explicitly unchanged: `validate_merged_done_evidence` strictness (exact-head
  binding, chain-audited owner/reviewer drift, delivery repository/commit
  citation), `command_done`, and `command_approve`.

## Superseded Independent Review Evidence
- Reviewer: Antigravity (independent of the implementation owner Claude).
- Prior decision: `review_approved`, recorded 2026-08-06T11:37:07Z.
- Prior bound head: `dd6c4c21db9f2b70c972afbd9728bad1a738090a`.
- Prior proof ref: `refs/tags/pantheon-review/approve/dd6c4c21db9f2b70c972afbd9728bad1a738090a`.
- Required status `Pantheon canonical review gate`: success (status id 51760210537).
- Prior reviewer findings: actor check admits `Human/Ops` and the task's
  *current* reviewer only; a reviewer reassigned away no longer qualifies
  because the gate reads `task["reviewer"]` at execution time; evidence
  validation was not weakened.

## Exact-Head Re-review Requirement
The prior decision is retained as audit evidence only. Its exact-head binding
was invalidated by the rewrite below and must not be reused for this task's
closeout. Antigravity must independently review and approve the current task
branch tip, including this manifest, through the governed approval flow before
PR #4589 can merge. No source behavior changed during this owner re-dispatch.

## Head Rewrite Record (auditable)
The originally approved head `dd6c4c21db9f2b70c972afbd9728bad1a738090a` could not
merge: its subject `OPS-RECONCILE-REVIEWER-SELF-SERVICE-20260806: add task brief
evidence manifest` was 78 characters, and the required `Commit trailers` check
(`scripts/git/check_commit_trailers.py`, re-scanning `origin/dev..<head>`)
rejects any subject over 72. Because that check re-scans the whole PR range, no
follow-up commit could clear it; per `docs/conventions/GIT_WORKFLOW.md` §7.2
force push is allowed on `task/*`, so the tip was re-created with an identical
file payload under a compliant subject and pushed with `--force-with-lease`.
The rewrite changed no reviewed source file: `scripts/ai_status.py` and
`scripts/test_ai_status.py` remain byte-identical to the replacement's parent.

## Verification
- `/home/lupin/pantheon/.venv/bin/python -m pytest scripts/test_ai_status.py -q`
  -> 194 passed, 31 subtests passed on the rewritten source head.
- `python3 -m py_compile scripts/ai_status.py scripts/test_ai_status.py` -> clean.
- `git diff --check` -> clean.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
