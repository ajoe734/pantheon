# Task Brief: OPS-L12-PYTHON-PACKAGING-PROVISION-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Provision installed Python package for telemetry AC2
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: PR #4232 merged the reviewed delivery from exact head ca9d91b0209bad18dd9330c8301de9f4ba0a1e01 into dev as squash commit 3802799f81778c93728d9dbbe4028289f153c718 after both push and pull_request Branch CI runs passed all four jobs. Fresh bare /usr/bin/python3 bootstrap passed; focused packaging/discovery/evidence gates passed 50 tests/59 subtests; telemetry passed 353 tests/1 pre-existing NATS skip/35 subtests; checksum matched. This task-brief-only follow-up preserves those implementation and review artifacts while restoring merge-commit ancestry for the governed owner done gate, whose active runtime does not treat a squash-merged head as an ancestor.

## Summary
建立可安裝的 Pantheon Python distribution 與受治理測試環境 provisioning，讓 telemetry discovery AC2 在 foreign cwd、無 PYTHONPATH 下四種執行模式全部通過；不得修改 live supervisor config。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
