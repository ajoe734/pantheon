# MGMT-OPS-003-GAP-004 Hosted Rerun Evidence

Recorded: 2026-07-12 UTC

Verdict: `REQUEST_CHANGES`

- Hosted frontend: `a74e58696c900112557b0c748c3f8c69629da106`
- Build mode: live / strict / real writes disabled
- Desktop workflow: passed
- Mobile workflow: failed at Human Inbox because the page displayed
  `strict: Failed to fetch · seed fallback blocked`
- Playwright result: 1 passed, 1 failed

API captures use the reviewer/operator authenticated probe identity
`op-fe-gate` and include Portfolio Book core, holdings, and performance
attribution. Browser evidence includes the successful desktop final route and
the failed mobile state. No mock-only evidence was used for the verdict.

The browser failure is sufficient to fail the matrix rows for governed
workflow continuity, strict-live rendering, failed required requests, and
mobile hosted acceptance. A new deployment and full two-viewport rerun are
required before approval.
