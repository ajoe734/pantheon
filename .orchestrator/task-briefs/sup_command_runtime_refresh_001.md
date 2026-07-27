# Task Brief: SUP-COMMAND-RUNTIME-REFRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Refresh installed supervisor command runtime safely
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: The rejected dcadf09109d919e35bef078b061faea70b3df7a4 head was refreshed through origin/dev b79d3a540a797fe69851f4dbb3a119ddb647cf9a, then refreshed again after PR #4263 advanced dev to 52aa8a623e68336e1965d7241950cb3c22f0c827; the second merge commit is fd548825f408e3b64c82131ff6bfca1748b13dc5. Task artifacts and live config were hash-identical across both merges; latest validation passed 144/144, 30/30, 7/7, py_compile, manifest parse, and diff check. A 2026-07-27T16:51:09Z read-only snapshot reconfirmed sole live PID 500973 at candidate 29054ab270d552a56ed071cedf3f45150e948b6a, config sha256 adab474b01b99630041cb06d565ae9dbfd7d52badc1d9e612b7cb8d4129de77e, five live worker/queue lease pairs, and equal authoritative projection hashes. Push the new full head, keep auto-merge disabled, wait for all checks, then re-handoff the exact remote SHA/CLEAN result for fresh Codex2 review.

## Summary
在 supervisor truth 修復合併後，將 governed command runtime 更新到精確 accepted dev；重用既有 config，不改 config，不中斷 active lease，保留 rollback。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
