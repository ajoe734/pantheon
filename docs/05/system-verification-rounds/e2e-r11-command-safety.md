# E2E-R11 — Operator command-safety invariants (rollback / kill-switch gating)

**Round:** E2E-R11 (second campaign, round 1)
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r11-command-safety
**Business flow:** operator issues a high-risk command (rollback, hard rollback,
kill-switch, liquidate, safe-mode) → the command must pass confirmation /
two-man / approval / role gates before it can execute.

## Plan & verification

Verify the safety gating on destructive operator commands in the BFF action
catalog (`action_catalog.py`, 70 entries) and lock it with a regression test.

## Live/static result

Examined all 70 catalog entries. The defensible safety invariants **hold**:

```
risk levels: MEDIUM=27 LOW=13 HIGH=26 CRITICAL=4
CRITICAL actions fully gated (confirm_token + two_man + approval + idempotency): 4/4 ✓
destructive actions (ExecuteRollback, HardRollback, ActivateKillSwitch,
  LiquidateAll, IssueRiskOff, IssueSafeMode) require confirm_token + idempotency: 6/6 ✓
actions with no required roles: 0 (nothing fully ungated) ✓
violations: 0
```

I also probed the live `POST /api/v1/operator/commands` path — high-risk command
execution flows through a `requires_confirm_token` check that raises
`CONFIRMATION_REQUIRED` (main.py ~2688), and `HardRollback` / `ActivateKillSwitch`
additionally require `two_man` + `approver` role. (A full live submit needs a
valid command envelope + confirm-token issuance; the catalog-level invariant is
the durable regression guard.)

## Finding

Good-news round: the operator command-safety model is intact. All capital-
affecting / trading-halting commands keep their confirmation, two-man, approval,
idempotency, and role requirements; nothing is fully ungated.

## Disposition

- **Shipped (code/CI):** `test_action_catalog_safety_invariants.py` in the BFF
  test suite (runs in CI). It fails if any destructive action loses its
  confirm-token / two-man / approval / idempotency / role gate — preventing a
  silent weakening of operator command safety.

## Note (a deliberately narrowed invariant)

The HIGH tier is heterogeneous — several HIGH actions (StartRuntime, PauseRuntime,
IssueRiskOff, …) are operational and gated by role rather than approval/confirm.
The test therefore asserts the *defensible* invariants (CRITICAL tier + a curated
destructive-action set + no-ungated-action) rather than a blanket
"HIGH ⇒ approval", which would false-alarm on legitimate operational actions.

## Next round

E2E-R12: agora / consultation flow, or R19's deferred fix — persist signal dedup
across worker restarts.
