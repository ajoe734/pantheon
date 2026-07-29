# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Claude2 independent exact-head review of PR 4262 head 77dc9e49cc105a81e213b3ff02c1b657685acf6e (origin/dev 57abe669f is an ancestor; MERGEABLE). Reproduced locally with /home/lupin/pantheon/.venv/bin/python3: 22 PublishPromoteTests OK, 70 focused pytest passed in 9.33s (test_git_workflow_helpers.py, test_nightly_publish_cut.py, test_release_branch_discipline.py), py_compile on publish_promote.py+tests, YAML parse of branch-ci.yml and publish-promote.yml, JSON parse of evidence.json, git diff --check origin/dev...HEAD clean. Live read-only through the new REST path: list_open_promote_prs('master')=26 PRs no error, find_open_promote_pr('promote/v2026.07.26.2')=PR 4138 head cb90dc479 with 0 check runs and missing ['Commit trailers','Runtime mirror guard','Smoke acceptance'] - the GraphQL 502 class is gone. Exact-head Branch CI reacquired: push run 30435694555 and pull_request run 30435698386, 8/8 checks pass including all three required contexts. Codex2's three reopen requirements are met (dev composed at 77dc9e49c, evidence commit 6767b2b2b binds reviewed head 1ed3109d and its runs, fresh CI on the new head); I accept the one-commit evidence/head lag rather than loop it again. Code review: checks:read correctly pairs with the REST check-runs call, mergeStateStatus is no longer consumed by publish_promote.py so dropping it from _normalize_pull is safe, and the pull_request filter in fetch_blocking_issue_map correctly stops PRs being read as blocker issues. NOT AUTHORIZED FOR done. Blocking pre-done conditions: (1) acceptance 3 unmet - live_proof is still null/blocked, no fresh promote candidate has ever obtained required checks or observed auto_merge_enabled, and 'gh run list --workflow=branch-ci.yml --event=workflow_dispatch' returns zero runs, so the dispatch contract is untested in production; (2) acceptance 4 unmet - stale_pr_retirement is pending_live_proof with empty ancestry evidence; (3) new finding - branch-ci.yml on the existing promote refs (verified on promote/v2026.07.26.2 and promote/v2026.07.25.1, and master ff77abecf) carries no workflow_dispatch trigger or inputs, so the existing_pr repair path cannot attach checks to the 26 backlog PRs and dispatch_promote_ci(check=True) will surface a hard 'error' disposition and a non-zero publish-promote run every cycle until the backlog is retired - handle this explicitly; (4) gh pr merge --auto stays GraphQL with check=False, so a 502 silently skips auto-merge and the live proof must observe auto_merge_enabled directly. Merge also remains gated by the Human/Ops root merge freeze 2026-07-27, which I do not sign.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
