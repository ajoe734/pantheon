# AG-UIPOL-005 hosted evidence

This directory is the point-in-time hosted evidence used by
`../parity-matrix.md`. It is not a claim that Agora has reached design parity.

## Deployment pin

- Frontend: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- `execute-plans` deploy SHA:
  `1a4265c770825818396badbdf960ec2deaa44763`
- Deployed: `2026-07-13T12:31:18Z`
- Mode: `live` / `strict`
- Pin readback: `deployment.json`

Every screenshot in this directory was captured against that deployment. A
`nocache=<full SHA>` query was used for the supplemental captures.

## Primary live workflow capture

The deployed Winner Branch hosted gate was run from a clean detached
`execute-plans` worktree at the deployed SHA. The desktop run used Playwright's
`Desktop Chrome` project; the narrow run used its `Pixel 5` project.

```text
PANTHEON_FE_BASE_URL=<hosted FE> \
PANTHEON_BFF_BASE_URL=<hosted BFF> \
BFF_AUTH_TOKEN=<redacted dev bearer> \
PANTHEON_AUDIT_OUT_DIR=<this directory> \
npx playwright test e2e/agora-winner-branch-hosted.spec.ts --project=chromium --reporter=line

PANTHEON_FE_BASE_URL=<hosted FE> \
PANTHEON_BFF_BASE_URL=<hosted BFF> \
BFF_AUTH_TOKEN=<redacted dev bearer> \
PANTHEON_AUDIT_OUT_DIR=<this directory> \
npx playwright test e2e/agora-winner-branch-hosted.spec.ts --project=mobile-chromium --reporter=line
```

Both final runs passed (`1 passed` each). The desktop workflow used strategy
`full003-live-1783268175-13279b`; the narrow workflow used the next ready
strategy, `full003-postdeploy-1783268578-f4b6f0`, because the desktop run had
already accepted the first strategy's proposal. Candidate ordering was changed
only in the temporary detached test worktree for that narrow rerun. No
production source or deployed bundle was changed.

The gate exercised and archived these states for both viewports:

1. ready Strategy Workshop;
2. generated workspace proposal;
3. accepted seven-view workspace;
4. unsaved layout edit;
5. saved dashboard v2;
6. widget revision proposal;
7. accepted keep-copy dashboard v3;
8. version history;
9. rollback applied.

The `ag-dynui-full-006-live-summary-{desktop,mobile}.json` files are the
readback ledger. They record the exact live request paths and statuses. The
test created and accepted a workspace proposal, saved a layout revision,
created and accepted a widget revision, then rolled back. It did not call any
order, broker, capital-binding, or runtime-binding write route.

## Accepted seven-view sweep

The deployed frontend keeps an accepted workspace only in the current React
session; revisiting a strategy creates a fresh preview rather than reading an
already accepted workspace. To capture every accepted View A–G, a supplemental
script therefore generated and accepted one proposal in each 1440x960 and
390x844 session, then only switched tabs. `workspace-views-capture.json`
records all requests and the fourteen screenshot basenames. Each session made
exactly two writes: `POST .../trading-room/proposals` and `POST .../accept`.
There were no layout, widget-revision, rollback, order, broker, capital, or
runtime-binding writes, and both sessions recorded zero console errors.

An initial sweep incorrectly waited for a persisted workspace before clicking
Accept. Because route entry itself creates a preview at this deploy, that
failed attempt may have created unaccepted preview proposals; it did not click
an acceptance or any other mutation control. This limitation is recorded here
rather than hidden from the evidence ledger.

## Supplemental captures

`performance-{desktop,narrow}.png` and
`servant-drawer-placeholder-{desktop,narrow}.png` add the Performance tab and
global Servant drawer at 1440x960 and 390x844 CSS pixels. The `-viewport`
variants retain the first viewport when the full-page screenshot is very tall.
`supplemental-recapture.json` records URLs, viewport sizes, console errors,
failed requests, and readiness.

The desktop Performance request encountered a CORS failure on its first load,
then returned a complete surface after a bounded reload. The JSON deliberately
retains both attempts' console/request history; the final screenshot is the
complete surface. The independently captured narrow surface loaded completely.

The original contextual-drawer attempt is retained as
`servant-drawer-{desktop,narrow}.png` with `supplemental-capture.json`. Opening
the drawer on `/agora/strategy-workshop/:workshopId` produced the deployed
Agora route error boundary (`Cannot read properties of undefined (reading
'title')`). The healthy placeholder capture uses `/agora/strategy-workshop`:
it proves the drawer shell/overlay exists while also showing that the shipped
drawer contains only a contextual-state prompt, not the designed Servant task
workflow.

The older hosted-gate and supplemental JSON files retain their absolute
capture-time `/tmp/...` paths as raw provenance. Use the basename catalogue
below when reading the evidence from another clone.

## Screenshot index

| State | Desktop | Narrow |
|---|---|---|
| Ready workshop | `ag-dynui-full-006-01-live-ready-workshop-desktop.png` | `ag-dynui-full-006-01-live-ready-workshop-mobile.png` |
| Workspace proposal | `ag-dynui-full-006-02-live-workspace-proposal-desktop.png` | `ag-dynui-full-006-02-live-workspace-proposal-mobile.png` |
| Accepted workspace | `ag-dynui-full-006-03-live-workspace-accepted-desktop.png` | `ag-dynui-full-006-03-live-workspace-accepted-mobile.png` |
| Unsaved grid | `ag-dynui-full-006-04-live-grid-unsaved-desktop.png` | `ag-dynui-full-006-04-live-grid-unsaved-mobile.png` |
| Saved dashboard v2 | `ag-dynui-full-006-05-live-grid-saved-v2-desktop.png` | `ag-dynui-full-006-05-live-grid-saved-v2-mobile.png` |
| Widget revision preview | `ag-dynui-full-006-06-live-widget-revision-preview-desktop.png` | `ag-dynui-full-006-06-live-widget-revision-preview-mobile.png` |
| Widget revision v3 | `ag-dynui-full-006-07-live-widget-revision-v3-desktop.png` | `ag-dynui-full-006-07-live-widget-revision-v3-mobile.png` |
| Version history | `ag-dynui-full-006-08-live-version-history-desktop.png` | `ag-dynui-full-006-08-live-version-history-mobile.png` |
| Rollback applied | `ag-dynui-full-006-09-live-rollback-applied-desktop.png` | `ag-dynui-full-006-09-live-rollback-applied-mobile.png` |
| Workspace View A — Strategy Overview | `workspace-view-01-strategy_overview-desktop.png` | `workspace-view-01-strategy_overview-narrow.png` |
| Workspace View B — Candidates & Entry | `workspace-view-02-candidates_entry-desktop.png` | `workspace-view-02-candidates_entry-narrow.png` |
| Workspace View C — Winner Branch Intelligence | `workspace-view-03-winner_branch_intelligence-desktop.png` | `workspace-view-03-winner_branch_intelligence-narrow.png` |
| Workspace View D — Relationships & Migration | `workspace-view-04-related_party_flow_migration-desktop.png` | `workspace-view-04-related_party_flow_migration-narrow.png` |
| Workspace View E — Event Lead | `workspace-view-05-event_lead-desktop.png` | `workspace-view-05-event_lead-narrow.png` |
| Workspace View F — Positions & Exit | `workspace-view-06-positions_exit-desktop.png` | `workspace-view-06-positions_exit-narrow.png` |
| Workspace View G — Evidence & Monitoring | `workspace-view-07-evidence_monitoring-desktop.png` | `workspace-view-07-evidence_monitoring-narrow.png` |
| Performance | `performance-desktop.png` | `performance-narrow.png` |
| Servant placeholder | `servant-drawer-placeholder-desktop.png` | `servant-drawer-placeholder-narrow-viewport.png` |
| Contextual Servant failure | `servant-drawer-desktop.png` | `servant-drawer-narrow.png` |
