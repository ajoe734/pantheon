# Task Brief: SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-V2-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Supersede Wave0X #4385 evidence-anchor repair with current-head spec
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Addressing reviewer reopen items for durable-record defects: (1) Rebind task brief & evidence.json owner=Antigravity, reviewer=Claude. (2) Demote prior Codex2 review in evidence.json to prior_reviews history and set current review pending Claude. (3) Refresh task_head_sha and dev_head composition fields. (4) Document dev-side supervisor import breakage as out-of-scope while verifying composed head 941c15a3.

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

## Observed Out-of-Scope Condition
- At current `dev` head (e.g. `23ae23c21` / `4ee7fc95fe5c8aafa9c3d8c60f4882b6a2fbaf4c`), `.orchestrator/supervisor.py:90` attempts to import `provider_auth_probe_due` from `provider_permissions.py`, which does not define it.
- This breakage is present on `dev` and out of scope to fix in this evidence-anchor task.
- Verification of supervisor tests was confirmed on composed head `941c15a34208e54e96cdd148ba3a5bfcd339abab`.

## Verification
- Provisioned checkout-scoped Python distribution.
- Verified corrected anchor `9d53a94a295d71ee49aea6f4b96e47fbcfd29093` ancestry and confirmed invalid object `9d53a94a265c55af4c8d15c50ab3751f1440ac0f` is absent.
- Verified JSON manifests, `git diff --check`, commit trailers, and `.orchestrator/config.json` boundary.

