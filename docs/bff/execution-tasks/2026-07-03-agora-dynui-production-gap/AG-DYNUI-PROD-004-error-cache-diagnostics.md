# AG-DYNUI-PROD-004 - Error Diagnostics And Stale Bundle Recovery

Owner: Codex2
Reviewer: Claude
Depends on: none

## Problem

The root Trading Room load failure currently collapses to
`Failed to load Trading Room`. The user cannot see whether the failure is auth,
BFF, stale bundle, schema drift, or network. The cache-header repair is merged,
but the UI still lacks production diagnostics and recovery.

## Scope

- Preserve BFF error status/code/request id/correlation id in the page state.
- Add retry and safe reload behavior for stale deployment/bundle suspicion.
- Add tests for 401/403/404/409/412/500/network failure paths.
- Keep secrets out of the UI and logs.
- Extend probes so hosted checks fail when the page only shows the generic
  failure string.

## Acceptance

- The user-facing error state has actionable diagnostics and retry behavior.
- Browser probes capture BFF statuses, console errors, deployment id, and cache
  headers.
- CI or smoke tests fail on a generic-only `Failed to load Trading Room` state.
- The merged cache-header policy from PR #2845 remains verified.

## Implementation Notes

- `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` now throws
  `TradingRoomBffError` for HTTP and network failures. The diagnostic payload
  preserves method, URL, HTTP status, BFF error code/message, request id,
  correlation id, and retryability while keeping response details out of the UI.
- Trading Room BFF calls now honor `VITE_BFF_BASE_URL` before falling back to
  the browser origin. This prevents Pantheon-owned static FE hosting from
  accidentally sending `/bff/agora/*` requests to the static asset origin.
- `TradingRoomPage` renders a diagnostic root error state with status/code,
  request/correlation ids, retry, and cache-busting safe reload. The old
  generic-only `Failed to load Trading Room.` state is no longer emitted.
- `execute-plans/scripts/probe-hosted-browser-bff.mjs` now probes
  `/agora/trading-room`, records BFF response statuses and request/correlation
  headers, captures `deployment.json` identity/cache headers, checks shell and
  hashed asset cache policy, and fails when the page only exposes the generic
  Trading Room failure text.

## Validation

- `node --check execute-plans/scripts/probe-hosted-browser-bff.mjs`
- `npm test -- --run src/lib/bff-v1/agora/tradingRoom.test.ts src/agora/pages/trading-room/TradingRoomPage.test.tsx`
- `npm run build:agora`
- `bash -n deploy/caddy/sync-caddy.sh`

Pre-deploy hosted smoke was also run with output redirected to `/tmp`:

- `PANTHEON_AUDIT_OUT_DIR=/tmp/ag-dynui-prod-004-probe PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io node scripts/probe-hosted-browser-bff.mjs`
- Result: expected `pass=false` against the current deployed dev bundle, because
  that host has not received this branch and currently exposes BFF CORS/chunk
  preload failures. The smoke did verify the probe captures deployment id,
  cache headers, failed request evidence, and console errors without writing
  generated audit files into this repo.
