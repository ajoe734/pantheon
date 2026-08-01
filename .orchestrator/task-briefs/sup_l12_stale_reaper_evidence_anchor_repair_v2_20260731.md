# Task Brief: SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-V2-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Supersede Wave0X #4385 evidence-anchor repair with current-head spec
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Helper-claimed by Codex while Codex2 is dispatch-paused.

## Summary
Supersedes preempted immutable task SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-20260731 after bridge rejected spec update. Repair #4385 nonexistent evidence anchor before stale-reaper can satisfy Wave 0.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Dispatch Reconciliation
- The failed runtime receipt at `.orchestrator/assistant-dev-packets/receipts/pkt-l12-wave0x-pipeline-blockers-requeue-20260731T1252Z.json` records a non-retryable bridge assignment conflict for prior task `SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-20260731`.
- That prior task is immutable-bound to packet `pkt-l12-wave0x-fleet-reconcile-fallout-20260731T1225Z`, digest `24fcc3087b0aa6e1aa1d99cd1d03387f2f2fc59f36c1eab79314e5a8192986fc`, and spec `c5a998ac1677d802a3929d63c2d65f5bd60970060ade7d5356776dfac59d39a2`.
- This V2 task is the superseding lane admitted from packet `pkt-l12-wave0x-pipeline-blockers-supersede-20260731T1255Z`; it does not mutate or reuse the immutable prior task row.

## Source Head Verification
- PR #4385 was still open at required head `f5e70e86e01bde005dae5fed94b151c9bc07f389` when inspected on 2026-08-01.
- PR #4395 had moved from the brief's `f68827c8e17d6a1f081afe24f62ba85c116166e8` to `edb1698aa6626d84039243d862dfdc33a8f87770` before this task edited evidence.
- Because the reconciliation head moved and #4385 remains owned by the prior task branch, this task delivers an equivalent superseding PR while preserving #4385's original commits as ancestors.

## Verification
- The checkout-scoped Python distribution was provisioned successfully.
- Five focused stale-reaper regressions passed in 0.010 seconds.
- The complete supervisor suite passed 473 tests in 13.747 seconds.
- Both evidence manifests, commit trailers, whitespace, anchor ancestry, and the `.orchestrator/config.json` boundary passed focused checks.
- Independent exact-head review, protected merge, and governed owner closeout remain required.
