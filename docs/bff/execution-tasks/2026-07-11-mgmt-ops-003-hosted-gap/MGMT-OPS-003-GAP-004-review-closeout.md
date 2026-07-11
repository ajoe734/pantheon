# MGMT-OPS-003-GAP-004 - Independent Difference Closeout

Owner: Codex2

Reviewer: Codex

Repositories: `ajoe734/pantheon`, `ajoe734/execute-plans`

## Dependencies

- `MGMT-OPS-003-GAP-001`
- `MGMT-OPS-003-GAP-002`
- `MGMT-OPS-003-GAP-003`

## Goal

Independently prove that every plan-to-live difference is closed. This is an
acceptance task, not a documentation-only rubber stamp.

## Required Work

- Rebuild the difference matrix from current hosted responses and screenshots.
- Verify repository PRs, merge ancestry, deployment runs, served bundle
  identity, and BFF OpenAPI contract all refer to the delivered commits.
- Compare UI counts, labels, confidence, stage, incidents, filters, and links
  with captured authenticated BFF payloads.
- Record residual risks with explicit owner and follow-up task. A residual risk
  that violates an MGMT-OPS-003 acceptance criterion blocks closure.

## Acceptance

- Every row in `MGMT_OPS_003_HOSTED_GAP.md` has a pass verdict and direct
  evidence reference.
- Reviewer personally reruns hosted desktop and mobile probes plus authenticated
  Portfolio Book and attribution API probes.
- Reviewer records console errors and failed network requests, including an
  explicit zero count when clean.
- Reviewer rejects stale screenshots, mock-only tests, mismatched deployed
  SHAs, hidden incidents, missing filters, and confidence labels stronger than
  source truth.
- `MGMT-PERF-IA-003` receives a handoff that preserves the completed behavior
  during Performance Center consolidation.
- Final status may move to `review_approved` only after the signed checklist is
  complete; the owner may move it to `done` only after both repository changes
  are merged and hosted evidence passes.

## Artifacts

- `docs/04/pantheon_mgmt_ops_003_hosted_gap_2026-07-11/archive`
- `docs/deployment/evidence/mgmt-ops-003-gap`
- `execute-plans:hosted-dev-evidence`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/REVIEWER_CHECKLIST.md`
