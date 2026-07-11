# MGMT-PERF-IA-003 - Performance Center Consolidation

Owner: Claude

Reviewer: Antigravity

Wave: 1

Repository: `ajoe734/execute-plans`

Dependencies:

- `MGMT-PERF-IA-001`
- `MGMT-PERF-IA-002`

## Goal

Build the canonical Performance Center and consolidate overview, attribution,
exposure, and holdings investigation into one coherent operator surface.

## Required Work

- Implement `/management/performance` tabs: Overview, Attribution, Exposure &
  Holdings.
- Migrate Portfolio Book and Performance Attribution behavior without losing
  source rows, risk diagnostics, or empty/degraded states.
- Share filters across tabs and preserve them through refresh and deep links.
- Show source confidence, freshness, coverage, unmatched bindings, and source
  timestamps at the point of decision.
- Label Persona Fleet summary fallback explicitly and never count it as formal
  attribution.
- Replace operator-facing `nan`, `NaN`, `undefined`, and false zeroes with
  explicit missing/unavailable states.
- Add responsive table, adapter, route, and degraded-state tests.

## Acceptance

- One center answers results, attribution, exposure, and holdings questions.
- A focus persona with no formal attribution is visibly fallback/degraded.
- Paper, canary, and live stages are distinguishable.
- Legacy capital and attribution URLs land on the correct tab with filters.
- Frontend PR is merged and hosted dev evidence is recorded.

## Artifacts

- `execute-plans:src/management/pages`
- `execute-plans:src/management/components`
- `execute-plans:src/lib`
- `execute-plans:e2e`
