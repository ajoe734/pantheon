# MGMT-PERF-IA-008 - Hosted Acceptance And Closeout

Owner: Codex2

Reviewer: Claude

Wave: 3

Repositories: `ajoe734/pantheon`, `ajoe734/execute-plans`

Dependencies:

- `MGMT-PERF-IA-001`
- `MGMT-PERF-IA-002`
- `MGMT-PERF-IA-003`
- `MGMT-PERF-IA-004`
- `MGMT-PERF-IA-005`
- `MGMT-PERF-IA-006`
- `MGMT-PERF-IA-007`

## Goal

Prove on hosted dev that the new information architecture supports the complete
monitoring, analysis, ranking, governance, review, and receipt workflow.

## Required Work

- Verify every child task has a merged PR or an explicit, evidence-backed
  supersession.
- Record Pantheon and execute-plans merge SHAs and deployed revisions.
- Run hosted desktop and mobile route crawl for canonical and legacy URLs.
- Capture evidence for formal, partial/fallback, degraded, and unavailable data
  states without visible `nan`.
- Demonstrate filter and context preservation from Fleet through all centers.
- Demonstrate a recommendation to Human Review and an auditable applied or
  safely non-applied result.
- Confirm ranking tables exist only in Rankings Center.
- Archive screenshots, API evidence, test output, residual risks, and redirect
  expiry owners under the source archive.

## Acceptance

- The full operator loop works on hosted dev.
- Sidebar, command palette, breadcrumbs, cockpit, and deep links agree.
- No legacy URL is a dead end or redirect loop.
- No ranking or analysis surface bypasses Human Review.
- Human/Ops records an approval or precise blocker.
- Closeout is merged to Pantheon `dev` with final evidence references.

## Artifacts

- `docs/04/pantheon_management_performance_ranking_ia_gap_2026-07-11/archive`
- Pantheon and execute-plans PR/merge/deployment evidence
- hosted screenshots and route/API smoke output

## Closeout Evidence (2026-07-12)

Detailed hosted evidence and residual-risk disposition are archived in
`docs/04/pantheon_management_performance_ranking_ia_gap_2026-07-11/archive/HOSTED_ACCEPTANCE_CLOSEOUT_2026-07-12.md`.

All dependencies `MGMT-PERF-IA-001` through `MGMT-PERF-IA-007` are terminal
`done` in the canonical task archive. The deployed frontend is execute-plans
`dev` commit `e4217ee6c49c40ef66284acec491b1b375971d0f` (PR #271), as reported by
the hosted `/deployment.json` at `2026-07-12T13:02:20Z`. The frontend and BFF
health probes both returned HTTP 200.

The deployment is deliberately configured with `VITE_BFF_REAL_WRITES=false`.
The accepted hosted outcome is therefore safely non-applied: navigation,
analysis, ranking, governance, and Human Review routing may be exercised, but
this environment cannot produce a real capital-affecting apply receipt. No
such receipt is claimed by this closeout.
