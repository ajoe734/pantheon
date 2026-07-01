# MGMT-GAP-010-SIDECAR-BFF-HANDOFF

Task: `MGMT-GAP-010-SIDECAR-BFF-HANDOFF`  
Parent task: `MGMT-GAP-010`  
Owner: `Codex2`  
Reviewer handoff target: `Claude`  
Helper kind: `bff_handoff_packet`  
Date: 2026-07-01

## Scope Boundary

This is a sidecar support packet only. It does not define canonical
architecture, update L1 truth, or change runtime implementation. Parent owner
and reviewer can use it to absorb the current BFF/frontend handoff into the
main `MGMT-GAP-010` closeout path.

Do not treat this file as a replacement for:

- `docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md`
- reviewer-approved `MGMT-LOAD-*` closeout artifacts

## Inputs Read

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/mgmt_gap_010_sidecar_bff_handoff.md`
- `.orchestrator/skills/worker-anchor-commit.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `ai-status.json`
- `docs/bff/execution-tasks/2026-06-30-management-console-production-gap/MGMT-GAP-010-management-load-gate.md`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-001-baseline-route-probes.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-002-bff-shell-summary.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-002-review.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-004-management-route-code-split.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-005-bff-read-concurrency.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-006-release-load-gate.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-007-load-closeout.md`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/MGMT-LOAD-001-closeout-2026-07-01.md`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/bff-fanout-baseline-2026-07-01.md`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/bff-fanout-local-before-after-2026-07-01.md`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/mgmt-load-004-route-load-hosted-2026-07-01.md`
- targeted BFF source references in `services/control-plane/bff/main.py`

I intentionally did not read `current-work.md` or the full
`ai-activity-log.jsonl`.

## Handoff Summary

The load gap is not primarily an Evidence data-size problem. The operator path
to `/management/evidence` paid the combined cost of broad frontend route graph
startup, shell fanout, duplicate jobs reads, and BFF synchronous read
aggregation under concurrency.

The current handoff shape is:

1. BFF provides a cheap shell summary route for first-mount chrome.
2. Frontend shell consumes that summary instead of fetching full lists for
   badge counts.
3. BFF management reads are isolated from the event loop and return explicit
   degraded envelopes on timeout.
4. Frontend route readiness probes use content milestones and primary API
   completion, never `networkidle`.
5. Parent closeout must prove the composed deployed behavior, not only the
   individual implementation slices.

## BFF Query Gap Packet

### Before-state fanout

The baseline route-load evidence showed `/management/evidence` starting these
bounded BFF reads around first content:

- primary read: `GET /bff/management/evidence`
- shell/session reads: `GET /bff/me`, `GET /health`
- full-list badge reads: `GET /bff/approvals`, `GET /bff/alerts`,
  `GET /bff/jobs`
- duplicate jobs read: another `GET /bff/jobs`
- realtime stream: `GET /bff/events/stream` as long-lived SSE

`MGMT-LOAD-001` archived the before-state p95 fanout numbers:

| Route | Baseline p95 |
|---|---:|
| `/health` | 1328 ms |
| `/bff/management/evidence` | 1423 ms |
| `/bff/alerts` | 1513 ms |
| `/bff/approvals` | 1537 ms |
| `/bff/jobs` | 1538 ms |

### BFF implementation now available in this worktree

The BFF handoff surface now includes these support points:

- `GET /bff/management/shell-summary`
  - returns `data.counts.pending_approvals`, `data.counts.open_alerts`,
    `data.counts.running_jobs`
  - returns redacted `data.session`
  - returns `data.transport`
  - reports freshness and degraded state through `meta.surfaces`
  - avoids returning full approval, alert, or job lists
- cheap count helpers:
  - `_shell_summary_pending_approvals_count`
  - `_shell_summary_open_alerts_count`
  - `_shell_summary_running_jobs_count`
  - `_SHELL_SUMMARY_COUNT_CACHE` guarded by `_SHELL_SUMMARY_COUNT_CACHE_LOCK`
- one canonical `@app.get("/bff/jobs")` route remains
- management read isolation:
  - `_run_management_read(...)` offloads synchronous read-store aggregation
    through `asyncio.to_thread`
  - bounded wait uses `asyncio.wait(...)` and
    `PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS`
  - timeout responses use explicit degraded `meta.surfaces` instead of
    hanging unrelated routes
  - `/health` remains independent from management read aggregation

Primary BFF source touchpoints:

- `services/control-plane/bff/main.py`
  - `_run_management_read`
  - `_management_read_timeout_surface`
  - `bff_management_shell_summary`
  - `bff_management_evidence`
  - `bff_list_alerts`
  - `bff_list_jobs`
- tests:
  - `services/control-plane/bff/test_mgmt_load_002_shell_summary.py`
  - `services/control-plane/bff/test_mgmt_load_005_read_concurrency.py`

### BFF absorb checklist for parent closeout

Before `MGMT-GAP-010` closes, confirm all of the following in the deployed dev
BFF, not only local code:

- OpenAPI exposes `GET /bff/management/shell-summary`.
- Source and route registry still have one canonical `GET /bff/jobs`.
- `GET /bff/management/shell-summary` does not return full approvals, alerts,
  or jobs list payloads.
- Shell-summary count surfaces include freshness/degraded metadata.
- `GET /health` p95 stays within the parent budget while shell summary and
  Evidence reads are concurrently requested.
- Evidence timeout/degraded paths are explicit and do not block health.
- Hosted fanout artifacts name the BFF host, deploy/commit evidence, token
  shape without secret value, probe timestamp, and p95 numbers.

## Operator Journey Packet

The operator-critical journey for this slice is direct navigation to
`/management/evidence`.

Expected ready path:

1. Browser loads the FE document.
2. Minimal app shell attaches.
3. Evidence route chunk loads.
4. Evidence route heading becomes visible.
5. `GET /bff/management/evidence` completes.
6. First Evidence row or honest empty state becomes visible.
7. Non-primary shell chrome may hydrate after primary content, from
   shell-summary, cache, drawer-open, or idle callback.
8. SSE may connect after primary content or remain excluded from route-ready
   measurement.

The route probe must record, at minimum:

- `domcontentloaded`
- shell attached
- route heading visible
- primary Evidence API complete
- first row or empty state visible
- all requests before first row
- duplicate startup requests
- whether `networkidle` was used

The `MGMT-LOAD-004` hosted five-sample result already showed the route-ready
path under budget after frontend route splitting:

| Metric | Result | Budget |
|---|---:|---:|
| first row/empty state p75 | 931 ms | <= 1500 ms |
| first row/empty state p95 | 1203 ms | <= 2500 ms |
| primary Evidence API p75 | 837 ms | not specified |
| primary Evidence API p95 | 1131 ms | not specified |

Residual caveat: that evidence still reported 70 requests before first row.
`MGMT-LOAD-003`/`MGMT-LOAD-006` must decide whether the final request budget is
enforced against BFF requests only, all browser requests, or a documented
category split. Do not silently compare local dev, hosted production build, and
all-asset request counts as if they are the same metric.

## Frontend Handoff Packet

Frontend work should absorb the BFF contract with these concrete expectations:

- `TopBar` should use `GET /bff/management/shell-summary` for first-mount
  counts, session chrome, and transport state.
- Full approvals, alerts, and jobs list hydration should be deferred until
  after primary route content, drawer open, or a documented idle callback.
- `JobProgressDrawer` should not issue a duplicate `/bff/jobs` request before
  first Evidence row or empty state.
- Notification and heavyweight drawer hydration should not block primary route
  content.
- Route probes must not wait on `networkidle`; `/bff/events/stream` is a
  healthy long-lived realtime stream, not route readiness.
- SSE reconnect/Last-Event-Id behavior still needs separate realtime coverage.
- Management route splitting should preserve direct navigation, redirect
  aliases, and lazy chunk error/degraded states.

Expected FE touchpoints in `execute-plans`:

- `src/App.tsx`
- `src/platform/PlatformShell.tsx`
- `src/platform/components/TopBar.tsx`
- `src/platform/components/JobProgressDrawer.tsx`
- `src/platform/components/NotificationCenter.tsx`
- `src/lib/bff-v1/paths.ts`
- `src/lib/bff-v1/management.ts`
- `scripts/probe-route-load-baseline.mjs`
- `scripts/probe-bff-fanout-concurrency.mjs`
- `e2e/22-management-evidence-load.spec.ts`

Read-only note: the local `/home/lupin/code/execute-plans` checkout inspected
for orientation was on `task/MGMT-GAP-008-detail-honesty-followup`, not the
MGMT-LOAD closeout branch. Treat archived hosted evidence and merged PR SHAs as
the source of truth for this sidecar, not that local checkout.

## Parent Closeout Notes

`ai-status.json` still showed `MGMT-GAP-010` and `MGMT-LOAD-001` through
`MGMT-LOAD-007` as `todo` when this sidecar was prepared, while task docs and
archive evidence already contain several implementation and review records.
Parent closeout should reconcile status truth before marking `MGMT-GAP-010`
complete.

Specific items for `MGMT-LOAD-007` or parent owner review:

- Confirm which `MGMT-LOAD-*` branches/PRs have merged and record merge SHAs.
- Re-run the hosted BFF fanout probe after the BFF changes are deployed to dev.
- Confirm shell-summary hosted p95 under concurrency or archive the exact
  blocker.
- Confirm no duplicate `/bff/jobs` before first route content on hosted FE.
- Confirm final load-gate artifacts are wired into the management acceptance
  harness.
- Hand `MGMT-GAP-006` the exact load artifact paths it must require.

## Not Changing

This sidecar intentionally does not:

- change L1 canonical documents
- change BFF routes or tests
- change frontend code
- change release-gate code
- update parent task status
- claim `MGMT-GAP-010` is done

## Sidecar Validation

Commands run from the Pantheon task worktree:

```text
git diff --check
```

Result: passed.

```text
python3 -m pytest services/control-plane/bff/test_mgmt_load_002_shell_summary.py services/control-plane/bff/test_mgmt_load_005_read_concurrency.py -q
```

Result: `12 passed, 8 warnings in 22.90s`. Warnings were the existing FastAPI
`on_event` deprecation warnings from `services/control-plane/bff/main.py`.

## Reviewer Decision Request

Reviewer should treat this packet as a support handoff for absorption into
`MGMT-GAP-010` / `MGMT-LOAD-007`, not as standalone product acceptance. The
main review question is whether this packet accurately captures the BFF and FE
handoff boundary without broadening canonical truth.
