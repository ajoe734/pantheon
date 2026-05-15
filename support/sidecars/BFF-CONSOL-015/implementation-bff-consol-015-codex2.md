# BFF-CONSOL-015 Implementation - Codex2

Task: Mock-only badge implementation (live mode)
Owner: Codex2
Reviewer: Claude (fallback; Claude2 rate-limited during review)
Date: 2026-05-13
Status: review approved; finalizing

## Delivered Scope

- Added the live-mode mock-data badge/empty-state UI in the sibling frontend checkout:
  - `../execute-plans/src/components/data/MockDataBadge.tsx`
  - `../execute-plans/src/components/data/mockDataBadgeModel.ts`
- Embedded the BFF-CONSOL-007 taxonomy JSON into the frontend runtime:
  - `../execute-plans/src/lib/bff-v1/seed-taxonomy.json`
  - `../execute-plans/src/lib/bff-v1/seedTaxonomy.ts`
- Updated `../execute-plans/src/lib/bff-v1/seed.ts` so `mock_only_dev` and `deferred` helpers do not return seed data while `VITE_BFF_MODE=live`.
- Wired visible empty/mock states into the current seed-only helper surfaces:
  - settings Accept-Language helper
  - allocation simulation helper
  - fitness/mutation rule helper panels
  - MCP secrets helper panel
- Added regression tests proving:
  - `mock_only_dev` helpers return empty/null in live mode
  - `deferred` helpers return explicit empty values in live mode
  - `live_required` helpers are not badged or blocked by this helper
  - badge behavior is derived from the taxonomy JSON

## Path Note

The task brief names `execute-plans/src/lib/bff/seed.ts`, but BFF-CONSOL-007 records the actual frontend surface as `../execute-plans/src/lib/bff-v1/seed.ts`. This implementation follows that taxonomy record.

## Verification

```bash
npm test -- src/components/data/MockDataBadge.test.tsx src/lib/bff-v1/__tests__/seedTaxonomy.test.ts
```

Result: `2 passed (10 tests)`.

```bash
npx eslint src/components/data/MockDataBadge.tsx src/components/data/mockDataBadgeModel.ts src/components/data/MockDataBadge.test.tsx src/lib/bff-v1/seedTaxonomy.ts src/lib/bff-v1/__tests__/seedTaxonomy.test.ts src/lib/bff-v1/seed.ts src/management/components/detail/AllocationSimulationPanel.tsx src/management/components/detail/FitnessFormulaPanel.tsx src/management/components/detail/McpSecretsPanel.tsx src/management/pages/phase2/Settings.tsx
```

Result: no lint output.

```bash
npm run build
```

Result: build passed. Vite emitted pre-existing bundle/chunk warnings.

```bash
git -C /home/lupin/code/execute-plans diff --check -- src/components/data/MockDataBadge.tsx src/components/data/mockDataBadgeModel.ts src/components/data/MockDataBadge.test.tsx src/lib/bff-v1/seedTaxonomy.ts src/lib/bff-v1/__tests__/seedTaxonomy.test.ts src/lib/bff-v1/seed.ts src/management/components/detail/AllocationSimulationPanel.tsx src/management/components/detail/FitnessFormulaPanel.tsx src/management/components/detail/McpSecretsPanel.tsx src/management/pages/phase2/Settings.tsx
```

Result: no whitespace errors.

## Closeout Verification

Re-run during closeout:

```bash
npm test -- src/components/data/MockDataBadge.test.tsx src/lib/bff-v1/__tests__/seedTaxonomy.test.ts
```

Result: `2 test files passed (13 tests)`.

```bash
npx eslint src/components/data/MockDataBadge.tsx src/components/data/mockDataBadgeModel.ts src/components/data/MockDataBadge.test.tsx src/lib/bff-v1/seedTaxonomy.ts src/lib/bff-v1/__tests__/seedTaxonomy.test.ts src/lib/bff-v1/seed.ts src/management/components/detail/AllocationSimulationPanel.tsx src/management/components/detail/FitnessFormulaPanel.tsx src/management/components/detail/McpSecretsPanel.tsx src/management/pages/phase2/Settings.tsx
```

Result: no lint output.

```bash
git -C /home/lupin/code/execute-plans diff --check -- src/components/data/MockDataBadge.tsx src/components/data/mockDataBadgeModel.ts src/components/data/MockDataBadge.test.tsx src/lib/bff-v1/seedTaxonomy.ts src/lib/bff-v1/__tests__/seedTaxonomy.test.ts src/lib/bff-v1/seed-taxonomy.json src/lib/bff-v1/seed.ts src/lib/bff-v1/index.ts src/management/components/detail/AllocationSimulationPanel.tsx src/management/components/detail/FitnessFormulaPanel.tsx src/management/components/detail/McpSecretsPanel.tsx src/management/pages/phase2/Settings.tsx
```

Result: no whitespace errors.

Closeout note: frontend implementation files are already durable in sibling checkout commit
`20945d8 BFF-CONSOL-015 add live mock-data badges`; those task paths are clean in the
current frontend worktree. The frontend checkout also has unrelated dirty files from later
tasks, not owned by BFF-CONSOL-015.

## Coordination Note

Claude approved BFF-CONSOL-015 and treated independent Copilot taxonomy critique/signoff as a follow-on audit item. The implementation is taxonomy-backed; if a later taxonomy critique changes a category, the code behavior follows the JSON update.
