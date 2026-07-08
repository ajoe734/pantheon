# AG-DYNUI-LIVE-PERFORMANCE-010

## Scope

Fix the live `/agora/trading-room` Performance tab so the routed Agora shell no
longer renders the placeholder copy and instead exposes inspectable strategy
performance state from live BFF read contracts.

## Implementation

- Frontend source of truth is `ajoe734/execute-plans`, PR
  `https://github.com/ajoe734/execute-plans/pull/216`.
- `src/routes/agora.tsx` now routes `/agora/strategy-performance` to a live
  `StrategyPerformancePage` instead of the old placeholder.
- `StrategyPerformancePage` reads:
  - `GET /bff/agora/trading-room`
  - `GET /bff/agora/trading-room/decision-events`
  - `GET /bff/management/performance-attribution/by-strategy?period=latest&page_size=50`
- The page renders strategy-level performance KPIs, attribution rows, BFF
  source health, telemetry coverage, decision-event count, and an explicit
  missing-attribution state for Trading Room strategies that do not yet have an
  attribution row.
- `src/lib/bff-v1/agora/tradingRoom.ts` now includes typed read-only client
  support for the by-strategy performance attribution contract and preserves the
  existing auth/tenant header flow.
- No Pantheon BFF backend route change was required for this task.

## Validation

- `npx vitest run src/lib/bff-v1/agora/tradingRoom.test.ts src/agora/pages/strategy-performance/StrategyPerformancePage.test.tsx src/routes/agora.test.tsx src/agora/TradingDeskLayout.test.tsx`
  - Passed: 4 files, 75 tests.
- `npx eslint src/lib/bff-v1/agora/tradingRoom.ts src/lib/bff-v1/agora/tradingRoom.test.ts src/agora/pages/strategy-performance/StrategyPerformancePage.tsx src/agora/pages/strategy-performance/StrategyPerformancePage.test.tsx src/routes/agora.tsx src/routes/agora.test.tsx`
  - Passed with no output.
- `npm run build`
  - Passed.
  - Existing warnings observed: Browserslist data is stale, Rollup reported an
    existing circular chunk dependency involving `runActionSafe`, CSS minifier
    reported `Expected identifier but found "-"`, and Vite reported large
    chunks.

## Pull Request

- execute-plans PR: `https://github.com/ajoe734/execute-plans/pull/216`
- Head commit: `4b7fa00459b481e3f150e40b100c5210c2605cbf`
- Merged at: `2026-07-08T00:49:56Z`
- Merge commit: `91c039d051bf596d42d4468c8c4f5b9b8f82803d`
- GitHub check: `integration-gate` passed at `2026-07-08T00:49:27Z`.

## Hosted Proof Requirement

After the dev frontend is redeployed from execute-plans merge commit
`91c039d051bf596d42d4468c8c4f5b9b8f82803d`, run a hosted browser smoke
against:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
  npm run probe:browser
```

The hosted proof should confirm that clicking `Performance` in the Agora shell
loads the `Strategy Performance` page and does not show the old placeholder.

See:

- `docs/deployment/evidence/ag-dynui-live-tabs-010/20260708T003924Z/README.md`
