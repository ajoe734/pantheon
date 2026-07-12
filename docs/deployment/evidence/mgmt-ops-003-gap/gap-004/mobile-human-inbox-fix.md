# MGMT-OPS-003-GAP-004 Mobile Human Inbox Repair Evidence

Date: 2026-07-12 UTC
Owner: Codex2
Reviewer: Codex

## Delivery

- Repository: `ajoe734/execute-plans`
- Repair PR: `#265` (`https://github.com/ajoe734/execute-plans/pull/265`)
- Integration-gate follow-up PR: `#267`
  (`https://github.com/ajoe734/execute-plans/pull/267`)
- Task commit: `d31544d22f454b0a34af5a8e4d0fe495e22ff6d6`
- Follow-up commit: `871f408cf167ef11b498dc90e56b79e2af365d1d`
- Final `dev` merge commit: `5647d4b03b42c8f4487c63f604fd39555660dee3`
- Frontend: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- Deployment run: `29174965397` (success)
- Post-merge integration gate: `29174965400`

`GET /deployment.json` reported the exact final merge commit above with source branch
`dev`, `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and
`VITE_BFF_REAL_WRITES=false`.

## Repair

Human Inbox now resets stale strict-live transport state immediately before its
own required list request. The hosted regression waits for
`GET /bff/management/human-inbox`, requires HTTP 200, records its payload, and
still fails on console errors, request failures, BFF 4xx/5xx responses, or seed
fallback indicators. This preserves fail-closed behavior: a failing inbox
request reports failure again rather than exposing mock or seed data.

The first post-merge gate (`29174258016`) exposed adjacent hosted-suite
regressions after PR #265. PR #267 closed them without relaxing strict fallback:
Persona Fleet resets the same stale transport state before its live request,
route-split assertions distinguish Vite source modules from hosted hashed
chunks, the SSE performance harness has bounded shutdown, and click-map
expectations follow the canonical Rankings and Performance routes.

## Hosted verification

Command:

```sh
PANTHEON_HOSTED_E2E=1 \
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
npx playwright test e2e/21-portfolio-workflow-hosted.spec.ts --project=chromium
```

Result against deployed merge `5647d4b03b42c8f4487c63f604fd39555660dee3`:

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

The Playwright run completed `2 passed (15.3s)`.
