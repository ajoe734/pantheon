# Task Brief: OPS-TASK-STATE-NONTERMINAL-DROP-GUARD-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reject nonterminal task-state collapse to empty snapshot
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Continue the canonical follow-up after merged PR #4199 rejection. Replace count-only previous_nonterminal greater than zero to new_nonterminal zero rejection with identity-aware disappearance or mass-replacement detection so legitimate final-task completion remains possible. Require explicit audited drain marker only for actual task removal. Reproduce exact sequence 1592 22 tasks to sequence 1593 empty then recovery. Add last-task-done malformed tasks task-worktree event-log isolation environment scrubbing assertions rejected-write byte invariance parity and unrelated-worker preservation. Antigravity is quota-terminal. No config edit and do not weaken hash-chain validation.

## Summary
防止 authoritative journal 在仍有非終態任務時被 worker 或 supervisor 測試／投影一次寫成空 task state，避免整批 workers 被錯誤 supersede。

## Delivery Record

- Review evidence manifest:
  `docs/deployment/evidence/twelve-loop-gap/OPS-TASK-STATE-NONTERMINAL-DROP-GUARD-001/evidence.json`
- Superseded approach: PR #4199 merged the count-only rule
  (`previous_nonterminal > 0 and new_nonterminal == 0`), which was then
  rejected in review because it also refuses the legitimate completion of
  the final task on the board.
- Delivered guard: `validate_state_transition` now compares the previous and
  new boards by task identity. A commit is refused when task identities that
  were still live disappear — reported as `disappearance` when some live
  identities survive and as `mass replacement` when none do, which is the
  shape of the sequence 1592 → 1593 incident. Real removal stays possible
  through an explicit audited `task_state_drain` marker that names exactly
  the dropped ids.
- Not changing: hash-chain and event-digest validation, journal storage
  layout, `.orchestrator/config.json`, supervisor dispatch policy, and the
  `ai_status` archive/prune flow.
- Known follow-up: `scripts/ai_status.py prune_archived_active_tasks` removes
  an active row whose id already has an archive snapshot. When such a row is
  still nonterminal the guard now refuses that write until the caller emits a
  drain marker. That repair path is outside this task's artifact scope; the
  normal `done` flow is unaffected because a row is archived and pruned only
  after it is already terminal in the predecessor commit.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
