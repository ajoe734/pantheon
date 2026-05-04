# FRONT-STAGING-REPO-HYGIENE-PUBLISH-CLOSEOUT Evidence

Date: 2026-05-04
Owner: Codex
Reviewer: Claude
Task: FRONT-STAGING-REPO-HYGIENE-PUBLISH-CLOSEOUT
Front repo: /home/lupin/code/front-ai-trading-system
Front branch: pkt-004-detail-fix

## Scope

This closeout classifies the dirty front-repo coordination and delivery record
set, confirms that dev/demo modules are still outside the staging-live
production route graph, and records the front branch publication state.

## Dirty Set Classification

Keep and publish:

- Modified `.coordination/requests/*.example.yaml` templates that add the
  current frontend feedback or BFF-gap return shape.
- Modified `.coordination/responses/*-lovable-prompt.md` and
  `*-lovable-ui-task.yaml` files that add contract-ready dependencies,
  required feedback bundles, and replay-clean return instructions.
- Modified and new `.coordination/responses/*-backend-delivery.yaml` and
  `*-contract-ready.yaml` records that mirror Pantheon review outcomes.
- Modified and new `docs/pantheon-delivery/**` delivery notes and contract
  locks that record loop-complete or follow-up-required outcomes.
- Modified and new `docs/pantheon-handoffs/**` specs, examples, prompt files,
  and task files that keep front handoff bundles aligned with Pantheon records.
- `docs/reviews/2026-04-22-exec-closeout-frontend-002-summary.md`, retained as
  a prior closeout summary artifact.

No source, package, or build-tool files were dirty before the closeout commit:
`git diff --name-only -- src package.json package-lock.json bun.lock bun.lockb scripts eslint.config.js tailwind.config.ts vite.config.ts tsconfig.json tsconfig.app.json tsconfig.node.json` returned no paths.

No files were deleted or ignored for this closeout.

## Dev/Demo Route Graph

The production route graph starts at `src/main.tsx` and is checked by
`scripts/check_no_demo_prod_routes.mjs`. The guard walked 160 modules and found
no forbidden demo imports or demo token coupling.

Demo-backed modules still exist in the repository for development or legacy
surfaces, but they are not reachable from the current `src/App.tsx`
staging-live route graph. Examples include `src/pages/health/Health.tsx`,
`src/pages/evolution/Center.tsx`, `src/pages/trainer/Trainer.tsx`,
`src/pages/tools/Center.tsx`, `src/pages/persona/NewPersona.tsx`,
`src/components/dashboard/*`, `src/pages/Alerts.tsx`, and several legacy
persona tab modules. The staging-live routes now use BFF-backed pages or
explicit BFF cutover placeholders instead of those demo modules.

## Verification

- `npm run check:prod-demo-routes`
  - Result: `Production frontend route demo guard passed (160 modules checked).`
- `VITE_PANTHEON_ENV=staging-live VITE_PANTHEON_AUTH_MODE=jwt_bff npm run build`
  - Result: built successfully in 28.04s.
  - Existing warning: browserslist/caniuse-lite database is stale.
  - Existing warning: one production chunk is larger than 500 kB after
    minification.
- `rg -n -e "quick-login" -e "Dev-local login only" -e "Admin User" -e "dev-local:" -e "@/demo" -e "demo/api" -e "demo/zpb" dist`
  - Result: 0 matches.
- `git diff --name-only -- dist`
  - Result: no tracked dist changes.

## Publish Readiness

At inspection time, the front repo was already ahead of
`origin/pkt-004-detail-fix` by one commit:

- `68d8f38a6116d2c1fcaaebc430c6be6ac25527f4`
- Subject: `SVC-BLUEPRINT-FRONT-AUTH-DEMO-CUTOFF frontend auth cutoff`

This task added one narrow front-repo hygiene commit containing only
coordination, handoff, delivery, and review records:

- `b031bd022c918a8c54832cb1de070e6d6f40c1d6`
- Subject:
  `FRONT-STAGING-REPO-HYGIENE-PUBLISH-CLOSEOUT sync front handoff records`

After this commit, the front branch is clean and ahead of
`origin/pkt-004-detail-fix` by two commits: the prior auth cutoff commit and
this hygiene closeout commit. Normal publication should use a non-force push to
`origin pkt-004-detail-fix` after reviewer approval and final owner closeout.
