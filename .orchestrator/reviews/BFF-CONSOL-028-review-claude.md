# Review: BFF-CONSOL-028 — Deferred Seed Adjunct Live Route Follow-up

Reviewer: Claude
Date: 2026-05-14
Status: **APPROVED**

## Artifacts Reviewed

- `docs/bff/seed-elimination-2026-05-13.md`
- `docs/bff/seed-taxonomy.json`
- `../execute-plans/src/lib/bff-v1/seed-taxonomy.json` (at `/home/lupin/code/execute-plans/src/lib/bff-v1/seed-taxonomy.json`)

## Acceptance Criteria Check

### 1. Deferred helpers no longer point at BFF-CONSOL-025
PASS. All 15 deferred helpers have `"follow_up_tasks": []`. No helper in either taxonomy file references BFF-CONSOL-025 or BFF-CONSOL-028 as a pending follow-up.

### 2. Route or hide policy decided per helper family
PASS. Two policies are applied cleanly:

**Promoted to `live_required` (folded into existing parent/detail routes):**
- `bff.routePolicies.list` / `.get` → `/bff/personas` + `/bff/personas/{id}/route-policy`
- `bff.memoryUpdates.list` → `/bff/personas` + `/bff/personas/{id}/memory`
- `bff.consultRules.list` / `.get` → `/bff/personas/{id}/route-policy` consult_policy payload
- `bff.evolutionRuns.list` → `/bff/evolution-programs` + `{id}/runs`
- `bff.evolutionCandidates.forRun` → program-scoped candidate route via run-to-program resolution
- `bff.evaluationRuns.list` / `.forSubject` → `/bff/personas/{id}/evaluations` (non-persona subjects hidden)
- `bff.objectVersions.forSubject` → `/bff/strategies/{id}/specs` (non-strategy subjects hidden)

**Remaining deferred — explicit strict-live empty/unavailable (no seed fallback):**
- `bff.policyVersions.list`
- `bff.permissionMatrix.get` / `bff.permissionMatrices.list`
- `bff.fitnessFormulas.list` / `.get`
- `bff.mutationRules.list`
- `bff.policyViolations.list` / `.forSubject`
- `bff.featureSets.forStrategy`
- `bff.performanceSeries.forStrategy`
- `bff.allocationLimits.forPool`
- `bff.poolFreezes.forPool`
- `bff.promotions.forProgram`
- `bff.metricFreezes.forRebalance`
- `bff.rebalanceOverrides.forRebalance`

All carry notes of the form "BFF-CONSOL-028 decision: strict live returns explicit empty/unavailable until a canonical BFF route or parent DTO exists."

### 3. Strict live tests cover each remaining adjunct family
PASS. Verification reported in seed-elimination-2026-05-13.md:
- `npm test -- seedTaxonomy.test.ts`: 8 tests passed
- `npm test -- seedTaxonomy + lists + client`: 32 tests passed (3 files)
- `npm run build`: production build completed successfully
- `jq empty` on both taxonomy copies: passed
- scoped `git diff --check`: passed

### 4. No seed fallback masquerades as live truth
PASS. The seed-elimination doc confirms: "In `VITE_BFF_MODE=live`, direct live-required helpers call live BFF and throw typed transport errors instead of returning seed rows." Deferred helpers return explicit empty/unavailable, not silent seed rows.

## Category Counts Verified

Both taxonomy copies agree: `live_required: 62, mock_only_dev: 4, deprecated: 2, deferred: 15`. Taxonomy files are byte-for-byte equivalent except for a cosmetic label difference in the human-readable `elimination_order.P0.helpers[0]` field (`"bff.mutations"` in the pantheon copy vs `"mutations"` in execute-plans). This is not in the helper catalog and does not affect runtime behavior.

## Decision

All four acceptance criteria are satisfied. Both taxonomy files are in sync. Deferred adjuncts are now either routed through live parent DTOs or explicitly silent in strict live. No seed fallback is masked as live truth.

**Approved. Returning to Codex for closeout.**
