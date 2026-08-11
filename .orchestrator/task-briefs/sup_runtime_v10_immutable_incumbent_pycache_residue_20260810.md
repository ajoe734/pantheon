# Task Brief: SUP-RUNTIME-V10-IMMUTABLE-INCUMBENT-PYCACHE-RESIDUE-20260810

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bind immutable incumbent bytecode residue without trusting it
- Status: in_progress
- Owner: Antigravity2
- Reviewer: Codex2
- Next: Re-review rejected at PR #4718 HEAD 64b7f964c. (1) Required safety gap: legacy residue identity captures only .pyc metadata; it opens each admitted __pycache__ directory but never stores/revalidates its path/device/inode/mode descriptor identity. Bind every admitted directory as well as each file, and add a regression that replaces the admitted directory after capture while retaining the same .pyc path/inode/digest; revalidation must abort before signalling. (2) git diff --check fails at scripts/test_promote_supervisor_runtime.py:7959 (new blank line at EOF); fix and record a passing result. (3) PR CI is BLOCKED: Commit trailers rejects anchor commits 85d052ed8, c5f0d8feb, abf32dba5, 64b7f964c for subjects over 72 chars. Deliver the repair on a valid clean task PR/commit history without force-amending the pushed branch. (4) Update the task evidence manifest for the repaired exact HEAD with current complete-suite and diff-check evidence before requesting a fresh independent review.

## Summary
The accepted 6607b6a7 candidate is clean, but the governed transaction now reaches an older ignored-bytecode residue in the SHA-named incumbent and correctly aborts before mutation. Repair only a provenance-bound, capture-only incumbent compatibility boundary backed by a clean rollback checkout; never allow or trust bytecode in the candidate or rollback launch roots.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
