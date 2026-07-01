# MGMT-LOAD-006 - Management Load Release Gate

Owner: Gemini2
Reviewer: Codex
Parent: `MGMT-GAP-010`
Depends on: `MGMT-LOAD-001`, `MGMT-LOAD-002`, `MGMT-LOAD-003`, `MGMT-LOAD-004`, `MGMT-LOAD-005`

## Problem

Without a release gate, the management console can regress back to a large
initial bundle, early shell fanout, duplicate jobs reads, or `networkidle`-based
false readiness.

## Scope

- Add a route-load budget file for management pages.
- Fail the gate when initial management JS, Evidence route chunk, first-row
  timing, non-primary startup request count, duplicate startup request count, or
  BFF fanout latency exceeds budget.
- Emit JSON and Markdown artifacts with FE commit, BFF host, route timings,
  request waterfall, bundle sizes, and BFF fanout timings.
- Wire the gate into the existing release/smoke aggregation path used by
  management production acceptance.

## Acceptance

- CI or release smoke fails on primary JS budget breach, duplicate startup
  `/bff/jobs`, excessive non-primary startup requests, `networkidle`-only
  readiness, or BFF fanout latency regression.
- Artifacts are archived and linked from the task closeout.
- Existing management acceptance harness can consume the new load evidence.
- `MGMT-GAP-006` is updated or handed off with the exact artifact paths it must
  require before final production acceptance.

## Closeout Evidence

- Pantheon gate script: `scripts/aggregate-release-gate.mjs` (dependency
  pass-eligibility, bundle budget, route-timing/readiness, startup-request
  waterfall classification with duplicate-`/bff/jobs` detection, BFF fanout
  latency; JSON+Markdown output; non-zero exit on fail).
- Pantheon test: `scripts/test_aggregate_release_gate.py` (8 cases: pass on
  in-budget evidence, fail on duplicate jobs, fail on excess non-primary
  requests, fail on `networkidle` readiness, fail on bundle breach, fail on
  fanout regression, fail-closed on a non-terminal dependency, fail-closed
  (not silently green) on missing evidence). `python3 -m pytest
  scripts/test_aggregate_release_gate.py -q` — 8 passed.
- Frontend evidence: `ajoe734/execute-plans` PR
  [#138](https://github.com/ajoe734/execute-plans/pull/138) adds
  `scripts/bundle-budget-check.mjs`, wires `probe:bundle-budget` into
  `pantheon-integration-gate.yml` right after `Build`, and records current
  bundle evidence in `docs/testing/mgmt-load-006-release-load-gate.md` (not
  yet merged at the time of this closeout — see Residual Risk below).
- Real gate run against currently available evidence (MGMT-LOAD-001's
  hosted route-timing/request-waterfall archive, MGMT-LOAD-001's hosted and
  MGMT-LOAD-005's local BFF-fanout archive, and a fresh `npm run build &&
  npm run probe:bundle-budget` from `execute-plans` `dev` tip
  `5b7d6b724f91`):
  - `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.json`
  - `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.md`
  - `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-route-timing-2026-07-01.json`
  - `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-request-waterfall-2026-07-01.json`
  - `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-bff-fanout-2026-07-01.json`
  - `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-bundle-2026-07-01.json`
  - Result: `overall: fail`, `pass: false`. All 5 dependency tasks and the
    bundle-budget gate pass. The route-timing, startup-request, and BFF
    fanout gates fail because the only archived route-timing/waterfall/fanout
    evidence is the MGMT-LOAD-001 **pre-fix baseline** (predates the
    MGMT-LOAD-002/003/005 shell-summary and read-isolation fixes): it shows a
    real duplicate startup `/bff/jobs` request, 5 non-primary BFF requests
    before first row, and fanout p95 values captured before the fixes. This
    matches the fail-closed runbook in
    `support/sidecars/MGMT-LOAD-006/MGMT-LOAD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md`:
    the gate is real and working, but a **true green** result requires a
    fresh hosted route-load + BFF-fanout probe run against the merged
    dev FE/BFF pair (`npm run probe:route-load && npm run probe:bff:fanout`
    from `execute-plans`, then re-run this gate against the fresh output).

## Residual Risk

- `execute-plans` PR #138 (bundle-budget evidence + CI wiring) is open, not
  yet merged. Until it merges, `frontend-checkout:scripts` evidence for this
  task lives only on the task branch; `probe:bundle-budget` is not yet part
  of every `dev` PR's automated evidence.
- No fresh hosted route-load/BFF-fanout probe was run for this closeout: the
  hosted dev BFF requires a bearer token this worker does not have standing
  authorization to source from docs/secrets, so a post-merge hosted re-run is
  left as the explicit next step (either for `MGMT-LOAD-007` or a
  human/operator with token access) rather than attempting to acquire one.
- Owner: Claude. Reviewer: Codex. Handoff target: `MGMT-LOAD-007` and
  `MGMT-GAP-006` should require the exact `release-load-gate-*`/`release-*`
  artifact paths above (once regenerated from a fresh hosted run) before
  final production acceptance, per this doc's original acceptance criteria.
