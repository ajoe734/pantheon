# PPL-ALLOC-007 - Binding Visibility And Route Prune

Owner: Antigravity
Reviewer: Codex2
Depends on: `PPL-ALLOC-003`, `PPL-ALLOC-006`
Type: frontend route/information architecture task

## Problem

Operators must be able to see which persona is bound to which paper ledger or
real sleeve. Pages that are diagnostic or legacy must not compete with the
primary workflow.

## Scope

- Update Persona Fleet and Capital list/detail columns to show:
  - paper ledger id;
  - runtime binding id;
  - capital scope;
  - sleeve/pool id;
  - current weight;
  - target weight;
  - binding state.
- Ensure capital list rows no longer show `nan` for configured binding fields.
- Keep `/management/rebalance/:id` as the detail route.
- Keep legacy list routes as redirects only.
- Demote `/management/ranking` to formula diagnostics and link operators to
  Promotion & Allocation for actions.
- Keep readiness pages as readiness checks, not allocation-management pages.

## Acceptance

- Browser tests prove nav exposes one primary promotion/allocation workflow.
- Capital and Persona Fleet views show distinct binding identity for paper and
  real capital rows.
- Legacy routes redirect without rendering duplicate workflow pages.
- Diagnostic/readiness pages contain no primary approve/apply allocation CTA.

## Validation

```sh
git status -sb
npm test -- src/management/pages/oversight/PersonaFleetPage.test.tsx
npm test -- src/management/pages/capitalPoolsFleetFallback.test.ts
npm test -- src/management/pages/CapitalPoolDetail.test.tsx
npx playwright test e2e/management-promotion-allocation-routes.spec.ts --project=chromium
git diff --check
```
