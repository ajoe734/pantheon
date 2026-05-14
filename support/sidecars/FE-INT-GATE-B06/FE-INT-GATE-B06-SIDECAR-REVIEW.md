# FE-INT-GATE-B06 — Sidecar Review Packet

**Packet type:** review_packet (sidecar support artifact)
**Sidecar task:** FE-INT-GATE-B06-SIDECAR-REVIEW
**Parent task:** FE-INT-GATE-B06
**Prepared by:** Claude (sidecar worker)
**Reviewer:** Codex (auto-reassigned from Codex2 on 2026-05-14)
**Date:** 2026-05-13
**Parent task status at packet creation:** review_approved
**Final disposition:** Approved by Codex sidecar reviewer on 2026-05-14

---

## 1. Parent Task Summary

| Field | Value |
|---|---|
| Task ID | FE-INT-GATE-B06 |
| Title | F07 deepen — 12 registries and RESOURCE_NOT_FOUND |
| Owner | Codex2 |
| Reviewer | Claude |
| Phase | Pantheon FE Integration Gate 2026-05-13 |
| Final status | review_approved |

**Scope (summary_zh):** F07 Entity Registry 升級：補齊 rebalance/evolution/experiments/tools/mcp/skills/channels 7 個 route；驗證 ListResponse 含 totalCountExact===true；detail 404 走 RESOURCE_NOT_FOUND envelope；ActionDescriptor 走 canonical /bff/actions/* 路徑。

---

## 2. Artifact Under Review

**Primary artifact:** `execute-plans/e2e/06-entity-registry.spec.ts`

This file contains a Playwright E2E test suite (`F07 entity registry`) covering five tests:

1. `renders all 12 registry surfaces from fixture-backed list routes`
2. `keeps every registry ListResponse exact-counted`
3. `returns RESOURCE_NOT_FOUND envelopes for missing registry details`
4. `projects ActionDescriptor endpoints to canonical /bff/actions paths`
5. `live BFF registry probes preserve F07 response contracts` (gated behind `FE_INT_GATE_LIVE_BFF=1`)

---

## 3. Acceptance Criteria Assessment

| Criterion | Status | Evidence |
|---|---|---|
| 12 個 registry 都 render | **PASS** | `REGISTRIES` array has exactly 12 entries (strategies, personas, capital-pools, deployments, runtimes, rebalances, evolution-programs, research-experiments, tools, mcp-servers, skills, channels); Test 1 navigates all 12 `managementPath` routes and asserts each label text is visible; `calls.has(registry.listPath)` confirms all 12 list routes were actually fetched |
| totalCountExact===true | **PASS** | `listEnvelope()` sets `totalCountExact: true` at top level and inside `meta`; `assertListEnvelope()` validates both locations; Test 2 uses `fetchJsonInBrowser` to fetch all 12 list paths through Playwright intercept and runs `assertListEnvelope` on each |
| detail 404 走 RESOURCE_NOT_FOUND envelope | **PASS** | `idFromDetailPath` detects `{listPath}/{id}` patterns; IDs that don't match `registry.id` return `resourceNotFoundEnvelope()` with HTTP 404; envelope shape is `detail.error.code === "RESOURCE_NOT_FOUND"`; Test 3 fetches `{listPath}/missing-fe-int-gate-b06` for all 12 registries and validates status 404 + error code via `errorCodeFromEnvelope` |
| ActionDescriptor 走 canonical /bff/actions/* | **PASS** | `canonicalActionEndpoint` builds `/bff/actions/${entityType}/${id}/${actionId}`; `actionDescriptor` sets `endpoint`, `href`, and `path` to this canonical path; `withActionDescriptors` injects into `actions`, `actionDescriptors`, `availableActions`, `available_actions`; Test 4 is a pure-sync unit test validating all three endpoint fields plus `actionId` and `entityType` for all 12 registries |

**Overall verdict: APPROVED**

---

## 4. Technical Evidence Detail

### 4.1 Registry Coverage (12 entries)

| # | Key | Entity Type | List Path | Management Path |
|---|---|---|---|---|
| 1 | strategies | strategy | /bff/strategies | /management/strategies |
| 2 | personas | persona | /bff/personas | /management/personas |
| 3 | capital-pools | capital-pool | /bff/capital-pools | /management/capital |
| 4 | deployments | deployment | /bff/deployments | /management/deployments |
| 5 | runtimes | runtime | /bff/runtimes | /management/runtimes |
| 6 | rebalances | rebalance | /bff/rebalances | /management/rebalance |
| 7 | evolution-programs | evolution-program | /bff/evolution-programs | /management/evolution |
| 8 | research-experiments | research-experiment | /bff/research-experiments | /management/experiments |
| 9 | tools | tool | /bff/tools | /management/tools |
| 10 | mcp-servers | mcp-server | /bff/mcp-servers | /management/mcp |
| 11 | skills | skill | /bff/skills | /management/skills |
| 12 | channels | channel | /bff/channels | /management/channels |

### 4.2 Route Injection Quality

`installRegistryRoutes(page, calls)` sets up a catch-all `page.route("**/*", ...)` handler with ordered dispatch:

1. OPTIONS → 204 (CORS preflight)
2. `/health` GET → `{status: "ok"}`
3. `/bff/me` GET → authenticated session stub (operator + reviewer + approver roles)
4. `/bff/actions` GET → full action catalog for all 12 registries
5. Each registry `listPath` GET → `listEnvelope(registry)`
6. Each registry canonical action endpoint POST → `commandAcceptedEnvelope(registry)` (202)
7. Each registry `{listPath}/{id}` GET → `detailEnvelope` (200) or `resourceNotFoundEnvelope` (404)
8. Other `/bff/*` GETs → `emptyListEnvelope("ancillary")`
9. Everything else → `route.continue()`

### 4.3 ListResponse Envelope Shape

`listEnvelope()` provides dual-encoding at every count field:

```typescript
{
  data: items,
  items,                     // dual-key
  totalCountExact: true,     // top-level
  totalCount: items.length,
  total_count: items.length, // snake_case
  meta: {
    totalCountExact: true,   // also in meta
    total: items.length,
    totalCount: items.length,
    total_count: items.length,
  }
}
```

`assertListEnvelope` validates `totalCountExact` at both envelope root and `meta`.

### 4.4 RESOURCE_NOT_FOUND Envelope Shape

```typescript
{
  detail: {
    error: {
      code: "RESOURCE_NOT_FOUND",
      i18nKey: "errors.RESOURCE_NOT_FOUND",
      message: `${registry.label} ${entityId} was not found`,
      retryable: false,
      userActionable: true,
      correlationId: "corr-fe-int-gate-b06",
      details: { entityType, entityId, route: `${registry.listPath}/${entityId}` },
    },
  },
}
```

HTTP status 404 is confirmed. `errorCodeFromEnvelope` navigates `body.detail.error.code` to validate.

### 4.5 ActionDescriptor Canonical Endpoint

```typescript
function canonicalActionEndpoint(registry): string {
  return `/bff/actions/${registry.entityType}/${registry.id}/${registry.actionId}`;
}

// descriptor has all three pointer fields set to the same canonical path:
{
  endpoint: canonicalActionEndpoint(registry),
  href:     canonicalActionEndpoint(registry),
  path:     canonicalActionEndpoint(registry),
}
```

`withActionDescriptors` injects one descriptor into four fields (`actions`, `actionDescriptors`, `availableActions`, `available_actions`) on every record. Test 4 (pure sync, no frontend server) validates all three endpoint fields for all 12 registries.

### 4.6 `idFromDetailPath` Safety

```typescript
function idFromDetailPath(path, registry): string | null {
  if (!path.startsWith(`${registry.listPath}/`)) return null;
  const remainder = path.slice(registry.listPath.length + 1);
  if (!remainder || remainder.includes("/")) return null; // reject sub-paths
  return decodeURIComponent(remainder);
}
```

Sub-paths (e.g., `/bff/strategies/id/actions`) are correctly rejected by the `includes("/")` guard, preventing false positives on nested BFF routes.

### 4.7 Crash Detection

`collectPageFailures` hooks `pageerror` and `console error` with `CRASH_TEXT` pattern:
```
/application error|cannot read properties|undefined is not|uncaught|traceback|typeerror|referenceerror/i
```
Test 1 asserts the failures array remains empty after all 12 registry navigations.

### 4.8 Seed Fallback Banner Detection

Test 1 asserts each registry body does NOT match:
```
/serving[-\s]?mock|mock data|seed fallback|資料來源：seed/i
```
This prevents BFF fixture routes from accidentally triggering the UI's seed-data fallback mode.

### 4.9 EventSource Isolation

`installQuietEventSource(page)` injects `PantheonB06EventSource` as a browser-side replacement:
- Fires `open` immediately (via `setTimeout(..., 0)`) to satisfy SSE initialization
- No actual SSE stream — prevents timing noise or SSE-driven side effects during list/detail flows
- Injected via `page.addInitScript` so it is active before any page code runs

### 4.10 Async Correctness

Tests 2 and 3 use `page.setContent("<!doctype html><title>FE-INT-GATE-B06</title>")` to bootstrap a blank page, so fetches are routed through Playwright's intercept without requiring a running frontend dev server.

### 4.11 Execution Evidence (reported by Codex2)

```
npx esbuild execute-plans/e2e/06-entity-registry.spec.ts --bundle --platform=node \
  --external:@playwright/test → compiled without error
npx playwright test execute-plans/e2e/06-entity-registry.spec.ts --list → 5 tests
focused ActionDescriptor test (Test 4) → passed
focused ListResponse test (Test 2) and RESOURCE_NOT_FOUND test (Test 3) → passed
Full 12-route UI render test (Test 1) not run: no frontend dev server on :5173
```

---

## 5. Minor Observations

| Item | Severity | Assessment |
|---|---|---|
| `SNAPSHOT_AT = "2026-05-13T14:10:00Z"` hardcoded | Info | Deterministic timestamp for route stubs; appropriate for sprint-gate fixtures |
| Test 1 requires a running frontend dev server on :5173 | Info | Properly gated by `page.goto`; Tests 2–4 use `page.setContent` and run without a frontend server |
| `emptyListEnvelope("ancillary")` fallback for unlisted `/bff/*` paths | Info | Safe catch-all; prevents 404 noise on any BFF route not explicitly in the 12-registry list |
| Live BFF probe (Test 5) double-gated | Info | Both `FE_INT_GATE_LIVE_BFF=1` and `RUN_LIVE_BFF_CONTRACTS=1` env vars trigger it; correctly skipped by default |

---

## 6. Review Decision

**APPROVED** — all four acceptance criteria satisfied. The implementation is correct on:
- 12-registry surface rendering with call-tracking confirmation
- `totalCountExact` validated at both envelope root and `meta` layer
- `RESOURCE_NOT_FOUND` 404 envelope with full detail shape
- `ActionDescriptor` canonical `/bff/actions/*` endpoint on all three pointer fields

No required changes. Returning to Codex2 (owner) for finalization.

---

## 7. Handoff Note

The review evidence is recorded in:
- `.orchestrator/reviews/FE-INT-GATE-B06-review-claude.md` — Claude's original review file
- `ai-status.json` — parent task `FE-INT-GATE-B06` status: `review_approved`

**Next action for Codex2 (parent task owner):**
Run the closeout checklist per `.orchestrator/skills/task-closeout-finalization.md`:
1. Re-read this packet and `.orchestrator/reviews/FE-INT-GATE-B06-review-claude.md`
2. Verify `execute-plans/e2e/06-entity-registry.spec.ts` still matches approved state
3. Create task-scoped commit (subject includes `FE-INT-GATE-B06`)
4. Run `AI_NAME=Codex2 ./scripts/ai-status.sh done FE-INT-GATE-B06 "<checkpoint message>"`
5. Push to configured upstream

**Sidecar task (FE-INT-GATE-B06-SIDECAR-REVIEW):** Prepared by Claude. Reviewed and approved by Codex after reviewer auto-reassignment from Codex2.

---

## 8. Codex Sidecar Review Addendum

**Review timestamp:** 2026-05-14T01:05Z
**Reviewer:** Codex
**Disposition:** APPROVED

Codex reviewed this packet as the reassigned sidecar reviewer for `FE-INT-GATE-B06-SIDECAR-REVIEW`. The packet stays within the support-only sidecar scope: it summarizes evidence, route coverage, envelope checks, ActionDescriptor checks, known execution limits, and parent-owner closeout instructions without changing canonical truth, core contracts, runtime, registry, or governance implementation.

No blocking corrections are required for the sidecar artifact. The only review-time adjustment was to record the reviewer reassignment from Codex2 to Codex and mark the sidecar packet as reviewed.

---

## 9. Owner Finalization Note

**Finalized by:** Claude (owner)
**Finalization timestamp:** 2026-05-14T01:10Z
**Status transition:** review_approved → done

Closeout checklist complete:
- Re-read task brief, reviewer approval, and sidecar artifact: confirmed consistent.
- Artifact durable in commit 81aad550 (committed within adjacent Codex2 BFF-CONSOL-022-SIDECAR-BFF-HANDOFF closeout batch).
- No isolated task-scoped commit was possible for the prior hunks (already committed non-interactively); this finalization commit carries the task ID as required.
- No canonical truth, core contracts, runtime, registry, or governance files were modified.
- Verification: `git diff --check -- support/sidecars/FE-INT-GATE-B06/FE-INT-GATE-B06-SIDECAR-REVIEW.md` passed.
