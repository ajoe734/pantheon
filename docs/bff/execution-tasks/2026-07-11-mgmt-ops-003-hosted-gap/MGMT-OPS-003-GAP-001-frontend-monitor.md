# MGMT-OPS-003-GAP-001 - Frontend Portfolio Monitor Closure

Owner: Codex2

Reviewer: Copilot

Repository: `ajoe734/execute-plans`

Merge target: `main`

## Goal

Make the hosted Portfolio Book faithfully render the live MGMT-OPS-003 BFF
contract without inventing fallback confidence or hiding degraded records.

## Required Work

- Render source coverage counters, row-level incidents, severity, source issues,
  risk state, and Human Review actions.
- Add stage, broker, runtime, source-status, stale-telemetry, and risk-state
  filters with URL round-trip and refresh persistence.
- Render explicit paper-ledger, canary-sleeve, live-capital-pool, and unknown
  capital-scope states. Unknown scope must not inherit a paper/live style.
- Replace aggregate `covered` or `formal attribution` labels with confidence
  derived from the corresponding BFF response.
- Preserve persona, runtime, pool, holding, period, and source context when
  linking to Persona Fleet, Performance Attribution, and Human Review.
- Keep the implementation compatible with the planned Performance Center
  consolidation; do not create another competing page.

## Acceptance

- A fixture containing 14 degraded holdings and 10 missing bindings renders 14
  visible incidents and never renders a formal/covered success state.
- All six required filters affect requests, survive reload, and are covered by
  component and Playwright tests.
- Mixed paper, canary, live, and unknown fixtures have distinct, accessible
  labels and cannot be identified by color alone.
- Empty, partial, degraded, stale, and unavailable states are independently
  tested.
- The execute-plans PR is merged to `main`, deployed to Pantheon dev, and the
  reviewer records the merge SHA, bundle identity, screenshots, console errors,
  and failed network requests.
- Reviewer completes every applicable item in `REVIEWER_CHECKLIST.md`; missing
  hosted evidence requires changes.

## Artifacts

- `execute-plans:src/management/pages/oversight/PortfolioBook.tsx`
- `execute-plans:src/lib/v5/management/portfolio.ts`
- `execute-plans:e2e`
- `execute-plans:hosted-dev-evidence`
