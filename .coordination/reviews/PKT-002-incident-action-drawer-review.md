# PKT-002 Incident Action Drawer Review Packet

## Date

2026-04-24

## Reviewer

Codex

## Prior Context

- Pantheon's earlier 2026-04-24 review left this packet open for two
  front-owned reasons: PKT-005 initial-read-before-stream sequencing and a
  stale feedback bundle that still described runtime verification as the only
  remaining follow-up.
- The current remote-visible request pair on `origin/pkt-004-detail-fix` now
  points to reviewed source commit
  `82b1ceb76a7de4f8e49b5f08c0b9fcb865f52bd9` through publish commit
  `b146ba7e40286753aa7419740dd695cdbbf6e5f5`.
- Diffing `82b1ceb..b146ba7` over the PKT-002 incident-action-drawer paths now
  changes only the two request files, so `b146ba7` is a replay publish commit
  rather than a new UI snapshot.

## Evidence Reviewed

- `../front-ai-trading-system/.coordination/requests/PKT-002-incident-action-drawer-ui-done.yaml`
- `../front-ai-trading-system/.coordination/requests/PKT-002-incident-action-drawer-frontend-feedback.yaml`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-action-drawer/API_GAP_REQUESTS.json`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-action-drawer/LOVABLE_CHANGE_FEEDBACK.md`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-action-drawer/UI_DECISIONS.md`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-action-drawer/QA_STATUS.md`
- `../front-ai-trading-system/src/components/operator/IncidentActionDrawer.tsx`
- `../front-ai-trading-system/src/pages/operator/IncidentActionDrawerPage.tsx`
- `../front-ai-trading-system/src/lib/bffClient.ts`
- `../front-ai-trading-system/src/pages/operator/types.ts`
- `../front-ai-trading-system/src/App.tsx`
- `docs/bff/PKT-002-incident-action-drawer.md`
- `docs/screens/PKT-002-incident-action-drawer.md`
- `docs/examples/PKT-002-incident-action-drawer.json`
- `docs/pantheon-handoffs/PKT-005-sse-substrate/FRONTEND_CHANGE_SPEC.md`

## Findings

No blocking findings remain.

## Verified Positives

- `origin/pkt-004-detail-fix` now resolves to
  `b146ba7e40286753aa7419740dd695cdbbf6e5f5`, and that publish commit contains
  the current PKT-002 action-drawer request pair.
- The published request pair now pins `source_commit` to
  `82b1ceb76a7de4f8e49b5f08c0b9fcb865f52bd9`, and diffing
  `82b1ceb..b146ba7` over the PKT-002 action-drawer paths shows only the two
  request files changed for republish.
- The route host remains truthful and now satisfies the PKT-005 sequencing
  rule: `IncidentActionDrawerPage.tsx:89-142` opens
  `/api/v1/kill-switch/updates` only after `initialSnapshotReady`, and
  `IncidentActionDrawer.tsx:348-382` now raises
  `onInitialSnapshotReadyChange(true)` only after
  `operatorApi.getKillSwitchStatus()` resolves.
- The drawer stays on the published PKT-002 endpoints only:
  `bffClient.ts:1063-1069` uses `GET /api/v1/kill-switch/status`,
  `bffClient.ts:1182-1188` uses `POST /api/v1/operator/commands`, and
  `IncidentActionDrawer.tsx:229-291` still builds the published
  command-discriminated `Runtime`-targeted payloads.
- The operator route remains mounted at
  `/operator/incidents/:incidentId/action` in `App.tsx:202-204`.
- Static verification passed in an isolated checkout of publish commit
  `b146ba7e40286753aa7419740dd695cdbbf6e5f5`:
  - `./node_modules/.bin/tsc --noEmit --pretty false`
  - `npx eslint src/components/operator/IncidentActionDrawer.tsx src/pages/operator/IncidentActionDrawerPage.tsx src/lib/bffClient.ts src/pages/operator/types.ts src/App.tsx`
  - `npm run build`
- Pantheon's targeted PKT-002 command and kill-switch smoke slice passed:
  `python3 -m pytest -q services/control-plane/bff/smoke_test_incident.py -k 'in05_pause_execution_command_schema or in05_issue_risk_off_command_schema or in05_liquidate_all_command_schema or in05_hard_rollback_command_schema or in05_issue_safe_mode_command_schema or in05_kill_switch_status or in05_kill_switch_unavailable_disables_actions or in05_kill_switch_admin_only'`
  returned `8 passed, 12 deselected`.
- The active local runtime at `http://127.0.0.1:18001` now exposes all three
  required live routes in `/openapi.json`:
  - `GET /api/v1/kill-switch/status`
  - `GET /api/v1/kill-switch/updates`
  - `POST /api/v1/operator/commands`
- Live HTTP probes confirm the current auth and write surfaces:
  - operator-only auth returns `403 INSUFFICIENT_ROLE` on
    `GET /api/v1/kill-switch/status`
  - admin-auth `GET /api/v1/kill-switch/status` returns a contract-valid
    degraded/unavailable envelope rather than a transport failure
  - admin-auth `POST /api/v1/operator/commands` accepts published
    `PauseExecution` and `IssueSafeMode` envelopes and returns receipts with
    `status = accepted`
- Live SSE substrate verification also passed:
  - `POST /api/v1/internal/sse/publish?event_type=kill_switch_activated`
    published `evt-1777027991-115d005e`
  - `GET /api/v1/kill-switch/updates` replayed that event as a proper SSE block
    with `id`, `event`, and JSON `data`
  - the front repo's `SseReconciler` buffered the same event before hydration
    and flushed it after `setHydrated(true)`, matching the committed host-page
    wiring for the drawer

## Decision

`PKT-002-incident-action-drawer` is **approved for closeout**.

The current request pair is replay-clean, the front-owned PKT-005 sequencing
follow-up is resolved in the reviewed source commit, the PKT-002 command schema
still passes Pantheon smoke verification, and the live kill-switch read/write
plus SSE substrate can now be exercised without inventing alternate endpoints or
shadow state.

## Residual Risk

- Live browser QA against a running Pantheon BFF was not rerun in this closeout
  step.
- The current live runtime happened to return a degraded/unavailable kill-switch
  snapshot during review, so a later ops QA pass should still re-check the
  fallback and fully-unavailable presentation in browser. That does not reopen
  the contract or coordination loop for this cycle.
