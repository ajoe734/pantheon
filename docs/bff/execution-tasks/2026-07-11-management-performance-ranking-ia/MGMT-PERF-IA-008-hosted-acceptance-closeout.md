# MGMT-PERF-IA-008 - Hosted Acceptance And Closeout

Owner: Codex2

Reviewer: Human/Ops

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
