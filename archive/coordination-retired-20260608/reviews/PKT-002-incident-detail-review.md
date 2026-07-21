# PKT-002 Incident Detail Review Packet

## Date

2026-04-24

## Reviewer

Codex

## Prior Context

- Pantheon's earlier 2026-04-24 review left this packet open for four
  front-owned reasons: non-replayable transport, untruthful operator
  navigation, missing `opened_at`, and missing per-action rationale copy.
- The coordination bus payload that reopened this check cited source commit
  `40bbe670433d86143d36515bb107cae5977d30ad`.
- The currently published request pair on `origin/pkt-004-detail-fix` now
  points to `43691dae0847423b5080db00781dac7fec452c59`. Diffing
  `40bbe67..43691da` over the incident-detail UI and feedback paths is empty,
  so the later SHA is a transport-only superseding snapshot for this slice
  rather than a new incident-detail code change.

## Evidence Reviewed

- `../front-ai-trading-system/.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
- `../front-ai-trading-system/.coordination/requests/PKT-002-incident-detail-frontend-feedback.yaml`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-detail/API_GAP_REQUESTS.json`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-detail/LOVABLE_CHANGE_FEEDBACK.md`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-detail/UI_DECISIONS.md`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-detail/QA_STATUS.md`
- `../front-ai-trading-system/src/pages/operator/IncidentDetail.tsx`
- `../front-ai-trading-system/src/pages/operator/IncidentActionDrawerPage.tsx`
- `../front-ai-trading-system/src/components/operator/IncidentActionDrawer.tsx`
- `../front-ai-trading-system/src/lib/sseClient.ts`
- `../front-ai-trading-system/src/lib/sseReconciler.ts`
- `../front-ai-trading-system/src/pages/operator/types.ts`
- `docs/bff/PKT-002-incident-detail.md`
- `docs/screens/PKT-002-incident-detail.md`
- `docs/examples/PKT-002-incident-detail.json`

## Findings

No blocking findings remain.

## Verified Positives

- `origin/pkt-004-detail-fix` now resolves to
  `dd1836416fa4ef8b695bcb94cee77dc96273ed31`, and that publish commit contains
  the current incident-detail request pair, feedback bundle, and reviewed UI
  files.
- The published request pair now pins `source_commit` to
  `43691dae0847423b5080db00781dac7fec452c59`, and diffing
  `43691da..dd18364` over the incident-detail paths shows only the two request
  files changed for republish.
- `IncidentDetail.tsx:196-200` still performs the initial read only through
  `operatorApi.getIncidentResponse()` on
  `GET /api/v1/operator/incident-response/{incident_id}`.
- The detail page now routes back to `/operator/incidents` and opens the
  action drawer on `/operator/incidents/:incidentId/action` via the shared
  operator route host (`IncidentDetail.tsx:462-475`, `516-519`, `863-875`;
  `App.tsx:184-186`).
- The incident summary renders `opened_at`
  (`IncidentDetail.tsx:631-637`), and the action strip renders per-action
  rationale copy (`IncidentDetail.tsx:843-857`).
- The PKT-005 live-update overlay remains composed-view-first and incremental:
  SSE wiring starts only after the composed response exposes `runtime_id`, and
  accepted runtime, incident, and kill-switch events update visible fields or
  trigger refreshes rather than replacing the composed view wholesale
  (`IncidentDetail.tsx:281-458`).
- The remaining HardRollback target-artifact note stays non-blocking:
  `API_GAP_REQUESTS.json` records the request with `"blocking": false`, while
  the shared drawer explicitly requires `rollbackArtifactId` for
  `HardRollback` and disables the button when host context does not supply it
  (`IncidentActionDrawer.tsx:270-276`, `409-437`, `773-785`).
- Targeted verification passed again:
  - `./node_modules/.bin/tsc --noEmit --pretty false`
  - `npx eslint src/pages/operator/IncidentDetail.tsx src/pages/operator/IncidentActionDrawerPage.tsx src/components/operator/IncidentActionDrawer.tsx src/pages/operator/types.ts src/lib/sseClient.ts src/lib/sseReconciler.ts src/App.tsx`
  - `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q -k 'in02_incident_detail or composed_incident_response or in05_kill_switch_status or in05_kill_switch_unavailable_disables_actions'` -> `5 passed, 15 deselected`

## Decision

`PKT-002-incident-detail` is **approved for closeout**.

The current request pair is replay-clean, the detail screen remains aligned
with the canonical PKT-002 composed read and PKT-005 SSE boundary rules, and
the only remaining note is the explicitly non-blocking HardRollback
target-artifact publication follow-up.

## Residual Risk

- Live browser QA was not rerun in this closeout step.
- HardRollback from Incident Detail will remain intentionally disabled until a
  later Pantheon contract publishes canonical rollback target context for this
  host surface.

## 2026-04-24 `82b1ceb` Redispatch Addendum

Pantheon re-reviewed the currently published PKT-002 Incident Detail handoff
after `origin/pkt-004-detail-fix` republished the request pair against source
commit `82b1ceb76a7de4f8e49b5f08c0b9fcb865f52bd9`.

### Verification Performed

- Verified `origin/pkt-004-detail-fix` now resolves to
  `b146ba7e40286753aa7419740dd695cdbbf6e5f5`.
- Verified publish commit `b146ba7e40286753aa7419740dd695cdbbf6e5f5`
  publishes:
  - `.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
  - `.coordination/requests/PKT-002-incident-detail-frontend-feedback.yaml`
  - `docs/pantheon-feedback/PKT-002-incident-detail/`
- Verified both request bodies in that publish commit now point
  `source_commit` at `82b1ceb76a7de4f8e49b5f08c0b9fcb865f52bd9`.
- Verified the reviewed source delta relative to the previously approved
  incident-detail snapshot is limited to:
  - `src/components/operator/IncidentActionDrawer.tsx`
  - `src/pages/operator/IncidentActionDrawerPage.tsx`
  - `.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
  - `.coordination/requests/PKT-002-incident-detail-frontend-feedback.yaml`
- Verified diffing
  `82b1ceb76a7de4f8e49b5f08c0b9fcb865f52bd9..b146ba7e40286753aa7419740dd695cdbbf6e5f5`
  over the incident-detail paths changes only the two request files.
- Verified the action-drawer host route now delays the PKT-005 kill-switch SSE
  stream until the embedded drawer reports a fresh initial
  `GET /api/v1/kill-switch/status` snapshot, keeping live updates sequenced
  behind the canonical operator route host context
  (`IncidentActionDrawerPage.tsx:40-123`, `206-214`;
  `IncidentActionDrawer.tsx:348-382`).
- Verified the shared drawer still fetches `GET /api/v1/kill-switch/status`
  fresh on open and refresh, and still requires `rollbackArtifactId` host
  context before enabling `HardRollback`
  (`IncidentActionDrawer.tsx:118-119`, `348-382`, `443-445`, `798-803`).
- Re-ran clean verification in `../front-ai-trading-system`:
  - `./node_modules/.bin/tsc --noEmit --pretty false`
  - `npx eslint src/pages/operator/IncidentDetail.tsx src/pages/operator/IncidentActionDrawerPage.tsx src/components/operator/IncidentActionDrawer.tsx src/pages/operator/types.ts src/lib/sseClient.ts src/lib/sseReconciler.ts src/App.tsx`
  - `npm run build`
- Re-ran the Pantheon incident-detail smoke slice:
  - `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q -k 'in02_incident_detail or composed_incident_response or in05_kill_switch_status or in05_kill_switch_unavailable_disables_actions'` -> `5 passed, 15 deselected`

### Findings

No blocking findings remain.

### Decision

`PKT-002-incident-detail` remains **approved for closeout**.

The current transport is replay-clean at publish commit
`b146ba7e40286753aa7419740dd695cdbbf6e5f5` with reviewed source commit
`82b1ceb76a7de4f8e49b5f08c0b9fcb865f52bd9`. The remaining HardRollback target
artifact request stays explicitly non-blocking.
