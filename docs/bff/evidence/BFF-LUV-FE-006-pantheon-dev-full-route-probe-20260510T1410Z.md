# BFF-LUV-FE-006 Pantheon Dev Full Route Probe

Date: 2026-05-10T14:10:49Z

Target: https://pantheon-dev.lovable.app

Lovable deployment id: `347015fb-e064-400f-8fdf-f9ece03bfe6f`

Execute-plans HEAD at verification: `b699c897c9ec78609d121a9f083183ef4bab67fb`

Included BFF route fix commit: `7138d50449e94b95f77241a13998cdbab0bb2717`

## What Changed

- `src/lib/bff/agora.ts` no longer calls the non-contract detail path `/bff/agora/signals/{id}`.
- Signal detail now resolves through the canonical list route `/bff/agora/signals`.
- Strict detail adapters now support expected not-found handling for v5 detail readers without tripping global live fallback.
- Live list normalization from `c0d761d` remains active for `{ data: [], page_info: ... }` and `{ items: [], count: ... }` BFF payloads.

## Local Verification Before Publish

- `npm run test -- --run src/lib/bff/__tests__/liveAdapters.test.ts src/lib/bff-v1/__tests__/lists.test.ts src/lib/bff-v1/__tests__/headers.test.ts`
  - Result: 3 files passed, 26 tests passed.
- `npm run build`
  - Result: passed. Existing chunk/dynamic-import warnings only.
- Production preview targeted route `/agora/signals/sig_0`
  - `LIVE BFF UNAVAILABLE`: false
  - Rendered not-found state: true
  - BFF requests: `/health`, `/bff/agora/signals`
  - Non-contract request `/bff/agora/signals/sig_0`: false
  - Page errors: 0
  - Console errors: 0

## Hosted Full Route Probe

Method: Playwright Chromium against `https://pantheon-dev.lovable.app`, 109 routes from `src/App.tsx`, including aliases, detail routes, Agora routes, QA/audits, and not-found smoke route.

Final adjusted full-route result:

```json
{
  "deploymentId": "347015fb-e064-400f-8fdf-f9ece03bfe6f",
  "routes": 109,
  "badRoutes": 0,
  "totalBffResponses": 268,
  "totalNon2xx": 0,
  "totalFailures": 0,
  "totalPageErrors": 0,
  "totalConsoleErrors": 0,
  "bad": []
}
```

The first full-route pass also had `totalNon2xx=0`, `totalFailures=0`, `totalPageErrors=0`, and `totalConsoleErrors=0`. It flagged two false positives only because the crawler's rendered-body length threshold treated expected short not-found views as not rendered:

- `/agora/signals/sig_0`: correctly displayed `找不到 Signal`.
- `/route-smoke-not-found`: correctly displayed the SPA 404 page.

The adjusted second pass treats those expected short views as rendered and finished with `badRoutes=0`.

## Targeted Hosted Signal Detail Probe

Route: `/agora/signals/sig_0`

```json
{
  "banner": false,
  "notFound": true,
  "bff": [
    { "method": "GET", "status": 200, "path": "/health" },
    { "method": "GET", "status": 200, "path": "/bff/agora/signals" },
    { "method": "GET", "status": 200, "path": "/bff/events/stream" }
  ]
}
```

No request was made to `/bff/agora/signals/sig_0`.
