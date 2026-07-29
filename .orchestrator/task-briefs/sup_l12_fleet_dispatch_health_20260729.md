# Task Brief: SUP-L12-FLEET-DISPATCH-HEALTH-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Verify live supervisor and provider-first dispatch health
- Status: review_approved
- Owner: Codex
- Reviewer: Antigravity
- Task branch: `task/SUP-L12-FLEET-DISPATCH-HEALTH-20260729`
- Pull request: `#4328`
- Approved head: Antigravity independently approved
  `54593d18d37a91b6d75bdd41f6426cc02e13c613`.
- Base drift: after that approval, `dev` advanced from
  `3e0ea2136c1ebe0214e07cfbf6411bb20bb5809a` to
  `e51c1220ab3582c9f45f2689dd546ee4a660b4e1`. The exact-head integrator
  therefore returned `waiting`; the approval is preserved as evidence but
  cannot authorize a replacement head.
- Next: the owner merges the latest `origin/dev`, reruns the focused checks,
  and returns PR #4328 to Antigravity for exact-head re-review. Human/Ops then
  supplies the required root-freeze context on that same head before the
  owner reruns the integrator and governed `done`. Ownership and reviewer
  assignment remain unchanged.

## Summary
Fleet health task to ensure this packet is actually handled by supervisor/auto workers.

## Acceptance
- Prove the live supervisor uses `/home/lupin/pantheon-ci-deploy/dev-root`.
- Prove assistant dev packet `pkt-l12-current-gap-drain-20260729T0107Z`
  reaches a pending, processed, receipt, or archive record.
- Read Antigravity and Claude2 readiness from live worker-runtime facts.
- Record repeated failure loops without editing `.orchestrator/config.json`.

## Evidence
- Review manifest:
  `docs/deployment/evidence/twelve-loop-gap/SUP-L12-FLEET-DISPATCH-HEALTH-20260729/evidence.json`
- Human-readable summary:
  `docs/deployment/evidence/twelve-loop-gap/SUP-L12-FLEET-DISPATCH-HEALTH-20260729/README.md`
- Fleet verdict: acceptance satisfied; live health observed as
  `degraded_but_failover_working`.
- Antigravity approval bound PR #4328 and exact head
  `54593d18d37a91b6d75bdd41f6426cc02e13c613`; it is not reused for the next
  refreshed head.

## Owner Closeout Verification
- `python3 -m json.tool evidence.json` passed.
- `sha256sum -c evidence.sha256` passed from the evidence directory.
- `.orchestrator/config.json` remains absent from the task diff.
- The canonical review gate succeeded for
  `54593d18d37a91b6d75bdd41f6426cc02e13c613`.
- The safe auto-integrator dry-run correctly returned `waiting` after `dev`
  advanced to `e51c1220ab3582c9f45f2689dd546ee4a660b4e1`, preserving
  exact-head review semantics.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
