# AG-UIPOL-002 hosted evidence

Captured: 2026-07-13 20:39:24 UTC

This record proves the standalone Agora shell scroll-owner and Workshop
runtime-header acceptance criteria. It does not claim data-readiness or design
parity for the three tab contents.

## Delivered revisions

- execute-plans PR [#291](https://github.com/ajoe734/execute-plans/pull/291)
  delivered the fixed-height standalone shell, the single `main` scroll owner,
  the Workshop ghost-header removal, and the hosted Playwright contract; merge
  commit `71e84a9f5d9ec237376cb9c13680a2d87fe1cfff`.
- Shared gate recovery PR
  [#309](https://github.com/ajoe734/execute-plans/pull/309) preserved zero
  Playwright retries and required explicit successful live mutation responses;
  merge commit `34ff25cd58b801d203dc7960f311362e2e9e6953`.
- The accepted `dev` descendant was
  `8ad0a152b1869c2038f077c24d6cc7beafaa7f8f`.
- Exact integration run
  [29280017449, attempt 2](https://github.com/ajoe734/execute-plans/actions/runs/29280017449/attempts/2)
  passed every release-gate step: 1,346 unit/integration tests, six contract
  tests, and 162 Playwright tests passed with 50 expected skips.
- Pantheon dev FE deploy run
  [29282861281](https://github.com/ajoe734/execute-plans/actions/runs/29282861281)
  passed candidate validation, exact release-identity validation, static build
  deployment, evidence upload, and deployment summary.

The first attempt of run `29280017449` is deliberately not hidden: its sole
runnable failure was an honest, non-retried HTTP 502 from the live BFF for a
Winner Branch layout PATCH; the mobile form of that flow passed. Attempt 2
reran the complete unchanged workflow against the same FE/BFF identities and
passed the aggregate gate. No test retry, response interception, or write
retry was added to make it green.

## Exact deployment identity

[AG-UIPOL-002-deployment.json](./AG-UIPOL-002-deployment.json) is the hosted
`/deployment.json` readback captured after deploy. It records:

- FE commit `8ad0a152b1869c2038f077c24d6cc7beafaa7f8f` on `dev`;
- BFF commit `2851dcee6fef84fcd1501d15c86775a81dfcf917`, with
  `sourceCommitKnown: true`;
- exact gate run `29280017449`;
- `VITE_BFF_MODE=live` and `VITE_BFF_FALLBACK=strict`;
- `VITE_BFF_REAL_WRITES=false` and
  `VITE_BFF_ALLOW_DEV_STUB_WRITES=false`;
- no `temporaryLiveRepair` field.

## Hosted shell proof

The real hosted frontend was tested without intercepting Agora responses:

```text
AG_UIPOL_002_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
PANTHEON_AUDIT_OUT_DIR=<task-evidence-dir> \
npx playwright test e2e/agora-shell-hosted.spec.ts --project=chromium --reporter=list
```

Result: 6/6 passed in 9.7 seconds.

For every case, Playwright asserted that body height and width stayed within
the viewport, the standalone shell used `overflow: hidden`, and the one
designated page scroll owner used `overflow-x: hidden` and `overflow-y: auto`.
For both Workshop viewports, it additionally asserted that the visible runtime
header contained zero `h1`-`h6` elements.

| Tab | 1280 x 900 | 2560 x 1440 |
|---|---|---|
| Trading Room | [narrow](./ag-uipol-002-trading-room-desktop-narrow.png) | [wide](./ag-uipol-002-trading-room-desktop-wide.png) |
| Strategy Workshop | [narrow](./ag-uipol-002-strategy-workshop-desktop-narrow.png) | [wide](./ag-uipol-002-strategy-workshop-desktop-wide.png) |
| Strategy Performance | [narrow](./ag-uipol-002-strategy-performance-desktop-narrow.png) | [wide](./ag-uipol-002-strategy-performance-desktop-wide.png) |

Visual inspection of all six captures confirmed that no browser/outer second
scrollbar appeared, the bottom shell rail remained inside the viewport, and
the Workshop status row had no overlapping or half-painted title. The live
content regions were still showing their loading states in these captures;
this evidence proves the shell/header contract and does not claim content-data
readiness.

## Focused validation

- `npx vitest run src/agora/TradingDeskLayout.test.tsx src/agora/pages/strategy-workshop/StrategyWorkshopPage.test.tsx src/agora/pages/trading-room/TradingRoomPage.test.tsx src/agora/trading-room/WorkspaceLayoutProposalDrawer.test.tsx`
  -> 113/113 passed on the accepted `8ad0a152` descendant.
- Hosted `e2e/agora-shell-hosted.spec.ts` -> 6/6 passed.
- Exact integration gate attempt 2 -> aggregate release gate passed.
- Exact dev FE deploy -> passed and switched the hosted manifest.

## Artifact SHA-256

- `6301c64037eee5c9b045a4368180ef609b0a76f0011dc55cc868c2fc686f72db`
  — Trading Room, 1280 x 900
- `21a41ed6600ec65af15c7889206dad7365a7766b999c5938a2a9bc36de365f51`
  — Trading Room, 2560 x 1440
- `3ef69b54941276f5a981e01315ba7b386fcd86fac07de4d5a0285cbcc82eab3e`
  — Strategy Workshop, 1280 x 900
- `d6c9e804b393bff471b85146897b2c12203be0a37e348c4565d58ca2eaf1d568`
  — Strategy Workshop, 2560 x 1440
- `65698b3e9544423b641adae566c2f30396dc9b34b1e66b6edbabe74f578b7b49`
  — Strategy Performance, 1280 x 900
- `eb03c2be411b0a20e9454f7a40ec06545f3d9899fe8ec03402aaab26987b5332`
  — Strategy Performance, 2560 x 1440
