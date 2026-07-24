# Task Brief: OPS-DISPATCH-AUTHORITY-RECOVERY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Harden governed dispatch and retry recovery
- Status: review_approved
- Owner: Codex2
- Reviewer: Antigravity
- Next: Reviewed implementation at f1b026c53040b3c7a7b16102cb0b00132b93d27a under governed runtime (/home/lupin/pantheon-ci-deploy/dev-root, SHA f4f5f8fce8e85cc500684ceee703231fae71619f). Verified path symlink rejection and dirty runtime file validation in dispatch_pantheon_agora_remaining_work_2026-07-22.py and all 12 dispatcher unittests pass cleanly.

## Summary
修正 Agora bulk dispatch 的 authoritative runtime／archive safety 與 GitHub explicit retry 的隔離 worktree 缺陷，避免再次污染 task journal 或落到 shared checkout。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Review Approval
- Reviewer Antigravity approved implementation commit `f1b026c53040b3c7a7b16102cb0b00132b93d27a`.
- The review ran under governed command runtime `/home/lupin/pantheon-ci-deploy/dev-root` pinned to `f4f5f8fce8e85cc500684ceee703231fae71619f`.
- Reviewer verification covered symlink rejection, dirty executable/import-file rejection, and all 12 dispatcher regression tests.

## Owner Closeout Evidence
- Delivery PR `#4017` merged into `dev` as `f4f5f8fce8e85cc500684ceee703231fae71619f`.
- Final focused verification on 2026-07-24:
  - `python3 .orchestrator/test_github_bus.py` — 14 tests passed.
  - `python3 .orchestrator/test_supervisor.py` — 325 tests passed.
  - `python3 scripts/test_dispatch_pantheon_agora_remaining_work_2026_07_22.py` — 12 tests passed.
  - `python3 scripts/dispatch_pantheon_agora_remaining_work_2026-07-22.py --dry-run` — passed for 16 new tasks plus 2 existing task updates with no writes.
  - `python3 -m py_compile ...` for the dispatcher, dispatcher tests, GitHub bus, supervisor, and their focused tests — passed.
  - `git diff --check` — passed.
