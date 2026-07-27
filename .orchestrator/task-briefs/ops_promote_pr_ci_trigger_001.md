# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Independent review at exact head 50c1a229f4d0bc31035a8dd67146e8dc5f28b211. Verified diff vs origin/dev is scoped to publish-promote.yml (+checks: read), publish_promote.py REST rewrite, its tests, and the evidence manifest. Reproduced both owner test slices myself: 22 unittest PublishPromoteTests OK and 70 pytest across test_git_workflow_helpers.py, test_nightly_publish_cut.py, test_release_branch_discipline.py. Ran a live read-only smoke against the new code paths: list_open_promote_prs('master') returned 26 promote PRs with no error, find_open_promote_pr('promote/v2026.07.26.2') returned PR 4138 at cb90dc479214c6ff0779aff70f915593ec9196c4 with zero check runs and missing_required_promote_checks reporting all three contexts, a nonexistent promote branch returned (None, None), and _required_check_rollup on 50c1a229f returned 8 check runs whose names match REQUIRED_PROMOTE_CHECKS exactly so idempotency holds on real API bytes. Confirmed the fetch_blocking_issue_map pull_request filter is needed and correct because the REST issues endpoint includes PRs while gh issue list did not. Confirmed dropping mergeStateStatus from the normalized rows breaks no consumer inside publish_promote.py. Confirmed CI runs 30299838270 (push) and 30299840477 (pull_request) both carry head_sha 50c1a229f and eight SUCCESS checks. Required follow-ups, none of which block this gate: (1) gh pr merge --auto at publish_promote.py:609 and :686 still uses the GraphQL enablePullRequestAutoMerge path with check=False, so the same 502 class that broke discovery would silently skip auto-merge; the pending live proof must record auto_merge_enabled from an actual observation, not from the call not raising. (2) acceptance items 'prove one exact promote candidate obtains checks and can auto-merge' and 'retire stale promote PRs only with evidence' remain unmet by construction and cannot be met before this merges; do not record done until live_proof and stale_pr_retirement are populated in the manifest and the review block records this decision and reviewed_head. (3) find_open_promote_pr lists all open PRs unfiltered instead of using the REST head= filter; harmless at 62 open PRs, worth tightening later. I did not submit a GitHub PR approval, did not create the 'Pantheon canonical review gate' or 'Pantheon root merge freeze 2026-07-27' commit statuses, and did not touch branch protection; releasing that external gate is a Human/Ops action. PR #4262 is also BEHIND dev and will need a local merge of dev, which will produce a new head requiring fresh CI and last-push approval.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
