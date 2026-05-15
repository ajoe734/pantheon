# BFF-CONSOL-005 Review — Claude — 2026-05-13

**Task:** Live status banner UI (real/hybrid/mock)
**Owner:** Codex2
**Reviewer:** Claude
**Decision:** APPROVED

---

## Artifacts Reviewed

- `execute-plans/src/components/layout/LiveStatusBanner.tsx`
- `execute-plans/src/lib/bff/liveTransport.ts`
- `execute-plans/src/platform/PlatformShell.tsx` (integration point)
- `execute-plans/src/components/layout/LiveStatusBanner.test.tsx` (4 tests)
- `execute-plans/src/lib/bff/__tests__/liveTransportSnapshot.test.ts` (4 tests)

---

## Acceptance Criteria Verification

| Criterion | Result | Evidence |
|---|---|---|
| banner 在 Management Console/Agora/v5 三套 page 顯示 | PASS | PlatformShell.tsx line 10 imports `LiveStatusBanner`; line 31 renders it within the shared shell wrapping all three page families via `<Outlet>` |
| real 狀態 hide/dim 顯示 | PASS | LiveStatusBanner.tsx line 43–45: `transportMode === "real" && !apiVersionMismatch` returns `null`; test "hides visual noise for healthy strict real mode" asserts `container.toBeEmptyDOMElement()` |
| hybrid/mock 顯示警告文字與顏色 | PASS | hybrid: `bg-status-warning/10 text-status-warning` + "hybrid" + "資料來源：live / seed fallback armed"; mock-fallback and mock: `bg-status-warning/10 text-status-warning` + "資料來源：seed"; tests confirm text content |
| getLiveStatusSnapshot() 結果即時反映 transport mode | PASS | `liveTransport.ts` derives snapshot from `liveStatus.get()` + `detectManagementMode()` on every call; `useLiveStatusSnapshot()` uses `useSyncExternalStore(liveStatus.subscribe, ...)` for reactivity |
| strict mode 下也正確顯示 typed-error 狀態 | PASS | `liveTransport.ts` lines 84–97: `configuredMode === "real"` + `status.effective === "mock"` → `transportMode: "real-error"`, `typedError: true`, `usingSeed: false`; banner shows "strict typed error"/"seed fallback blocked" without "資料來源：seed" |
| Lovable preview build 確認 banner 不會 layout break | PASS | npm run build passed (per task brief); banner uses full-width `border-b` stripe with responsive `flex-col sm:flex-row` layout that does not inject sizing or overflow constraints on outer shells |

---

## Code Quality Notes

- **Snapshot caching** (`cached()` + `snapshotKey()`) is correct. Module-level cache invalidates on any field change; `liveStatus._reset()` in test afterEach ensures fresh state.
- **`usingSeed` semantics** are correctly separated from `fellBack`: strict real-error keeps `usingSeed: false` even though the BFF is unreachable — prevents operator from treating a transport error as a seed-data situation.
- **`real-error` never renders "資料來源：seed"** — the test `queryByText("資料來源：seed") not.toBeInTheDocument()` explicitly guards this invariant.
- **API version mismatch strip** composes correctly after each mode-specific strip.
- Minor: 8 tests cover the four key mode transitions. Retry button interaction is not unit-tested but is a UI affordance wiring `liveStatus.retry()` + `connectLiveSse()` — acceptable gap at this layer.

---

## Post-Approval Downstream

- BFF-CONSOL-015 (mock-only badge) depends on this task — `transportMode` from `getLiveStatusSnapshot()` is its input gate.
- BFF-CONSOL-027 (final acceptance packet) lists this task as a dependency.
