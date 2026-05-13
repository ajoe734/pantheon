# BFF-CONSOL-025 Seed Elimination

Task: `BFF-CONSOL-025`
Owner: `Codex`
Reviewer: `Claude`
Date: 2026-05-13

## Scope Note

The task brief names `execute-plans/src/lib/bff/seed.ts`. The active frontend
checkout keeps the public seed accessor at
`../execute-plans/src/lib/bff-v1/seed.ts`; that is the file updated for runtime
behavior. The Pantheon-side taxonomy remains at `docs/bff/seed-taxonomy.json`,
with the frontend runtime copy at
`../execute-plans/src/lib/bff-v1/seed-taxonomy.json`.

## Delivered State

| Acceptance item | Result |
| --- | --- |
| `live_required` helpers map to BFF routes | Done. Direct legacy helpers in `bff-v1/seed.ts` now use strict live reads for list/detail routes, persona route-policy, persona memory, evolution program runs, deployment stages, rebalance workflow, and `/bff/search`. |
| `mock_only_dev` helpers hidden in live mode | Done. Existing `seedTaxonomy` live gating still returns disabled/empty values for `getAcceptLanguage`, allocation simulations, watchers, and MCP secrets in live mode. |
| Deprecated helpers removed from `seed.ts` | Done. `bff.mutations` and `bff.commands.requestConfirmToken` are no longer exported from the seed accessor. Legacy write call sites now import `mutations` directly or use `runActionSafe` / `bffWrites`. |
| Deferred helpers point to follow-up | Done. Deferred taxonomy entries now point to `BFF-CONSOL-028`, created as the follow-up task for adjunct surfaces that need new routes, DTO folding, or strict-live UI removal. |
| Strict live mode has zero silent seed fallback | Done for the seed accessor. In `VITE_BFF_MODE=live`, direct live-required helpers call live BFF and throw typed transport errors instead of returning seed rows. Explicit hybrid fallback remains available only through `withLiveOrMock` list/detail adapters and is still visible via `liveStatus`. |

## Route Coverage Summary

Core Management families now strict-read from their existing BFF routes when the
legacy `bff.*` helper is called in live mode:

- `/bff/strategies`, `/bff/strategies/{id}`
- `/bff/personas`, `/bff/personas/{id}`
- `/bff/capital-pools`, `/bff/capital-pools/{id}`
- `/bff/ranking-formulas`, `/bff/ranking-formulas/{id}`
- `/bff/rebalances`, `/bff/rebalances/{id}`
- `/bff/deployments`, `/bff/deployments/{id}`
- `/bff/evolution-programs`, `/bff/evolution-programs/{id}`
- `/bff/research-experiments`, `/bff/research-experiments/{id}`
- `/bff/artifacts`, `/bff/artifacts/{id}`
- `/bff/jobs`
- `/bff/runtimes`, `/bff/runtimes/{id}`
- `/bff/alerts`, `/bff/alerts/{id}`
- `/bff/incidents`, `/bff/incidents/{id}`
- `/bff/approvals`, `/bff/approvals/{id}`
- `/bff/audit`
- `/bff/tools`, `/bff/tools/{id}`
- `/bff/mcp-servers`, `/bff/mcp-servers/{id}`
- `/bff/mcp-tools`, `/bff/mcp-tools/{id}`
- `/bff/skills`, `/bff/skills/{id}`
- `/bff/channels`, `/bff/channels/{id}`

Adjunct live-required helpers now use their route or parent DTO:

- `bff.routePolicies.forPersona` -> `/bff/personas/{id}/route-policy`
- `bff.memoryUpdates.forPersona` -> `/bff/personas/{id}/memory`
- `bff.evolutionRuns.forProgram` -> `/bff/evolution-programs/{id}/runs`
- `bff.decisionJournal.*` -> `/bff/agora/journal`
- `bff.deploymentStages.forDeployment` -> `/bff/deployments/{id}`
- `bff.rebalanceWorkflow.forRebalance` -> `/bff/rebalances/{id}`
- `bff.search` -> `/bff/search`

## BFF-CONSOL-028 Adjunct Follow-Up

`BFF-CONSOL-028` resolved the deferred adjunct families with one of two strict
live policies.

Routeable helpers now fold into existing BFF parent/detail routes:

- `bff.routePolicies.list/get` synthesize global policy rows from
  `/bff/personas` plus `/bff/personas/{id}/route-policy`.
- `bff.memoryUpdates.list` synthesizes the global queue from
  `/bff/personas/{id}/memory`.
- `bff.consultRules.list/get` derives visible trigger rules from the live
  `consult_policy` payload carried by `/bff/personas/{id}/route-policy`.
- `bff.evolutionRuns.list` uses `/bff/evolution-programs` plus
  `/bff/evolution-programs/{id}/runs`.
- `bff.evolutionCandidates.forRun` resolves the run's program from live run
  routes, then reads `/bff/evolution-programs/{id}/candidates`.
- `bff.evaluationRuns.list/forSubject` route persona subjects through
  `/bff/personas/{id}/evaluations`; unsupported subject kinds return empty in
  strict live.
- `bff.objectVersions.forSubject` routes strategy subjects through
  `/bff/strategies/{id}/specs`; unsupported subject kinds return empty in
  strict live.

Helpers with no safe BFF route or parent DTO remain explicit strict-live
empty/unavailable surfaces: policy versions, permission matrices,
fitness/mutation rules, policy violations, feature sets, performance series,
allocation limits, pool freezes, promotions, metric freezes, and rebalance
overrides. These helpers do not expose seed rows while `VITE_BFF_MODE=live`.

## Verification

Commands run from `../execute-plans`:

```bash
npm test -- src/lib/bff-v1/__tests__/seedTaxonomy.test.ts src/lib/bff-v1/__tests__/lists.test.ts src/lib/bff/__tests__/client.test.ts src/lib/bff-v1/__tests__/writes.test.ts src/lib/v4/h1-wiring.test.ts
npm test -- src/lib/bff-v1/__tests__/seedTaxonomy.test.ts
npm run build
```

Results:

- `5` Vitest files passed, `46` tests passed.
- Final focused seed taxonomy rerun passed, `6` tests passed.
- Production build completed successfully. Vite reported only existing bundle
  size and dynamic-import chunking warnings.
- Scoped `git diff --check` and `jq empty` validation passed for the touched
  seed/taxonomy/docs files.

Additional `BFF-CONSOL-028` verification from `../execute-plans`:

```bash
npm test -- src/lib/bff-v1/__tests__/seedTaxonomy.test.ts
npm test -- src/lib/bff-v1/__tests__/seedTaxonomy.test.ts src/lib/bff-v1/__tests__/lists.test.ts src/lib/bff/__tests__/client.test.ts
npm run build
```

Additional Pantheon-side checks:

```bash
jq empty docs/bff/seed-taxonomy.json
jq empty ../execute-plans/src/lib/bff-v1/seed-taxonomy.json
git diff --check -- docs/bff/seed-elimination-2026-05-13.md docs/bff/seed-taxonomy.json
```

Results:

- Focused seed taxonomy test passed, `8` tests passed.
- Broader BFF client/list/taxonomy run passed, `3` files and `32` tests
  passed.
- Frontend production build completed successfully. Vite reported only existing
  Browserslist, dynamic-import chunking, and bundle-size warnings.
- JSON validation and scoped Pantheon `git diff --check` passed.
