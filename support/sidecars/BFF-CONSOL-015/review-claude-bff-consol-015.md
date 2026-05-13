# Review: BFF-CONSOL-015 — Mock-only badge implementation (live mode)

Reviewer: Claude  
Date: 2026-05-13  
Outcome: **APPROVED**

## Acceptance Criteria Verification

| Criterion | Result | Notes |
|---|---|---|
| badge 在 live mode 顯示 mock 狀態 | PASS | `MockDataBadge.tsx` renders badge when `configuredMode !== "mock"` and helper is non-`live_required` |
| mock_only_dev helper 在 live mode 不再回 seed | PASS | `delaySeed()` gates via `seedHelperMustReturnEmptyInLive()`; returns empty value in live mode |
| deferred helper 顯示明確空狀態 | PASS | `MockDataEmptyState` + `delaySeed()` returns explicit empty for deferred category |
| live_required helper 不掛 badge | PASS | `getMockDataBadgeModel()` returns null for `live_required` entries and unknown helpers |
| taxonomy JSON 變更後 badge 行為跟著動 | PASS | All behavior derived from `seed-taxonomy.json` at runtime via `getSeedTaxonomyEntry()` |
| Copilot 簽核 badge 行為對齊 taxonomy | DEFERRED | Acknowledged as follow-on audit item; does not block approval |

## Code Review

**MockDataBadge.tsx / mockDataBadgeModel.ts**
- Clean separation: view component + pure model function
- Correct tone mapping: `mock_only_dev` → `blocked`, `deferred` → `warning`, `deprecated` → `muted`
- Returns null in mock mode (no badge when not in live mode)
- `MockDataEmptyState` provides the more prominent empty state for panels

**seedTaxonomy.ts**
- Category → behavior mapping is correct
- `seedHelperMustReturnEmptyInLive` correctly covers both `disabled` and `empty_state`
- `seedHelperEmptyReason` returns actionable human-readable strings

**seed.ts**
- `liveEmpty()` + `delaySeed()` pattern is clean and non-invasive
- `getSeedHelperUnavailableReason()` exported for UI use, not re-exported from seed
- Verified `mock_only_dev` helpers (`watchers.forSubject`, `allocationSimulations`, `mcpSecrets`, `getAcceptLanguage`) return empty in live mode
- Verified `deferred` helpers return explicit empty values

**Tests (10 tests, 2 suites)**
- `MockDataBadge.test.tsx`: badge model derivation, rendering, empty state rendering
- `seedTaxonomy.test.ts`: category classification, live gating, seed accessor surface, adjunct live routing

## Path Note

Task brief artifact listed as `execute-plans/src/lib/bff/seed.ts`; actual file is `execute-plans/src/lib/bff-v1/seed.ts` per BFF-CONSOL-007 taxonomy record. Implementation follows the correct taxonomy record path.

## Follow-on

Copilot taxonomy sign-off remains a separate acceptance gate (not replaced by this review). If a later taxonomy critique changes a category, the code behavior follows the JSON update automatically.
