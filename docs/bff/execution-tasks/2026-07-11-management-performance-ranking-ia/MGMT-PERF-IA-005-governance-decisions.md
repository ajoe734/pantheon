# MGMT-PERF-IA-005 - Governance Decisions Consolidation

Owner: Antigravity

Reviewer: Codex2

Wave: 1

Repository: `ajoe734/execute-plans`

Dependencies:

- `MGMT-PERF-IA-001`
- `MGMT-PERF-IA-002`

## Goal

Refactor Promotion Allocation into a governance workspace that consumes
ranking evidence without duplicating ranking pages.

## Required Work

- Implement `/management/governance-decisions` tabs: Recommendations Queue,
  Capital Allocation, and Ranking Policy.
- Remove embedded `real-ranking` and `paper-candidates` tables; replace them
  with immutable snapshot references and links to Rankings Center.
- Show recommendation, review, approval, rejection, expiry, blocked, applied,
  and superseded states.
- Show proposal impact, limits, preconditions, reviewer, timestamps, and apply
  receipt for capital/rebalance/access actions.
- Provide honest unavailable states for empty rebalance and policy/formula
  collections.
- Gate all mutating actions through Human Review and governed apply.
- Add tests proving ranking rows cannot directly mutate live state.

## Acceptance

- Governance Decisions contains no competing full ranking table.
- Recommendation and applied action are visibly separate.
- Capital/access changes require review and receipt evidence.
- Legacy Promotion Allocation links land on the relevant governance tab.
- Frontend PR is merged and hosted dev evidence is recorded.

## Artifacts

- `execute-plans:src/management/pages/oversight`
- `execute-plans:src/management/components`
- `execute-plans:src/lib`
- `execute-plans:e2e`
