# Review: FE-INT-GATE-B08 — F15 deepen strict vs hybrid

Reviewer: Claude
Date: 2026-05-13
Artifact: execute-plans/e2e/09-strict-vs-hybrid.spec.ts

## Verdict

**Approved.**

## Acceptance Criteria Check

| Criterion | Coverage | Notes |
|---|---|---|
| strict 5xx 注入後 spec fail | ✓ | Strict test asserts `STRICT_ERROR_TEXT` visible; `expectNoSeedFallback` confirms no mock data rendered |
| hybrid 5xx 注入後顯示 LiveBffBanner | ✓ | Hybrid test asserts `FALLBACK_ACTIVE_TEXT` banner visible + seed strategy text present |
| 4xx 不 fallback | ✓ | 4xx test asserts zero count of both `STRICT_ERROR_TEXT` and seed fallback text |
| strict 無 mock data | ✓ | Strict test calls `expectNoSeedFallback()` which checks both fallback text and seed strategy name |

## Implementation Quality

- **Self-contained route injection**: `page.route` with `exactPath()` predicate prevents cross-route bleed; each test installs only what it needs.
- **CORS handling**: `corsHeaders()` echoes the request origin and covers preflight OPTIONS — correct for browser-originated fetch.
- **STRICT detection**: Reads from `VITE_BFF_FALLBACK`, `BFF_FALLBACK`, and `PANTHEON_E2E_STRICT`; forward-compatible multi-source detection.
- **Test isolation**: `installStableShellRoutes` + `isolateOtherAncillaryBffRoutes` provide a stable shell so assertion failures come from the strategies route only.
- **EventSource stub**: `installQuietEventSource` via `addInitScript` prevents SSE noise from polluting assertions.
- **Response sequencing**: `gotoStrategiesAndWaitForInjectedStatus` uses `waitForResponse` before the assertion block — avoids race between page navigation and injection.
- **4xx test mode-agnostic**: Runs in both strict and hybrid branches, which is correct since 4xx is a typed BffError in both modes.

## Minor Observations (non-blocking)

- The 250ms `waitForTimeout` in `injectBffResponse` is acceptable as a realism delay for simulating backend latency.
- `liveListEnvelope` returns empty `items` — fine since the test only cares about the strategies surface, not other BFF surfaces.

## Summary

Spec is clean, self-contained, and covers all four acceptance criteria with correct assertions and mode branching. Codex's verification (3 tests listed, hybrid passed 2, strict-focused passed strict 5xx, full strict passed 4xx) is consistent with the spec structure. Approved for finalization.
