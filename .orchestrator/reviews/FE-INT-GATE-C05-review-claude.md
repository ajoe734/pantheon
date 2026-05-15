# Review: FE-INT-GATE-C05 — e2e helpers — auth fixtures sse

Reviewer: Claude
Date: 2026-05-13
Decision: **APPROVED**

## Acceptance Criteria Check

| # | Criterion | Status |
|---|---|---|
| 1 | auth.ts 提供 Bearer 注入 helper | ✅ Pass |
| 2 | fixtures.ts 提供 seeded id 常數 | ✅ Pass |
| 3 | sse.ts 提供 EventSource control + Last-Event-Id helper | ✅ Pass |
| 4 | B5/B7/C2/C3/C4 spec 可 import 使用 | ✅ Pass |

## auth.ts

- `normalizeBearerToken` strips `Bearer ` prefix case-insensitively; correct.
- `makeDevAuthToken` constructs `operatorId:role1,role2:mfa` format with sensible defaults.
- `authToken` resolves via options → env var fallback chain (`BFF_AUTH_TOKEN`, `PANTHEON_BFF_SMOKE_BEARER_TOKEN`, etc.) → `DEFAULT_DEV_AUTH_TOKEN`; correct priority order.
- `authHeaders` / `mutationAuthHeaders` produce complete header maps including optional `X-Tenant-Id` and `Content-Type`.
- `installOidcDevLogin` uses both `addInitScript` (pre-navigation) and `evaluate` (current page) to write session keys into `sessionStorage`/`localStorage`/`both`, covering the race where init scripts run before the page has a durable origin. The `catch(() => undefined)` on `evaluate` is correct since the page may not yet be navigated.
- Aliases `installDevOidcLogin` / `devLogin` provided for compatibility.
- `E2ePage` interface is a minimal Playwright-compatible structural type — avoids importing `@playwright/test` in the helper layer, keeping it portable.

## fixtures.ts

- `SEEDED_IDS` covers all expected entity types (strategy, persona, capital, ranking-formula, deployment, evolution-program, etc.).
- Named re-exports (`STRATEGY_DEV_ID`, `PERSONA_DEV_ID`, …) provide ergonomic direct imports.
- `SEEDED_RESOURCE_IDS` maps REST collection paths to IDs — useful for mock-route handlers.
- `listEnvelope` / `dataEnvelope` produce consistent API envelope shapes with `snapshot_at` metadata.
- Helper generators (`seededCorrelationId`, `seededRequestId`, `seededIdempotencyKey`, `seededCommandId`) produce namespaced, deterministic values suitable for idempotency testing.

## sse.ts

- `appendLastEventId` correctly handles both absolute and relative URLs using `URL` with a dummy base.
- `lastEventIdFromHeaders` handles both `Headers` objects and plain record objects, with case-insensitive key fallbacks (`Last-Event-ID`, `last-event-id`, `Last-Event-Id`).
- `formatSseBlock` produces a valid SSE protocol block with `id:`, `event:`, `data:` lines and double-newline terminator.
- `installSseController` installs a full EventSource controller in the browser context:
  - reconnects cleanly via `reconnect()` with optional `lastEventId` override
  - handles `system.resync_required` with fetch + reconnect when `resyncOnSystemRequired` is set
  - exposes `state` object for test assertions (events, opens, errors, connectionUrls, readyState)
- `installQuietEventSource` replaces `window.EventSource` with a stub class that never opens a real connection, exposing `emit()` for synthetic event injection. `autoOpen: true` fires a queued microtask open event — correct for tests that need readyState OPEN without a server.
- `waitForSseOpen` uses `waitForFunction` (poll-based) rather than `evaluate`, which is correct for async browser state.
- `browserEventSourceOpenState` returns `EventSource.OPEN` constant from browser context — guards against constant value drift.

## Verification (from owner handoff)

- `tsc --noEmit --pretty false --target ES2022 --module NodeNext --moduleResolution NodeNext --types node --lib DOM,DOM.Iterable,ES2022 --skipLibCheck` passed for all three files.
- `esbuild --bundle --platform=node --format=esm` bundle passed for all three files.
- `git diff --check` passed (no whitespace errors).

## Conclusion

All three helper files are clean, correctly typed, and satisfy the acceptance criteria. The abstractions are appropriately scoped (no over-engineering), types are portable (no direct Playwright import), and the exports are suitable as prerequisite imports for Sprint C specs B5/B7/C2/C3/C4. No changes required. Returning to Codex for closeout finalization.
