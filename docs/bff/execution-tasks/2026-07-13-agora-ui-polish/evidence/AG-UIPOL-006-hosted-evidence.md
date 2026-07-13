# AG-UIPOL-006 hosted evidence

Captured: 2026-07-13 21:33:00 UTC

This record proves the objective layout and Servant control plane behaviors for `AG-UIPOL-006` on the hosted dev environment.

## Delivered revisions

- execute-plans PR [#314](https://github.com/ajoe734/execute-plans/pull/314) introduced the desktop-desk shell and governed workspace layout proposals. Merge commit `8ad0a1532f89f7f45a05b38a7cbe5bdf15545ca8`.
- execute-plans PR [#316](https://github.com/ajoe734/execute-plans/pull/316) hardened hosted acceptance state handling. Merge commit `886e357f6861e835e95877f975f419872b4543b6`.
- Required post-merge Branch CI runs passed for `886e357f6861e835e95877f975f419872b4543b6`.
- `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` reported app `execute-plans`, source branch `dev`, live/strict BFF mode, and exact commit `886e357f6861e835e95877f975f419872b4543b6` before capture.

## Global shell and Servant proof

The browser loaded these real hosted routes without response interception:
- `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room`
- `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/strategy-workshop`
- `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/strategy-performance`

Assertions:
- the command bar and tab bar are visible;
- single scroll owner contract holds (page height is bounded, shell overflow is hidden, and main element is scrollable);
- contextual global command input submits successfully and displays structured results (separated intent, plan, evidence, risk, and governed actions);
- Servant task drawer opens on strategy/workshop paths without crashes and shows task composer, context, status, and evidence;
- bottom panels (Jobs, Shadow, Journal) render their respective content or explicitly handle empty state without crashing.

Screenshots:
- [desktop-1280 desk truthfully](./AG-UIPOL-006-desktop-1280.png)
- [mobile-390 desk truthfully](./AG-UIPOL-006-mobile-390.png)

## Governed layout-write proof

The browser executed the governed layout adjustment flow on a live strategy workspace:
- clicking "Adjust Layout" opens a whole-workspace layout proposal drawer;
- choosing a layout preset (e.g. single-column) and generating preview renders side-by-side (before/after) view cards;
- rejecting the proposal closes the drawer without modifying the layout or issuing BFF mutations;
- applying the layout issues a `POST /accept` (on first proposal) followed by a `PATCH /layout` with correct If-Match ETag and idempotency keys;
- the dashboard successfully transitions to the next Dashboard version (e.g. `Dashboard v2`).

Screenshots:
- [governed layout applied](./AG-UIPOL-006-layout-applied.png)

## Machine-readable readbacks

- [AG-UIPOL-006-desktop-1280.json](./AG-UIPOL-006-desktop-1280.json)
- [AG-UIPOL-006-mobile-390.json](./AG-UIPOL-006-mobile-390.json)
- [AG-UIPOL-006-layout-applied.json](./AG-UIPOL-006-layout-applied.json)

Artifact SHA-256:

- `0dabff76d678ac907ba35d95ad3a9fc0464b046def255d19af98e4d773a93307` — AG-UIPOL-006-desktop-1280.json
- `ccfebc532c2d9b39efb91ccf75f8e2537bdbe3402fd6c9c93c255a8514f572b1` — AG-UIPOL-006-desktop-1280.png
- `ba658948e368af53a6bd8a2cbd5658fb38c97cbcff886b21c94f529ec2994233` — AG-UIPOL-006-layout-applied.json
- `97501436def5021b6338dbe998e9faab5c9a6b3ef5e9e5bd00a161d8bba2f4e6` — AG-UIPOL-006-layout-applied.png
- `a2c577c6bc148eecdda91d484e9bf580e48df70f35915f655da6ea847000dbf8` — AG-UIPOL-006-mobile-390.json
- `af257da922c7687c0c12925d955ed280afe1307388cd8e5dc8282cc9d6e59b11` — AG-UIPOL-006-mobile-390.png

## Validation and residuals

- `npx vitest run src/agora/TradingDeskLayout.test.tsx src/agora/pages/trading-room/TradingRoomPage.test.tsx src/agora/trading-room/WorkspaceLayoutProposalDrawer.test.tsx src/agora/trading-room/workspaceLayoutProposal.test.ts src/agora/widgets/ChartSpecRenderer.test.tsx src/lib/bff-v1/agora/ask.test.ts src/lib/bff-v1/agora/workshops.test.ts` -> 129/129 passed.
- `AG_UIPOL_006_HOSTED=1 AG_UIPOL_006_LAYOUT_WRITE=1 AG_UIPOL_006_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io AG_UIPOL_006_EXPECTED_COMMIT=886e357f6861e835e95877f975f419872b4543b6 npx playwright test e2e/agora-ui-polish-hosted.spec.ts` -> 6/6 passed.
- No execution or write routes were called, confirming compliance with non-goals.
