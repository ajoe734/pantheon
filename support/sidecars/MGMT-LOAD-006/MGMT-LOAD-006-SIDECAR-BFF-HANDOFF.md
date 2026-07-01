# MGMT-LOAD-006 Sidecar BFF / Frontend Handoff

Date: 2026-07-01
Owner: Codex2
Reviewer: Claude
Parent task: `MGMT-LOAD-006`
Helper kind: `bff_handoff_packet`
Scope: support-only packet; does not change canonical truth, BFF runtime code,
frontend code, release-gate implementation, or governance registry behavior.

## Purpose

This packet gives the `MGMT-LOAD-006` owner a compact handoff for turning the
management-console load work into a release gate. It summarizes the BFF query
gap, the operator route journey that the gate must measure, the frontend/BFF
artifact contract, and the residual evidence that must not be mistaken for a
closed production gate.

## Evidence Sources Read

| Source | Relevant use |
|---|---|
| `docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md` | canonical load-gap diagnosis and target budgets |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md` | task sequencing and global acceptance |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-001-baseline-route-probes.md` | route-load and BFF fanout probe expectations |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-002-bff-shell-summary.md` | shell-summary endpoint and jobs canonicalization evidence |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-002-review.md` | reviewer-approved hosted timing deferral rationale |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md` | FE shell startup request expectations |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-004-management-route-code-split.md` | route split closeout and hosted timing result |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-005-bff-read-concurrency.md` | BFF read isolation implementation evidence |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-006-release-load-gate.md` | parent release-gate scope |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-007-load-closeout.md` | downstream closeout artifact needs |
| `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/*` | baseline, hosted route-load, and local BFF fanout evidence |

## Current Evidence Snapshot

| Area | Before / current evidence | Gate implication |
|---|---|---|
| Baseline route load | `route-load-baseline-2026-07-01.md`: first row or empty state at 4668 ms; `/bff/jobs` fetched twice; SSE excluded from readiness; `usedNetworkidle=false`. | Gate must fail on `networkidle` readiness, duplicate startup jobs, and excessive pre-content shell reads. |
| Baseline BFF fanout | `bff-fanout-baseline-2026-07-01.md`: `/health` p95 1328 ms and `/bff/management/evidence` p95 1423 ms while fanned out with alerts, approvals, jobs. | Gate must include BFF fanout p95 budgets, not only isolated route timings. |
| Shell summary | `MGMT-LOAD-002`: `GET /bff/management/shell-summary` returns counts/session/transport without full list payloads; local p95 9.916 ms sequential warm; hosted concurrent p95 was deferred by reviewer to the 001/005 probe path. | Gate still needs a hosted or release-smoke measurement for shell-summary p95 <= 200 ms under concurrent load. |
| Jobs route | `MGMT-LOAD-002` review verifies one canonical `@app.get("/bff/jobs")` source/registered route. | Gate should still inspect startup waterfall for duplicate client requests to the canonical route. |
| Route split | `MGMT-LOAD-004` hosted samples show first row p75 931 ms and p95 1203 ms on FE commit `255e60414e0ca36e29c1b2e39f0543d23d2eea80`. | Gate can use these as post-split timing precedent, but must not overfit to one route sample run. |
| BFF read isolation | `bff-fanout-local-before-after-2026-07-01.md`: synthetic slow-read reproduction moved `/health` p95 from 1629 ms to 189 ms and Evidence p95 to 425 ms. | Gate still needs a post-merge hosted fanout rerun against the deployed dev BFF. |

## BFF Query / Fanout Gate

`MGMT-LOAD-006` should treat the BFF load gate as a route family, not a single
endpoint check. The minimum release-smoke fanout set is:

| Request | Gate role | Target / assertion |
|---|---|---|
| `GET /health` | Proves health is not blocked by management read aggregation. | p95 <= 200 ms during concurrent shell-summary/Evidence fanout. |
| `GET /bff/management/evidence` | Primary Evidence route data. | p95 <= 750 ms during shell fanout; isolated p95 <= 300 ms when measured separately. |
| `GET /bff/management/shell-summary` | Cheap shell badge/session/transport snapshot. | p95 <= 200 ms under 10 concurrent requests; response contains no full approvals, alerts, or jobs lists. |
| `GET /bff/alerts` | Historical expensive shell read; still useful as a fanout stressor. | Must return or explicitly degrade within the read timeout envelope; should not delay `/health`. |
| `GET /bff/approvals` | Historical shell read; fanout stressor. | Must return or explicitly degrade within the read timeout envelope; should not delay `/health`. |
| `GET /bff/jobs` | Canonical jobs list route and duplicate-request detector. | Exactly one BFF route implementation remains; first-route client waterfall has zero duplicate `/bff/jobs` requests before primary content. |
| `GET /bff/events/stream` | Realtime SSE stream. | Record as long-lived stream; exclude from route-ready and bounded-request p95 calculations. |

Release-gate failure should be based on classified fields, not only a raw
request total. The hosted `MGMT-LOAD-004` route-load summary reports
`Requests before first row = 70`; that count is useful diagnostic material, but
it is not equivalent to the spec's "non-primary BFF requests before first row"
budget. The gate should emit both:

- `non_primary_bff_before_first_row`: BFF/API requests other than the primary
  Evidence API that start before first row/empty state; budget <= 2.
- `duplicate_startup_requests`: repeated bounded BFF route calls before first
  row/empty state; `/bff/jobs` budget = 0 duplicates.
- `total_requests_before_first_row`: diagnostic count including FE document,
  assets, chunks, and BFF/API requests; do not fail solely on this field unless
  a separate asset/chunk budget is defined.

## Operator Journey To Measure

The gate should model the operator's cold route entry, not a synthetic isolated
API call:

1. Operator navigates directly to `/management/evidence` on the Pantheon dev FE
   host with `VITE_BFF_MODE=live` and `VITE_BFF_FALLBACK=strict`.
2. Browser waits for `domcontentloaded`, shell attachment, route heading,
   primary Evidence API completion, and first row or empty state.
3. Shell badge/session state may load via `shell-summary`; full alerts,
   approvals, and jobs lists must not be required before Evidence primary
   content renders.
4. If shell-summary or read-store sources are degraded, the UI should surface
   honest stale/degraded count state rather than fetching full lists early.
5. SSE may connect, but readiness must not wait for `networkidle`; the
   long-lived `/bff/events/stream` request is healthy when open.
6. After primary content, drawers and full list hydration may run, but those
   requests must be visible in the waterfall so regressions are attributable.

## Frontend / Release-Gate Artifact Contract

The `MGMT-LOAD-006` release gate should emit a machine-readable JSON artifact
and a short Markdown summary that downstream `MGMT-LOAD-007` can link without
rerunning interpretation. Minimum fields:

| Field | Required content |
|---|---|
| `environment` | FE base URL, BFF base URL, probe timestamp, route path, primary API path, token shape without secret value. |
| `build` | execute-plans commit, Pantheon/BFF commit or deployment evidence, bundle manifest or asset inventory. |
| `readiness` | `usedNetworkidle=false`, `domContentLoadedMs`, `shellVisibleMs`, `headingVisibleMs`, `primaryApiCompleteMs`, `firstRowOrEmptyVisibleMs`. |
| `routeBudgets` | heading p75/p95, first row or empty-state p75/p95, primary JS gzip, Evidence route chunk gzip, pass/fail per budget. |
| `requestWaterfall` | bounded FE/BFF requests with start, duration, status, method, path, and classification (`primary`, `non_primary_bff`, `asset`, `sse`, `deferred_shell`). |
| `startupGuards` | non-primary BFF request count before first row, duplicate BFF route requests before first row, duplicate `/bff/jobs` boolean/count. |
| `bffFanout` | per-route min/max/p95 for `/health`, Evidence, shell-summary, alerts, approvals, jobs under concurrent fanout. |
| `degradedSurfaces` | explicit route-level degraded metadata, including timeout/degraded envelopes where returned. |
| `artifactPaths` | relative paths to JSON, Markdown, waterfall, route timing, bundle, and BFF fanout outputs. |

Recommended archive naming under the existing load-gap archive directory:

- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.json`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.md`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-route-timing-2026-07-01.json`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-request-waterfall-2026-07-01.json`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-bff-fanout-2026-07-01.json`

## Budget Checklist For MGMT-LOAD-006

| Budget | Pass condition |
|---|---|
| Readiness model | Probe uses content milestones and records `usedNetworkidle=false`. |
| First row / empty state | p75 <= 1.5 s and p95 <= 2.5 s on deployed dev FE. |
| Heading visible | p75 <= 800 ms and p95 <= 1.5 s where the probe emits heading percentiles. |
| Initial management JS | gzip <= 800 KB, or a reviewer-approved exception names the shared vendor blocker. |
| Evidence route chunk | gzip <= 150 KB excluding shared vendor cache, or a documented equivalent budget is approved. |
| Startup BFF fanout | non-primary BFF requests before first row <= 2. |
| Duplicate jobs | duplicate `/bff/jobs` requests before first row = 0. |
| BFF health fanout | `/health` p95 <= 200 ms while shell-summary/Evidence reads are concurrent. |
| Evidence fanout | `/bff/management/evidence` p95 <= 750 ms during shell fanout. |
| Shell summary fanout | `/bff/management/shell-summary` p95 <= 200 ms under 10 concurrent requests. |
| Degraded semantics | Timeout/degraded paths return explicit degraded metadata and do not hang unrelated routes. |

## Residual Items To Keep Visible

| Item | Owner to absorb | Why it matters |
|---|---|---|
| Hosted shell-summary 10-concurrent p95 | `MGMT-LOAD-006` gate or `MGMT-LOAD-007` closeout | `MGMT-LOAD-002` reviewer accepted deferral, not omission. |
| Hosted post-merge BFF fanout rerun | `MGMT-LOAD-006` gate or `MGMT-LOAD-007` closeout | `MGMT-LOAD-005` local reproduction is strong but not hosted deployment proof. |
| Request classification after route split | `MGMT-LOAD-006` gate | `MGMT-LOAD-004` reports 70 requests before first row; gate must distinguish asset/chunk requests from early non-primary BFF reads. |
| Final artifact paths for `MGMT-GAP-006` | `MGMT-LOAD-007` | Parent production acceptance needs exact JSON/Markdown paths, not a prose claim that probes passed. |

## Reviewer Notes

This packet is ready for review if it remains support-only and the only repo
changes are this file plus the generated task brief for this sidecar. It should
compose with the `MGMT-LOAD-006` owner by supplying gate inputs and residual
evidence requirements; it should not be treated as approval of the release gate
implementation itself.
