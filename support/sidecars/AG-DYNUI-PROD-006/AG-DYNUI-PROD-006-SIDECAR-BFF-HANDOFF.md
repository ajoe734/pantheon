# AG-DYNUI-PROD-006 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-006` |
| Parent title | Hosted Winner Branch E2E publish gate |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Sidecar task | `AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Claude` / `Claude2` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |

This is a support artifact only. It does not define canonical truth, update L1
contracts, edit BFF/runtime code, edit frontend code, or approve the parent
implementation. Parent ownership and review decide how to absorb this packet.

---

## 1. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_dynui_prod_006_sidecar_bff_handoff.md` | Sidecar scope is support-only: BFF query gap, operator journey, frontend handoff materials; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Meaningful support-doc work should be committed through the task workflow with explicit scope. |
| `.orchestrator/skills/task-closeout-finalization.md` | Closeout applies now: this sidecar is `review_approved` and the owner must make the approved support state durable before `done`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF` | Sidecar is `review_approved`, owner `Claude`, reviewer `Claude2`, `depends_on` is `AG-DYNUI-PROD-001` and `AG-DYNUI-PROD-004` only (not the full parent dependency set), with `review_notes_zh` confirming the route inventory, gap table, and dependency snapshot were verified against current code. |
| `python3 scripts/ai_status.py show AG-DYNUI-PROD-006` | Parent is `todo`, owner `Codex`, reviewer `Claude2`, depends on all of `AG-DYNUI-PROD-001..005`; parent branch `task/AG-DYNUI-PROD-006` has not been created yet. |
| `python3 scripts/ai_status.py show AG-DYNUI-PROD-001` / `-004` (archive) | Both are `done`. PROD-001 merged in PR #2851 (source/deploy truth restored). PROD-004 merged in PR #2855 (Trading Room diagnostics + cache policy), with hosted probe already passing. |
| `python3 scripts/ai_status.py show AG-DYNUI-PROD-002` / `-003` / `-005` | PROD-002 is `review` (standalone Agora shell). PROD-003 is `review_approved` (dynamic default entry into Trading Room). PROD-005 is `todo` (dynamic workflow closeout: proposal preview, grid editor, widget revision drawer, version/rollback wiring) — this is the task that must land before the E2E flow in this packet has full frontend coverage. |
| `docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-006-hosted-e2e-publish-gate.md` | Parent scope: hosted E2E for the full Winner Branch flow (Strategy Workshop → readiness → join Trading Room → proposal preview → accept → grid edit → widget revision → before/after → keep-original-add-modified-copy → version history → rollback), desktop/mobile screenshots, and confirmation of no order/capital/broker/RuntimeBinding/Management leakage. |
| `services/control-plane/bff/agora/trading_room/router.py` | Backend route surface for the full flow already exists (see §3). |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` and `workshops.ts` | Frontend BFF clients currently wrap only a subset of the backend routes (see §3.2 gap table). |
| `execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx`, `execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx` | Current pages implement Strategy Workshop readiness gating and the Trading Room root aggregate/diagnostics; they do not yet render workspace proposal preview, grid editor, widget revision drawer, or version/rollback UI. |
| `execute-plans/playwright.config.ts`, `execute-plans/package.json` | `npm run e2e` runs `playwright test` with `testDir: "./e2e"`, which resolves to `execute-plans/e2e/`, not `execute-plans/tests/e2e/` (a separate Vitest-driven smoke suite). |
| `execute-plans/e2e/13-agora.spec.ts` | Existing Agora Playwright coverage to extend rather than replace. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned.

---

## 2. Handoff Summary

`AG-DYNUI-PROD-006` is the final production gate for the Winner Branch V10-to-V11
dynamic UI: it must prove the whole flow end-to-end against the hosted dev FE and
live BFF, not against imagined or static screenshots. The backend route surface
for every step in the flow already exists in
`services/control-plane/bff/agora/trading_room/router.py`. The frontend gap is
that the BFF client (`tradingRoom.ts`) and pages do not yet call most of those
routes — that wiring is the explicit scope of `AG-DYNUI-PROD-005`, which is still
`todo`.

Practically, this means:

1. `AG-DYNUI-PROD-006`'s hosted E2E cannot exercise proposal preview, accept,
   grid edit, widget revision, version history, or rollback until
   `AG-DYNUI-PROD-005` lands the frontend client calls and page components for
   those routes.
2. `AG-DYNUI-PROD-002` (standalone shell) and `AG-DYNUI-PROD-003` (dynamic
   default entry into Trading Room) are further upstream and currently
   `review` / `review_approved` — the hosted E2E route path assumptions in this
   packet depend on those two landing first.
3. This sidecar's own `depends_on` (`AG-DYNUI-PROD-001`, `AG-DYNUI-PROD-004`) is
   already satisfied, so this packet can be prepared now; the parent task itself
   is still blocked on `AG-DYNUI-PROD-002/003/005`.

The key handoff is therefore a **route inventory + gap matrix + operator journey
script** the parent owner can use once PROD-002/003/005 land, rather than a new
canonical contract.

---

## 3. BFF Query Surface And Gap Matrix

### 3.1 Backend routes already implemented (`services/control-plane/bff/agora/trading_room/router.py`)

| Step in Winner Branch flow | Route | Notes |
|---|---|---|
| Strategy Workshop readiness | `GET /bff/agora/workshops/{id}`, `GET /bff/agora/workshops/{id}/readiness` (via `workshops.ts: getWorkshop`, `getWorkshopReadiness`) | Already wired; gates `tradingRoomReady` on `highest_ready_gate === "trading_room"`. |
| Join Trading Room (per-strategy) | `GET /bff/agora/trading-room/strategies/{strategy_id}` | Returns `allowedActions` (`record_decision`, `submit_handoff`, `request_shadow`) and pending decision counts. |
| Root Trading Room aggregate | `GET /bff/agora/trading-room` | Already wired via `getTradingRoom()`. |
| Create workspace proposal preview | `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals` | Accepts `strategyVersion`, `personalizationHints`, `evidenceRefs`, `dataFreshness`; declarative WidgetSpec/ChartSpec only, no executable code accepted. Supports `Idempotency-Key`. |
| Read workspace proposal | `GET /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}` | Returns `ETag` for the proposal. |
| Accept workspace proposal | `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept` | Validates `expectedStatus === "preview"`; supports `Idempotency-Key`. |
| Read workspace (grid) | `GET /bff/agora/trading-room/workspaces/{workspace_id}` | — |
| Edit grid layout | `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/layout` | — |
| Add workspace view | `POST /bff/agora/trading-room/workspaces/{workspace_id}/views` (201) | — |
| Edit workspace view | `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/views/{view_id}` | — |
| Add widget | `POST /bff/agora/trading-room/workspaces/{workspace_id}/widgets` (201) | — |
| Edit widget | `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}` | — |
| Propose widget revision (before/after preview) | `POST /bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}/...` (revision-proposal creation, ~line 2294) | Returns `before_after_preview=True` payload. |
| Accept widget revision | `POST /bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept` | `acceptanceAction` supports `apply` and `keep_original_add_modified_copy` / `keep_original_and_add_modified_copy` / `add_modified_copy` / `keep_copy` — this is the exact "keep original and add modified copy" behavior the parent brief requires. Requires `If-Match`; supports `Idempotency-Key`. Rejects if the proposal is not `preview`, if scope mismatches, or if the widget/view no longer matches the proposal's captured `beforeSpec`/`viewId` (409). |
| List version history | `GET /bff/agora/trading-room/workspaces/{workspace_id}/versions` | Returns ordered version records and `latest_version_id`. |
| Rollback to a version | `POST /bff/agora/trading-room/workspaces/{workspace_id}/versions/{version_id}/rollback` | Requires `If-Match`; supports `Idempotency-Key`; re-validates every restored view before persisting; response includes new `ETag`, the restored workspace, the new version record, and `rollbackOfVersion`. |
| Decision events (existing, adjacent) | `GET /bff/agora/trading-room/decision-events`, `GET .../decision-events/{id}`, `POST .../decision-events/{id}/decisions` | Already wired via `listDecisionEvents`, `getDecisionEvent`, `decideOnEvent`. Out of scope for this packet; keep untouched. |

### 3.2 Frontend BFF client gap (`execute-plans/src/lib/bff-v1/agora/tradingRoom.ts`)

| Client function needed | Current state |
|---|---|
| `getTradingRoom`, `getTradingRoomStrategy`, `listDecisionEvents`, `getDecisionEvent`, `decideOnEvent` | Implemented today. |
| Create/read/accept workspace proposal | **Missing.** No wrapper for `POST .../trading-room/proposals`, `GET .../proposals/{id}`, or `POST .../proposals/{id}/accept`. |
| Read/patch workspace layout, views, widgets | **Missing.** No wrapper for `GET/PATCH .../workspaces/{id}`, `.../layout`, `.../views`, `.../widgets`. |
| Propose/accept widget revision | **Missing.** No wrapper for the widget-revision-proposal creation or `.../widget-revision-proposals/{id}/accept`, including the `keep_original_add_modified_copy` action. |
| List versions / rollback | **Missing.** No wrapper for `GET .../versions` or `POST .../versions/{id}/rollback`. |

This gap is exactly the scope of `AG-DYNUI-PROD-005`
(`docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-005-dynamic-workflow-closeout.md`),
whose declared artifacts already name
`WorkspaceProposalPreview.tsx`, `WorkspaceGridEditor.tsx`,
`WorkspaceWidgetRevisionDrawer.tsx`, and updates to `tradingRoom.ts` /
`trading_room.py`. **No new canonical BFF route, schema, or contract is required
by this sidecar packet** — the backend already has the surface; only the
frontend client and page components need to catch up.

---

## 4. Dependency State Snapshot

| Task | Status | Relevance to the hosted E2E |
|---|---|---|
| `AG-DYNUI-PROD-001` | `done` (PR #2851) | Restored design/source truth; unblocks all downstream work. |
| `AG-DYNUI-PROD-002` | `review` | Standalone Agora workbench shell; the E2E must run against this shell, not the legacy Trading Desk layout. |
| `AG-DYNUI-PROD-003` | `review_approved` | Dynamic default entry into Trading Room; the "join Trading Room" step in the journey below assumes this lands. |
| `AG-DYNUI-PROD-004` | `done` (PR #2855) | Trading Room diagnostics + cache policy; hosted probe pattern in §8 builds on this. |
| `AG-DYNUI-PROD-005` | `todo` | Frontend wiring for proposal/grid/widget-revision/version/rollback — **hard prerequisite** for the E2E steps in §5 beyond "join Trading Room". |
| `AG-DYNUI-PROD-006` (parent) | `todo`, no branch yet | Cannot start the hosted E2E authoring against the full flow until PROD-002/003/005 are merged; can start authoring the Strategy Workshop → readiness → join Trading Room portion now. |

---

## 5. Operator Journey Packet (Winner Branch V10-to-V11)

1. **Strategy Workshop.** Operator opens a workshop, exchanges messages, and
   watches `workshop.readiness.updated` events raise `highest_ready_gate`
   through `preliminary_research` → `full_validation` → `trading_room`.
   `getWorkshopReadiness` gates the "加入操盤室" action; the UI must keep the
   action disabled with a reason string until `tradingRoomReady(readiness)` is
   true (see `StrategyWorkshopPage.tsx:128-138`).
2. **Join Trading Room.** Once ready, the operator triggers `onAddToTradingRoom`
   and lands in the Trading Room for that strategy
   (`GET /bff/agora/trading-room/strategies/{strategy_id}`). The page should
   surface `allowedActions.record_decision` / `submit_handoff` /
   `request_shadow` truthfully rather than assuming all are enabled.
3. **Workspace proposal preview.** The frontend calls
   `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals` with the
   strategy version. The response is a declarative WidgetSpec/ChartSpec
   payload only — the UI renders it as a preview, it does not execute
   generator-supplied code.
4. **Accept proposal.** `POST .../proposals/{proposal_id}/accept` with
   `expectedStatus: "preview"`. On success the workspace (grid) exists and is
   readable via `GET /bff/agora/trading-room/workspaces/{workspace_id}`.
5. **Grid edit.** Operator edits layout (`PATCH .../layout`), adds/edits views
   (`POST`/`PATCH .../views...`), and adds/edits widgets
   (`POST`/`PATCH .../widgets...`). Every mutating call should carry the
   workspace `ETag` where the route expects `If-Match`.
6. **Widget revision with before/after.** Operator requests a widget revision;
   the response includes `before_after_preview=True` with both specs. Operator
   then either:
   - accepts as `apply` (replaces the widget), or
   - accepts as `keep_original_add_modified_copy` (keeps the original widget
     and adds the modified one as a new widget) — this is the literal
     "keep original and add modified copy" acceptance criterion from the
     parent brief and must be exercised explicitly in the E2E, not just
     `apply`.
7. **Version history.** `GET .../workspaces/{workspace_id}/versions` must show
   the version created by the widget-revision accept (and any grid edits)
   with correct ordering and `latest_version_id`.
8. **Rollback.** `POST .../versions/{version_id}/rollback` with `If-Match`
   restores an earlier version's views, bumps `dashboardVersion`, and returns
   a new version record plus `rollbackOfVersion`. The E2E should assert the
   grid visually reverts and that a *new* version is appended (rollback is
   forward-only history, not a delete).
9. **Screenshots.** Capture desktop and mobile viewport screenshots at
   minimum after step 2 (Trading Room joined), step 4 (proposal accepted /
   grid visible), step 6 (before/after widget revision), and step 8
   (post-rollback grid) — matching the design-pack layout, not the legacy
   empty Trading Desk shell.
10. **Leakage check.** At no point in this journey should any order-routing,
    capital-binding, broker-execution, RuntimeBinding-control, or Management
    surface be reachable from these pages; the E2E should assert their
    absence (no visible controls, no BFF calls to those route families).

---

## 6. Frontend Handoff Checklist

Parent review should verify, once `AG-DYNUI-PROD-005` lands and this E2E is
authored:

- `tradingRoom.ts` exposes typed client functions for proposal
  create/read/accept, workspace read/patch (layout/views/widgets), widget
  revision create/accept, and version list/rollback — mirroring the existing
  `getTradingRoom`/`decideOnEvent` patterns (shared auth headers,
  `credentials: "include"`, `Idempotency-Key` where the route accepts one,
  `If-Match` where the route requires it).
- `WorkspaceProposalPreview`, `WorkspaceGridEditor`, and
  `WorkspaceWidgetRevisionDrawer` (or equivalent components landed by
  `AG-DYNUI-PROD-005`) are reachable from the joined Trading Room page, not a
  separate unreachable route.
- The widget revision UI offers both `apply` and
  `keep_original_add_modified_copy` as distinct, explicit operator choices —
  do not silently default to `apply` only.
- Version history UI lets the operator pick any prior version and triggers
  rollback with the current workspace `ETag`, not a stale one.
- No order-routing, capital-binding, broker, or RuntimeBinding control is
  introduced anywhere in this flow.
- Desktop and mobile layouts both render the grid/widget/version surfaces
  without collapsing to the legacy empty Trading Desk shell.

---

## 7. BFF Client Handoff Checklist

Parent review should verify:

- Every mutating call that the router marks with `If-Match` sends the
  workspace or proposal `ETag` captured from the prior read/response.
- Every mutating call that the router accepts an `Idempotency-Key` on sends
  one, so retried E2E steps do not double-create proposals, views, widgets,
  or rollbacks.
- 409 responses (stale `If-Match`, proposal not in `preview`, widget spec
  drifted since the proposal was created) are surfaced as actionable
  diagnostics, consistent with the diagnostic pattern
  `AG-DYNUI-PROD-004` established for the Trading Room root load.
- No direct `fetch()` calls bypass the `tradingRoom.ts` client from page
  components.

---

## 8. Test Location And Probe Checklist

| Check | Expected result |
|---|---|
| E2E test location | New hosted E2E specs belong in `execute-plans/e2e/` (what `npm run e2e` / `playwright test` actually runs via `testDir: "./e2e"` in `execute-plans/playwright.config.ts`), **not** `execute-plans/tests/e2e/` (a separate Vitest-oriented smoke-test directory unrelated to Playwright). |
| Existing coverage to extend | `execute-plans/e2e/13-agora.spec.ts` already covers some Agora surface; prefer extending or adding a sibling spec over duplicating setup helpers in `execute-plans/e2e/helpers`. |
| Screenshot artifacts | Match the parent task's declared artifact globs `/tmp/agora-dynui-prod-e2e-*.png` and `/tmp/agora-dynui-prod-e2e-*.json`; do not leave generated evidence unowned in the repo tree. |
| Hosted vs local | Local Playwright run against `localhost:5173` is necessary but not sufficient; parent closeout still needs a hosted run against the Pantheon-owned dev FE + live BFF (`PANTHEON_FE_BASE_URL` pointed at the hosted origin), per `AG-DYNUI-PROD-004`'s precedent of deferring hosted proof until after deploy. |
| Leakage assertion | E2E should include an explicit assertion (DOM query or route-call audit) that no order/capital/broker/RuntimeBinding/Management control is present, not just an implicit absence. |

---

## 9. Suggested Reviewer Questions For Claude2

1. Does the parent E2E exercise `keep_original_add_modified_copy` as a
   distinct assertion, or only the `apply` path?
2. Does the parent PR land the frontend client wiring from
   `AG-DYNUI-PROD-005` in the same deployable branch, or does `AG-DYNUI-PROD-006`
   correctly wait for that PR to merge first?
3. Are the new E2E specs actually placed under `execute-plans/e2e/` so
   `npm run e2e` picks them up, or did they land under
   `execute-plans/tests/e2e/` by mistake?
4. Does the rollback assertion confirm a *new* version record is appended
   (forward-only history) rather than the version list shrinking?
5. Is hosted proof (dev FE + live BFF, not `localhost`) captured before parent
   closeout, consistent with the `AG-DYNUI-PROD-004` precedent?
6. Does the E2E assert the absence of order/capital/broker/RuntimeBinding
   surfaces, or only the presence of the DYNUI features?

---

## 10. Recommended Parent Closeout Evidence

Before `AG-DYNUI-PROD-006` moves from review toward done, record:

- parent PR number(s) and merged commit SHA(s), including confirmation that
  `AG-DYNUI-PROD-002`, `AG-DYNUI-PROD-003`, and `AG-DYNUI-PROD-005` are already
  merged into `dev`;
- dev FE deployment id/source commit that contains the full Winner Branch
  flow;
- hosted E2E command, FE URL, BFF URL, and output artifact paths
  (`/tmp/agora-dynui-prod-e2e-*.png`, `/tmp/agora-dynui-prod-e2e-*.json`);
- hosted E2E result after deployment, including the
  `keep_original_add_modified_copy` step and the rollback step;
- desktop and mobile screenshots showing the design-pack layout, not the
  legacy empty Trading Desk shell;
- confirmation that no order-routing, capital-binding, broker, or
  RuntimeBinding control is reachable from the exercised pages.

This packet should be handed to `Claude2` for sidecar review and to the parent
owner (`Codex`) as support material. It should not be treated as
implementation approval by itself, and it does not modify
`services/control-plane/bff/agora/trading_room/router.py`,
`execute-plans/src/lib/bff-v1/agora/tradingRoom.ts`, or any other runtime file.

---

## 11. Sidecar Closeout State

`Claude2` approved this packet (recorded in `ai-status.json` /
`AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF`)
after independently verifying the route inventory, frontend client gap table,
and `AG-DYNUI-PROD-001..005` dependency snapshot against `router.py`,
`tradingRoom.ts`, and current `ai_status.py show` output, and confirming the
`execute-plans/e2e` vs `execute-plans/tests/e2e` test-location distinction.
The task returned to owner `Claude` for finalization.

Closeout re-verification for this finalization pass re-read `router.py`,
`tradingRoom.ts`, `playwright.config.ts`, and current status for
`AG-DYNUI-PROD-001` through `AG-DYNUI-PROD-006`; the dependency snapshot in
§4 is unchanged (`PROD-001` done, `PROD-002` review, `PROD-003`
review_approved, `PROD-004` done, `PROD-005` todo, `PROD-006` todo) and the
route/gap claims in §3 still hold. No canonical truth, BFF/runtime code, or
frontend code was touched in this closeout pass — only this packet and the
mirrored task brief.

Focused closeout verification used:

- `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF`
- `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-001`
- `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-002`
- `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003`
- `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-004`
- `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-005`
- `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006`
- `git diff --check -- .orchestrator/task-briefs/ag_dynui_prod_006_sidecar_bff_handoff.md support/sidecars/AG-DYNUI-PROD-006/AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF.md`
- `gh pr view 2869 --json number,state,mergeable,mergeStateStatus,statusCheckRollup,autoMergeRequest`

After this closeout commit merges, the owner should run:

```bash
AI_NAME=Claude ./scripts/ai-status.sh done AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF "Sidecar handoff packet reviewed by Claude2, closeout record merged; support-only BFF/frontend handoff is ready for parent-owner (Codex) absorption."
```
