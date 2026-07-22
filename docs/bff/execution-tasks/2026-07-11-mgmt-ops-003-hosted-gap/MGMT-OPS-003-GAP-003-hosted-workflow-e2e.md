# MGMT-OPS-003-GAP-003 - Hosted Portfolio Workflow E2E

Owner: Codex

Reviewer: Copilot

Repositories: `ajoe734/pantheon`, `ajoe734/execute-plans`

## Dependencies

- `MGMT-OPS-003-GAP-001`
- `MGMT-OPS-003-GAP-002`

## Goal

Prove the real hosted workflow from Portfolio Book through diagnostics,
attribution, Persona Fleet, and Human Review using the exact deployed commits.

## Required Work

- Add or extend Playwright coverage for desktop and mobile widths.
- Exercise every required filter and context-preserving link.
- Verify incident selection reaches Human Review with holding, persona, runtime,
  pool, risk state, and source issue context.
- Verify Performance Attribution cannot turn a degraded Portfolio Book row into
  formal attribution.
- Capture browser console errors, failed network requests, API response
  summaries, screenshots, and deployed frontend/backend commit identities.

## Acceptance

- Hosted dev proves the complete path:
  `Portfolio Book -> incident -> Persona Fleet/Performance Attribution -> Human Review`.
- Desktop and mobile runs have no blank route, lazy-chunk error, overlapping
  controls, clipped labels, unexpected fallback data, console exception, or
  failed required request.
- Paper, canary, live, and unknown stage rendering is exercised with controlled
  fixtures or auditable dev records.
- Expected UI counts and labels are asserted against the same captured BFF
  responses, not against independent mocks.
- Evidence names frontend main SHA, Pantheon dev SHA, deployment runs, test
  commands, and timestamps.
- Reviewer reruns at least one normal and one degraded scenario and completes
  `REVIEWER_CHECKLIST.md` before approval.

## Artifacts

- `execute-plans:e2e`
- `execute-plans:hosted-dev-evidence`
- `docs/deployment/evidence/mgmt-ops-003-gap`
