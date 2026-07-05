# PPL-GOV-007 Production Closeout - 2026-07-05

Status: complete
Closed at: 2026-07-05T15:56:44Z

## Scope

This closeout proves the persona promotion-governance packet reached
production-level readiness for dev:

```text
recommendation -> submit -> promotion review -> human decision -> auditable receipt
```

It also records the paper-ledger correction: paper personas use isolated
`paper_ledger_id` references; `capital_pool_id` is reserved for explicit
canary/live targets or legacy migration trace.

## Merged Pull Requests

| Repo | PR | Purpose | Merge commit |
|---|---:|---|---|
| `ajoe734/pantheon` | #3008 | Gap spec, execution packet, BFF promotion review / submit baseline | `08d406f541d2b25cc654b78ceb86cc908678ca8c` |
| `ajoe734/pantheon` | #3029 | Isolated paper ledger BFF contract and docs | `2fde0a55b12ed0b770270a00cf6b2de37c3cc289` |
| `ajoe734/execute-plans` | #181 | Governed recommendation submit UI, Human Inbox links, paper ledger display, mobile gate fixes | `d6228248f5ca6d39a7ae21dc5942ba92ed5348f2` |

Current verified dev refs during closeout:

- `ajoe734/pantheon` `origin/dev`: `4933c36564b30085480dce5a0e0bfc71d7806c49`
- `ajoe734/execute-plans` `origin/dev`: `d6228248f5ca6d39a7ae21dc5942ba92ed5348f2`

## Hosted / CI Evidence

Pantheon BFF:

- PR #3029 state: merged.
- Commit trailers: pass.
- Runtime mirror guard: pass.
- Smoke acceptance: pass.
  - `https://github.com/ajoe734/pantheon/actions/runs/28745770384/job/85236312575`
  - `https://github.com/ajoe734/pantheon/actions/runs/28745771352/job/85236315896`

Execute Plans frontend:

- PR #181 state: merged.
- Integration gate: pass, 11m10s.
- Gate job: `https://github.com/ajoe734/execute-plans/actions/runs/28746141868/job/85237263364`
- The gate completed lint, unit/integration tests, build, contract drift,
  authenticated BFF smoke, live dry-run write probe, management hosted
  production acceptance, and Playwright E2E.

## Local Validation Evidence

BFF validation run before merge:

```sh
git diff --check
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/test_bff_strategy_persona_contract.py services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py
python3 -m pytest services/control-plane/bff/test_bff_strategy_persona_contract.py services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py services/control-plane/bff/test_bff_promotion_review_governance.py services/control-plane/bff/tests/test_bff_pm12_persona_league.py -q
```

Result: 57 passed.

Frontend validation run before merge:

```sh
git diff --check
npm test -- src/lib/bff-v1/__tests__/management-pm12.test.ts src/lib/bff-v1/__tests__/management.test.ts src/lib/v5/management/__tests__/pm12.test.ts src/management/pages/oversight/RankingRecommendationPages.test.tsx src/management/pages/oversight/HumanGateDetail.test.tsx src/management/pages/oversight/PersonaFleetPage.test.tsx
npm run lint
npm run build
PORT=4194 PANTHEON_FE_BASE_URL=http://127.0.0.1:4194 FRONTEND_BASE_URL=http://127.0.0.1:4194 FE_INT_GATE_SSE_WINDOW_MS=1000 npx playwright test e2e/04-sentinel-remediation.spec.ts e2e/04b-optimization-loop.spec.ts e2e/17-a11y-v5.spec.ts e2e/18-perf.spec.ts --project=mobile-chromium --workers=1 --reporter=list
```

Results:

- Targeted unit/integration tests: 71 passed.
- Lint: pass with pre-existing warnings.
- Build: pass with pre-existing Rollup/CSS/chunk warnings.
- Mobile Playwright gate: 18 passed.

## Product Surface Evidence

BFF dev now contains:

- `POST /bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit`
- `GET /bff/management/promotion-reviews`
- `GET /bff/management/promotion-reviews/{review_id}`
- `POST /bff/management/promotion-reviews/{review_id}/decisions`
- Paper persona projections exposing `paper_ledger_id` / `paperLedgerId`
  while keeping paper `capital_pool_id` empty unless an explicit live target is
  under review.

Frontend dev now contains:

- Persona League and Quarterly Ranking submit actions calling the BFF governed
  recommendation-submit adapter.
- Human review messaging that states `liveCapitalMutation=false`.
- Human Inbox / Human Gate deep links for returned promotion reviews.
- Persona Fleet display of paper ledgers instead of legacy shared paper pool
  ids.

## Invariants Verified

- New personas start in `paper_running`.
- Paper personas use isolated paper ledger references.
- Paper rows do not display legacy paper pool ids as shared real capital pools.
- Recommendation submit creates or returns a human-review item.
- Recommendation submit and approval do not directly place orders or mutate live
  capital.
- Promotion/capital changes remain human-gated.
- Emergency containment remains separate from promotion and cannot promote or
  increase capital.

## Residual Risk

| Risk | Owner | Expiry | Disposition |
|---|---|---:|---|
| Frontend lint/build still report pre-existing warnings for fast refresh, deprecated v3 imports, hook dependencies, Rollup circular chunks, CSS minification, and chunk size. | Frontend Platform | 2026-07-19 | Non-blocking for this packet; CI and local gates pass. |
| Hosted smoke proves route availability and gate behavior, but not real broker/live capital mutation because live mutation is intentionally disabled by policy. | Risk Owner / Operator | 2026-07-19 | Expected invariant; canary/live target execution remains a separate human-approved deployment command. |

## Closeout Decision

PPL-GOV-007 is complete. The gap spec, execution tasks, BFF implementation,
frontend implementation, PR merges, dev checks, hosted gate, and residual-risk
accounting are recorded.
