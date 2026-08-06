# Task Brief: SUP-L12-HELD-CLOSE-OVERLAP-GUARD-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Order the held closeout sink behind current controller integration
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Reopen at PR #4425 head a31ddbf8b: defect 4 (manifest actor rebinding) is only partly applied. VERIFIED PASSING, do not redo: _held_close_overlap_is_release_ordered admits only the exact integration<->L12-CLOSE-001 registry pair pinned by CURRENT_CATALOG_CANONICAL_SHA256 plus per-task contract digests, all other overlaps still raise DispatchError; 53 passed on the regression suite; --validate-only --current reproduces task_count 28 and maximum_parallel_frontier_G1 25 with catalog_sha256 6adf2d2e987d8ebed96689e35db346e9f4eacb3d63a0b635bf8a51426f9ce02f; all 4 integrity.source_artifact_sha256 values recompute exactly; the 3 catalog inputs are byte-identical to their 23ae23c21^ blobs; origin/dev dispatcher def-set (24) is a strict subset of head (52) so the merge reverts nothing on dev; PR #4528 collision hazard is now recorded in both artifacts. REQUIRED FIXES (evidence-only, no code change): (1) evidence.json line 274 security_and_safety.two_person_approval.proof still reads 'Independent Antigravity exact-head review is required before merge and closeout' - Antigravity is the OWNER, so this states owner self-review satisfies two-person approval; the reviewer is Claude. (2) evidence.json line 319 acceptance AC5 blocking_until still opens with 'Antigravity exact-head review of the re-cut branch' - same wrong actor in the operative merge-gate condition. (3) README.md restore-fidelity paragraph says the test file omits '6 defs present at 23ae23c21^'; the literal count is 11 omitted defs (5 test_ defs: test_authority_uses_one_validated_snapshot_generation, test_corrected_bff_scope_avoids_nonterminal_lifecycle_overlap, test_current_dry_run_fails_closed_without_journal_authority, test_current_dry_run_fails_closed_without_provisioned_lock, test_previous_current_profile_remains_available_and_exact, plus 6 helpers) against 5 added - 6 is the NET delta, so restate it unambiguously. The 5 dropped tests all exercise the 626631be8 authoritative-snapshot symbols this PR intentionally does not restore, so the omission itself is in scope and needs no code work.

## Summary
修正 current guarded dispatcher 對被 release gate 明確 hold 的 L12-CLOSE-001 誤判為 unordered overlap，同時維持所有其他 live overlap fail-closed。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
