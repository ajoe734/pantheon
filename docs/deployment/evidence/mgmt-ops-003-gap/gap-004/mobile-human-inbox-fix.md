# MGMT-OPS-003-GAP-004 Mobile Human Inbox Repair Evidence

Date: 2026-07-12 UTC
Owner: Codex2
Reviewer: Codex

## Delivery

- Repository: `ajoe734/execute-plans`
- PR: `#265` (`https://github.com/ajoe734/execute-plans/pull/265`)
- Task commit: `d31544d22f454b0a34af5a8e4d0fe495e22ff6d6`
- `dev` merge commit: `b65bae467a2439a2d83ed73e44ccd67597103dee`
- Frontend: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- Deployment run: `29174258079` (success)
- Post-merge integration gate: `29174258016`

`GET /deployment.json` reported the exact merge commit above with source branch
`dev`, `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and
`VITE_BFF_REAL_WRITES=false`.

## Repair

Human Inbox now resets stale strict-live transport state immediately before its
own required list request. The hosted regression waits for
`GET /bff/management/human-inbox`, requires HTTP 200, records its payload, and
still fails on console errors, request failures, BFF 4xx/5xx responses, or seed
fallback indicators. This preserves fail-closed behavior: a failing inbox
request reports failure again rather than exposing mock or seed data.

## Hosted verification

Command:

```sh
PANTHEON_HOSTED_E2E=1 \
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
npx playwright test e2e/21-portfolio-workflow-hosted.spec.ts --project=chromium
```

Result against deployed merge `b65bae467a2439a2d83ed73e44ccd67597103dee`:

- desktop (1440 x 1000): passed
- mobile (390 x 844): passed
- required Human Inbox request failures: 0
- console errors: 0
- failed browser requests: 0
- BFF 4xx/5xx responses: 0
- lazy chunk failures: 0
- mock/seed fallback indicators: 0
- target context: holding, persona, and runtime query parameters preserved;
  selected holding visible on Human Inbox

The Playwright run completed `2 passed (28.3s)`.
