# Task Brief: OPS-TASK-STATE-NONTERMINAL-DROP-GUARD-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reject nonterminal task-state collapse to empty snapshot
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Independent review of PR #4224 head fb5f8c58b30fb3bdcf6ad7be03c5e2281a7773a3: declared suites pass (143 store/verifier; 133 ai_status plus 25 subtests; 399 supervisor) and CI is green, but validate_state_transition accepts non-auditable drain markers. Reproduced acceptance when only live A is removed with task_ids [A, NEVER_EXISTED], duplicate [A, A], approved_at not-a-timestamp or 2099-01-01T00:00:00Z, and non-string reason/actor. Require task_ids to be unique non-empty strings exactly equal to the removed live-id set, reason/actor to be non-empty strings, approved_at to be timezone-aware parseable and not future, add negative regressions for extra/duplicate IDs and malformed/future audit fields, then update evidence.json and evidence.sha256. PR is open and not merged; return to Codex2 review after the corrected head is pushed.

## Summary
防止 authoritative journal 在仍有非終態任務時被 worker 或 supervisor 測試／投影一次寫成空 task state，避免整批 workers 被錯誤 supersede。

## Delivery Record

- Review evidence manifest:
  `docs/deployment/evidence/twelve-loop-gap/OPS-TASK-STATE-NONTERMINAL-DROP-GUARD-001/evidence.json`
- Superseded approach: PR #4199 merged the count-only rule
  (`previous_nonterminal > 0 and new_nonterminal == 0`), which was then
  rejected in review because it also refuses the legitimate completion of
  the final task on the board.
- Delivered guard: `validate_state_transition` compares the previous and new
  boards by task identity. A commit is refused when task identities that were
  still live disappear — reported as `disappearance` when some live identities
  survive and as `mass replacement` when none do, which is the shape of the
  sequence 1592 → 1593 incident. Real removal stays possible through an
  explicit audited `task_state_drain` marker that names exactly the dropped
  ids.
- Review round 1 (Codex2, head `fb5f8c58b`): the guard accepted
  non-auditable drain markers. Remediated on this branch — `reason` and
  `actor` must now be non-empty strings, `approved_at` must parse as a
  timezone-aware ISO 8601 timestamp that is not in the future, and
  `task_ids` must be a list of unique non-empty strings whose set equals
  exactly the live ids removed by that commit (a padded, phantom, or
  duplicated id is refused). Ten new negative cases plus two accepted-form
  regressions pin the contract.
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
