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

## 2026-07-12 Independent Rerun

Dependency readiness was checked against the canonical task archive, not the
stale active-task list. `MGMT-OPS-003-GAP-001`, `-002`, and `-003` each have
`terminal_outcome: completed` in `ai-task-archive/tasks/`.

The hosted deployment identified frontend commit
`a74e58696c900112557b0c748c3f8c69629da106` in strict live mode. The reviewer
checked out that exact commit in an isolated clone and ran:

```bash
PANTHEON_HOSTED_E2E=1 \
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
VITE_BFF_MODE=live VITE_BFF_FALLBACK=strict \
npx playwright test e2e/21-portfolio-workflow-hosted.spec.ts --project=chromium
```

Result: desktop passed; mobile failed (1 passed, 1 failed). On the mobile
Human Inbox navigation the hosted UI rendered
`strict: Failed to fetch · seed fallback blocked`. The fail-closed assertion
correctly rejected the visible fallback state. Current authenticated Portfolio
Book, holdings, and performance-attribution responses and both browser
screenshots are captured under
`docs/deployment/evidence/mgmt-ops-003-gap/gap-004/20260712T000000Z/`.

## Verdict

`REQUEST_CHANGES`

The hosted-browser acceptance rows cannot pass while the mobile governed Human
Review route enters a strict fetch failure/fallback state. Repair the mobile
request failure, redeploy, and rerun both viewports against the newly reported
deployment SHA. This verdict does not reopen dependency completion; it is a
new hosted regression found by the independent closeout.
