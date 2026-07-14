# AG-UIPOL-002: Fix standalone shell double scrollbar and ghost headers

## Scope

Hosted dev screenshots (2026-07-13) show on all three Agora tabs:

- Two vertical scrollbars: the browser page scrollbar plus a nested custom
  scroll container (with arrow buttons) at the top-right of the shell. The
  page body must never double-scroll; wide/tall content scrolls inside one
  designated content region only.
- Strategy Workshop renders a ghost/overlapping "Strategy workshop" heading
  behind the "open / Readiness / Cards / Events" status row (two headers
  painted in the same area, one half-faded).

## Work (execute-plans)

1. Audit the standalone Agora shell (`src/routes/agora.tsx` +
   `TradingDeskLayout`) overflow chain; establish a single scroll owner
   (content region), `overflow: hidden` on the shell frame, no nested
   `overflow-y: auto` wrappers around the whole page.
2. Fix the workshop header stacking/duplication (likely a positioned legacy
   heading left under the new status row).
3. Verify at 1280px and 2560px widths and on the three tabs; no horizontal
   scroll on the page body.

## Acceptance

- Hosted screenshots (desktop wide + narrow) show exactly one scrollbar per
  tab and no overlapping headings.
- Playwright check asserting `document.body.scrollHeight <= window.innerHeight`
  (or the shell's single-scroll-owner invariant) added to the Agora e2e suite.

## Completion evidence (2026-07-13)

- execute-plans PR #291 merged the shell/header fix as
  `71e84a9f5d9ec237376cb9c13680a2d87fe1cfff`.
- The accepted hosted descendant is
  `8ad0a152b1869c2038f077c24d6cc7beafaa7f8f`, qualified by integration run
  `29280017449` attempt 2 and deployed by run `29282861281`.
- Hosted validation passed 6/6 cases across all three tabs at 1280 and 2560
  pixels, including the body/shell/main scroll-owner invariant and the
  Workshop no-visible-heading assertion.
- Full manifest, validation details, and six screenshots are recorded in
  [AG-UIPOL-002 hosted evidence](./evidence/AG-UIPOL-002-hosted-evidence.md).
