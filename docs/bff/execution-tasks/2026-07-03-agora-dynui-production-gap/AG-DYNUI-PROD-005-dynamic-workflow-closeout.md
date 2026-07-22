# AG-DYNUI-PROD-005 - Dynamic Workflow Closeout

Owner: Claude
Reviewer: Codex2
Depends on: `AG-DYNUI-PROD-002`, `AG-DYNUI-PROD-003`, `AG-DYNUI-PROD-004`

## Problem

Workspace proposal preview, grid editor, and widget revision components exist,
but production readiness requires proving that the whole workflow is wired
through strict BFF contracts in the hosted app.

## Scope

- Verify and repair the full flow: proposal generation, proposal acceptance,
  workspace load, layout patch, widget revision proposal, apply, keep-copy,
  version history, and rollback.
- Ensure every mutation uses idempotency, optimistic concurrency, and
  Agora/user scope isolation.
- Confirm WidgetSpec/ChartSpec rendering only uses the allowlisted registry.
- Remove or block any fallback that simulates success without BFF persistence.

## Acceptance

- All V11 dynamic workflow operations work through BFF in strict live mode.
- Tests cover success, conflict, permission, stale etag, and unsupported widget
  paths.
- No arbitrary React/JavaScript/HTML is generated or injected.
- Closeout records backend and execute-plans PRs, merge SHAs, and hosted proof.

## Closeout

Status: done.

### Dependency gate

`AG-DYNUI-PROD-002`, `AG-DYNUI-PROD-003`, and `AG-DYNUI-PROD-004` are all
archived `done` (`python3 scripts/ai_status.py show <id>` for each returns
`terminal_status: done`, `terminal_outcome: completed`).

### Implementation PR

- `ajoe734/execute-plans` PR #176, "AG-DYNUI-PROD-005: wire workshop route
  handoff" — merge commit `eaad3fa90d7c55a4476ed8dcda0063457933a1cc`, tip
  commit `0089eea` on `dev`. Confirmed on `dev` with
  `git merge-base --is-ancestor 0089eea origin/dev`.
- Wires `onAddToTradingRoom` from the Strategy Workshop route into
  `/agora/trading-room`, and mounts `WorkspaceProposalPreview`,
  `WorkspaceGridEditor`, and `WorkspaceWidgetRevisionDrawer` in
  `TradingRoomPage.tsx` against the `agora.trading.v1` BFF surface
  (`src/lib/bff-v1/agora/tradingRoom.ts`).
- No pantheon-repo backend change was required for this task: the
  `services/control-plane/bff/agora/trading_room/router.py` and
  `store.py` routes for proposal generate/accept, workspace get, layout
  patch, widget-revision propose/apply/keep-copy, version list, and
  rollback already existed and are exercised unchanged by the newly
  wired frontend.

### Verification run for this closeout

- Backend: `python3 -m pytest bff/agora/trading_room/test_trading_room.py -q`
  (from `services/control-plane/`) — 45 passed. Covers idempotency-key
  enforcement, `If-Match` optimistic concurrency (stale-etag rejection),
  cross-user Agora/user scope isolation, widget-allowlist enforcement and
  code-injection rejection, widget-revision apply/keep-copy, and
  version-history rollback.
- Frontend: `npx vitest run src/agora/pages/trading-room/TradingRoomPage.test.tsx`
  (from the real `ajoe734/execute-plans` `task/AG-DYNUI-PROD-005` checkout,
  HEAD `0089eea`) — 56 passed. Covers proposal generation/accept, layout
  patch (move/resize/remove/restore/duplicate/add-registered-widget) with
  workspace ETag + idempotency key, widget-revision proposal
  apply/keep-copy/cancel, version rollback with current ETag, and typed
  403/409/422/501 failures that surface without any fake-success fallback.
- Hosted deploy/BFF-wiring probe:
  `docs/deployment/evidence/ag-dynui-prod-005/20260704T155514Z/` —
  confirms the hosted dev FE serves the bundle matching commit `0089eea`
  and that `/agora/trading-room` issues its BFF reads against the
  intended live BFF host (`GET /bff/agora/trading-room` and
  `GET /bff/agora/trading-room/decision-events` both `200`, zero hits on
  the old BFF host, zero failed requests, zero console errors).

### Scope clarifications resolved during closeout

- **Dashboard-family components are out of scope.**
  `src/agora/dashboard/DashboardProposalPreview.tsx`,
  `DashboardGridEditor.tsx`, and `DashboardChangeLog.tsx` (with their own
  `src/lib/bff-v1/agora/dashboard.ts` client) are a distinct, pre-existing
  Agora Dashboard surface, separate from the Trading Room `Workspace*`
  components this task's artifacts name. This task's scope is limited to
  the Trading Room `agora.trading.v1` workspace workflow; the Dashboard
  surface is unaffected and untouched.
- **`GET /bff/agora/trading-room/stream` remains an intentional stub.**
  The route returns a self-documented empty SSE response
  ("Full typed-event streaming is deferred pending SSE infrastructure
  task."). Real-time push is not part of this task's acceptance criteria
  (proposal, accept, workspace load, layout patch, widget revision,
  version history, rollback) and is left for a future SSE-infrastructure
  task.
- **Full authenticated hosted E2E with screenshots is owned by
  `AG-DYNUI-PROD-006`.** PR #176's own description defers the
  interactive hosted walkthrough (desktop/mobile screenshots across
  proposal → accept → grid edit → widget revision → version history →
  rollback) to `AG-DYNUI-PROD-006` (Hosted Winner Branch E2E publish
  gate), which depends on this task and is the next wave in the packet's
  execution order. This closeout's hosted evidence is limited to
  deploy-freshness + BFF-host-wiring confirmation, consistent with that
  division of labor.

### Residual risk

- SSE-based real-time push for Trading Room events is not implemented;
  the UI relies on request/response reads only. Owner: whichever future
  task stands up SSE infrastructure. No expiry — tracked as a known gap,
  not a regression, since it was never in this task's acceptance
  criteria.
