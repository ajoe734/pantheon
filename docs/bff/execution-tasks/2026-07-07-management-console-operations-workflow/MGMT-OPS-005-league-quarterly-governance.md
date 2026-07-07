# MGMT-OPS-005 - Persona League And Quarterly Governance Inputs

Owner: Gemini

Reviewer: Codex2

Wave: 2

Dependencies:

- `MGMT-OPS-001`
- `MGMT-OPS-002`

Source plan:

- `docs/04/pantheon_management_console_operations_workflow_2026-07-07/MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md`

## Goal

Reframe Persona League and Quarterly Ranking as ranking and governance inputs
that feed Human Review, rather than independent pages that imply direct
promotion or allocation authority.

## Required Work

- Separate Persona League ranking rows from status/readiness summaries.
- Normalize ranking endpoint responses and avoid treating status rows as ranking
  rows.
- Show criteria, eligibility, exclusion reasons, evidence coverage, rank, score,
  period, and source confidence.
- Make Quarterly Ranking show governance-cycle state: recommendation, submitted
  review, approved review, applied receipt, rejected, blocked, or expired.
- Link each ranking row to Persona Fleet, Performance Attribution, and Human
  Review.
- Add tests for degraded telemetry coverage, null score/performance fields, and
  snake_case/camelCase normalization.

## Acceptance

- League is clearly short-cycle operations ranking.
- Quarterly Ranking is clearly formal governance-cycle ranking.
- Ranking pages cannot imply live capital mutation.
- Recommendation and apply states are separate and auditable.
- Degraded or missing evidence is visible before review submission.

## Artifacts

- `services/control-plane/bff`
- `execute-plans:src/management/pages`
- `execute-plans:src/lib`
- `execute-plans:e2e`
