# Task Brief: L12-TEACH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make Persona Teaching authenticated, tenant-safe, and HA
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Independent review verified PR #4149 (6636507f274ffcaf4b6d42b1c3cb8adb09dd49f5), hardening PR #4166 (022bb35f4cd93c82571fcaf2799905a7043efcd2), and evidence refresh PR #4176 (26bf434f2ebab5c47af4692cb1eb71440a74d839) are merged to dev with green gates; reviewer reran the full training-session suite with both real-Postgres cases: 129 passed, 1 warning, and confirmed syntax, JSON, SHA-256, diff checks, and zero leftover l12_teach_* tables. Reopen because canonical tasks.json/task doc require hosted persona terminal readback, while evidence.json explicitly records hosted_terminal_readback.status=not_claimed and acceptance.hosted_proof=deferred_to_downstream_activation_and_verification. Provide merged task-scoped proof binding the exact deployed commit to a real owner-service terminal persona record (no local/mock/seed substitution), or have the supervisor formally revise the canonical task contract/ownership before returning to review.

## Summary
為 teaching API/worker 加 inbound authority 與 tenant，將 session/job/replay 移入 authoritative store，讓 functional health 與真正 eval/commit 結果一致。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
