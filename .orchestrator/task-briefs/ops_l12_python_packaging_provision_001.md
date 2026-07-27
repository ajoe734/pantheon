# Task Brief: OPS-L12-PYTHON-PACKAGING-PROVISION-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Provision installed Python package for telemetry AC2
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Independent review approved: implementation candidate dd64fd9d4833766eeb32e7a18901a65a73a5df49 and reviewer-evidence head 733f43f9a155f31b6843e590b478b15790b9b8bf verified. Fresh bare /usr/bin/python3 bootstrap passed 40 tests/23 subtests; telemetry passed 353/1 pre-existing skip/35 subtests; bare discovery ran 20 with only 2 intended ambient-pytest skips; unprovisioned M2/M3 and unsafe explicit/current-mode controls failed closed; finalized evidence gate passed 10/36 subtests and checksum/source digests matched; exact-head push 30268773783 and pull_request 30268774097 each have all four jobs green; PR #4232 remains open, BEHIND, and auto-merge disabled for Codex owner closeout.

## Summary
建立可安裝的 Pantheon Python distribution 與受治理測試環境 provisioning，讓 telemetry discovery AC2 在 foreign cwd、無 PYTHONPATH 下四種執行模式全部通過；不得修改 live supervisor config。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
