# Task Brief: OPS-TASK-STATE-NONTERMINAL-DROP-GUARD-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reject nonterminal task-state collapse to empty snapshot
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Review rejected on PR #4224 head 2e8e1d7286bceb548362302b8be9f917d941102e: behavior passes 159 focused, 133 plus 25 ai_status subtests, and 399 supervisor tests; all six current-head Branch CI checks pass. Committed evidence is stale: verification.integrated_head=a593091043717da8c6c2ddd186b7314d21cb377f predates hardening commit 4503b8757fe7d3813b0c52fc0c79d52313fd50a9 and final head 2e8e1d72; integrated_dev_head=6578ef968 predates branch merge base 8d1b5077996a2d27aafb83ff5756f0290d0e90bc and current dev 643181a067ec5c344faac0766c69de0d5cfb32eb. Manifest lacks final head/base and immutable source/test/evidence blob identities; observed blobs are task_state_store dee49ce7, store tests 40f15003, verifier 80eceac5, verifier tests 11e8d20f, evidence d94d7bbb. PR is OPEN/BEHIND, unmerged, auto-merge disabled. Refresh from dev, recut evidence.json and evidence.sha256 with hardening commit plus new exact final head/base, immutable blob hashes, and exact-head CI/local checks, then return to Codex2 review.

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
