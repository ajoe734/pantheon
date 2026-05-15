# Review: FE-INT-GATE-D04 — F17 upgrade — axe-core a11y on 6 v5 pages

**Reviewer:** Claude
**Date:** 2026-05-13
**Artifact:** execute-plans/e2e/17-a11y-v5.spec.ts
**Decision:** APPROVED

## Acceptance Criteria Check

| Criterion | Result | Evidence |
|---|---|---|
| 6 v5 pages axe critical+serious===0 | ✅ PASS | `V5_PAGE_SCENARIOS` has exactly 6 entries (control-room, research loop, execution loop / PersonaHealthMatrix, optimization loop, sentinel, interventions). Test loop at line 332–338 calls `expectCriticalSeriousAxeClean` for each. `BLOCKING_IMPACTS` set is `{"critical","serious"}` — precise match to the criterion. `AxeBuilder` uses WCAG 2.0+2.1 A+AA tags for full coverage. |
| focus return to trigger | ✅ PASS | "drawer focus returns to the trigger after keyboard close" (line 340): focuses a `tbody tr[tabindex="0"]` row, opens dialog via Enter, closes via Escape, asserts `trigger` is focused again and `dialog` count is 0. |
| ESC closes only top overlay | ✅ PASS | "ESC closes only the top overlay before closing the underlying drawer" (line 359): opens a sentinel finding drawer (1 dialog), then clicks an emergency-run button to stack a second overlay (2 dialogs), presses ESC once — asserts count=1 and original drawer still visible, presses ESC again — asserts count=0 and finding trigger re-focused. |
| reduced motion respected | ✅ PASS | "motion-safe v5 status indicators respect reduced motion" (line 390): `page.emulateMedia({ reducedMotion: "reduce" })` before navigation; evaluates computed `animationName`, `animationDuration`, and `animationIterationCount` on elements with Tailwind `motion-safe:animate-pulse` class; asserts each is `animationName === "none"` or `(duration ≤ 1 ms && iterations ≤ 1)`. |

## Spec Quality

- `AxeBuilder` scoped with `withTags(["wcag2a","wcag2aa","wcag21a","wcag21aa"])` — WCAG 2.0/2.1 A+AA, correct for critical/serious gating
- `BLOCKING_IMPACTS` set + typed `AxeViolation` type make the filter safe and readable
- `formatAxeViolations()` produces actionable failure output (up to 3 nodes per violation, each with selector and `failureSummary`)
- `corsHeaders()` + `fulfillJson()` utilities DRY across all 6 BFF endpoints
- `installV5A11yRoutes` mock covers all BFF paths required by the 6 pages; unknown `/bff/` paths return `{ items: [] }`; non-BFF requests fall through to `route.fallback()`
- `firstVisible()` helper avoids false-positive locator failures when multiple candidates exist
- `gotoReady()` waits for `networkidle` (soft-catch timeout), crash-text check, and page-specific ready regex before proceeding — robust against flaky paint
- Sibling `17-a11y-v5.spec.ts` confirmed present at `/home/lupin/code/execute-plans/e2e/`

## Verification (from owner)

- `npx tsc --noEmit` → passed clean
- `npx playwright test e2e/17-a11y-v5.spec.ts --list` → 9 tests listed
- `PANTHEON_FE_BASE_URL=http://127.0.0.1:5178 npx playwright test e2e/17-a11y-v5.spec.ts --project=chromium` → 9/9 passed

## Decision

All acceptance criteria satisfied. Spec is well-typed, follows established FE-INT-GATE harness patterns, and provides actionable failure output. Approved for finalization.
