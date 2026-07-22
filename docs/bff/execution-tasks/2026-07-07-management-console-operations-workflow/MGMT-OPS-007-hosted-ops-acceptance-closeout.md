# MGMT-OPS-007 - Hosted Operations Acceptance And Closeout

Owner: Codex2

Reviewer: Human/Ops

Wave: 3

Dependencies:

- `MGMT-OPS-002`
- `MGMT-OPS-003`
- `MGMT-OPS-004`
- `MGMT-OPS-005`
- `MGMT-OPS-006`

Source plan:

- `docs/04/pantheon_management_console_operations_workflow_2026-07-07/MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md`

## Goal

Close the management console operations workflow with merged PRs, validation,
dev publish, hosted smoke evidence, and residual-risk accounting.

## Required Work

- Collect all child task PRs, review approvals, merge commits, and validation
  summaries.
- Publish the dev frontend/BFF if changed by the child tasks.
- Run hosted smoke tests across Portfolio Book, Persona Fleet, Performance
  Attribution, Persona League, Quarterly Ranking, and Human Review.
- Capture the focus-persona path:
  `persona-20260528-04688755 -> performance attribution -> diagnostic or formal
  evidence -> human review state`.
- Verify `nan` is absent from operator-facing metric cells.
- Verify fallback attribution is labeled and not counted as formal attribution.
- Verify ranking pages cannot directly mutate live capital.
- Archive closeout evidence and residual risks.

## Acceptance

- Every child task is done, merged, or explicitly superseded with evidence.
- Hosted smoke proves the full operator loop.
- Residual risks have owners and expiry.
- The final closeout names PRs, merge SHAs, deployment target, validation, and
  hosted evidence links.

## Artifacts

- `docs/04/pantheon_management_console_operations_workflow_2026-07-07/archive`
- `services/control-plane/bff`
- `execute-plans:src`
- `ai-status.json`
