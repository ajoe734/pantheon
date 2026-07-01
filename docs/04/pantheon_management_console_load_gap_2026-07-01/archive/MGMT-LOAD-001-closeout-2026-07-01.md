# MGMT-LOAD-001 Closeout - Management Load Baseline And Route-Ready Probes

Date: 2026-07-01

## Status

MGMT-LOAD-001 (Wave 0 of the management console load gap fleet) is complete.
Hosted route-load and BFF fanout baseline probes are checked in, run against
the deployed dev environment, and their first-run evidence is archived here.

## Delivery Evidence

- execute-plans PR: https://github.com/ajoe734/execute-plans/pull/130
- PR #130 merge commit: `7cd606037b3b4916fe67483b1be145c32881217d`
- execute-plans branch: `task/mgmt-load-001-baseline-probes`
- New scripts: `scripts/probe-route-load-baseline.mjs`,
  `scripts/probe-bff-fanout-concurrency.mjs`
- New CI-safe fixture-mocked spec: `e2e/22-management-evidence-load.spec.ts`
- New npm scripts: `probe:route-load`, `probe:bff:fanout`
- This PR only adds probe tooling and a fixture-mocked e2e spec; it does not
  change any user-facing runtime bundle, so no FE redeploy was required for
  the baseline evidence in this closeout.

## Local Validation

- `npx playwright test e2e/22-management-evidence-load.spec.ts --project=chromium`
  against a local `vite` dev server — 1 passed. The spec recorded (as soft,
  non-gating annotations): `/bff/jobs` fetched 3 times on first route load
  and 23 non-primary BFF/FE requests observed before first row in the local
  fixture harness (higher than hosted, because local dev serves unminified
  chunks and additional polling requests not present in the production
  build).
- `npx eslint e2e/22-management-evidence-load.spec.ts` — clean.

## Hosted Baseline Evidence

- FE: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- FE commit probed: `18b406d9bb3d`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- Auth token shape: `op-<id>:admin` (dev stub-auth shape; not a production
  secret)
- Probe timestamp: `2026-07-01T06:18:29.901Z` (route-load) /
  `2026-07-01T06:18:50.255Z` (BFF fanout)

Route-load milestones for `/management/evidence` (ms since navigation start):

| Milestone | ms |
|---|---:|
| domcontentloaded | 2799 |
| shell (#root) attached | 4328 |
| route heading visible | 4508 |
| primary Evidence API complete | 4485 |
| first row or empty-state visible | 4668 |

Confirmed gaps (matches
`MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md` section 1-3):

- 6 non-primary BFF requests (`/bff/me`, `/bff/approvals`, `/bff/alerts`,
  `/bff/jobs` x2, `/health`) fire concurrently with the primary Evidence read.
- `/bff/jobs` is fetched twice on first route load (TopBar + JobProgressDrawer
  duplicate reads) — `MGMT-LOAD-P1-002`.
- The probe never uses `networkidle`; `/bff/events/stream` opens at ~4.2s and
  is explicitly excluded from readiness milestones.

BFF fanout concurrency (5 rounds, ms, p95):

| Route | p95 ms | Target |
|---|---:|---:|
| `/health` | 1328 | <=200ms under 10 concurrent (target from spec §7.2) |
| `/bff/management/evidence` | 1423 | <=750ms under shell fanout |
| `/bff/alerts` | 1513 | n/a (no explicit target yet) |
| `/bff/approvals` | 1537 | n/a (no explicit target yet) |
| `/bff/jobs` | 1538 | n/a (no explicit target yet) |

This reproduces `MGMT-LOAD-P0-002`: BFF read concurrency delays `/health`
well past its isolated baseline, confirming the read-isolation gap that
MGMT-LOAD-005 must close.

Full evidence: `route-timing-2026-07-01.json`, `request-waterfall-2026-07-01.json`,
`route-load-baseline-2026-07-01.md`, `bff-fanout-baseline-2026-07-01.json`,
`bff-fanout-baseline-2026-07-01.md` (this directory).

## Follow-On Gates

- `MGMT-LOAD-002` (BFF shell summary) and `MGMT-LOAD-004` (FE route code
  splitting) are now unblocked.
- `MGMT-LOAD-003` (FE shell fanout reduction) and `MGMT-LOAD-005` (BFF read
  concurrency isolation) depend on this baseline and can use the numbers
  above as their before-state.
- `MGMT-LOAD-006` (release-gate budgets) should promote this probe's soft
  annotations (non-primary request count, duplicate jobs reads) into hard CI
  budgets once MGMT-LOAD-002/003/005 land.

## Residual Risks

- The hosted baseline is a single run, not a statistical sample; MGMT-LOAD-006
  should decide the sampling/percentile methodology for the release gate.
- The local fixture e2e spec's non-primary-request count (23) is not directly
  comparable to the hosted count (6) because local dev serves additional
  unminified asset/chunk requests; both are recorded so future runs can track
  their own deltas rather than being compared to each other.
- This task does not fix any of the confirmed gaps — it only makes them
  measurable. Fixing shell fanout, BFF read concurrency, and duplicate jobs
  reads remains MGMT-LOAD-002/003/005 scope.
