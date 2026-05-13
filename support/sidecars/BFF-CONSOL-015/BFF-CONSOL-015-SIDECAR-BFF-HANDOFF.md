# BFF-CONSOL-015 Sidecar: BFF and Frontend Handoff Packet

Task ID: BFF-CONSOL-015-SIDECAR-BFF-HANDOFF
Parent Task: BFF-CONSOL-015 - Mock-only badge implementation (live mode)
Helper Kind: bff_handoff_packet
Prepared by: Claude
Reviewer: Codex
Date: 2026-05-13
Mutates canonical truth: false

## Purpose

This packet gives downstream frontend and operator tooling work a support-only
reference for the mock-data badge contract that BFF-CONSOL-015 delivered. It
does not change L1 canonical truth, core contracts, runtime code, registry code,
or governance implementation.

BFF-CONSOL-015 added the live-mode badge and empty-state UI surface that makes
seed-backed helpers visually distinct from live-backed data in any non-mock BFF
mode. The badge behavior is fully driven by the BFF-CONSOL-007 taxonomy JSON,
which classifies all 83 seed helpers into four categories.

## Parent Task Delivery Summary

BFF-CONSOL-015 is `done`. Sibling frontend commit `20945d8` (in
`../execute-plans`) and pantheon review/sidecar commit `dd8345e0` delivered:

| Artifact | Path | Purpose |
|---|---|---|
| `MockDataBadge` | `src/components/data/MockDataBadge.tsx` | Badge + empty-state presentational components |
| `mockDataBadgeModel.ts` | `src/components/data/mockDataBadgeModel.ts` | Badge model logic; null in mock mode |
| `seedTaxonomy.ts` | `src/lib/bff-v1/seedTaxonomy.ts` | Behavior resolver reading taxonomy JSON |
| `seed-taxonomy.json` | `src/lib/bff-v1/seed-taxonomy.json` | BFF-CONSOL-007 taxonomy embedded in frontend |
| `seed.ts` (updated) | `src/lib/bff-v1/seed.ts` | Live-mode gates: `liveEmpty`, `delaySeed` |

Four UI surfaces were wired:

| Panel | Helper name | Badge type |
|---|---|---|
| `Settings.tsx` | `bff.getAcceptLanguage` | `MockDataBadge` (mock_only_dev -> disabled) |
| `AllocationSimulationPanel.tsx` | `bff.allocationSimulations.forRebalance` | `MockDataEmptyState` (deferred) |
| `FitnessFormulaPanel.tsx` | `bff.fitnessFormulas.list` | `MockDataEmptyState` (deferred) |
| `FitnessFormulaPanel.tsx` | `bff.mutationRules.list` | `MockDataEmptyState` (deferred) |
| `McpSecretsPanel.tsx` | `bff.mcpSecrets.forServer` | `MockDataEmptyState` (deferred) |

Verification from the parent closeout:

```
npm test -- src/components/data/MockDataBadge.test.tsx \
             src/lib/bff-v1/__tests__/seedTaxonomy.test.ts
=> 2 passed (10 tests)

npm run build
=> Passed; pre-existing bundle/chunk warnings only.
```

## Badge Contract (Locked)

The following behavior is asserted by 10 tests and must not change without a
corresponding test update:

| Taxonomy category | `getSeedHelperLiveBehavior()` return | Badge component | Visual tone |
|---|---|---|---|
| `mock_only_dev` | `"disabled"` | `MockDataBadge` | `blocked` (red) - "mock data disabled" |
| `deferred` | `"empty_state"` | `MockDataEmptyState` | `warning` (amber) - "Live data not wired" |
| `deprecated` | `"legacy_mock"` | `MockDataBadge` | `muted` (grey) - "legacy mock data" |
| `live_required` | `"live_required"` | none (returns null) | no badge rendered |

Badge visibility rule: `getMockDataBadgeModel()` returns `null` when
`configuredMode === "mock"`. Badges only appear when `VITE_BFF_MODE` is `live`,
`hybrid`, or `strict`.

Live-mode seed gate rule: `seedHelperMustReturnEmptyInLive(helperName)` returns
true for `mock_only_dev` (disabled) and `deferred` (empty_state) categories when
`VITE_BFF_MODE=live`. The `liveEmpty` and `delaySeed` wrappers in `seed.ts`
invoke this check before returning any seed value.

Path note: the task brief named `execute-plans/src/lib/bff/seed.ts`. The
actual frontend surface is `execute-plans/src/lib/bff-v1/seed.ts` per
BFF-CONSOL-007's taxonomy record. This deviation is documented in the
parent implementation sidecar.

## Taxonomy Count Baseline

As of BFF-CONSOL-007 (commit `42ac7b0e`), the taxonomy JSON contains 83 helpers:

| Category | Count | Live-mode behavior |
|---|---|---|
| `live_required` | 52 | no badge; seed not blocked |
| `deferred` | 25 | empty_state badge; seed gated |
| `mock_only_dev` | 4 | disabled badge; seed gated |
| `deprecated` | 2 | legacy_mock badge; seed not gated |
| **Total** | **83** | |

## BFF Query Gap Matrix

Helpers in `deferred` and `mock_only_dev` categories represent surfaces that
do not yet have a safe live route replacement.

### Currently Gated (seed returns empty in live mode)

| Helper name | Category | Follow-up task(s) |
|---|---|---|
| `bff.getAcceptLanguage` | mock_only_dev | BFF-CONSOL-015 (badge only; no live route needed) |
| All 25 deferred helpers | deferred | Various (see taxonomy JSON `follow_up_tasks` per entry) |

### Deprecated (seed not blocked, but callers should migrate)

| Helper name | Category | Replacement path |
|---|---|---|
| `bff.mutations` | deprecated | `POST /bff/v1/commands` or `POST /bff/actions/{entityType}/{entityId}/{actionId}` - BFF-CONSOL-019, BFF-CONSOL-020 |
| `bff.actions` (where applicable) | deprecated | Command client path - BFF-CONSOL-020 |

### Still Open for Frontend Integration

| Gap | Action required |
|---|---|
| 25 deferred helpers have no live route yet | Each deferred entry names the follow-up task responsible for wiring or hiding the surface |
| BFF-CONSOL-025 seed elimination | Must consume this taxonomy as the elimination priority input (P0 = mock_only_dev + deprecated; P1-P3 = deferred by group) |
| BFF-CONSOL-028 adjunct helpers | Deferred surfaces in governance/evolution/capital families need route or hide decisions before strict cutover |
| Copilot taxonomy signoff | Deferred from BFF-CONSOL-015 approval; follow-on audit will validate category assignments against Copilot critique |

## Operator Journey

### Live mode: page loads a seed-backed helper (deferred)

```text
Operator opens execute-plans in VITE_BFF_MODE=live
  -> Page renders AllocationSimulationPanel
  -> Panel calls bff.allocationSimulations.forRebalance(rebalanceId)
  -> seed.ts liveEmpty("bff.allocationSimulations.forRebalance", []) intercepts
  -> Returns [] immediately (empty, no delay)
  -> simulationGate = getMockDataBadgeModel("bff.allocationSimulations.forRebalance", liveStatus)
     => returns MockDataBadgeModel { behavior: "empty_state", tone: "warning" }
  -> Panel renders <MockDataEmptyState> with amber warning icon
  -> Title: "Live data not wired"
  -> Description: "Live route deferred; waiting for <follow-up task>."
```

### Live mode: page loads a mock_only_dev helper (disabled)

```text
Operator opens Settings page in VITE_BFF_MODE=live
  -> Panel calls bff.getAcceptLanguage()
  -> seed.ts inline gate: seedHelperMustReturnEmptyInLive("bff.getAcceptLanguage") -> true
  -> Returns null immediately
  -> MockDataBadge renders with red blocked icon
  -> Label: "mock data disabled"
  -> Title: "Mock-only helper disabled"
  -> Description: "Development-only helper disabled while VITE_BFF_MODE=live."
```

### Mock mode: no badge visible

```text
Operator opens execute-plans in VITE_BFF_MODE=mock (default dev)
  -> getMockDataBadgeModel(...) returns null for all helpers
  -> No badges or empty states rendered; seed data populates normally
  -> Operator sees full mock UI without any visual indicator
```

### Strict mode: same badge behavior as live mode

```text
Operator opens execute-plans in VITE_BFF_MODE=strict (BFF-CONSOL-022 preview branch)
  -> isLiveConfigured(snapshot) = true (strict is not "mock")
  -> Badge behavior identical to live mode
  -> Deferred helpers show amber empty states; mock_only_dev shows red disabled badge
```

## Suggested Frontend Verification Commands

These commands verify the badge contract for frontend integration work:

```bash
# Badge component tests (10 tests cover all 4 taxonomy categories)
npm test -- src/components/data/MockDataBadge.test.tsx \
             src/lib/bff-v1/__tests__/seedTaxonomy.test.ts

# Lint task-scoped frontend files
npx eslint src/components/data/MockDataBadge.tsx \
           src/components/data/mockDataBadgeModel.ts \
           src/lib/bff-v1/seedTaxonomy.ts \
           src/lib/bff-v1/seed.ts

# Validate taxonomy JSON syntax
python3 -m json.tool execute-plans/src/lib/bff-v1/seed-taxonomy.json >/dev/null

# Count helpers by category in taxonomy JSON
node -e "const t=require('./execute-plans/src/lib/bff-v1/seed-taxonomy.json'); \
  const c={}; t.helpers.forEach(h=>{c[h.category]=(c[h.category]||0)+1}); console.log(c)"
# Expected: { live_required: 52, deferred: 25, mock_only_dev: 4, deprecated: 2 }
```

Frontend unit test matrix recommended for this contract:

| Test | Expected result |
|---|---|
| `getMockDataBadgeModel` in mock mode | returns `null` for all helpers |
| `getMockDataBadgeModel` for `live_required` in live mode | returns `null` (no badge) |
| `getMockDataBadgeModel` for `mock_only_dev` in live mode | returns model with `behavior: "disabled"`, `tone: "blocked"` |
| `getMockDataBadgeModel` for `deferred` in live mode | returns model with `behavior: "empty_state"`, `tone: "warning"` |
| `getMockDataBadgeModel` for `deprecated` in live mode | returns model with `behavior: "legacy_mock"`, `tone: "muted"` |
| `seedHelperMustReturnEmptyInLive` for `mock_only_dev` | returns `true` in live mode |
| `seedHelperMustReturnEmptyInLive` for `deferred` | returns `true` in live mode |
| `seedHelperMustReturnEmptyInLive` for `live_required` | returns `false` |
| `seedHelperMustReturnEmptyInLive` in test env | returns `false` (MODE=test guard) |
| `MockDataBadge` renders badge for `mock_only_dev` helper in live mode | badge with blocked tone rendered |
| `MockDataEmptyState` renders empty state for deferred helper in live mode | empty state with warning tone rendered |

## Parent Absorption Risks and Gates

- **Taxonomy JSON is the single source of truth.** If a helper's category
  changes in `seed-taxonomy.json`, badge behavior and the live-mode seed gate
  both follow automatically. Do not hardcode category assumptions in panel code.
- **BFF-CONSOL-025 seed elimination** uses this taxonomy as its P0-P3 elimination
  priority input. P0 = `mock_only_dev` (4 helpers) + `deprecated` (2 helpers);
  P1-P3 = `deferred` subgroups ordered by follow-up risk. Do not change category
  assignments without aligning with BFF-CONSOL-025.
- **BFF-CONSOL-028 adjunct surface follow-up** is responsible for wiring or
  hiding the governance/evolution/capital deferred helpers before strict cutover.
  Until BFF-CONSOL-028 is done, those surfaces remain amber empty states in live
  mode and must not be promoted to live-required in the taxonomy.
- **Copilot taxonomy critique** is a deferred follow-on from the BFF-CONSOL-015
  review. If Copilot reclassifies any helper, the fix is a taxonomy JSON update;
  the badge and seed gate code does not need to change.
- **BFF-CONSOL-022/023 strict cutover** soak depends on badge behavior being
  correct in strict mode. Do not disable badges or change `isLiveConfigured`
  logic while the staging soak is in progress.
- This sidecar is support-only. It does not modify the taxonomy JSON,
  `MockDataBadge.tsx`, `seedTaxonomy.ts`, `seed.ts`, canonical documents, or
  any execute-plans runtime files.

## Handoff Checklist for Codex (Reviewer)

- Confirm the badge behavior table matches the `getSeedHelperLiveBehavior`
  mapping in `src/lib/bff-v1/seedTaxonomy.ts`.
- Confirm the taxonomy category counts (52/25/4/2) match the JSON counts in
  `src/lib/bff-v1/seed-taxonomy.json`.
- Confirm the four wired UI surfaces match those listed in the parent
  implementation sidecar (`implementation-bff-consol-015-codex2.md`).
- Confirm no canonical truth, runtime, or registry files were modified by
  this sidecar.
- Confirm the operator journey sections are consistent with the live-mode
  gate logic in `seed.ts` (`liveEmpty`, `delaySeed`, inline `getAcceptLanguage`
  guard).

## Verification for This Sidecar

Performed as read-only context checks plus artifact creation:

- Read task-scoped context: `AI_COLLABORATION_GUIDE.md`,
  `.orchestrator/task-briefs/bff_consol_015_sidecar_bff_handoff.md`,
  `.orchestrator/skills/task-closeout-finalization.md`, and `ai-status.json`.
- Read parent task archive: `ai-task-archive/tasks/BFF-CONSOL-015.json`.
- Read parent review artifact: `.orchestrator/reviews/BFF-CONSOL-015-review-claude.md`.
- Read parent implementation sidecar:
  `support/sidecars/BFF-CONSOL-015/implementation-bff-consol-015-codex2.md`.
- Read sibling frontend source files:
  - `execute-plans/src/components/data/MockDataBadge.tsx`
  - `execute-plans/src/components/data/mockDataBadgeModel.ts`
  - `execute-plans/src/lib/bff-v1/seedTaxonomy.ts`
  - `execute-plans/src/lib/bff-v1/seed-taxonomy.json` (first 50 lines for counts)
  - `execute-plans/src/lib/bff-v1/seed.ts` (grep for live-mode gate calls)
- Searched all `MockDataBadge` / `MockDataEmptyState` usages in `execute-plans/src/`
  to enumerate wired surfaces.
- Read dependency task archives: `BFF-CONSOL-007.json`, `BFF-CONSOL-005.json`
  (first 80 lines each) for dependency context.
- Read format reference: `support/sidecars/BFF-CONSOL-012/BFF-CONSOL-012-SIDECAR-BFF-HANDOFF.md`.

No canonical truth, core contract truth, runtime implementation, registry code,
or governance implementation was modified by this sidecar.
