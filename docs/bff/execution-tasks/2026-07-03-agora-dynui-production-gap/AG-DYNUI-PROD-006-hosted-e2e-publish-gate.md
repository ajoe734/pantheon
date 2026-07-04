# AG-DYNUI-PROD-006 - Hosted E2E Publish Gate

Owner: Codex
Reviewer: Claude2
Depends on: `AG-DYNUI-PROD-001`, `AG-DYNUI-PROD-002`, `AG-DYNUI-PROD-003`, `AG-DYNUI-PROD-004`, `AG-DYNUI-PROD-005`

## Problem

The previous closure treated partial route/BFF recovery as production-level.
The final gate must instead prove the design-pack dynamic UI on the hosted
route end to end.

## Scope

- Write hosted E2E for the Winner Branch flow:
  Strategy Workshop input, reconstruction card, readiness, join Trading Room,
  workspace proposal preview, accept, grid edit, widget revision, before/after,
  keep original and add modified copy, version history, and rollback.
- Capture desktop and mobile screenshots.
- Confirm no direct order, capital binding, broker, RuntimeBinding, or
  Management leakage.
- Confirm CI, deploy, and live probes pass after merge.

## Acceptance

- E2E passes against the hosted dev FE and live BFF.
- Screenshot artifacts match the design-pack layout and do not show the old
  empty Trading Desk shell.
- Publish checklist includes PR numbers, merge commits, deploy run IDs, and
  live probe artifacts.
- The task is not closed until the PRs are merged and hosted validation passes.

## Evidence / Publish Checklist (2026-07-04, Claude)

E2E spec: `execute-plans/e2e/agora-winner-branch-hosted.spec.ts` (desktop +
`mobile-chromium` Pixel 5 Playwright projects). Covers Strategy Workshop
intake, readiness, join Trading Room, workspace proposal preview, accept,
grid edit, widget revision (before/after + keep-original-add-copy), version
history, rollback, and asserts no order/capital/broker/RuntimeBinding/
Management leakage in warnings text or BFF request paths.

Disclosure (also emitted at runtime in the summary JSON): steps 1-3 are live
against the real hosted dev BFF (dev-login, Strategy Workshop list, a
workshop created for real via an authenticated BFF POST, Trading Room default
entry with zero ready strategies today, confirming the servant/persona
pipeline that would progress a workshop to `trading_room` readiness is not
wired end-to-end yet — a known, disclosed Global Loop Autopilot gap, not a
fabrication). Steps 4-10 exercise the real, unmodified `TradingRoomPage` /
`WorkspaceGridEditor` / `WorkspaceWidgetRevisionDrawer` product code against
`page.route()` fixtures shaped exactly like the real `TradingRoom*` BFF
contracts, following the same disclosed-mock precedent as AG-DYNUI-PROD-003's
hosted evidence.

Running the E2E against the real hosted dev BFF surfaced two real production
bugs, both fixed in the same PR (not test-only workarounds):

1. `getWorkshopCompleteness` / `getWorkshopReadiness`
   (`execute-plans/src/lib/bff-v1/agora/workshops.ts`) crashed
   `StrategyCompletenessRail` on the live BFF's `{"data": null}`
   "not yet assessed" envelope (200 OK) — `dataFrom()`'s `root.data ?? value`
   fallback treats an explicit `null` as absent and falls through to a
   truthy placeholder, whose `.dimensions.length` access then threw.
2. The widget "⋯" menu button and its dropdown in `WorkspaceGridEditor.tsx`
   are nested inside the `react-grid-layout` drag handle
   (`.workspace-widget-drag-handle`), so every click there was captured by
   the drag library before React's `onClick` ran — grid edit, widget
   revision, and version-history/rollback interactions silently no-opped.
   Fixed with `onMouseDown` `stopPropagation` on the menu/panel wrapper.

Verification run before PR:
- `npx vitest run src/lib/bff-v1/agora/workshops.test.ts` — 9 passed
- `npx vitest run src/agora` — 272 passed
- `npm run build`, `npx eslint` on touched files — clean
- E2E validated against a local preview build of the fixed source (FE) +
  the real hosted live BFF (unchanged) for both `chromium` and
  `mobile-chromium` projects — both passed end-to-end, all 12 screenshots
  and the disclosure summary JSON produced per viewport
  (`/tmp/agora-dynui-prod-e2e-*-{desktop,mobile}.png`,
  `/tmp/agora-dynui-prod-e2e-summary-{desktop,mobile}.json`). This step was
  necessary because the hosted dev FE only picks up the fix after merge +
  auto-deploy (`.github/workflows/pantheon-dev-fe-deploy.yml` deploys on
  every push to `execute-plans` `dev`), so a hosted run before merge would
  still have hit the pre-fix bundle.

Publish checklist:
- [ ] PR: `ajoe734/execute-plans#177` (`task/AG-DYNUI-PROD-006` -> `dev`) — opened 2026-07-04, pending `integration-gate` checks
- [ ] Merge commit SHA: _pending merge_
- [ ] `Pantheon Dev FE Deploy` run ID / deployed SHA: _pending — auto-triggers on push to `execute-plans` `dev`_
- [ ] Post-deploy hosted E2E re-run (desktop + mobile) against
      `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io` +
      `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`: _pending_
- [ ] Live probe artifacts: _pending_
