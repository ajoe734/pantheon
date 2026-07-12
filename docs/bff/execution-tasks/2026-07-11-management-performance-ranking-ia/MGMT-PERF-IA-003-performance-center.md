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

## Closeout Evidence (2026-07-12)

- Frontend PR `ajoe734/execute-plans#261` merged to `dev` (merge commit
  `cdeac3aabaa62a8f253cced4283aa826191040dc`) after human approval per
  execute-plans self-merge governance.
- Deployed on `pantheon-lupin-dev-fe` at `2026-07-12T10:24:47Z`
  (`deployment.json` commit matches the merge commit).
- Task-owned Playwright specs `e2e/26-mgmt-perf-ia-canonical-manifest.spec.ts`
  and `e2e/27-mgmt-perf-ia-003-performance-center.spec.ts` passed on both
  chromium and mobile-chromium in the post-merge integration-gate run
  ([29188935347](https://github.com/ajoe734/execute-plans/actions/runs/29188935347)).
- Reviewer verdict: `docs/reviews/2026-07-12-mgmt-perf-ia-003-antigravity-review.md`
  (PASS).
- Residual note: that same post-merge integration-gate run reports overall
  `FAIL` because `e2e/25-persona-fleet-live-linked-pages.spec.ts` (chromium
  only; passed on mobile-chromium) and its Gate 6/7 rollup failed. That spec
  covers Persona Fleet focused pagination, not Performance Center, and is
  already tracked separately under the blocked
  `MGMT-OPS-003-GAP-001-FOCUS-PAGINATION-FIX` /
  `MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX` tasks. It does not affect this
  task's acceptance criteria.
