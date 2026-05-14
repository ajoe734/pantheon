# FE-INT-GATE-C02 Review - Codex2

Status: approved
Reviewer: Codex2
Reviewed at: 2026-05-14

## Acceptance Check

| Criterion | Result |
|---|---|
| 9 resource POST create intents covered | Pass |
| Idempotency-Key header asserted for every create request | Pass |
| CommandResponse<T>.data required and structurally checked | Pass |
| VALIDATION_FAILED.fieldErrors checked for invalid create intents | Pass |
| Deployment create remains plan-only and does not start live execution | Pass |

## Evidence

- Reviewed `execute-plans/e2e/08-create-intent.spec.ts` and `execute-plans/e2e/helpers/fixtures.ts` from task commit `2455930e`.
- Verified the spec covers these resources: strategies, personas, capital-pools, ranking-formulas, rebalances, deployments, evolution-programs, research-experiments, artifacts.
- Verified the deployment fixture sends paper/plan-only intent fields and asserts `executionStarted: false` plus `liveCapitalSideEffects: false`.

## Verification

- `npx --yes playwright test execute-plans/e2e/08-create-intent.spec.ts --reporter=list` failed before running tests because this repo checkout has no local `@playwright/test` package.
- `tmpdir=$(mktemp -d /tmp/pw-c02-XXXXXX) && npm --prefix "$tmpdir" install --silent @playwright/test && NODE_PATH="$tmpdir/node_modules" "$tmpdir/node_modules/.bin/playwright" test execute-plans/e2e/08-create-intent.spec.ts --reporter=list`
  - Result: 2 passed, 1 skipped.
  - The skipped test is the live BFF probe, gated by `FE_INT_GATE_LIVE_BFF=1` or `F08_CREATE_INTENT_LIVE_BFF=1`.

## Decision

Approved. The fixture-driven browser coverage satisfies FE-INT-GATE-C02 acceptance, and the opt-in live probe is present without making normal review depend on staging availability.
