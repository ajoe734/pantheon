# V3 — Service/contract test suites were never in the CI gate (direction E, deepening)

- Date: 2026-06-14
- Branch: task/verify-v3-suite-runner
- Non-duplication: task-briefs/PRs show others on ops/rllib/marketdata/broker/MGMT;
  none wire a service-suite CI gate or a unified verification runner. Distinct.

## Finding (root cause of silent test rot)
The PR gate (`branch-ci.yml`) runs only: Commit trailers, Runtime mirror guard, and
`scripts/run-acceptance.sh smoke` (stage0 validate/baseline). The `full` acceptance
mode runs only top-level `pytest -q tests` — and even that is non-blocking
(`|| echo "pytest reported failures"`). **The ~600 service/contract suites under
`services/**/test_*.py` are NOT run by any gate.** That is exactly why the BFF
contract-test rot (envelope shape, error-code drift — fixed earlier this session in
PRs #1525-#1531) accumulated undetected.

## Fix (this round)
- Added a `verify` mode to `scripts/run-acceptance.sh` that runs the verified-green
  service-layer suites (optimizer / evolution / governance / consultation / telemetry /
  execution-runtime-manager / foundation / research-worker-gateway / broker) under
  `PANTHEON_BFF_AUTH_STUB=true`, and FAILS on regressions.
- Verified locally: `run-acceptance.sh verify` -> **607 passed**.
- Wired a `verify-suites` job into `branch-ci.yml` (installs the suites' requirements
  best-effort, runs the verify mode). Marked `continue-on-error: true` for now so it is
  informational and cannot block the multi-agent fleet's merges while CI service-dep
  installation is hardened.

## Follow-ups
- Harden service-dep install in CI, then drop `continue-on-error` and add the
  `verify-suites` check to branch protection to make it a true blocking regression gate.
- Extend `verify` to the BFF contract suites once the escalated v5 confirm-token
  failures are resolved (currently they would make a full BFF run red).
