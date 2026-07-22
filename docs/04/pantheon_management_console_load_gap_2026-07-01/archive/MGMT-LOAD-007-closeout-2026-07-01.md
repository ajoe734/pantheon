# MGMT-LOAD-007 Closeout - Load Gap Parent Gate

Date: 2026-07-01
Owner: Codex
Reviewer: Claude
Parent: `MGMT-GAP-010`

## Verdict

`MGMT-LOAD-001` through `MGMT-LOAD-006` are terminal `done` in the live task
archive. The load-gap work is merged and the release gate is real: it fails on
stale pre-fix evidence instead of reporting a false green result.

`MGMT-GAP-010` is not production-green yet. The remaining blocker is a fresh
hosted route-load plus BFF-fanout probe against the merged dev FE/BFF pair, then
a re-run of `scripts/aggregate-release-gate.mjs` that produces
`result.pass: true`.

## Child Task Evidence

| Task | Terminal evidence | Merge / deploy evidence | Gate impact |
|---|---|---|---|
| `MGMT-LOAD-001` | Archived `done`; baseline route-load and BFF fanout probes approved. | Pantheon PRs `#2661` / `#2664` / closeout sync `#2674`; execute-plans PR `#130`; archive delivery commit `e88b78ba49640d2d25f92ce86a90dd95752ae8b5`. | Baseline proves the original slow shape: first row 4668 ms, duplicate startup `/bff/jobs`, and BFF fanout p95 over budget. |
| `MGMT-LOAD-002` | Archived `done`; shell-summary and canonical `/bff/jobs` route approved. | Pantheon PR `#2677` merged at `d1390c5e356340aff82b2027be2ffc2c19b52485`. | Enables cheap badge counts and removes the duplicate route definition. |
| `MGMT-LOAD-003` | Archived `done`; shell fanout reduction approved after route-primary-ready follow-up. | execute-plans PR `#136` merged at `75a943ed3fb007c61f056496e5b8f7dfdb305a53`; Pantheon evidence PR `#2705`; closeout PR `#2709` merged at `8315eb8a466c08e08b5d01346dc6d2ed5cee3357`. | Defers full-list fallback and jobs hydration until after primary route readiness. |
| `MGMT-LOAD-004` | Archived `done`; route code-splitting and hosted route-load evidence approved. | execute-plans PR `#134` merged at `255e60414e0ca36e29c1b2e39f0543d23d2eea80`; dev FE deploy run `28514407926` succeeded; Pantheon closeout PR `#2683`. | Hosted `/management/evidence` first row p75 931 ms, p95 1203 ms after route split. |
| `MGMT-LOAD-005` | Archived `done`; read-concurrency isolation approved. | Pantheon PR `#2682` merged at `40d82bc08b4b2981f246b2d06c4cb14a128b8ac8`; closeout PR `#2685`. | Local before/after reproduction: `/health` p95 improves from 1629 ms to 189 ms under synthetic fanout; Evidence p95 improves from 1795 ms to 425 ms. |
| `MGMT-LOAD-006` | Archived `done`; release gate approved with fail-closed stale-baseline result. | Pantheon PR `#2711` merged at `154980a97940e2cb78f6f325bca2c5413f63e32a`; Pantheon PR `#2712` merged at `65ba4685badf07f533e100ad1c7e822a299762ea`; execute-plans PR `#138` merged at `cbd833c49edc3a2006b0caeda0234c8eeaf44fac`. | `scripts/aggregate-release-gate.mjs` fails on route timing, startup requests, duplicate jobs, and BFF fanout when fed stale pre-fix baseline evidence. |

## Current Hosted Deployment

Public smoke on 2026-07-01:

- FE deployment manifest:
  `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`
  reported execute-plans `dev` commit
  `cbd833c49edc3a2006b0caeda0234c8eeaf44fac`, deployed at
  `20260701T171617Z`, with `VITE_BFF_MODE=live`,
  `VITE_BFF_FALLBACK=strict`, and `VITE_BFF_REAL_WRITES=false`.
- BFF health:
  `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health`
  returned `{"status":"ok","service":"operator-bff","version":"0.2.0"}`.
- BFF `/deployment.json` returned 404; no BFF commit manifest was available
  from that public path.
- No `PANTHEON_BFF_ACCESS_TOKEN` or
  `PANTHEON_BFF_SMOKE_BEARER_TOKEN` was configured in this worker, so no
  authorized hosted route-load/BFF-fanout green probe was run.

## Measurement Summary

| Evidence | File | Result |
|---|---|---|
| Original hosted route-load baseline | `route-timing-2026-07-01.json`, `request-waterfall-2026-07-01.json` | `usedNetworkidle:false`; first row 4668 ms; 5 non-primary BFF startup requests before first row; duplicate `/bff/jobs` observed. |
| Hosted route split after `MGMT-LOAD-004` | `mgmt-load-004-route-load-hosted-2026-07-01.md` | Five samples passed: first row p75 931 ms, p95 1203 ms; primary Evidence API p75 837 ms, p95 1131 ms. |
| Local BFF concurrency before/after | `bff-fanout-local-before-after-2026-07-01.json` / `.md` | `/health` p95 1629 ms -> 189 ms; Evidence p95 1795 ms -> 425 ms under synthetic 400 ms concurrent slow reads. |
| Bundle budget after execute-plans PR `#138` | `release-bundle-2026-07-01.json` | Initial management JS gzip 269474 bytes <= 819200; Evidence route chunk gzip 13345 bytes <= 153600. |
| Release load gate manifest | `release-load-gate-2026-07-01.json` / `.md` | `result.pass:false`; dependency and bundle gates pass, but stale pre-fix route timing, startup request, and BFF fanout inputs fail. Regenerated during this closeout with the archived `release-bundle-2026-07-01.json` as `inputs.bundleFile`. |

## MGMT-GAP-006 Required Artifact Paths

The hosted production acceptance harness must require the following paths from
`docs/04/pantheon_management_console_load_gap_2026-07-01/archive/` and must
read `result.pass` from the manifest:

- `release-load-gate-*.json`
- `release-load-gate-*.md`
- `release-route-timing-*.json`
- `release-request-waterfall-*.json`
- `release-bff-fanout-*.json`
- `release-bundle-*.json`

The exact current files are:

- `release-load-gate-2026-07-01.json`
- `release-load-gate-2026-07-01.md`
- `release-route-timing-2026-07-01.json`
- `release-request-waterfall-2026-07-01.json`
- `release-bff-fanout-2026-07-01.json`
- `release-bundle-2026-07-01.json`

The current manifest is blocking production acceptance because it is
`pass:false`. `MGMT-GAP-006` must not accept the load/release detector until a
fresh hosted run produces a `pass:true` manifest.

## Residual Risks

| Risk | Blocking | Owner | Expiry | Required action |
|---|---|---|---|---|
| No post-merge hosted route-load and BFF-fanout pass manifest after all `MGMT-LOAD-*` merges. | Yes, blocks `MGMT-GAP-010` production-green and `MGMT-GAP-006` final acceptance. | `MGMT-GAP-006` owner Claude, with Human/Ops bearer-token support if needed. | Before `MGMT-GAP-006` review, no later than 2026-07-02T23:59Z. | Run `npm run probe:route-load` and `npm run probe:bff:fanout` from execute-plans against the hosted dev FE/BFF pair, then re-run `scripts/aggregate-release-gate.mjs` in this repo and archive a `pass:true` manifest. |
| Public BFF deployment commit evidence is not exposed at `/deployment.json`. | Non-blocking for load detector if route/fanout probes pass, but blocks a stronger final deploy provenance claim. | `MGMT-GAP-007` owner Codex with BFF deploy owner support. | Before `MGMT-GAP-007` final production closeout. | Capture BFF deploy commit through an authorized deploy record, release run, or a public manifest endpoint. |

## Parent Gate Status

`MGMT-GAP-010` can be marked implementation-complete only with this residual
blocker attached. It should not be reviewer-approved as production-green until
the fresh hosted load gate returns `result.pass:true`.
