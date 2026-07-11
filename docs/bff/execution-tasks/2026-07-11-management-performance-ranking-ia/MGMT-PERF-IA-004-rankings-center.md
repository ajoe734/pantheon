# MGMT-PERF-IA-004 - Rankings Center Consolidation

Owner: Antigravity

Reviewer: Claude

Wave: 1

Repository: `ajoe734/execute-plans`

Dependencies:

- `MGMT-PERF-IA-001`
- `MGMT-PERF-IA-002`

## Goal

Make Rankings Center the only full authority for rolling Persona League and
quarterly formal evaluation.

## Required Work

- Implement `/management/rankings` tabs: Rolling and Quarterly.
- Consolidate standalone Persona League and Quarterly Ranking behavior.
- Remove ranking-table responsibility from Promotion Allocation through stable
  interfaces consumed by `MGMT-PERF-IA-005`.
- Show period, snapshot id, criteria, eligibility, exclusions, evidence
  coverage, source confidence, rank, score, and trend where supported.
- Link each row to Fleet, Performance Center, and its recommendation/review.
- Allow recommendation creation or inspection, never direct apply.
- Test null metrics, degraded telemetry, field normalization, filtering,
  sorting, pagination, and legacy redirects.

## Acceptance

- Rolling and Quarterly have distinct cadence and purpose.
- No status/readiness row is silently treated as a ranking row.
- Ranking snapshots remain reproducible after recommendations change state.
- Legacy League and Quarterly URLs land on the correct tab.
- Frontend PR is merged and hosted dev evidence is recorded.

## Artifacts

- `execute-plans:src/management/pages`
- `execute-plans:src/management/components`
- `execute-plans:src/lib`
- `execute-plans:e2e`
