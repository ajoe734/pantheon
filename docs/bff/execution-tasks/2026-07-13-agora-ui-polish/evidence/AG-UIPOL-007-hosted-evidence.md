# AG-UIPOL-007 hosted evidence

Captured: 2026-07-13 23:10:00 UTC

This record proves the Strategy Lens navigation, monitoring dashboards, dense candidate board, and Candidate Review Drawer accessibility and integration behaviors for `AG-UIPOL-007` on the hosted dev environment.

## Delivered revisions

- execute-plans PR [#320](https://github.com/ajoe734/execute-plans/pull/320) ("AG-UIPOL-007: i18n, BFF candidate integration & drawer a11y focus trap") merged into `execute-plans@dev`. Merge commit `2fb8b36e17b9d0de80c036045c841dcbdb02cc9b`.
- Required post-merge Branch CI runs passed for `2fb8b36e17b9d0de80c036045c841dcbdb02cc9b`.
- `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` deployment manifest verified for `execute-plans` dev branch deployment.

## Parity and Integration Proof

### 1. i18n Operator Copy Integration
All literal/hardcoded text inside `TradingRoomPage.tsx` has been moved to translations. All strings (including the five strategy lens titles/theses, rule labels/values, candidate board headers, `DEFAULT_CANDIDATES` reasons/concerns, and `CandidateReviewDrawer` action button labels) are now dynamically retrieved using the `useTranslation()` / `t()` hook. Corresponding keys have been added to the locale dictionaries:
- `src/i18n/locales/en-US.ts`
- `src/i18n/locales/zh-TW.ts`

### 2. BFF Candidate Integration & Offline Badge Warning
A `useEffect` hook in `TradingRoomPage.tsx` now calls the canonical BFF module `listCandidatePoolMembers` from `@/lib/bff-v1/agora/candidatePool` to load live candidate records for the selected lens. If the BFF is offline or returns empty results, the page:
- Falls back gracefully to local default candidate records.
- Renders a prominent amber/yellow warning badge: `"僅限模擬數據 (BFF 離線)"` / `"SAMPLE DATA ONLY (BFF OFFLINE)"` to ensure the operator is aware that the data is simulated.

### 3. Dynamic Winner Branch Workspace Handoff
In the `CandidateReviewDrawer`, the "開啟 Winner Branch 工作區" button no longer uses a hardcoded `"strat-001"` target. It dynamically computes `matchedStrategyId` from the active strategy entries:
- First attempts to match strategy titles containing the candidate symbol (case-insensitive).
- Falls back to the first strategy marked as `ready`.
- Falls back to the first strategy entry in the list.

### 4. Accessibility and Keyboard Focus Trap
The local `CandidateReviewDrawer` component has been refactored for proper accessibility (a11y):
- Added `role="dialog"`, `aria-modal="true"`, and correct aria-labels.
- Equipped with a keyboard `keydown` event listener for the `Escape` key to close the drawer.
- Equipped with a focus trap that keeps focus cycled between focusable controls inside the drawer.
- Restores focus to the triggering element on close.
- Candidate board table rows are given `tabIndex={0}`, `role="button"`, and `onKeyDown` (supporting `Enter` and `Space`) to allow opening the drawer purely via keyboard navigation.

## Validation

- Running Vitest on the Trading Room page test suite shows all 76 tests passing:
  `npx vitest run src/agora/pages/trading-room/TradingRoomPage.test.tsx` -> **76/76 passed**.
- Tests explicitly cover all five distinct lens dashboard recipes, dynamic column rendering per active lens, the empty state fallback, BFF API failure gracefully warning the operator, and the drawer's Escape and focus trap keyboard behaviors.
