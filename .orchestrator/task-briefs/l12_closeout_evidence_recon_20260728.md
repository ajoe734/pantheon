# Task Brief: L12-CLOSEOUT-EVIDENCE-RECON-20260728

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair reconcile-safe closeout evidence for merged nonterminal L12 rows
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Recovery briefs and task-scoped evidence are validated and ready for exact-head review; do not restart either merged implementation.

## Summary
修復已 merged 但仍無法 reconcile_done 的 L12 closeout evidence；不得重新做已合併實作。

## Reconciliation Scope

- Repair `.orchestrator/task-briefs/l12_dist_001.md` so the immutable merged
  evidence binds `review_approved`, canonical owner/reviewer,
  `ajoe734/pantheon`, and the full reviewed delivery commit.
- Repair `.orchestrator/task-briefs/l12_fleet_worker_outcome_001.md` so it no
  longer claims `in_progress` and binds PR #4301 exact head and merge commit.
- Record the recovery inputs and verification in
  `docs/deployment/evidence/twelve-loop-gap/L12-CLOSEOUT-EVIDENCE-RECON-20260728/reconciliation.md`.
- Do not change or rerun either task's merged implementation.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
