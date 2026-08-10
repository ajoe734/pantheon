# Task Brief: SUP-RUNTIME-V10-ROLLBACK-COLLISION-SAFE-DESTINATION-20260810

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make rollback materialization collision-safe without root reuse
- Status: in_progress
- Owner: Antigravity2
- Reviewer: Codex2
- Next: Reject exact head 61027b74cd146cdcefd0b3ac0b5b51fd751ac10c: _atomic_no_replace_rename falls back to check-plus-replacing os.rename when libc lacks renameat2 or returns ENOSYS/EINVAL, and it swallows other renameat2 OSErrors into that fallback. Independent probe proved an attacker-created empty destination after the fallback check is replaced. Remove every replacing fallback: use renameat2(RENAME_NOREPLACE) or an actually atomic no-replace alternative, and fail closed without installing when unavailable or any syscall error occurs. Add regression that simulates unsupported renameat2 plus a collision at install time and proves os.rename is never called, destination is untouched, and no downstream side effects occur; also exercise syscall-level EEXIST after the helper precheck. Re-run focused 6, full 330, git diff --check, update the committed evidence for the new exact head.

## Summary
Repair the next narrow rollback-materialization collision discovered after PR #4726: keep the existing different-root 0305 runtime untouched and provide a deterministic, independently verified fresh rollback identity for the governed promotion transaction.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
