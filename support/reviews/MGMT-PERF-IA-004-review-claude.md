# MGMT-PERF-IA-004 Review: Rankings Center consolidation

Reviewer: Claude
Date: 2026-07-11
Repo: `ajoe734/execute-plans`
PR reviewed: #259 (already merged into `dev` at merge commit `4ed80ce6`, task commit `0b07b384`)

## Verdict: REQUEST_CHANGES

## Scope Verified

Checked out the exact merged commit `0b07b384399407f6997cc265fbe6779a4de313b8` in an
isolated worktree (`git worktree add --detach ... 0b07b384...`) to review real behavior
rather than the PR diff alone.

Confirmed satisfied:

- `RankingsCenterPage` (built by `MGMT-PERF-IA-001`) mounts `PersonaLeaguePage` and
  `QuarterlyRankingPage` as `rolling`/`quarterly` tabs at `/management/rankings`.
- `PromotionAllocationPage` no longer embeds ranking tables; `paper-candidates` and
  `real-ranking` tabs now show stub cards linking to
  `/management/rankings?tab=quarterly` / `?tab=rolling`.
- Legacy `/management/persona-league` and `/management/quarterly-ranking` routes go
  through `ManagementCanonicalRedirect` (from `MGMT-PERF-IA-001`'s route manifest) to
  the correct Rankings Center tab — acceptance item "Legacy League and Quarterly URLs
  land on the correct tab" holds.
- `RankingRecommendationPages.test.tsx` (7 tests, the file this PR touched) passes.

## Blocking Finding: fabricated persona data rendered as live ranking rows

`PersonaLeague.tsx` and `QuarterlyRanking.tsx` both add a `getTest*()` seed generator
(~120 lines of hardcoded persona rows — "Alpha Trader", "Risk Guard", "FX Scout", ...)
and return it directly from the page's row `useMemo` whenever the live API returns no
data:

```ts
// PersonaLeague.tsx
const useFallback = !apiData || apiData.length === 0;
...
const rows = useMemo(() => {
  if (useFallback) {
    return getTestPersonaLeague();   // <-- fabricated rows
  }
  return apiData ?? [];
}, [apiData, useFallback]);
```

```ts
// QuarterlyRanking.tsx — identical pattern
const useFallback = !rows || rows.length === 0;
...
if (useFallback) {
  return getTestQuarterlyRanking(); // <-- fabricated rows
}
```

There is a "Degraded Standby" banner, so the fallback state is visually labeled — but
this repo already has a repo-wide, pre-existing policy against this exact behavior,
enforced by `src/management/pages/oversight/LiveOnlyFallbacks.test.tsx` (not touched by
this PR):

```ts
it("does not render seeded Persona League rows", () => {
  renderPage(<PersonaLeaguePage />);
  expect(screen.queryByText("Alpha Trader")).not.toBeInTheDocument();
  expect(screen.queryByText("Risk Guard")).not.toBeInTheDocument();
});
```

Other pages in the same file (`TradingPulsePage`, `Ep5CanaryReadinessPage`,
`PersonaIntentTracesPage`, `PortfolioBookPage`) all follow the correct pattern: render
an explicit "Live data unavailable" state instead of seeded rows. `PersonaLeague.tsx`
used to follow this too before this PR; this change reintroduces the seeded-fallback
anti-pattern the policy test exists to catch.

### Reproduced locally

```
$ npx vitest run src/management/pages/oversight/LiveOnlyFallbacks.test.tsx
 ✓ does not render the local Trading Pulse seed model when live data is unavailable
 ✓ does not render seeded readiness checklist rows
 ✓ does not render deterministic Persona Intent traces
 ✗ does not render seeded Persona League rows
   TestingLibraryElementError: Found multiple elements with the text: Alpha Trader
     at src/management/pages/oversight/LiveOnlyFallbacks.test.tsx:66:19
 ✓ does not render seeded Portfolio Book rows
 ✓ does not render raw NaN values on the Quarterly Ranking empty state

 Test Files  1 failed (1)
      Tests  1 failed | 5 passed (6)
```

This is the same failure signature recorded on the merged PR's `integration-gate`
check (`Aggregate release gate` = FAILURE, error text "Found multiple elements with
the text: Alpha Trader" against a `Persona League` / "Degraded Standby" render) —
https://github.com/ajoe734/execute-plans/actions/runs/29161197831/job/86566463016.

`QuarterlyRanking.tsx`'s `getTestQuarterlyRanking()` has the identical anti-pattern
(same `alpha-trader` / `Alpha Trader` seed row) but happens not to be caught by an
explicit assertion in `LiveOnlyFallbacks.test.tsx` today — it should be fixed the same
way, not left as a latent instance of the same bug.

## Separate governance gap (not owner's fault, flagging for the record)

PR #259 is already **merged** into `execute-plans:dev` even though its required
`integration-gate` check recorded `conclusion: FAILURE`. Branch protection should have
blocked this merge. This needs attention independent of the code fix above — worth a
follow-up to whoever owns `execute-plans` branch protection / auto-merge wiring.

## Required Changes

1. Remove the seeded-fallback rendering path from `PersonaLeaguePage` and
   `QuarterlyRankingPage`: when live data is unavailable, render an explicit
   "Live data unavailable" state (matching `TradingPulsePage` / `PortfolioBookPage`),
   not fabricated rows. Keep `getTestPersonaLeague`/`getTestQuarterlyRanking` (or
   equivalent fixtures) only in test files, not shipped in the page component.
2. Add `QuarterlyRankingPage` seeded-row coverage to `LiveOnlyFallbacks.test.tsx`
   (assert `screen.queryByText("Alpha Trader")` etc. is absent there too), matching the
   existing Persona League assertion, so this regression class is caught for both tabs.
3. Re-run `npm run test` and `npm run build` for real before re-submitting — the
   original commit trailer claimed `Verified: npm run test, npm run build`, but the
   pre-existing `LiveOnlyFallbacks.test.tsx` suite was not green at merge time.
4. Since the branch is already merged into `dev`, the fix should land as a new
   follow-up commit/PR on `dev` (or a fresh `task/MGMT-PERF-IA-004` branch), not by
   trying to rewrite the merged history.

## Review Notes (ZH)

審查結果：REQUEST_CHANGES。`PersonaLeague.tsx`／`QuarterlyRanking.tsx` 在 live API
無資料時，改為回傳寫死的假資料（`getTestPersonaLeague`／`getTestQuarterlyRanking`，
「Alpha Trader」「Risk Guard」等），雖然有「Degraded Standby」提示樣式，但違反本repo
既有的 live-only 政策（`LiveOnlyFallbacks.test.tsx`，本 PR 未修改此檔）。本機重跑
`npx vitest run src/management/pages/oversight/LiveOnlyFallbacks.test.tsx` 可重現：
6 個測試中 1 個失敗，錯誤訊息與已合併 PR #259 上失敗的 `integration-gate` /
`Aggregate release gate` 完全一致。另外 PR #259 在必要檢查為 FAILURE 的狀態下仍被
合併進 `dev`，這是另一個需要獨立追蹤的治理缺口。要求：兩個頁面在無 live 資料時改為
顯示明確的「Live data unavailable」狀態，而不是假資料；補上 QuarterlyRanking 對應
的無假資料斷言；修好後才能重新宣稱測試通過。
