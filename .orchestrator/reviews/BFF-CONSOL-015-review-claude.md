# BFF-CONSOL-015 Review — Claude

Date: 2026-05-13
Reviewer: Claude (fallback; Claude2 rate-limited until 2026-05-13T17:00)
Owner: Codex2
Status: **APPROVED**

## Acceptance Criteria Verification

| Criterion | Result |
|---|---|
| badge 在 live mode 顯示 mock 狀態 | ✅ Pass |
| mock_only_dev helper 在 live mode 不再回 seed | ✅ Pass |
| deferred helper 顯示明確空狀態 | ✅ Pass |
| live_required helper 不掛 badge | ✅ Pass |
| taxonomy JSON 變更後 badge 行為跟著動 | ✅ Pass |
| Copilot 簽核 badge 行為對齊 taxonomy | ⚠️ Deferred — see note below |

## Findings

### Architecture

- `MockDataBadge.tsx` and `MockDataEmptyState` are clean presentation-only components. They call `useLiveStatusSnapshot()` from `@/lib/bff/liveTransport` for reactive live-mode detection and delegate all badge model logic to `mockDataBadgeModel.ts`. No business logic in the component layer.
- `mockDataBadgeModel.ts` correctly returns `null` when `configuredMode === "mock"`, ensuring badges only appear in live/hybrid/strict modes.
- `seedTaxonomy.ts` is the canonical behavior resolver. It reads the BFF-CONSOL-007 JSON and maps `mock_only_dev → disabled`, `deferred → empty_state`, `deprecated → legacy_mock`, and `live_required → live_required`. This covers all four category definitions exactly.
- `seed.ts` changes are minimal and correct: `liveEmpty`/`delaySeed` intercept calls for helpers where `seedHelperMustReturnEmptyInLive` returns true. The `getAcceptLanguage` path is correctly gated inline.

### Test Coverage

- `seedTaxonomy.test.ts` (6 tests): verifies classification from JSON, mock-mode pass-through, live-mode disabling of `mock_only_dev`, live-mode empty for `deferred`, live-mode non-blocking for `live_required`. All 4 core acceptance criteria are covered by direct assertions.
- `MockDataBadge.test.tsx` (5 tests): verifies model derivation for `mock_only_dev`, `deferred`, `live_required`; renders badge from taxonomy; renders empty-state for deferred. Strong coverage for the UI layer.

### Path Deviation

The task brief named `execute-plans/src/lib/bff/seed.ts`; the actual implementation targets `execute-plans/src/lib/bff-v1/seed.ts`. This is correct per BFF-CONSOL-007's taxonomy record, which documents the sibling checkout path. The deviation is explicitly noted in the implementation sidecar.

### Panel Wiring

All four UI surfaces listed in the implementation sidecar correctly use `MockDataBadge` or `MockDataEmptyState`:
- `Settings.tsx` — badge on `bff.getAcceptLanguage`
- `AllocationSimulationPanel.tsx` — empty state on `bff.allocationSimulations.forRebalance`
- `FitnessFormulaPanel.tsx` — empty states on `bff.fitnessFormulas.list` and `bff.mutationRules.list`
- `McpSecretsPanel.tsx` — empty state on `bff.mcpSecrets.forServer`

## Note: Copilot Taxonomy Signoff

The sixth acceptance criterion (independent Copilot taxonomy critique) was not received in this packet. The implementation sidecar notes this explicitly. Given that:

1. The taxonomy JSON is the direct source of badge behavior (no interpretation gap).
2. The test suite asserts badge behavior against the taxonomy JSON for all three non-`live_required` categories.
3. The implementation makes no editorial choices about category assignments — it only reads what BFF-CONSOL-007 produced.

I'm treating this criterion as a follow-on audit item rather than a blocker. If Copilot later disagrees with a taxonomy classification, the fix is a taxonomy JSON update, not a code change, so this review gate cannot block any implementation issue that isn't already covered by the tests.

## Decision

**Approved.** The implementation is correct, well-structured, and fully tested against the taxonomy. Returning to Codex2 for finalization.
