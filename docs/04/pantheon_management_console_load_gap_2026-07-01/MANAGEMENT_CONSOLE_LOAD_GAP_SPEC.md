# Management Console Load Gap Spec - 2026-07-01

| | |
|---|---|
| **Doc ID** | `MGMT_CONSOLE_LOAD_GAP_2026-07-01` |
| **Version** | 1.0 |
| **Date** | 2026-07-01 |
| **Author** | Codex investigation handoff |
| **Audience** | execute-plans FE owners, Pantheon BFF owners, release-gate owners |
| **Primary symptom** | `/management/evidence` feels slow even when the Evidence dataset contains only two rows |
| **Probe FE** | `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io` |
| **Probe BFF** | `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` |
| **Observed FE deploy** | `execute-plans` deployment JSON reported `commit=c771b02de1c43864c2b7d3df90e4b6796c36549b`, `deployedAt=20260701T013703Z` |
| **Related repos** | `ajoe734/execute-plans`, `ajoe734/pantheon` |

## 0. Executive Summary

The Evidence Explorer page is not slow because the Evidence read model is large.
The page currently loads slowly because a small route pays the full management
console startup cost:

1. The execute-plans app eagerly imports the management route graph. A user
   entering `/management/evidence` downloads and executes the broad console
   bundle before the page-specific Evidence request can start.
2. The shared shell mounts `TopBar`, global drawers, job progress UI, command
   palette hooks, realtime state, and live SSE for every management route.
3. `TopBar` fetches full approvals, alerts, jobs, current-user, and health
   state during first mount. `JobProgressDrawer` separately fetches jobs again.
   These requests are not required for Evidence primary content.
4. The BFF exposes async FastAPI routes that perform synchronous read-store and
   aggregation work in-process. Under concurrent shell startup reads, even
   `/health` and `/bff/management/evidence` can queue behind other work.
5. Release probes that wait for `networkidle` misread the page, because the
   shell opens `/bff/events/stream`, a long-lived SSE connection.

This is a production gap. The fix is not to add a nicer loading skeleton to
Evidence. The fix is to make primary route content cheap, defer or aggregate
global shell work, remove duplicate startup requests, code-split the console,
and harden BFF read concurrency.

## 1. Observed Behavior

### 1.1 Isolated HTTP timings

Direct BFF and FE static responses were fast when measured in isolation:

| Request | Observed total time |
|---|---:|
| FE HTML `/management/evidence` | about 45-165 ms |
| BFF `/bff/management/evidence` with bearer token | about 60-160 ms in the initial isolated probe |
| BFF `/api/v1/knowledge/evidence/{ref_id}` with bearer token | about 35-150 ms |
| FE-host proxy `/bff/management/evidence` with bearer token | about 50-100 ms |

This rules out "two Evidence rows are expensive" as the primary root cause.

### 1.2 Browser route timings

Playwright against the deployed FE saw:

| Milestone | Observed timing |
|---|---:|
| `domcontentloaded` | about 710 ms in one run |
| Evidence title visible | about 1.0 s in one run |
| Evidence row visible | about 1.28 s in one run |
| `networkidle` | not reached within 10 s |

Another request timeline showed a slower first-route path:

| Event | Time since navigation |
|---|---:|
| FE document request started | 27 ms |
| main JS and CSS started | 162 ms |
| main JS finished | 858 ms |
| shell and Evidence BFF fetches started | about 2.32 s |
| `/bff/management/evidence` finished | about 3.62 s |
| `/bff/me`, `/bff/alerts`, `/bff/approvals`, `/health`, `/bff/jobs` finished | about 3.67-3.77 s |

The important fact is that Evidence data did not start immediately after HTML.
It started after the large JS bundle was downloaded, parsed, and the shell
mounted.

### 1.3 Bundle size observation

The deployed route HTML loaded one primary JS asset:

```text
/assets/index-DRcPMir-.js
content-length: 5,544,178 bytes
gzip transfer: about 1,665,115 bytes
```

The local production build in the investigated checkout showed a comparable
primary asset:

```text
dist/assets/index-B1zZzvQE.js  5,539,444 bytes
gzip size about 1,589,981 bytes
dist total about 18 MB
```

That is too much first-route JavaScript for a two-row management page.

### 1.4 BFF concurrency observation

When the same shell startup requests were issued concurrently, endpoints that
were fast in isolation slowed together. A Node probe that concurrently requested
`/bff/me`, `/bff/approvals`, `/bff/alerts`, `/bff/jobs`, `/health`,
`/bff/management/evidence`, and a second `/bff/jobs` observed grouped response
times of about 0.7-1.7 s.

A heavier exploratory concurrent probe reproduced service-wide queueing:
several BFF requests aborted after 5 s, and a subsequent `/health` and
`/bff/management/evidence` took about 21.5 s to return. This should not be
treated as a normal load test result, but it is enough to classify the BFF as
vulnerable to startup read fanout and synchronous event-loop blocking.

## 2. Architecture Inventory

### 2.1 Evidence page data path

The Evidence list itself is narrow:

- `execute-plans/src/management/pages/oversight/_core.tsx`
  - `EvidenceExplorerList` calls `mgmt.evidence.overview`.
- `execute-plans/src/lib/bff-v1/management.ts`
  - `mgmt.evidence.overview` calls `GET /bff/management/evidence`.
  - `mgmt.evidence.detail` calls `GET /api/v1/knowledge/evidence/{ref_id}`.
- `execute-plans/src/lib/bff-v1/paths.ts`
  - `mgmtEvidenceExplorer()` maps to `/bff/management/evidence`.
  - `knowledgeEvidenceRef(id)` maps to `/api/v1/knowledge/evidence/{id}`.
- `pantheon/services/control-plane/bff/main.py`
  - `/bff/management/evidence` delegates to `_build_management_evidence_payload`.

This is the correct product shape for Evidence. The page should be able to show
primary content after one list request.

### 2.2 Eager FE route graph

`execute-plans/src/App.tsx` imports the route components at module top level,
including management pages, registry pages, detail pages, operations pages,
phase2 pages, v5 pages, and oversight pages. The `/management/evidence` route
then renders inside that already-loaded graph.

Impact:

- Evidence cannot start as a small route chunk.
- Heavy modules unrelated to Evidence contribute to parse and execute time.
- The browser starts BFF reads only after broad app startup.

### 2.3 Shared platform shell fanout

`execute-plans/src/platform/PlatformShell.tsx` mounts these for every route:

- `TopBar`
- `LiveStatusBanner`
- `RightDrawer`
- `NotificationCenter`
- `JobProgressDrawer`
- `HandoffDrawer`
- `BulkResultDrawer`
- `RollbackSagaDrawer`
- `KeyboardShortcutsHelp`
- `connectLiveSse()`

Impact:

- A page-specific route inherits global work before the user has interacted
  with those global surfaces.
- SSE starts immediately and keeps the network open.

### 2.4 TopBar startup reads

`execute-plans/src/platform/components/TopBar.tsx` does all of the following on
first mount:

- `useMe()` calls the current-user/session route.
- `Promise.all([lists.approvals(), lists.alerts(), lists.jobs()])` fetches full
  lists, then locally counts pending/open/running rows.
- `probeLiveHealth()` runs immediately.
- A 30 s interval keeps probing health.

Impact:

- Counts are derived by fetching full list payloads.
- Non-primary shell reads compete with the page's primary read.
- TopBar failure/fallback state is coupled to list surface quality.

### 2.5 Duplicate jobs read

`execute-plans/src/platform/components/JobProgressDrawer.tsx` fetches
`lists.jobs()` independently during mount. This duplicates the TopBar jobs
request on first route load.

Impact:

- The shell issues two jobs reads for one initial route render.
- The duplication hides where jobs truth should be owned and cached.

### 2.6 SSE and network-idle mismatch

`PlatformShell` opens live SSE immediately with `connectLiveSse()`. The SSE path
is `/bff/events/stream`. A long-lived event stream is healthy when it remains
open, so browser automation must not use `networkidle` as page-ready evidence
for management pages.

Impact:

- Human and CI probes can over-report slowness or hang waiting for network idle.
- Route readiness must be measured by content and primary API completion.

### 2.7 BFF read aggregation cost

`pantheon/services/control-plane/bff/main.py` builds `/bff/alerts` through
`_build_operator_alerts_payload`, which composes:

- incident alerts
- governance review alerts
- approval queue alerts
- kill-switch alerts
- runtime alerts

The runtime alert path reads runtime bindings and then telemetry summaries per
runtime. The governance alert path reads review and approval queues.

Impact:

- TopBar asks for a full alert feed only to render a small count.
- A count badge can trigger multi-surface BFF work.

### 2.8 BFF duplicate route definitions

The BFF currently contains two `@app.get("/bff/jobs")` definitions in
`services/control-plane/bff/main.py`. FastAPI route ordering determines which is
served, but duplicate definitions make behavior harder to reason about and
increase the chance that future changes patch the wrong implementation.

Impact:

- Jobs startup behavior is unclear.
- Contract tests can pass against one implementation while a later definition
  shadows it.

### 2.9 Async route, synchronous work

Several BFF routes are declared `async def` but perform synchronous read-store,
local fallback, and aggregation work directly. Under concurrent shell startup
fanout, one expensive synchronous route can delay unrelated routes on the same
event loop.

Impact:

- `/health` can be delayed by unrelated read work.
- Fast isolated timings do not guarantee acceptable concurrent route timings.

## 3. Gap Matrix

| ID | Severity | Gap | Evidence | Required outcome |
|---|---|---|---|---|
| `MGMT-LOAD-P0-001` | P0 | Primary route content is coupled to shell startup fanout | Evidence route issues one primary request, but browser startup also fetches me, approvals, alerts, jobs, health, and duplicate jobs | Page primary content must be independently prioritized and measurable |
| `MGMT-LOAD-P0-002` | P0 | BFF read concurrency can delay unrelated routes | Concurrent probes slowed or stalled `/health` and Evidence together | BFF health and lightweight reads remain responsive under shell fanout |
| `MGMT-LOAD-P0-003` | P0 | Shell counts fetch full lists | TopBar derives counts from approvals, alerts, and jobs lists | Add a cheap shell summary/counts contract |
| `MGMT-LOAD-P1-001` | P1 | Management routes are not route-level code split | `/management/evidence` loads a large primary JS asset | Evidence route gets its own small route chunk |
| `MGMT-LOAD-P1-002` | P1 | Jobs are fetched twice on first route load | TopBar and JobProgressDrawer both call `lists.jobs()` | Jobs startup state is shared, cached, or deferred |
| `MGMT-LOAD-P1-003` | P1 | SSE starts before first content and confuses readiness probes | `/bff/events/stream` opens during shell mount; `networkidle` not reached | SSE connection is delayed until after first paint or excluded from readiness probes |
| `MGMT-LOAD-P1-004` | P1 | `/bff/jobs` is duplicated in BFF source | Two route definitions in `main.py` | One canonical jobs route remains, with contract tests |
| `MGMT-LOAD-P2-001` | P2 | No explicit route performance budget | CI can pass while first content regresses | Add route-load budgets and browser timing probes |
| `MGMT-LOAD-P2-002` | P2 | No startup request inventory gate | New global shell reads can be added unnoticed | CI fails if a route exceeds allowed first-render request count |

## 4. Target Architecture

### 4.1 Page-first loading

For `/management/evidence`, the critical path must be:

1. HTML and minimal app shell.
2. Evidence route chunk.
3. `GET /bff/management/evidence`.
4. Render title, metrics, and rows.

Global shell reads must be either:

- served by one cheap summary endpoint,
- deferred until after primary content,
- triggered only when the user opens the related drawer,
- or hydrated from shared cache without duplicate network calls.

### 4.2 Shell summary endpoint

Add a BFF endpoint for shell badges and current health, for example:

```text
GET /bff/management/shell-summary
```

Required payload:

```json
{
  "data": {
    "counts": {
      "pending_approvals": 0,
      "open_alerts": 0,
      "running_jobs": 0
    },
    "session": {
      "operator_id": "pantheon-dev-browser",
      "display_label": "pantheon-dev-browser",
      "roles": ["admin"]
    },
    "transport": {
      "bff_status": "ok",
      "api_version": "2026-05-07"
    }
  },
  "meta": {
    "snapshot_at": "2026-07-01T00:00:00Z",
    "surfaces": {
      "shell_summary": {"status": "ok"}
    }
  }
}
```

Rules:

- The endpoint must not return full approvals, alerts, or jobs lists.
- The endpoint may use short TTL caching for counts.
- Count freshness must be explicit in `meta.surfaces`.
- It must remain cheap under concurrency.

### 4.3 BFF read isolation

Heavy read aggregations must not block health or unrelated small reads.

Allowed implementation options:

- Precompute alert and shell counts on write/SSE events.
- Add short TTL cache around alert and shell count aggregation.
- Move synchronous read-store aggregation into a threadpool boundary for async
  routes, with explicit timeout and degradation.
- Split count endpoints from list endpoints.

Non-goals:

- Do not make `/health` depend on read-store aggregation.
- Do not fetch full alert or approval lists to render a badge count.
- Do not mask slow reads with frontend-only skeletons.

### 4.4 Route-level code splitting

Management route clusters should be lazily loaded:

- oversight routes
- operations routes
- registry/detail routes
- v5 routes
- phase2 routes
- studios
- Agora routes

The Evidence route should not import registry detail pages, studios, or large
markdown/diagram/highlight libraries at initial navigation.

### 4.5 Readiness probe model

Use route-specific content milestones:

- document loaded
- React shell visible
- route heading visible
- primary route API returned
- first row or route empty state visible

Do not use `networkidle` for management routes with SSE.

## 5. Remediation Plan

### Phase 0 - Baseline and guardrails

Owner: FE + BFF + release-gate

Tasks:

1. Add a browser timing probe for `/management/evidence` that records:
   - navigation start to heading visible
   - navigation start to first Evidence row visible
   - request count before first Evidence row
   - primary JS decoded size
   - all BFF requests started before first row
2. Add a BFF concurrency probe that requests:
   - `/health`
   - `/bff/management/evidence`
   - `/bff/alerts`
   - `/bff/approvals`
   - `/bff/jobs`
3. Record per-route BFF duration logs with path, status, correlation id, and
   elapsed milliseconds.
4. Update release probes to avoid `networkidle` for SSE-backed pages.

Exit criteria:

- The current baseline is captured in CI artifacts.
- A failing route-load budget can identify whether the regression is FE bundle,
  shell fanout, or BFF latency.

### Phase 1 - Stop shell fanout from blocking primary content

Owner: execute-plans FE

Tasks:

1. Refactor `TopBar` to avoid full list fetches at first mount.
2. Prefer `GET /bff/management/shell-summary` when available.
3. Until the BFF endpoint lands, defer approvals/alerts/jobs count reads until
   after route primary content has rendered.
4. Share jobs state between `TopBar` and `JobProgressDrawer`, or make
   `JobProgressDrawer` lazy/hydrate only when jobs exist or the drawer is opened.
5. Keep `NotificationCenter` list hydration behind the drawer open state.

Exit criteria:

- `/management/evidence` starts no more than two non-primary BFF requests before
  first Evidence row.
- No duplicate `/bff/jobs` request occurs during first route load.

### Phase 2 - Add cheap BFF shell summary and isolate counts

Owner: Pantheon BFF

Tasks:

1. Add `GET /bff/management/shell-summary`.
2. Return badge counts without full list payloads.
3. Use TTL cache or precomputed counters for alert and job counts.
4. Do not call the full `_build_operator_alerts_payload` just to return
   `open_alerts`.
5. Add tests for summary shape, redaction, and degraded count surfaces.
6. Consolidate duplicate `/bff/jobs` route definitions into one canonical route.

Exit criteria:

- `/bff/management/shell-summary` p95 <= 200 ms under 10 concurrent requests in
  dev.
- `/health` p95 <= 200 ms while shell summary and Evidence are concurrently
  requested.
- `/bff/jobs` has one route definition and one contract test source of truth.

### Phase 3 - Code split management console routes

Owner: execute-plans FE

Tasks:

1. Replace eager management page imports in `App.tsx` with `React.lazy` route
   modules.
2. Split oversight, operations, registry/detail, phase2, v5, and studio routes.
3. Lazy-load command palette internals and heavyweight drawers after shell
   first paint or user interaction.
4. Audit bundle analyzer output for diagram, markdown, editor, syntax, and
   visualization libraries pulled into the initial chunk.

Exit criteria:

- Initial management route JS gzip <= 800 KB.
- Evidence route-specific JS gzip <= 150 KB, excluding shared vendor cache.
- Evidence first row visible p75 <= 1.5 s and p95 <= 2.5 s on dev FE.

### Phase 4 - SSE startup policy

Owner: FE + BFF

Tasks:

1. Open SSE after primary route content has rendered, or during
   `requestIdleCallback` with a timeout fallback.
2. Keep manual retry available for realtime status.
3. Ensure route readiness probes ignore live EventSource requests.
4. Confirm SSE reconnect and Last-Event-Id behavior still satisfy the
   realtime contract.

Exit criteria:

- SSE does not delay route primary content.
- Browser probes rely on content milestones, not `networkidle`.
- SSE reconnect checks remain covered separately.

### Phase 5 - Production perf gate

Owner: release-gate

Tasks:

1. Add a route-load budget file for management pages.
2. Add CI artifacts with request waterfalls and route timing JSON.
3. Fail the gate if:
   - primary JS exceeds the budget,
   - first row p95 exceeds budget,
   - non-primary startup requests exceed budget,
   - duplicate route requests are detected,
   - BFF `/health` p95 regresses under read fanout.

Exit criteria:

- New global shell requests cannot be added without an explicit budget update.
- BFF read fanout regressions fail before deployment.

## 6. Proposed Work Items

| Work item | Repo | Description | Depends on |
|---|---|---|---|
| `FE-MGMT-LOAD-001` | `execute-plans` | Add route-load instrumentation and first-row browser timing probe | none |
| `FE-MGMT-LOAD-002` | `execute-plans` | Defer TopBar full-list reads; consume shell summary when available | `BFF-MGMT-LOAD-001` for final path |
| `FE-MGMT-LOAD-003` | `execute-plans` | Share or defer jobs hydration; remove duplicate startup jobs fetch | none |
| `FE-MGMT-LOAD-004` | `execute-plans` | Route-level lazy loading for management console | none |
| `FE-MGMT-LOAD-005` | `execute-plans` | Delay SSE until after primary route content or idle callback | release-gate probe update |
| `BFF-MGMT-LOAD-001` | `pantheon` | Add `GET /bff/management/shell-summary` | none |
| `BFF-MGMT-LOAD-002` | `pantheon` | Add cached/precomputed alert, approval, and job counts | `BFF-MGMT-LOAD-001` |
| `BFF-MGMT-LOAD-003` | `pantheon` | Consolidate duplicate `/bff/jobs` route definitions | none |
| `BFF-MGMT-LOAD-004` | `pantheon` | Offload or cache synchronous read aggregations in async routes | baseline probe |
| `REL-MGMT-LOAD-001` | both | Replace `networkidle` readiness checks with content milestones | none |
| `REL-MGMT-LOAD-002` | both | Add BFF concurrent read fanout gate | BFF duration logging |

## 7. Acceptance Criteria

### 7.1 User-visible route performance

Measured against deployed dev FE with live BFF:

| Metric | Target |
|---|---:|
| Evidence heading visible | p75 <= 800 ms, p95 <= 1.5 s |
| Evidence first row or empty state visible | p75 <= 1.5 s, p95 <= 2.5 s |
| Non-primary BFF requests before first row | <= 2 |
| Duplicate `/bff/jobs` requests before first row | 0 |
| Initial management route JS gzip | <= 800 KB |
| Evidence route chunk gzip | <= 150 KB |

### 7.2 BFF responsiveness

Measured against dev BFF with bearer token:

| Metric | Target |
|---|---:|
| `/health` isolated | p95 <= 100 ms |
| `/health` during 10 concurrent shell-summary/Evidence reads | p95 <= 200 ms |
| `/bff/management/evidence` isolated | p95 <= 300 ms |
| `/bff/management/evidence` during shell fanout | p95 <= 750 ms |
| `/bff/management/shell-summary` under 10 concurrent requests | p95 <= 200 ms |

### 7.3 Probe correctness

- Management page readiness probes do not wait for `networkidle`.
- SSE health is tested in a separate realtime probe.
- CI artifacts show request waterfall and timing JSON for failed runs.

## 8. Risks and Non-Goals

### Risks

- Lazy loading can reveal circular imports hidden by the current eager graph.
- Deferring shell reads can temporarily show empty badges unless the UI has a
  clear loading or stale-count state.
- Count caching must surface staleness honestly.
- Moving synchronous work to a threadpool without bounding concurrency can move
  the bottleneck instead of fixing it.

### Non-goals

- Do not remove Evidence provenance fields or reduce Evidence correctness to
  improve perceived speed.
- Do not hide slow route startup behind a permanent skeleton.
- Do not treat SSE `networkidle` failure as an application failure.
- Do not add more client-side reconstruction of counts or evidence links.

## 9. Verification Commands

Suggested local and live checks after fixes:

```bash
# FE targeted tests
npm run test -- src/management/pages/oversight/EvidenceExplorerPage.test.tsx

# FE production build and bundle inventory
npm run build
find dist/assets -maxdepth 1 -type f -printf "%s %p\n" | sort -nr | head -20

# BFF targeted tests
python3 -m pytest services/control-plane/bff/tests/test_bff_b3_management_evidence.py

# Live isolated timings
curl -sS -o /dev/null -w "evidence code=%{http_code} ttfb=%{time_starttransfer} total=%{time_total}\n" \
  -H "Authorization: Bearer op-b3:admin" \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/management/evidence

curl -sS -o /dev/null -w "health code=%{http_code} ttfb=%{time_starttransfer} total=%{time_total}\n" \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health
```

Add a checked-in scripted version of the browser and concurrent BFF probes as
part of `FE-MGMT-LOAD-001` and `REL-MGMT-LOAD-002`; do not rely on manual curl
history as release evidence.

## 10. Immediate Next Step

Start with the smallest user-visible improvement:

1. Land `BFF-MGMT-LOAD-001` shell summary.
2. Land `FE-MGMT-LOAD-002` so TopBar consumes shell summary and no longer
   fetches full approvals, alerts, and jobs lists on first mount.
3. Land `FE-MGMT-LOAD-003` to remove duplicate jobs hydration.
4. Add the route-load probe before the route-level lazy split, so Phase 3 has a
   real before/after measurement.

This order reduces live page pain quickly while keeping the larger bundle split
work measurable and safe.
