# SUP-LOST-LEASE-DIRTY-WIP-ADOPTION-20260831 evidence

## Scope

This repair extends the existing supervisor worktree lease path. A replacement
may reuse a dirty worktree only when the canonical lost-lease receipt proves an
exact task, reassigned generation, and no live predecessor. Ordinary dirty,
unfenced, cross-task, or still-live worktrees remain fail-closed. The existing
router, task-state, auth, deployment, and product paths are unchanged.

The adopted worktree is never reset, cleaned, stashed, or given a synthetic
commit. This preserves the legitimate Persona reconciliation WIP that was
already present when its predecessor lease was lost.

## Implementation

- `.orchestrator/supervisor.py` adds a narrow receipt/generation eligibility
  check and passes that result into the existing reused-worktree refresh path.
- `.orchestrator/test_supervisor.py` covers exact-task adoption and negative
  guards for unfenced, pending, cross-task, live-predecessor, advanced-
  generation, and already-materialized receipts.
- No canonical task-state JSON, runtime state, credentials, product routes, or
  deployment configuration is edited by this task.

## Verification

Using the checkout-scoped Python distribution:

```text
14 passed, 205 deselected
```

The focused suite includes the two existing cross-repository workspace guards
that exercise the new fail-closed fallback in legacy non-authoritative
fixtures. The authoritative repair assertions all pass. The broader legacy
supervisor file also passes 218 tests; one unrelated pre-existing
`ReviewDecisionIntentLeaseRecoveryTests` archive-binding fixture fails before
this repair path is exercised. `git diff --check` and Python syntax validation
must be rerun on the final commit;
GitHub checks and exact-head review remain required before merge to `dev`.

## Delivery boundary

This evidence does not claim a live rollout or a completed Persona/browser
proof. After merge, the existing supervisor may safely materialize the
replacement worker, which can finish the original dirty WIP through the normal
branch/PR/review flow. Rollback is a revert of the eventual merge commit.
