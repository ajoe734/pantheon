# LOOP-PROD-SECURITY-PREFLIGHT-001

Status: deferred security backlog task; do not dispatch in the current phase

Plan:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/SECURITY_PREFLIGHT_AND_HOLD_MATRIX_2026-07-18.md`

## Objective (future security phase)

Inventory every security-sensitive and shared-control-plane task and prepare a
future default-deny admission rule. This task is documentation and future
planning only in the current phase; it must not be dispatched while security
work is paused.

## Fleet-owned scope

- `scripts/dispatch_loop_product_level_remediation_2026-07-13.py`
- `scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py`
- `docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/tasks.json`
- `docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/INDEX.md`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-SECURITY-PREFLIGHT-001`

This is a future fleet control-plane task, not a current product task. Do not
dispatch it now. When security work is explicitly resumed, use a clean task
worktree and submit a reviewed PR. Do not change live `ai-status.json`, the
activity log, credentials, deploy state, or product service behavior.

## Required behavior (when security work resumes)

1. Generate a content-addressed security inventory for all 48 primary tasks,
   the runtime bootstrap, and the two corrective tasks.
2. Mark every task in the hold matrix `security_blocked` until the preflight
   evidence and Human/Ops release decision exist.
3. Fail closed for an unclassified task, an artifact that crosses into a
   shared-control-plane path, a missing block reason, or a release decision
   that is not bound to the exact catalog digest.
4. Permit only read-only audit/plan actions during the hold; no merge, deploy,
   credential access, privileged route activation, live worker admission, or
   canonical status mutation.
5. Preserve the separate runtime-lock gate correction: the runtime lock
   implementation is needed before materialization, but the final verifier and
   Ed25519 completion signature remain final-closeout controls.

## Required evidence

- machine-readable task-to-risk inventory and SHA-256 digest;
- default-deny admission tests and unclassified-artifact negative tests;
- blast-radius, rollback, and maintenance-window matrix;
- exact catalog/source commit and current deployment identity;
- two different fleet reviewers' exact-head approvals; and
- a redacted Human/Ops release decision with actor, scope, order, expiry and
  rollback owner.

## Non-goals

- no product feature implementation;
- no credentials, tokens, private keys, or secret values;
- no live deploy/restart or broker/capital effect;
- no direct live status or historical archive rewrite; and
- no final program completion verdict.

## Handoff

After this task is merged and the preflight decision is accepted, release held
groups one at a time in the recorded order. Every released task still needs
its own PR, checks, deployment proof, independent review, and rollback proof.
