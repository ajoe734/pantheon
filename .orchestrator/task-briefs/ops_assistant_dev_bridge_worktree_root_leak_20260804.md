# Task Brief: OPS-ASSISTANT-DEV-BRIDGE-WORKTREE-ROOT-LEAK-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix assistant-dev-packet script REPO_ROOT worktree leak (P0)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Reopened by Human/Ops: this task's waiting_for/blocked state predates the 2026-08-05 Codex-quota mass reassignment, which overwrote 'next' with the reassignment note but never re-examined whether the underlying block still applies -- and because blocked tasks are structurally invisible to dispatch_ready_tasks (root cause tracked in SUP-BLOCKED-TASK-RECONCILIATION-20260804), nothing has looked at this since. Re-verify the actual current blocking condition under the new owner/reviewer; re-block with an accurate reason if it genuinely still applies, otherwise continue.

## Summary
scripts/drain_assistant_dev_task_packet_inbox.py 與 scripts/queue_assistant_dev_task_packet.py 的 REPO_ROOT 預設值用 Path(__file__).resolve().parent.parent 推算，在 worker 隔離 worktree 裡執行時會誤把 worktree 自己當成 repo root，導致在 worktree 裡建立出一個空的 .orchestrator/assistant-dev-packets/ 目錄，擋住任務收尾（protocol 禁止動不屬於自己 lane 的 worktree artifact）。應改為優先讀 PANTHEON_STATUS_ROOT 環境變數，其次才 fallback 到 __file__ 位置。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
