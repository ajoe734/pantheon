# Task Brief: OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Eliminate telemetry unittest discovery loader errors
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Approved PR #4273 exact head 5ce0b9a58924bb47f9c2b369fc30821411051e81 against origin/dev 7f545a33bf41e5682dc67f50333c84b42f09d17e: baseline reproduced 197 tests with exactly 2 loader errors and 1 skip; current evidence gate 13/13, discovery 20/20, unittest 342 OK with 1 skip and zero loader errors, pytest 353 passed with 1 skip and 35 subtests; schema, checksums, trailers, diff-check, production no-diff, merged dependency evidence, and all eight exact-head Branch CI jobs verified.

## Summary
修正 telemetry 完整 unittest discovery 的兩個裸模組 import error，讓乾淨 repo-root 與 package discovery 都能零 loader error。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
