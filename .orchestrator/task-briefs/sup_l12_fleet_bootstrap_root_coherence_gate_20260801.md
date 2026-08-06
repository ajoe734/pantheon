# Task Brief: SUP-L12-FLEET-BOOTSTRAP-ROOT-COHERENCE-GATE-20260801

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate fleet bootstrap on exact runtime root coherence
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Reviewer Claude gate review of 27cbdb25b: REOPEN. (1) Acceptance 5 is factually false in the manifest: this worker worktree's Git source authority is /home/lupin/pantheon/.git (git rev-parse --git-common-dir; git worktree list shows /home/lupin/pantheon as the owning repo), so the canonical status root IS a Git worktree source authority, yet evidence.json records status_root_isolation PASSED and status_root_isolated_for_canonical_state true. (2) Acceptance 4 unproven: live-supervisor-mainroot-config.json has no worker_worktrees.source_root key at all (worker_worktrees = enabled/root/base_ref/reuse_existing only), and no worker event was shown to record workspace_source_root; the manifest substitutes the worktree path for the source_root setting. (3) Acceptance 3 is false: PANTHEON_COMMAND_ROOT=/home/lupin/pantheon-ci-deploy/dev-root is not a command-runtimes/<40-hex> root and its basename is not HEAD f90e0aae6cb5e86f18b20db9f30bc834f6115745, yet canonical_command_root_content_addressed_and_merged is set true. (4) Acceptance 7 unaddressed: SUP-L12-HELD-CLOSE-OVERLAP-GUARD-20260731 is still status todo, neither cleared nor truthfully terminalized, and is not mentioned in the manifest. (5) Acceptance 8, 9, 10, 12 have no evidence entry at all: no two-Antigravity-owner plus one-Claude-review canary run ids with start/heartbeat/governed progress/terminal outcome, no projection checkpoint parity or duplicate worker/queue lease check, no config SHA recorded before and after, no no-manual-action attestation. (6) Every verification_results entry is a bare prose assertion with no captured command or output, so acceptance 1's required read-back and acceptance 6's cannot-fall-back-to-.orchestrator/config.json property are unverifiable as written. (7) acceptance_checklist_status has 8 booleans that do not map to the 12 row acceptance items, so the manifest cannot be audited against the task row. (8) Delivery is not reviewable as a merge candidate: task/SUP-L12-FLEET-BOOTSTRAP-ROOT-COHERENCE-GATE-20260801 is absent from origin (git ls-remote empty) and gh pr list returns no PR for merge_target dev. Required: re-run each gate item with captured command output, report honest FAIL for any item the live runtime does not satisfy (items 3/4/5 currently do not) rather than asserting PASS, cover items 7-12, key the checklist to the 12 acceptance strings, and push the branch and open the delivery PR.

## Summary
在任何 L12 重盤點或 25-task admission 前，證明 supervisor、watchdog、worker runner、command runtime、Git source root 與 status root 各自綁定正確且真實 auto-worker 可持續運行。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
