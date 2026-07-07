# AG-DYNUI-LIVE-READBACK-008

Task: Agora live Trading Room failed-page regression readback
Owner: Codex
Reviewer: Codex2
Date: 2026-07-07

## Scope

This task treats the operator-provided hosted
`/agora/trading-room` failed-to-load screenshot as a production regression
signal. The acceptance bar is live hosted readback against the Pantheon dev
frontend and BFF. Static UI fixtures, page-route mocks, and local-only proof do
not satisfy this task.

## Current Result

The failed Trading Room page is not reproducible against the current Pantheon
dev frontend deployment.

- Frontend host:
  `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF host:
  `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- Hosted deployment id / execute-plans commit:
  `4a4f256e0bc14c99820b7406de44822b6b1cbe2c`
- Deployment source:
  `execute-plans origin/dev`, merge commit for PR `#213`
- Shell route:
  `/agora/trading-room` returned HTTP `200`
- Required live BFF reads:
  `/bff/agora/trading-room` returned HTTP `200`
  and `/bff/agora/trading-room/decision-events` returned HTTP `200`
- Browser result:
  no `Failed to load Trading Room`, no Trading Room diagnostic error state,
  no browser console errors, and no old BFF host URL hit.

No FE or BFF code change was made in this task because the current hosted dev
deployment already passes the failed-page readback and the deeper Winner Branch
workflow gate. This task records the live evidence and leaves code surfaces
unchanged.

## Evidence

Primary evidence directory:

- `docs/deployment/evidence/ag-dynui-live-readback-008/`

Evidence artifacts:

- `readback/hosted-browser-bff-probe-2026-07-07.md`
  - execute-plans `origin/dev` hosted browser/BFF probe.
  - `pass: true`
  - required core BFF responses complete: `true`
  - `/bff/agora/trading-room`: `200`
  - `/bff/agora/trading-room/decision-events`: `200`
  - old BFF URL hit count: `0`
  - console errors: none
- `winner-branch/ag-dynui-full-006-live-summary-desktop.json`
  - live hosted Winner Branch gate summary.
  - workflow covered readiness cards, Strategy Workshop handoff, workspace
    proposal, proposal accept, grid edit save, widget revision keep-copy,
    version history, and rollback.
  - all observed BFF paths returned 2xx.
  - no forbidden broker/order/capital/runtime-binding path was observed.
- `winner-branch/ag-dynui-full-006-*.png`
  - desktop screenshots for the live Winner Branch workflow.
- `mobile/agora-trading-room-mobile.png`
  - mobile-sized Chromium screenshot captured after waiting for
    `[data-testid='trading-room-page']`.
- `bff/direct-bff-readback.json`
  - direct BFF readback summary with caller request ids.
  - `/bff/agora/trading-room`: `200`, `strategies: 2`
  - `/bff/agora/trading-room/decision-events`: `200`, `items: 0`
  - `/bff/agora/workshops/0bb956f7-0f2a-4318-9cb3-c6feae58306d/cards`:
    `200`, `data: 3`
  - `/bff/agora/workshops/0bb956f7-0f2a-4318-9cb3-c6feae58306d/readiness`:
    `200`, `gates: 3`, `evidence_refs: 2`
- `initial/hosted-browser-bff-probe-2026-07-07.md`
  - compatibility sanity probe from the in-repo legacy execute-plans mirror.
  - It also passed, but the primary frontend evidence is from the clean
    standalone execute-plans `origin/dev` worktree.

## Commands Run

From the clean standalone execute-plans worktree at
`/tmp/pantheon-worker-worktrees/execute-plans/ag-dynui-live-readback-008`:

```bash
npm ci
```

```bash
env \
  PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
  PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
  PANTHEON_HOSTED_PROBE_PATH=/agora/trading-room \
  PANTHEON_HOSTED_REQUIRED_BFF_PATHS=/bff/agora/trading-room,/bff/agora/trading-room/decision-events \
  PANTHEON_AUDIT_OUT_DIR=/tmp/pantheon-worker-worktrees/pantheon/ag-dynui-live-readback-008/docs/deployment/evidence/ag-dynui-live-readback-008/readback \
  node scripts/probe-hosted-browser-bff.mjs
```

Result: passed.

```bash
env \
  PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
  PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
  PANTHEON_AUDIT_OUT_DIR=/tmp/pantheon-worker-worktrees/pantheon/ag-dynui-live-readback-008/docs/deployment/evidence/ag-dynui-live-readback-008/winner-branch \
  npx playwright test e2e/agora-winner-branch-hosted.spec.ts --project=chromium
```

Result: `1 passed (10.1s)`.

```bash
npx playwright screenshot \
  --browser=chromium \
  --viewport-size="390,844" \
  --user-agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1" \
  --wait-for-selector="[data-testid='trading-room-page']" \
  --full-page \
  "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room?nocache=ag-dynui-live-readback-008-mobile" \
  "/tmp/pantheon-worker-worktrees/pantheon/ag-dynui-live-readback-008/docs/deployment/evidence/ag-dynui-live-readback-008/mobile/agora-trading-room-mobile.png"
```

Result: screenshot captured after the Trading Room page selector appeared.

## Residual Notes

- The live BFF did not return `x-request-id` or `x-correlation-id` response
  headers on the direct readback endpoints. The direct evidence records the
  caller-supplied request ids.
- The mobile-sized screenshot proves the hosted page loads instead of showing
  the failed-page regression. It also shows the current Trading Room workspace
  is visually cramped on narrow mobile widths; that is a layout polish follow-up
  rather than the failed-load regression tracked here.
