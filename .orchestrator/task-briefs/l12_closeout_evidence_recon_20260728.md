# Task Brief: L12-CLOSEOUT-EVIDENCE-RECON-20260728

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair reconcile-safe closeout evidence for merged nonterminal L12 rows
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Independent exact-head review approved PR #4306 at c385cfc0acd5c005a4b16f828e02ab14800d206b: evidence-only four-file diff; both repaired parent briefs pass reconcile validator with canonical owner/reviewer and full merged delivery commits; PR identities and origin/dev ancestry match; focused pytest 3 passed, py_compile/diff check and eight Branch CI jobs passed. Owner must perform the post-merge reconcile/actor-guard closeout.

## Summary
修復已 merged 但仍無法 reconcile_done 的 L12 closeout evidence；不得重新做已合併實作。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
