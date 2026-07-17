# OPS-WATCHDOG-LOCK-QUEUE-POSTMERGE-001

## Objective

Perform the post-merge product-level acceptance for watchdog lock contention;
do not rewrite the watchdog implementation.

Owner: `Codex`. Reviewer: `Codex2`. Target: `pantheon/dev`. Auto-merge: off.
Depends on: implementation PR #3790 merged at
`3705570e4e2a2cb26ba282603a9ca36f0da3c228`.

Read the archived plan
`docs/04/ops_watchdog_lock_queue_2026-07-17/archive/OPS-WATCHDOG-LOCK-QUEUE-POSTMERGE-PLAN-001.md`
and the original lock-queue plan before acting.

## Owned scope

- `docs/04/ops_watchdog_lock_queue_2026-07-17/` evidence/runbook only;
- isolated acceptance fixtures and redacted command output;
- no product source, runtime config, canonical task state, or activity archive.

## Required proof

1. Exact-head local watchdog suite passes and records the actual test count.
2. Ten or more concurrent held-lock probes and a held secondary metric lock are
   bounded, exit successfully, and leave no waiter accumulation.
3. The exact merged implementation is installed in the dev-root checkout, or
   the task stops with a precise install blocker.
4. Three consecutive real scheduler/watchdog cycles pass fresh health checks;
   stale or missing samples fail closed.
5. Before/after hashes, process identity, checkout SHA, commands, and exit
   codes are redacted and archived in an evidence-only PR.

## Delivery gates

Use an isolated task worktree for tests. Any dev-root installation requires the
documented maintenance procedure and planner approval; do not reset a dirty
checkout or kill unrelated processes. The distinct reviewer reruns the exact
final evidence head. This task cannot mark the implementation done by itself;
the original task is closed only after this evidence PR is accepted and merged.
