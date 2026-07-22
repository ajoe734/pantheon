# MGMT-GAP-010 Production-Green Closeout - Load/Release Gate

Date: 2026-07-01
Owner: Claude
Reviewer: Codex
Parent: `MGMT-GAP-010` (Management console load and release gate performance)

## Verdict

`MGMT-GAP-010` is now production-green on the load/release-gate detector.
A fresh hosted route-load and BFF-fanout probe against the merged dev FE/BFF
pair, followed by a rerun of `scripts/aggregate-release-gate.mjs`, produces
`result.pass: true` with zero failures and zero missing checks.

## Starting Point

`MGMT-LOAD-007` (archived `done`,
`docs/04/pantheon_management_console_load_gap_2026-07-01/archive/MGMT-LOAD-007-closeout-2026-07-01.md`)
confirmed `MGMT-LOAD-001` through `MGMT-LOAD-006` were terminal `done` with
merged implementation, but flagged that no worker had yet run an authorized
hosted route-load/BFF-fanout probe against the post-fix dev environment and
regenerated the gate. The archived `release-load-gate-2026-07-01.json` at
that point was `result.pass: false` on stale pre-fix inputs from
`MGMT-LOAD-001`'s original baseline.

## Gap Found During The Rerun

Re-running the probes exposed a second, previously undetected gap:
`execute-plans/scripts/probe-bff-fanout-concurrency.mjs` requested `/health`,
`/bff/management/evidence`, `/bff/alerts`, `/bff/approvals`, and `/bff/jobs`,
but never `/bff/management/shell-summary` — even though
`scripts/aggregate-release-gate.mjs` (this repo) gates that route's fanout
p95 at <= 200 ms. Every prior gate run therefore reported the
`/bff/management/shell-summary` fanout check as `missing` rather than
`pass`/`fail`. Since `result.pass` requires every check to resolve to
`pass`/`skip`/`warn` (not `missing`), the gate could never turn green even
after all `MGMT-LOAD-002/003/004/005` fixes landed and even with a fresh
hosted run — the probe itself needed to measure the route.

Fixed in `execute-plans` PR
https://github.com/ajoe734/execute-plans/pull/139 (merged
2026-07-01T18:05:35Z): adds `/bff/management/shell-summary` to
`FANOUT_ROUTES`. This is a probe-only change; it does not touch BFF or FE
runtime behavior.

## Hosted Rerun Evidence

- FE: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- FE/BFF commit probed: `cbd833c49edc3a2006b0caeda0234c8eeaf44fac` (same
  commit `execute-plans` PR #138 / `MGMT-LOAD-006` deployed; confirmed via
  `GET /deployment.json` at probe time)
- Auth token shape: `op-<id>:admin` (dev stub-auth shape; not a production
  secret)
- Probe run: 2026-07-01T18:02Z (route-load), 2026-07-01T18:02Z (BFF fanout,
  5 rounds), from a fresh `execute-plans` `origin/dev` worktree with the
  PR #139 fanout-route fix applied locally before merge, so the rerun
  reflects the fixed probe against the already-fixed hosted FE/BFF pair.

### Route-load milestones for `/management/evidence` (ms since navigation start)

| Milestone | ms | Budget |
|---|---:|---:|
| domcontentloaded | 141 | n/a |
| shell (#root) attached | 262 | n/a |
| route heading visible | 555 | n/a |
| primary Evidence API complete | 540 | n/a |
| first row or empty-state visible | 609 | <= 2500 |

- non-primary BFF startup requests before first row: 2 (`/bff/me`,
  `/bff/management/shell-summary`) — budget <= 2, **pass**
- duplicate `/bff/jobs` requests before first row: 0 — budget <= 0, **pass**
- `usedNetworkidle`: `false` (structural guarantee; `/bff/events/stream`
  observed opening at ~535 ms, excluded from readiness milestones)

Full evidence: `route-timing-2026-07-01-postfix.json`,
`request-waterfall-2026-07-01-postfix.json`,
`route-load-baseline-2026-07-01-postfix.md`.

### BFF fanout concurrency (5 rounds, ms, p95)

| Route | p95 ms | Budget |
|---|---:|---:|
| `/health` | 134 | <= 200 |
| `/bff/management/evidence` | 78 | <= 750 |
| `/bff/management/shell-summary` | 78 | <= 200 |
| `/bff/alerts` | n/a (no explicit budget) | n/a |
| `/bff/approvals` | n/a (no explicit budget) | n/a |
| `/bff/jobs` | n/a (no explicit budget) | n/a |

Full evidence: `bff-fanout-baseline-2026-07-01-postfix.json`,
`bff-fanout-baseline-2026-07-01-postfix.md`.

### Bundle budget

Reused `release-bundle-2026-07-01.json` from `MGMT-LOAD-006` as the
`--bundle-file` input (no FE bundle change since that closeout): initial
management JS gzip 269,474 bytes (budget 819,200), Evidence route chunk
gzip 13,345 bytes (budget 153,600) — both **pass**.

### Release load gate manifest

`scripts/aggregate-release-gate.mjs` regenerated in place at
`release-load-gate-2026-07-01.json` / `.md`:

```json
"result": {
  "pass": true,
  "overall": "pass",
  "failures": [],
  "missing": []
}
```

All five gates (`0_dependencies`, `1_bundle`, `2_route_timing`,
`3_startup_requests`, `4_bff_fanout`) report `pass` for every check.

## MGMT-GAP-006 Required Artifact Paths (unchanged locations, refreshed content)

- `release-load-gate-2026-07-01.json`
- `release-load-gate-2026-07-01.md`
- `release-route-timing-2026-07-01.json`
- `release-request-waterfall-2026-07-01.json`
- `release-bff-fanout-2026-07-01.json`
- `release-bundle-2026-07-01.json`

`MGMT-GAP-006` can now read `result.pass: true` from
`release-load-gate-2026-07-01.json` at the same path it was already told to
require.

## Residual Risks

| Risk | Blocking | Owner | Expiry | Required action |
|---|---|---|---|---|
| Public BFF deployment commit evidence is not exposed at `/deployment.json` (BFF returns 404 for that path). | Non-blocking for the load detector; blocks a stronger final deploy-provenance claim. | `MGMT-GAP-007` owner Codex with BFF deploy owner support (carried over from `MGMT-LOAD-007`, unchanged). | Before `MGMT-GAP-007` final production closeout. | Capture BFF deploy commit through an authorized deploy record, release run, or a public manifest endpoint. |
| `execute-plans` PR #139's own CI (`Pantheon FE-BFF Integration Gate`) was still `IN_PROGRESS` at the moment GitHub squash-merged it via auto-merge. | Non-blocking for this closeout's own evidence (produced by running the probes directly against the hosted pair, independent of that CI run), but worth a follow-up glance. | `MGMT-GAP-010` owner Claude. | Best-effort, no hard expiry. | Confirm `https://github.com/ajoe734/execute-plans/pull/139` checks finished green post-merge; if a check failed, open a fix-forward follow-up. |

## Parent Gate Status

`MGMT-GAP-010` is production-green on the load/release-gate detector as of
this closeout. `execute-plans` PR #139 merged at 2026-07-01T18:05:35Z.
