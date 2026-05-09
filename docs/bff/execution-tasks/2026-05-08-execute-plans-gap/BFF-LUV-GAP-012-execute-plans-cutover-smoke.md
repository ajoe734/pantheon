# BFF-LUV-GAP-012 - Execute-Plans Cutover Smoke

Priority: P0

Area: Cross-repo Lovable integration verification

## Goal

After the missing Pantheon BFF route families are implemented or explicitly superseded, verify the new `execute-plans` repo can run against Pantheon BFF without silently falling back to mock for live-required routes.

## Scope

Repo:

- `/home/lupin/code/execute-plans`

Already-added frontend wiring to preserve:

- `src/lib/bff/transport.ts`
- `src/lib/bff/client.ts`
- `src/lib/bff/v5.ts`
- `README.md`
- `.env.example`
- `.env.dev.example`
- `.env.staging-live.example`

## Acceptance Criteria

- `npm run test` passes in `execute-plans`.
- `npm run build` passes in `execute-plans`.
- A BFF smoke script proves live/hybrid mode reaches:
  - `GET /health`
  - `GET /bff/actions`
  - `GET /bff/approvals`
  - `GET /bff/v5/interventions`
  - at least one newly implemented route from each active gap task family.
- Public dev/staging BFF URL reachability is recorded. If the public URL times out, the task must identify whether this is networking, DNS, firewall, load balancer, or service health.
- Remaining mock fallback is documented as `deferred_with_task`, not accidental.

## Verification

```bash
cd /home/lupin/code/execute-plans
npm run test
npm run build
```

## 2026-05-09 Cutover Smoke Result

Target:

- `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`

Execute-plans acceptance:

- `npm run test` passed: 28 test files passed, 270 tests passed.
- `npm run build` passed: Vite production build completed. Warnings were limited to browserslist data age, mixed dynamic/static import chunking for `src/lib/bff/realtime.ts`, and bundle size.

Anonymous live BFF probe:

- Public health/docs/spec:
  - `GET /health` -> `200`
  - `GET /healthz` -> `200`
  - `GET /readyz` -> `200`
  - `GET /docs` -> `200`
  - `GET /openapi.json` -> `200`
- Route-registration/auth-gate smoke:
  - `GET /bff/events/stream` -> `401`
  - `GET /bff/me` -> `401`
  - `POST /bff/auth/refresh` -> `401`
  - `POST /bff/logout` -> `401`
  - `GET /bff/actions` -> `401`
  - `POST /bff/actions/strategy/strategy-alpha/promote` -> `401`
  - `GET /bff/strategies` -> `401`
  - `GET /bff/strategies/strategy-alpha` -> `401`
  - `POST /bff/strategies/strategy-alpha/actions/promote` -> `401`
  - `GET /bff/personas` -> `401`
  - `GET /bff/personas/persona-alpha` -> `401`
  - `GET /bff/capital-pools` -> `401`
  - `GET /bff/capital-pools/capital-alpha` -> `401`
  - `GET /bff/rebalances` -> `401`
  - `GET /bff/deployments` -> `401`
  - `GET /bff/evolution-programs` -> `401`
  - `GET /bff/jobs` -> `401`
  - `GET /bff/approvals` -> `401`
  - `POST /bff/approvals/approval-alpha/decide` -> `401`
  - `POST /bff/approvals/batch-decide` -> `401`
  - `GET /bff/alerts` -> `401`
  - `POST /bff/alerts/alert-alpha/acknowledge` -> `401`
  - `GET /bff/incidents` -> `401`
  - `GET /bff/audit` -> `401`
  - `GET /bff/artifacts` -> `401`
  - `GET /bff/runtimes` -> `401`
  - `GET /bff/mcp-servers` -> `401`
  - `POST /bff/mcp-servers/server-alpha/import-tools` -> `401`
  - `GET /bff/mcp-tools` -> `401`
  - `GET /bff/skills` -> `401`
  - `GET /bff/channels` -> `401`
  - `GET /bff/tools` -> `401`
  - `GET /bff/ranking-formulas` -> `401`
  - `GET /bff/research-experiments` -> `401`
  - `GET /bff/agora/signals` -> `401`
  - `GET /bff/agora/inbox` -> `401`
  - `GET /bff/agora/journal` -> `401`
  - `GET /bff/agora/postmortems` -> `401`
  - `GET /bff/agora/ask/sessions` -> `401`
  - `GET /bff/v5/loop-runs` -> `401`
  - `GET /bff/v5/sentinel/findings` -> `401`
  - `GET /bff/v5/interventions` -> `401`
  - `POST /bff/v5/interventions/int-alpha/decide` -> `401`
  - `GET /bff/v5/execution/persona-health` -> `401`

Interpretation:

- `401` is the expected anonymous result for protected routes and proves the route is registered behind the auth gate, not missing as `404`.
- The previous live-probe failure mode is no longer reproduced: `/openapi.json` is `200`, and the probed contract catalog has no `404`.
- Public dev BFF URL is reachable; this is not a DNS, firewall, load balancer, or service-health outage.

Remaining mock fallback:

- `deferred_with_task`: authenticated DTO hydration and write-flow smoke still require an operator Bearer token and write-governance approval. Until that is available, `execute-plans` should keep `VITE_BFF_MODE=hybrid` and `VITE_BFF_REAL_WRITES=false`.
- This fallback is intentional for authenticated data/write validation only; route registration for the probed BFF contract paths is verified live.
