# AG-DYNUI-LIVE-PERFORMANCE-010 Evidence

## Frontend PR

- Repository: `ajoe734/execute-plans`
- PR: `https://github.com/ajoe734/execute-plans/pull/216`
- Branch: `task/AG-DYNUI-LIVE-PERFORMANCE-010`
- Head commit: `4b7fa00459b481e3f150e40b100c5210c2605cbf`

## Local Verification

- Focused Vitest:
  `npx vitest run src/lib/bff-v1/agora/tradingRoom.test.ts src/agora/pages/strategy-performance/StrategyPerformancePage.test.tsx src/routes/agora.test.tsx src/agora/TradingDeskLayout.test.tsx`
  passed with 75 tests.
- Focused ESLint:
  `npx eslint src/lib/bff-v1/agora/tradingRoom.ts src/lib/bff-v1/agora/tradingRoom.test.ts src/agora/pages/strategy-performance/StrategyPerformancePage.tsx src/agora/pages/strategy-performance/StrategyPerformancePage.test.tsx src/routes/agora.tsx src/routes/agora.test.tsx`
  passed with no output.
- Production build:
  `npm run build` passed.

## Build Warnings

The production build completed with existing non-blocking warnings:

- Browserslist data is stale.
- Rollup reported an existing circular chunk dependency through
  `src/lib/bff-v1/runActionSafe.ts` and `src/lib/bff-v1/legacy.ts`.
- CSS minification reported `Expected identifier but found "-"`.
- Vite reported chunks larger than 500 kB after minification.

## Hosted Proof

Hosted proof is pending until execute-plans PR #216 merges and the dev frontend
is redeployed from that merged commit. The expected post-deploy smoke is:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
  npm run probe:browser
```

Acceptance target:

- `/agora/strategy-performance` or the Agora shell `Performance` tab renders
  `Strategy Performance`.
- The old placeholder text is absent.
  `Strategy Performance` must be backed by live BFF reads and must not render
  seed/demo fallback rows.
