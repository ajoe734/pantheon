# PPL-ALLOC-001 - Current State And Page Inventory Guard

Owner: Codex
Reviewer: Claude
Depends on: none
Type: source-of-truth guard

## Problem

The previous promotion-governance closeout proved recommendation submission and
human decision receipts, but the operator workflow remains ambiguous. The team
needs a current-state audit that turns the page inventory and lifecycle rules
into enforceable acceptance criteria before more code lands.

## Scope

- Verify the current Pantheon and Execute Plans dev state against
  `PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md`.
- Record which pages are primary workflow, supporting detail, diagnostics, or
  legacy redirects.
- Confirm whether persona creation currently produces a complete
  `paper_running` bundle or only a partial shell.
- Confirm whether capital pages expose distinct paper ledger, canary sleeve,
  live sleeve/pool, current weight, and target weight identities.
- Produce a short current-state audit artifact under the gap spec archive.

## Acceptance

- Audit names every management page in the spec inventory and marks it keep,
  redirect, demote, repair-only, or expand.
- Audit proves or disproves the current `paper_running` creation invariant with
  route/API evidence.
- Audit lists exact blocking gaps for `PPL-ALLOC-002` through
  `PPL-ALLOC-008`.
- No downstream task can mark complete by relying only on old `PPL-GOV`
  closeout claims.

## Validation

```sh
git status -sb
rg -n "promotion-allocation|persona-league|quarterly-ranking|rebalances|PersonaOnboarding" execute-plans:src || true
curl -fsS "$DEV_FE/deployment.json"
curl -fsS "$DEV_BFF/bff/management/persona-fleet" | jq .
git diff --check
```
