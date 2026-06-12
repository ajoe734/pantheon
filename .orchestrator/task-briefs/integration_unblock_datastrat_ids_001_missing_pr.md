# Task Brief: INTEGRATION-UNBLOCK-DATASTRAT-IDS-001-MISSING-PR

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Unblock integration for DATASTRAT-IDS-001: missing-pr
- Status: in_progress
- Owner: Codex2
- Reviewer: Claude2
- Next: Auto-integrator merged-PR recovery implemented; pending task PR review.

## Summary
auto-integrator 無法安全整合 DATASTRAT-IDS-001: missing-pr. 請修正 PR/rebase/CI 後交回整合。

## Resolution

Root cause: `DATASTRAT-IDS-001` had already merged through PR #1338 into
`dev`, but `scripts/git/auto_integrator.py` only searched open PRs before
opening a `missing-pr` unblock task. This could strand a task in
`review_approved` during the gap between GitHub merge and owner `done`
reconciliation.

The fix teaches the auto-integrator to check for a merged PR with the same
`task/<TASK-ID>` head and `dev` base when no open PR exists. If that merged PR's
merge commit is already in `origin/dev`, the integrator runs the existing owner
`done` reconciliation path instead of opening a false missing-PR unblock.

Evidence:

- `DATASTRAT-IDS-001` PR #1338 merged at
  `66f4fab24353b01b43d279b9bad56c24e97d21f7`.
- `ai-task-archive/tasks/DATASTRAT-IDS-001.json` records terminal status
  `done`.
- `python3 -m pytest scripts/git/test_auto_integrator.py -q` passed: 9 tests.
- `python3 -m py_compile scripts/git/auto_integrator.py scripts/git/test_auto_integrator.py` passed.
- `git diff --check` passed.
