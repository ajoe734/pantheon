# AG-DYNUI-LIVE-PERFORMANCE-010 Evidence

## Frontend PR

- Repository: `ajoe734/execute-plans`
- PR: `https://github.com/ajoe734/execute-plans/pull/216`
- Branch: `task/AG-DYNUI-LIVE-PERFORMANCE-010`
- Head commit: `4b7fa00459b481e3f150e40b100c5210c2605cbf`
- Merged at: `2026-07-08T00:49:56Z`
- Merge commit: `91c039d051bf596d42d4468c8c4f5b9b8f82803d`
- GitHub check: `integration-gate` passed at `2026-07-08T00:49:27Z`.

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

Hosted proof passed against the Pantheon dev frontend after the execute-plans
merge commit `91c039d051bf596d42d4468c8c4f5b9b8f82803d` was available on the
hosted route.

```bash
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
PANTHEON_HOSTED_PROBE_PATH=/agora/strategy-performance \
PANTHEON_HOSTED_REQUIRED_BFF_PATHS=/bff/agora/trading-room,/bff/agora/trading-room/decision-events,/bff/management/performance-attribution/by-strategy \
  npm run probe:browser
```

- Passed at: `2026-07-08T01:09:40Z`.
- Result: required live BFF responses were complete, old BFF URL hit count was
  `0`, failed request count was `0`, console error count was `0`, and the probe
  reported `pass: true`.

Reviewer screenshot smoke:

- Summary: `performance-hosted-smoke.json`.
- Desktop screenshot: `performance-desktop.png`.
- Mobile screenshot: `performance-mobile.png`.
- Passed at: `2026-07-08T01:12:09Z`.

Acceptance result:

- `/agora/strategy-performance` or the Agora shell `Performance` tab renders
  `Strategy Performance`.
- The old placeholder text is absent.
- The page is backed by live BFF reads:
  - `GET /bff/agora/trading-room`
  - `GET /bff/agora/trading-room/decision-events`
  - `GET /bff/management/performance-attribution/by-strategy?period=latest&page_size=50`
