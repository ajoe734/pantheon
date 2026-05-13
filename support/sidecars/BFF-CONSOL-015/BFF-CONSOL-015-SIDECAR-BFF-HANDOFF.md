# BFF-CONSOL-015 Sidecar: BFF and Frontend Handoff Packet

Task ID: BFF-CONSOL-015-SIDECAR-BFF-HANDOFF
Parent Task: BFF-CONSOL-015 - Mock-only badge implementation (live mode)
Helper Kind: bff_handoff_packet
Prepared by: Claude
Refreshed by: Codex2
Reviewer: Claude
Date: 2026-05-13
Mutates canonical truth: false

## Purpose

This packet gives downstream frontend and operator tooling work a support-only
reference for the mock-data badge contract that BFF-CONSOL-015 delivered. It
does not change L1 canonical truth, core contracts, runtime code, registry code,
or governance implementation.

BFF-CONSOL-015 added the live-mode badge and empty-state UI surface that makes
seed-backed helpers visually distinct from live-backed data in any non-mock BFF
mode. The badge behavior is fully driven by the taxonomy JSON category for each
helper. BFF-CONSOL-007 created the original 83-helper taxonomy; later
BFF-CONSOL-025 and BFF-CONSOL-028 updates changed category counts without
changing the badge behavior mapping.

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

Four UI panels were wired, covering five helper gates:

| Panel | Helper name | Badge type |
|---|---|---|
| `Settings.tsx` | `bff.getAcceptLanguage` | `MockDataBadge` (mock_only_dev -> disabled) |
| `AllocationSimulationPanel.tsx` | `bff.allocationSimulations.forRebalance` | `MockDataEmptyState` (mock_only_dev -> disabled) |
| `FitnessFormulaPanel.tsx` | `bff.fitnessFormulas.list` | `MockDataEmptyState` (deferred) |
| `FitnessFormulaPanel.tsx` | `bff.mutationRules.list` | `MockDataEmptyState` (deferred) |
| `McpSecretsPanel.tsx` | `bff.mcpSecrets.forServer` | `MockDataEmptyState` (mock_only_dev -> disabled) |

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
actual frontend surface is `../execute-plans/src/lib/bff-v1/seed.ts` per
BFF-CONSOL-007's taxonomy record. This deviation is documented in the
parent implementation sidecar.

## Taxonomy Count Baseline And Current State

BFF-CONSOL-015 consumed the BFF-CONSOL-007 taxonomy snapshot. That snapshot
contains 83 helpers:

| Category | Count | Live-mode behavior |
|---|---|---|
| `live_required` | 52 | no badge; seed not blocked |
| `deferred` | 25 | empty_state badge; seed gated |
| `mock_only_dev` | 4 | disabled badge; seed gated |
| `deprecated` | 2 | legacy_mock badge; seed not gated |
| **Total** | **83** | |

The current taxonomy state after BFF-CONSOL-025 and the in-review
BFF-CONSOL-028 work still contains 83 helpers, but 10 previously deferred
adjunct helpers have been promoted to `live_required` after live route or parent
DTO handling was added.

| Source checked | `live_required` | `deferred` | `mock_only_dev` | `deprecated` | Total |
|---|---:|---:|---:|---:|---:|
| `docs/bff/seed-taxonomy.json` | 62 | 15 | 4 | 2 | 83 |
| `../execute-plans/src/lib/bff-v1/seed-taxonomy.json` | 62 | 15 | 4 | 2 | 83 |

`docs/bff/seed-elimination-2026-05-13.md` records the post-025/028 split:
routeable adjunct helpers now fold into existing BFF parent/detail routes, and
the remaining 15 deferred helpers are explicit strict-live empty/unavailable
surfaces until a canonical BFF route or parent DTO exists.

## BFF Query Gap Matrix

Helpers in `deferred` and `mock_only_dev` categories still must not display
seed rows as live truth. In the current post-025/028 taxonomy, `deferred` means
"explicit empty/unavailable in strict live" rather than "unassigned follow-up."

### Currently Gated (seed returns empty in live mode)

| Helper name | Category | Follow-up task(s) |
|---|---|---|
| `bff.getAcceptLanguage` | mock_only_dev | BFF-CONSOL-015 (badge only; no live route needed) |
| `bff.allocationSimulations.forRebalance` | mock_only_dev | BFF-CONSOL-015/BFF-CONSOL-025; current UI warms a mock cache only |
| `bff.watchers.forSubject` | mock_only_dev | BFF-CONSOL-025; no canonical BFF truth source |
| `bff.mcpSecrets.forServer` | mock_only_dev | BFF-CONSOL-025; security-sensitive seed-only panel |
| 15 current deferred helpers | deferred | BFF-CONSOL-028 decision notes; keep explicit empty/unavailable until live authority exists |

### Deprecated (seed not blocked, but callers should migrate)

| Helper name | Category | Replacement path |
|---|---|---|
| `bff.mutations` | deprecated | `POST /bff/v1/commands` or `POST /bff/actions/{entityType}/{entityId}/{actionId}` - BFF-CONSOL-019, BFF-CONSOL-020 |
| `bff.actions` (where applicable) | deprecated | Command client path - BFF-CONSOL-020 |

### Still Open for Frontend Integration

| Gap | Action required |
|---|---|
| 15 deferred helpers remain strict-live unavailable | Keep returning empty/unavailable, with no seed rows, until a canonical BFF route or parent DTO is added and reviewed |
| BFF-CONSOL-025 seed elimination | Done; it consumed the original 52/25/4/2 taxonomy and removed silent live seed fallback for live-required and seed-only surfaces |
| BFF-CONSOL-028 adjunct helpers | In review; current taxonomy/docs are already at 62/15/4/2 and no deferred helper points at BFF-CONSOL-025 or BFF-CONSOL-028 as an open follow-up |
| Copilot taxonomy critique | Non-blocking follow-on from BFF-CONSOL-015; any future reclassification should update taxonomy JSON, not badge code |

## Operator Journey

### Live mode: page loads a seed-backed helper (deferred)

```text
Operator opens execute-plans in VITE_BFF_MODE=live
  -> Page renders FitnessFormulaPanel
  -> Panel calls bff.fitnessFormulas.list()
  -> seed.ts delaySeed("bff.fitnessFormulas.list", seed.fitnessFormulas, []) intercepts
  -> Returns [] immediately (empty, no delay)
  -> fitnessGate = getMockDataBadgeModel("bff.fitnessFormulas.list", liveStatus)
     => returns MockDataBadgeModel { behavior: "empty_state", tone: "warning" }
  -> Panel renders <MockDataEmptyState> with amber warning icon
  -> Title: "Live data not wired"
  -> Description explains that strict live is unavailable until a canonical route
     or parent DTO exists.
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
# Run from ../execute-plans.

# Badge component and taxonomy tests cover all 4 taxonomy categories.
npm test -- src/components/data/MockDataBadge.test.tsx \
             src/lib/bff-v1/__tests__/seedTaxonomy.test.ts

# Lint task-scoped frontend files
npx eslint src/components/data/MockDataBadge.tsx \
           src/components/data/mockDataBadgeModel.ts \
           src/lib/bff-v1/seedTaxonomy.ts \
           src/lib/bff-v1/seed.ts

# Validate taxonomy JSON syntax
python3 -m json.tool src/lib/bff-v1/seed-taxonomy.json >/dev/null

# Count helpers by category in taxonomy JSON
node -e "const t=require('./src/lib/bff-v1/seed-taxonomy.json'); \
  const c={}; t.helpers.forEach(h=>{c[h.category]=(c[h.category]||0)+1}); console.log(c)"
# Expected current post-025/028 state:
# { live_required: 62, deferred: 15, mock_only_dev: 4, deprecated: 2 }
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
- **BFF-CONSOL-025 seed elimination** is done. It consumed the original
  BFF-CONSOL-007 52/25/4/2 snapshot and made live-required helpers strict-read
  BFF routes while keeping seed-only helpers empty/unavailable in live mode.
- **BFF-CONSOL-028 adjunct surface follow-up** is in review. It promotes
  routeable adjunct helpers into `live_required` and leaves 15 helpers as
  explicit strict-live unavailable surfaces. Parent absorption should check
  BFF-CONSOL-028's final review outcome before treating those 62/15/4/2 counts
  as closed delivery truth.
- **Copilot taxonomy critique** is a deferred follow-on from the BFF-CONSOL-015
  review. If Copilot reclassifies any helper, the fix is a taxonomy JSON update;
  the badge and seed gate code does not need to change.
- **BFF-CONSOL-022/023 strict cutover** soak depends on badge behavior being
  correct in strict mode. Do not disable badges or change `isLiveConfigured`
  logic while the staging soak is in progress.
- This sidecar is support-only. It does not modify the taxonomy JSON,
  `MockDataBadge.tsx`, `seedTaxonomy.ts`, `seed.ts`, canonical documents, or
  any execute-plans runtime files.

## Handoff Checklist for Claude (Reviewer)

- Confirm the badge behavior table matches the `getSeedHelperLiveBehavior`
  mapping in `src/lib/bff-v1/seedTaxonomy.ts`.
- Confirm the original BFF-CONSOL-015 snapshot is identified as 52/25/4/2 and
  the current post-025/028 JSON counts are identified as 62/15/4/2.
- Confirm the four wired UI panels and five helper gates match those listed in
  the parent implementation sidecar
  (`implementation-bff-consol-015-codex2.md`).
- Confirm no canonical truth, runtime, or registry files were modified by
  this sidecar.
- Confirm the operator journey sections are consistent with the live-mode
  gate logic in `seed.ts` (`liveEmpty`, `delaySeed`, inline `getAcceptLanguage`
  guard).

## Verification for This Sidecar

Performed as read-only context checks plus support artifact refresh:

- Read task-scoped context: `AI_COLLABORATION_GUIDE.md`,
  `.orchestrator/task-briefs/bff_consol_015_sidecar_bff_handoff.md`,
  `.orchestrator/skills/task-closeout-finalization.md`, and `ai-status.json`.
- Read parent task archive: `ai-task-archive/tasks/BFF-CONSOL-015.json`.
- Read parent review artifact: `.orchestrator/reviews/BFF-CONSOL-015-review-claude.md`.
- Read parent implementation sidecar:
  `support/sidecars/BFF-CONSOL-015/implementation-bff-consol-015-codex2.md`.
- Read sibling frontend source files:
  - `../execute-plans/src/components/data/MockDataBadge.tsx`
  - `../execute-plans/src/components/data/mockDataBadgeModel.ts`
  - `../execute-plans/src/lib/bff-v1/seedTaxonomy.ts`
  - `../execute-plans/src/lib/bff-v1/seed-taxonomy.json`
  - `../execute-plans/src/lib/bff-v1/seed.ts` (grep for live-mode gate calls)
- Searched all `MockDataBadge` / `MockDataEmptyState` usages in `../execute-plans/src/`
  to enumerate wired surfaces.
- Read dependency task archives: `BFF-CONSOL-007.json`, `BFF-CONSOL-005.json`
  (first 80 lines each) for dependency context.
- Read format reference: `support/sidecars/BFF-CONSOL-012/BFF-CONSOL-012-SIDECAR-BFF-HANDOFF.md`.
- Refreshed post-review facts from:
  - `support/sidecars/BFF-CONSOL-025/BFF-CONSOL-025-SIDECAR-BFF-HANDOFF.md`
  - `support/sidecars/BFF-CONSOL-025/BFF-CONSOL-025-REVIEW.md`
  - `docs/bff/seed-elimination-2026-05-13.md`
  - `docs/bff/seed-taxonomy.json`
  - `../execute-plans/src/lib/bff-v1/seed-taxonomy.json`
- Rechecked taxonomy counts with `jq`:
  - `docs/bff/seed-taxonomy.json`: 62 live_required, 15 deferred,
    4 mock_only_dev, 2 deprecated.
  - `../execute-plans/src/lib/bff-v1/seed-taxonomy.json`: 62 live_required,
    15 deferred, 4 mock_only_dev, 2 deprecated.
- Rechecked the original BFF-CONSOL-007 snapshot with
  `git show 42ac7b0e:docs/bff/seed-taxonomy.json`: 52 live_required,
  25 deferred, 4 mock_only_dev, 2 deprecated.
- Ran scoped artifact checks:
  - `git diff --check -- support/sidecars/BFF-CONSOL-015/BFF-CONSOL-015-SIDECAR-BFF-HANDOFF.md`
  - `LC_ALL=C grep -nP '[^\x00-\x7F]' support/sidecars/BFF-CONSOL-015/BFF-CONSOL-015-SIDECAR-BFF-HANDOFF.md`

No canonical truth, core contract truth, runtime implementation, registry code,
or governance implementation was modified by this sidecar.
