# Master System Verification Program (10-round, deepening + broadening)

Goal: verify every direction in the system-verification inventory, archive each
round's plan + results, fix gaps via the normal dev workflow (worktree -> PR ->
CI -> merge -> deploy), then draft a deeper/broader plan. Repeat x10.

## Verification inventory (7 directions)
- A. Left half / real strategy pipeline (research->distill->experiment->approved
  artifact->deploy->bind). Biggest gap: everything has run on rescue placeholder
  bindings (artifacts don't exist).
- B. Governance/safety state machines: paper->canary->live promotion (#12),
  evolution lifecycle (#13), kill-switch/safe-mode (#14), rollback/position (#15),
  delivery closure (#16). None exercised live.
- C. Real broker/market integration (Shioaji TW / US sandbox; real market data;
  P0 activation lift). Currently broker paper+live disabled, mock adapter, synthetic data.
- D. Cross-cutting non-functional: security (21 dependabot vulns, real OIDC/JWT
  strict path), data consistency/persistence, saga/cross-service, concurrency/idempotency,
  HA/resilience, performance/backpressure, disaster recovery/alerting.
- E. Operational/deployment integrity: image drift, reconciler durability, CI gate
  coverage, config drift.
- F. Frontend & e2e (operator console renders live data; Playwright/integration suites).
- G. Test quality / multi-env (stub vs real behaviour; staging/prod parity).

## Verified before this program (prior sessions, PRs #1523-#1536)
Control plane/BFF (443 routes, fail-closed), ~125 stale contract tests fixed,
9 service suites (~600 tests) green, execution half proven (paper fills, fail-closed
broker), right half closed+deployed+verified (fill projection + synthetic market data
+ BFF trade visibility).

## Rounds
| # | Direction | Focus | Status |
|---|-----------|-------|--------|
| V1 | E | deploy-drift detectability + audit tool + git-SHA image labels | shipped |
