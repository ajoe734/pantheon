# Task Brief: OPS-REVIEW-BUS-BASE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bind ReviewBus to dev and exact merged task evidence
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Independent reviewer verification passed. Delivery: PR #4024/#4027/#4028/#4030 all MERGED with base=dev (final merge 881954166f92fb04755ac532f837468aec8b2431, head d3aec02ee5f838713f7f60c93ad8e8f6dc5475ac); all 3 required checks (Commit trailers / Runtime mirror guard / Smoke acceptance) pass on #4030; reviewed tree 04031f44f is an ancestor of dev tip 657fe342481d292915d639d1ff5245fdf6a2a3bb and the four artifacts are byte-identical to dev. A1 dev delivery base: default_branch() replaced by delivery_base_branch(); live .orchestrator/config.json (no delivery_base_branches) resolves to dev via branch_workflow.dev_branch while legacy github_bus.default_branch=master is explicitly demoted and unconfigured repos raise an actionable GitHubBusError. A2 no synthetic PR: find_existing_pr/edit/create fallback removed; merged candidates bind via _select_task_pr_evidence with run_gh.assert_not_called() asserted. A3 exact scope: review_branch_for_task returns only task/<TASK-ID> (rejects agent/current branch), and state records pr_number, head_sha, merge_commit, merged_at, base_branch, evidence_kind. A4 fail-closed: skipped_base_mismatch / skipped_head_mismatch / skipped_closed_pr / skipped_incomplete_merge_evidence / skipped_no_head_sha plus GitHubBusError on ambiguity, all caught in sync_outbound and logged as github_review_pr_failed / github_review_pr_skipped. A5 tests: 7 new focused tests; re-ran PYTHONPATH=.orchestrator python3 .orchestrator/test_github_bus.py = 21 OK, py_compile OK, config.example.json parse OK, git diff --check clean. Non-blocking notes for follow-up: remote_branch_exists() is now dead code; with task.github.head_sha set and no PR candidates the code never queries remote_branch_head_sha and reports the inaccurate 'not pushed to origin yet' diagnostic (still fail-closed); branch_has_diff now compares local origin/<base> and local task ref, which fails closed to skipped_no_commits in a stale worktree. Environment-only: test_supervisor.py is 326/327 here, test_run_once_watchdog_safe_mode_suppresses_new_dispatch fails from ModuleNotFoundError: No module named 'pydantic', unrelated to this change.

## Summary
修 ReviewBus 對已合併到 dev 的 task branch 仍建立 master PR，造成跨 226 commits 的錯誤 review；改成依 repo delivery base 與 exact merged PR/commit 做受管 review。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
