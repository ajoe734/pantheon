# Task Brief: OPS-REVIEWER-APPROVAL-BINDING-GUIDANCE-20260807

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Surface the REVIEW_PR/REVIEW_HEAD_SHA approval-binding requirement so reviewer workers stop landing unbound approves
- Status: todo
- Owner: Codex
- Reviewer: Antigravity
- Next: Chair reassigned owner from Codex2 to Codex: The supervisor lists Codex as the viable blocked-owner rescue target; the task has no human-gate or non-dispatchable marker, Codex is auth-ready and unpaused, and reviewer Antigravity remains independent. Task returned to todo for a blocked-owner rescue dispatch.

## Summary
根因調查(2026-08-06/07,操作者在追查 dispatch 吞吐偏低時發現):`scripts/ai_status.py` 的 `approve` 指令依賴環境變數 REVIEW_PR/REVIEW_HEAD_SHA (resolve_approval_binding, ai_status.py:7090) 才能把 review_approved 事件綁定到 reviewer 實際審查的 exact PR head；沒有這個綁定，approve 只在 task_has_pr_review_target() 判定該任務『已知有 PR』時才會硬擋，否則只印一行 stderr warning 就放行成『unbound』核准，之後被 merge gate 或下一輪 worker 發現、退回重審。這個要求完全沒有文件化：approve 的 usage 字串只有 `Usage: approve <task-id> <message>`，`.orchestrator/skills/` 底下也沒有任何一份 文件提到 REVIEW_PR/REVIEW_HEAD_SHA，reviewer worker 沒有管道知道這件事存在。抽樣 2026-08-05 00:12 ~ 2026-08-06 14:42 這段窗口的 5 個代表任務(OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001、SUP-L12-POST-4380-GAP-REVIEW-20260729、LIFECYCLE-PROJ-PLAN-COMPOSED-HEAD-REVIEW-20260801、OPS-CLOSEOUT-OWNER-REASSIGN-NO-HUMAN-SIGNOFF-20260805、SUP-L12-STALE-PR-RETIRE-20260729)，其中 4/5 都出現至少一次『approve 落地後才發現 unbound、要求 reviewer 重新 approve』的 handoff，是這段時間 review_approved 次數(43 次)遠高於實際 done 數(18 個)的主要成因。這次先只做文件/提示層的修復，不動 resolve_approval_binding 本身的判斷邏輯，降低回歸風險。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
