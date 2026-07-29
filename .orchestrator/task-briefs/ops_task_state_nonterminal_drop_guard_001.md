# Task Brief: OPS-TASK-STATE-NONTERMINAL-DROP-GUARD-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reject nonterminal task-state collapse to empty snapshot
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Round-2 manifest findings are remediated with no code change. Branch is refreshed from dev 643181a06, PR #4224 is OPEN/CLEAN, and all six Branch CI Gate checks plus all three local suites are green at head 2ab2ca7c0. evidence.json now pins that exact head, its CI run ids, the immutable source blob ids, and a structural rule a reviewer can re-derive at the final head. Returning to Codex2 for independent review.

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
  non-auditable drain markers. Remediated on this branch in `4503b8757` —
  `reason` and `actor` must now be non-empty strings, `approved_at` must
  parse as a timezone-aware ISO 8601 timestamp that is not in the future,
  and `task_ids` must be a list of unique non-empty strings whose set
  equals exactly the live ids removed by that commit (a padded, phantom,
  or duplicated id is refused). Ten new negative cases plus two
  accepted-form regressions pin the contract.
- Review round 2 (Codex2, head `2e8e1d728`): behavior accepted, manifest
  rejected as stale and unbindable — it recorded integration head
  `a59309104` and dev head `6578ef968`, both predating the hardening
  commit and the reviewed head, and carried no final head/base or blob
  identity. Remediated with no code change: the branch was refreshed from
  dev `643181a06` to integration head `8878dc217` (PR #4224 now CLEAN),
  all three local suites and all six Branch CI Gate checks were re-run
  green at that exact head, and `evidence.json` now pins the exact
  head/base/merge-base, the hardening commit, and the immutable git blob
  ids of every source, test, and verifier file. `evidence.sha256` is
  recut over the new manifest.
- Round-2 recut landed as docs-only commit `2ab2ca7c0`, and the manifest is
  re-pinned to it: all six Branch CI Gate checks pass there (push run
  `30226361956`, pull_request run `30226362879`) and all three local suites
  were re-run green there (159; 133 plus 25 subtests; 399). Source blob ids
  are unchanged from round-2 reviewed head `2e8e1d728`. Because a manifest
  cannot record the id of the commit that carries it,
  `verification.manifest_commit_note` states the binding structurally: the
  final head differs from `2ab2ca7c0` only in this brief and the two evidence
  files, which `git diff --name-only 2ab2ca7c0 <final-head>` confirms.
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
