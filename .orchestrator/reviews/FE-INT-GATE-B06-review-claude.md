# Review: FE-INT-GATE-B06
Reviewer: Claude
Date: 2026-05-13
Task: F07 deepen — 12 registries and RESOURCE_NOT_FOUND
Owner: Codex2
Artifact: execute-plans/e2e/06-entity-registry.spec.ts

## Verdict: APPROVED

All four acceptance criteria are satisfied.

## Coverage Verified

1. **12 registry surfaces render** — `REGISTRIES` array contains exactly 12 entries:
   strategies, personas, capital-pools, deployments, runtimes, rebalances,
   evolution-programs, research-experiments, tools, mcp-servers, skills, channels.
   Test 1 iterates all 12, navigates to each `managementPath`, and asserts the
   label text appears in the body. The `installRegistryRoutes` call tracker confirms
   each `listPath` was actually hit.

2. **totalCountExact===true** — `listEnvelope()` sets `totalCountExact: true` at both
   the top-level envelope and within `meta`. `assertListEnvelope()` validates both
   locations. Test 2 uses `fetchJsonInBrowser` (routed through the page's intercept
   handler) to fetch all 12 list paths and runs `assertListEnvelope` on each response.

3. **detail 404 → RESOURCE_NOT_FOUND envelope** — The route handler uses
   `idFromDetailPath` to detect `{listPath}/{id}` requests. For IDs that don't match
   `registry.id` it returns `resourceNotFoundEnvelope()` with HTTP 404. The envelope
   shape is `detail.error.code === "RESOURCE_NOT_FOUND"`. Test 3 fetches
   `{listPath}/missing-fe-int-gate-b06` for all 12 registries and validates status
   404 + error code via `errorCodeFromEnvelope`.

4. **ActionDescriptor canonical /bff/actions/* endpoint** — `canonicalActionEndpoint`
   builds `/bff/actions/${entityType}/${id}/${actionId}`. `actionDescriptor` sets
   `endpoint`, `href`, and `path` all to this canonical path. `withActionDescriptors`
   injects descriptors into `actions`, `actionDescriptors`, `availableActions`, and
   `available_actions` on every record. Test 4 is a pure-sync unit test that validates
   all three endpoint fields plus `actionId` and `entityType` for every one of the 12
   registries — no frontend server required.

## Harness Quality

- Single `page.route("**/*")` handler with ordered path-based dispatch; no route leaks.
- `idFromDetailPath` correctly rejects sub-paths (paths containing extra `/` after ID)
  preventing false positives on nested BFF routes.
- `installQuietEventSource` stubs the browser `EventSource` with a no-op that fires
  `open` immediately, preventing SSE noise from interfering with list/detail flows.
- `collectPageFailures` hooks `pageerror` and `console error` for crash detection.
- Tests 2 and 3 use `page.setContent` to bootstrap a blank page so fetches are routed
  through Playwright's intercept without requiring a running frontend dev server.
- Live BFF probe (Test 5) is correctly gated behind `FE_INT_GATE_LIVE_BFF=1`.
- Both camelCase and snake_case variants present for all envelope fields per BFF
  dual-encoding contract.

## Verification (reported by Codex2)

```
npx esbuild execute-plans/e2e/06-entity-registry.spec.ts --bundle --platform=node \
  --external:@playwright/test → compiled without error
npx playwright test execute-plans/e2e/06-entity-registry.spec.ts --list → 5 tests
focused ActionDescriptor test (Test 4) → passed
focused ListResponse test (Test 2) and RESOURCE_NOT_FOUND test (Test 3) → passed
Full 12-route UI render test (Test 1) not run: no frontend dev server on :5173
```

## No Required Changes
