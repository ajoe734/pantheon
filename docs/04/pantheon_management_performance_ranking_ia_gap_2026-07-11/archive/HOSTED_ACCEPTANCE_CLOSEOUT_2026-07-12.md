# Hosted Acceptance Closeout - 2026-07-12

Task: `MGMT-PERF-IA-008`

Owner: Codex2

Reviewer: Claude

## Dependency And Merge Ledger

The canonical task archive records `MGMT-PERF-IA-001` through
`MGMT-PERF-IA-007` as `done` with terminal outcome `completed`.

| Task | Delivery evidence |
|---|---|
| 001 | execute-plans PR #250, merge `7d1f011074a72e36e0da24e658e0b7b75d4317de`; Pantheon PR #3251 |
| 002 | Pantheon PR #3233, merge `cec3627bbaa6b565c9d27211783d570375671dca` |
| 003 | execute-plans PR #261, merge `cdeac3aabaa62a8f253cced4283aa826191040dc`; Pantheon PR #3407, merge `38dbff685c3a6f36e7dde833c387530fdf5209c2` |
| 004 | execute-plans PRs #259/#262, merge `1de7e2f5b40c74f5fbe91c5c48b209d0cb2d6990`; Pantheon PR #3255, merge `2acb121cd1a6f925ff332b298c3994c997737c5c` |
| 005 | execute-plans PR #260, merge `b0c1a62b3e347c56a2f826cf2f0dec73690e4ff4`; Pantheon PR #3403, merge `e8bce05c76595b31ae5ee6681b90b9ca68168aeb` |
| 006 | execute-plans PR #268; Pantheon delivery merge `5ff81e46bde0af7df586532d0cc9bee4d8dc97b9` |
| 007 | execute-plans PR #270, merge `a37a6ea32729f7ff6a6a7b6ea26eb8e9d4c37401`; Pantheon PRs #3413/#3415, final merge `8f9b8442b0b3670a54d2eb46fc4a203b16bf76b5` |
| 008 frontend probe | execute-plans PR #271, merge `e4217ee6c49c40ef66284acec491b1b375971d0f` |

## Hosted Deployment

Probe time: 2026-07-12 UTC.

- Frontend: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- `/deployment.json`: app `execute-plans`, environment `pantheon-dev-fe`,
  source branch `dev`, deployed at `20260712T130220Z`, commit/source ref
  `e4217ee6c49c40ef66284acec491b1b375971d0f`
- Build mode: `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`,
  `VITE_BFF_REAL_WRITES=false`
- `GET /management/performance`: HTTP 200
- BFF `GET /health`: HTTP 200

The deployment commit is the merge commit of execute-plans PR #271, so the
hosted bundle contains the final Persona Fleet-to-quarterly Rankings Center
probe correction.

## Desktop, Mobile, And Legacy Route Evidence

The canonical manifest acceptance spec
`e2e/26-mgmt-perf-ia-canonical-manifest.spec.ts` covers desktop 1440x900 and
mobile 390x844 layouts for Performance, Rankings, and Governance Decisions.
It also checks sidebar uniqueness, removal of the duplicate operations nav,
query allow-list preservation, redirect termination, and these compatibility
families:

- Portfolio Book, Performance Attribution, and Capital to Performance Center;
- Persona League and Quarterly Ranking to Rankings Center;
- Capital Pools, Rebalances, and Ranking Formulas to Governance Decisions;
- legacy Promotion Allocation tabs to Rankings or Governance Decisions;
- plural detail aliases to their canonical singular detail routes.

Task 001 recorded 8/8 Playwright passes across desktop and mobile. Task 003's
post-merge run 29188935347 passed task-owned specs 26 and 27 on chromium and
mobile-chromium. Task 007 independently recorded 5/5 focused route crawl
passes. PR #271 updates the remaining Persona Fleet quarterly deep-link probe
to the canonical Rankings Center URL while retaining persona context.

## Operator Loop And Safety Disposition

The merged artifacts establish the navigational and evidence chain:

```text
Persona Fleet -> Performance Center -> Rankings Center
              -> Governance Decisions -> Human Review
              -> safely non-applied outcome
```

Performance and Rankings are read/evidence authorities. Governance Decisions
references immutable ranking evidence and routes mutations through Human
Review; it does not provide a ranking table that can directly apply a change.
The hosted build disables real writes. Consequently, the auditable acceptance
outcome for this dev deployment is **safely non-applied**, not an applied
capital receipt. This is the precise Human/Ops boundary: an authorized
write-enabled environment and operator decision are required before any real
apply receipt can exist.

## Residual Risks And Ownership

- PR #270's post-merge integration run failed in the separately tracked Persona
  Fleet focused-pagination probe; the task-owned route tests passed, and the
  remaining issue is owned by `MGMT-OPS-003-GAP-001-FOCUS-PAGINATION-FIX` and
  `MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX`.
- A real apply receipt is intentionally absent because hosted dev has real
  writes disabled. Human/Ops owns any future authorized apply exercise.
- Compatibility redirect telemetry emits
  `pantheon:management-legacy-redirect`; `management-frontend` owns the expiry
  review scheduled for 2026-10-01.
- No claim is made here that fixture/degraded acceptance data is formal live
  attribution. Source-confidence behavior remains governed by task 002.

## Reproduction Commands

```text
curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
curl -fsS -o /dev/null -w '%{http_code}' https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/management/performance
curl -fsS -o /dev/null -w '%{http_code}' https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health
gh pr view 271 -R ajoe734/execute-plans --json state,mergedAt,mergeCommit,statusCheckRollup
```
