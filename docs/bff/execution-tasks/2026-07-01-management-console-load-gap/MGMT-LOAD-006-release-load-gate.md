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
