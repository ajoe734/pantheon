# AG-DYNUI-PROD-005 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-DYNUI-PROD-005` — Close Agora dynamic workflow wiring |
| Parent owner / reviewer | `Claude` / `Codex2` |
| Prepared by | `Claude2` |
| Reviewer | `Claude` |
| Date | 2026-07-04 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. It summarizes the BFF query gap, operator
journey, and frontend handoff boundaries for `AG-DYNUI-PROD-005`; the parent
owner decides whether and how to absorb it into the main implementation.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_dynui_prod_005_sidecar_bff_handoff.md` | Sidecar is support-only: BFF query gap, operator journey, frontend handoff materials; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes must pass task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`, owner `Claude2`, reviewer `Claude`, helper parent `AG-DYNUI-PROD-005`, helper kind `bff_handoff_packet`, depends on `AG-DYNUI-PROD-004` (now done). |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005` | Parent is `todo`; owner `Claude`, reviewer `Codex2`; depends on `AG-DYNUI-PROD-002` (`in_progress`), `AG-DYNUI-PROD-003` (`review`, PR #2860 merged, awaiting Claude2 review approval), `AG-DYNUI-PROD-004` (archived `done`, PR #2855 merged). |
| `docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-005-dynamic-workflow-closeout.md` | Scope: proposal generation, proposal acceptance, workspace load, layout patch, widget revision proposal, apply, keep-copy, version history, rollback — all through strict BFF; idempotency, optimistic concurrency, scope isolation, and widget allowlists must be tested; no fake-success fallback. |
| `docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/INDEX.md` | Wave 2 of 4; depends on Wave 1 (`PROD-002/003/004`); global rules: do not rebuild from imagination, do not bypass BFF auth/scope/allowlists, closeout needs branch/PR/merge/hosted evidence. |
| `docs/04/pantheon_agora_dynui_production_gap_2026-07-03/INDEX.md` | Production definition requires workspace proposal/accept/grid-edit/widget-revision/version-history/rollback wired through strict BFF calls, proven by hosted E2E. |
| Explore agent — frontend inventory (full read of `DashboardProposalPreview.tsx`, `DashboardGridEditor.tsx`, `WidgetRevisionDrawer.tsx`, `DashboardChangeLog.tsx`, `registry.ts`, `WidgetRenderer.tsx`, `ChartSpecRenderer.tsx`, `tradingRoom.ts`, `dashboard.ts`, `TradingRoomPage.tsx`, and all associated test files) | See §"Current State Observed" and §"BFF Query Gap Matrix" below. |
| Explore agent — backend inventory (full read of `bff/agora/trading_room/router.py`, `bff/agora/trading_room/store.py`, relevant parts of `bff/agora/dashboard/router.py`, `integrations/openclaw/skills/agora/trading_room_workspace/skill.py`, `specs/agora/trading_room_workspace.schema.json`, `specs/agora/widget_registry.v1.json`, and OpenAPI grep across `services/control-plane/openapi/*.yaml`) | See §"Current State Observed" and §"BFF Query Gap Matrix" below. |
| Direct verification: `grep -rn "trading-room/workspaces\|trading-room/proposals\|widget-revision-proposals\|proposeDashboardRecipe\|getTradingRoomWorkspace\|workspace_id\|workspaceId" execute-plans/src` | Zero non-embedded-snapshot references to the new V11 Trading Room workspace routes anywhere in frontend source; the only hit is the legacy `proposeDashboardRecipe` operationId inside the OpenAPI contract snapshot in `types.ts`. |
| `execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx` (direct read, lines 936-1225) | `StrategyRecipeSection` fetches via `getDashboardRecipeById` (legacy dashboard-recipe surface) and mounts `DashboardGridEditor` with three no-op callbacks and one local-state-only callback; no proposal/revision/changelog component is mounted anywhere in the page. |
| `find execute-plans/src/agora -type d`, `find execute-plans/src -iname "*trading*room*" -o -iname "*Workspace*"` | Confirmed the paths named in the parent task's `artifacts` field do not exist in this worktree; real files live under different directories/names (see §"Artifact Path Correction"). |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Artifact Path Correction (read before anything else)

The parent task's `artifacts` field in `ai-status.json` names files that **do
not exist** in this worktree. This is a factual correction the parent owner
needs before starting implementation — it is not a design opinion.

| Named in `AG-DYNUI-PROD-005.artifacts` | Exists? | Actual file |
|---|---|---|
| `execute-plans/src/agora/trading-room/WorkspaceProposalPreview.tsx` | No — directory `execute-plans/src/agora/trading-room/` does not exist at all | `execute-plans/src/agora/dashboard/DashboardProposalPreview.tsx` |
| `execute-plans/src/agora/trading-room/WorkspaceGridEditor.tsx` | No | `execute-plans/src/agora/dashboard/DashboardGridEditor.tsx` |
| `execute-plans/src/agora/trading-room/WorkspaceWidgetRevisionDrawer.tsx` | No | `execute-plans/src/agora/widgets/WidgetRevisionDrawer.tsx` |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Yes, exists (468 lines) | same path — but see below, it only covers the decision-event queue, not proposal/layout/revision/version/rollback |
| `services/control-plane/bff/agora/trading_room.py` | No — this is a package, not a flat file | `services/control-plane/bff/agora/trading_room/router.py` (3045 lines) + `trading_room/store.py` (377 lines) |
| (not named but directly relevant) | — | `execute-plans/src/agora/dashboard/DashboardChangeLog.tsx` (version history / rollback UI) |
| (not named but directly relevant) | — | `execute-plans/src/lib/bff-v1/agora/dashboard.ts` (the actual dashboard-recipe BFF client; only 2 of 9 declared endpoints implemented) |

The `execute-plans/src/agora/components/VersionCompareCard.tsx` file (which
name-matches "version history") is a **different, unrelated** component — it
renders read-only Strategy Workshop candidate-version diffs, not Trading Room
dashboard rollback. Do not confuse it with `DashboardChangeLog.tsx`.

## Current State Observed In This Worktree

### Backend (`services/control-plane/bff/agora/trading_room/router.py`)

The backend already implements the full V11 workspace lifecycle the parent
acceptance criteria describe. All 22 routes below are wired into
`bff/agora/router.py:175` (`create_trading_room_router`). Route numbers match
the detailed table below.

| # | Method + Path | Idempotency-Key | If-Match / ETag | Scope isolation |
|---|---|---|---|---|
| 3 | `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals` | optional | n/a (create) | tenant/user tagged at creation |
| 4 | `GET .../proposals/{proposal_id}` | n/a | ETag returned | 403 on cross-user access |
| 5 | `POST .../proposals/{proposal_id}/accept` | optional | none required (409 on non-preview status) | scope-checked |
| 6 | `GET /bff/agora/trading-room/workspaces/{workspace_id}` | n/a | ETag returned | scope-checked |
| 7 | `PATCH .../workspaces/{workspace_id}/layout` | optional | **required** (428/412) | scope-checked |
| 8-11 | views/widgets create/patch routes | optional | **required** | scope-checked |
| 12 | `POST .../widgets/{widget_id}/revision-proposals` | optional | none required (create-only) | scope-checked |
| 13 | `POST /bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept` (apply / keep_original_add_modified_copy) | optional | **required**, plus a second content-hash guard (412 if the widget changed since preview) | dual scope check (proposal + workspace) |
| 14 | `GET .../workspaces/{workspace_id}/versions` | n/a | n/a | store-level tenant/user filter (soft: empty list, not 403, on mismatch) |
| 15 | `POST .../versions/{version_id}/rollback` | optional | **required** | scope-checked |
| 16-17 | decision-events list/get | n/a | n/a | **no per-record scope check** (only `require_read_role`) |
| 18 | `POST decision-events/{id}/decisions` | **required** | **required** (presence-only, no computed ETag exists for this resource) | `require_read_role` only |
| 19 | `GET .../trading-room/stream` | n/a | n/a | self-documented **stub**: returns a static comment-only SSE payload, no real event streaming |
| 20-22 | trading-intents get/handoffs/withdraw | required on writes | required (presence-only) | `require_read_role` only, no per-record scope check |

Persistence: `TradingRoomStore` (`trading_room/store.py`) is an explicitly
**in-memory, single-process, non-durable** store — its own docstring states
"each restart starts empty." The idempotency-key dedup table is also
in-memory, so a replayed request after a BFF restart is not caught.

WidgetSpec/ChartSpec allowlist: a single canonical, file-backed registry
(`specs/agora/widget_registry.v1.json`, 42 active widget types) is enforced
identically by both this Trading Room router and the older Dashboard v2
router via a shared `_validate_widget_spec` function, plus an independent
forbidden-content-pattern scan in the workspace-generation skill
(`integrations/openclaw/skills/agora/trading_room_workspace/skill.py`). No
arbitrary widget/chart type can pass validation.

### Frontend

| Surface | Observed state | Handoff meaning |
|---|---|---|
| `lib/bff-v1/agora/tradingRoom.ts` | Implements only `getTradingRoom`, `getTradingRoomStrategy`, `listDecisionEvents`, `getDecisionEvent`, `decideOnEvent` — the decision-event queue workflow only. | Zero client functions exist for any of the 15 new V11 workspace/proposal/layout/widget-revision/version routes the backend already implements. |
| `lib/bff-v1/agora/dashboard.ts` | Implements only `getDashboardRecipeById` and `validateAgoraWidget`. The OpenAPI contract snapshot embedded in `types.ts` declares 7 more `agora-dashboard-v2` endpoints (propose/accept/layout-patch/rollback/feedback/versions/widget-feedback/propose-plugin) with **no client function anywhere**. | This is the **older, separate** dashboard-recipe surface (`/bff/agora/dashboard-recipes/...`, defined in `agora_v1_1`/`agora_v1_2.openapi.yaml`) — it is not the same surface as the new Trading Room V11 workspace contract in `agora_v1_5.openapi.yaml`. AG-DYNUI-PROD-005 must not conflate the two. |
| `DashboardProposalPreview.tsx` | Fully built presentational component: accept/reject/keep-both, idempotency-key generation, ETag/If-Match forwarding, scope-mismatch guard, concurrency-error display. **100% delegated to caller-supplied `onAccept`/`onReject`/`onKeepBoth` props — no BFF call inside it.** Never mounted anywhere in the app (only referenced by its own file and `DashboardChangeLog.tsx`'s shared-type imports). | AG-DYNUI-PROD-005 must mount this (or wire equivalent logic) and supply real `onAccept` bound to a workspace-proposal-accept client call. |
| `DashboardGridEditor.tsx` | Fully built grid editor: drag/resize/add/remove/change-chart, emits `PersonalizationEvent` on every action, sources allowed widget types/chart kinds strictly from `registry.ts`. **No BFF call inside it** — persistence is 100% delegated to caller callbacks. **Is mounted** in `TradingRoomPage.tsx` (`StrategyRecipeSection`, lines 982-995), but three of five callbacks are literal no-ops (`onWidgetRemove/onWidgetAdd/onWidgetChartChange={() => {}}`) and the fourth (`onPlacementsChange`) only updates local React state — nothing persists past a page refresh. | This is the one component partially wired, and its wiring is the weakest kind: visually functional, functionally inert. |
| `WidgetRevisionDrawer.tsx` | Fully built drawer: required `onRequestRevision` prop with no default implementation, real server-side validation via `validateAgoraWidget` gating Accept/Keep-both, no idempotency/ETag handling of its own. Never mounted anywhere in the app. | `onRequestRevision` has no BFF-backed implementation to bind to anywhere in this repo — the backend's `POST .../widgets/{widget_id}/revision-proposals` route has no matching frontend client function yet. |
| `DashboardChangeLog.tsx` | Fully built version-history/rollback table: idempotency-key generation, ETag/If-Match forwarding via `onRollback` prop, client-side guard against rolling forward. **No BFF call inside it.** Never mounted anywhere in the app. | Needs a real `onRollback` bound to the backend's `POST .../versions/{version_id}/rollback`, plus a version-list read bound to `GET .../versions`. Neither client function exists yet. |
| `registry.ts` / `WidgetRenderer.tsx` / `ChartSpecRenderer.tsx` | Two independent allowlist/validation layers (registry-entry check + chart-grammar/XSS-pattern scan), explicit interaction blocklist (`place_order`, `submit_order`, `enable_live`, `bind_capital`, `runtime_binding`, `invoke_broker`), fails closed to an error card on any violation. | This part is production-solid already; no gap to close here. |
| `execute-plans/e2e/13-agora.spec.ts` | Grepped for `trading-room`, `dashboard-recipe`, and all four dynamic-workflow component names — zero matches. | No E2E coverage exists for any part of the proposal/layout/revision/version/rollback workflow. |

## Parent Scope Boundary

`AG-DYNUI-PROD-005` owns:

- Wiring the full V11 dynamic workflow — proposal generation, proposal
  acceptance, workspace load, layout patch, widget revision proposal,
  apply/keep-copy, version history, rollback — through the **already-built**
  backend routes in `bff/agora/trading_room/router.py`.
- Adding the missing frontend BFF client functions for those routes (no
  client function exists today for any of them).
- Mounting `DashboardProposalPreview`, `WidgetRevisionDrawer`, and
  `DashboardChangeLog` into the actual Trading Room page flow, and replacing
  `DashboardGridEditor`'s no-op/local-only callbacks with real BFF-backed
  handlers.
- Testing idempotency (including the optional-header gaps below), optimistic
  concurrency (ETag/If-Match), scope isolation, and widget allowlist
  enforcement for every one of these operations.
- Deciding whether the legacy `dashboard-recipes` surface (`dashboard.ts`,
  `agora_v1_1`/`agora_v1_2.openapi.yaml`) is retired, kept parallel, or
  migrated — the parent task brief's "workspace load" language matches the
  new `trading-room/workspaces/{workspace_id}` surface, not the older
  recipe-id surface currently wired into `StrategyRecipeSection`.

`AG-DYNUI-PROD-005` does **not** own:

- The decision-event queue workflow (list/get/decide) — already fully
  implemented, tested, and wired through `tradingRoom.ts` /
  `decideOnEvent` with real ETag/Idempotency-Key/X-Request-Id forwarding.
  Do not re-touch this path.
- Any order-routing, `RuntimeBinding`, or capital-binding interaction — both
  the frontend registry (`BLOCKED_INTERACTION_KINDS`) and the backend
  validator (`_FORBIDDEN_INTERACTIONS`) already hard-block these; the parent
  task must preserve, not weaken, these blocklists.
- The SSE stream stub (`GET /bff/agora/trading-room/stream`) — it is
  self-documented as deferred pending a separate SSE infrastructure task; do
  not silently "complete" it as part of this closeout.
- `AG-DYNUI-PROD-002` (standalone workbench shell) and `AG-DYNUI-PROD-003`
  (default dynamic entry) — both are upstream dependencies, not part of this
  task's scope. `AG-DYNUI-PROD-003` is `review` (PR #2860 merged, awaiting
  Claude2 approval); `AG-DYNUI-PROD-002` is `in_progress`.

## BFF Query Gap Matrix

| Gap | Backend status | Frontend client status | Frontend wiring status |
|---|---|---|---|
| Proposal generation | `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals` implemented, scope-tagged at creation. | No client function. | Not called anywhere. |
| Proposal read | `GET .../proposals/{proposal_id}` implemented, ETag + 403 on cross-user. | No client function. | Not called anywhere. |
| Proposal accept | `POST .../proposals/{proposal_id}/accept` implemented, 409 on non-preview status. | No client function. | `DashboardProposalPreview.onAccept` has no implementation to bind to. |
| Workspace load | `GET /bff/agora/trading-room/workspaces/{workspace_id}` implemented, ETag + scope-checked. | No client function. | Page currently loads via `getDashboardRecipeById` (legacy recipe surface) instead — the V11 workspace-by-id read path is never called. |
| Layout patch | `PATCH .../workspaces/{workspace_id}/layout` implemented, **requires** If-Match (428/412). | No client function. | `DashboardGridEditor.onPlacementsChange` only updates local state; never persisted. |
| Widget add/remove/chart-change | `POST/PATCH .../widgets/{widget_id}` implemented, requires If-Match. | No client function. | `onWidgetAdd`/`onWidgetRemove`/`onWidgetChartChange` are literal no-ops in `TradingRoomPage.tsx`. |
| Widget revision proposal | `POST .../widgets/{widget_id}/revision-proposals` implemented. | No client function. | `WidgetRevisionDrawer.onRequestRevision` (required prop) has no implementation anywhere; drawer itself is never mounted. |
| Apply / keep-copy | `POST /bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept` implemented; body enum `apply` vs `keep_original_add_modified_copy`; second content-hash concurrency guard (412 if widget changed since preview). | No client function. | Never called; drawer never mounted. |
| Version history | `GET .../workspaces/{workspace_id}/versions` implemented; store-level scope filter is soft (empty list, not 403) on mismatch. | No client function. | `DashboardChangeLog` never mounted; no version list ever fetched. |
| Rollback | `POST .../versions/{version_id}/rollback` implemented, requires If-Match. | No client function. | `DashboardChangeLog.onRollback` has no implementation to bind to. |
| Legacy dashboard-recipe surface (propose/accept/layout/rollback/feedback/versions/widget-feedback/propose-plugin) | Backend routes exist per OpenAPI (`agora_v1_1`/`agora_v1_2.openapi.yaml`), separate from the V11 workspace surface above. | 7 of 9 declared endpoints have zero client function; only `getDashboardRecipeById` and `validateAgoraWidget` exist. | `StrategyRecipeSection` currently depends on this legacy surface for its only real read (`getDashboardRecipeById`). Parent owner must decide retire-vs-migrate before wiring the V11 workflow on top of it. |

## Operator Journey

### Journey A: Generate And Review A Workspace Proposal

1. Operator has a ready strategy in the Trading Room (post `AG-DYNUI-PROD-003` default-entry work).
2. Frontend calls a (to-be-added) `proposeTradingRoomWorkspace(strategyId)` → `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals`.
3. Frontend calls a (to-be-added) `getTradingRoomProposal(strategyId, proposalId)` → `GET .../proposals/{proposal_id}`, capturing the response `ETag`.
4. UI mounts `DashboardProposalPreview` with `proposal`, `activeRecipe` (if any prior workspace exists), and the captured `etag`.
5. Operator reviews the before/after delta, chart-kind coverage, and rationale/warnings/data-availability fields required by `trading_room_workspace.schema.json`.

### Journey B: Accept, Reject, Or Keep-Both A Proposal

1. Operator clicks Accept. `DashboardProposalPreview.onAccept` must call a (to-be-added) `acceptTradingRoomProposal(strategyId, proposalId, { ifMatch: etag, idempotencyKey, body: { expected_version, note } })` → `POST .../proposals/{proposal_id}/accept`.
2. Backend returns 409 if the proposal is no longer `preview` status (e.g., already accepted or superseded elsewhere) — UI must surface this, not silently retry.
3. On success, the response materializes an active `TradingRoomWorkspace` — UI should transition to Journey C (workspace load) using the new `workspace_id`.
4. Reject/Keep-both: `onReject`/`onKeepBoth` currently have no BFF-calling requirement per schema (proposal statuses `rejected`/`superseded` are implied client-side today) — parent owner should confirm whether the backend needs an explicit reject/keep-both endpoint or whether these are purely local UI transitions; none is currently implemented server-side per the router inventory above.

### Journey C: Load An Active Workspace

1. Frontend calls a (to-be-added) `getTradingRoomWorkspace(workspaceId)` → `GET /bff/agora/trading-room/workspaces/{workspace_id}`, capturing `ETag`.
2. UI renders the workspace's views/widgets — this should replace (or sit alongside, pending the retire-vs-migrate decision) the current `getDashboardRecipeById`-driven `StrategyRecipeSection` path.
3. Every subsequent mutation (layout patch, widget add/remove/chart-change, widget revision, rollback) must carry the current workspace `ETag` as `If-Match`, refreshing it from each mutation's response.

### Journey D: Patch The Layout (Drag/Resize/Add/Remove/Change-Chart)

1. Operator drags a widget. `DashboardGridEditor.onPlacementsChange` must call a (to-be-added) `patchTradingRoomWorkspaceLayout(workspaceId, { ifMatch: etag, idempotencyKey, body: { operations: [{ kind: "move_widget", ... }] } })` → `PATCH .../workspaces/{workspace_id}/layout`.
2. 428 if `If-Match` is missing, 412 if the workspace changed since the UI's last read — UI must refetch and show a conflict state, not silently overwrite.
3. Add/remove/chart-change must use the same route with `kind: add_registered_widget | remove_widget | replace_chart_spec`, replacing today's no-op callbacks.
4. `AddWidgetPanel`/`ChangeChartPanel` already source their choices from the allowlist registry — only the persistence call is missing, not the UI affordance.

### Journey E: Request And Review A Widget Revision

1. Operator asks the servant (or requests directly) to revise a widget. `WidgetRevisionDrawer.onRequestRevision` must call a (to-be-added) `requestWidgetRevision(workspaceId, widgetId, instruction)` → `POST .../widgets/{widget_id}/revision-proposals`.
2. Drawer already calls the real `validateAgoraWidget` (`POST /bff/agora/widgets/validate`) to gate Accept/Keep-both — this part needs no new client work.
3. Operator accepts (`apply`) or keeps both (`keep_original_add_modified_copy`) via a (to-be-added) `acceptWidgetRevisionProposal(proposalId, { ifMatch, idempotencyKey, body: { action } })` → `POST /bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept`.
4. Backend re-validates that the widget hasn't changed since the proposal was previewed (content-hash check) and returns 412 if it has — UI must handle this distinctly from the workspace-level ETag conflict.

### Journey F: View Version History And Roll Back

1. UI mounts `DashboardChangeLog` fed by a (to-be-added) `listTradingRoomWorkspaceVersions(workspaceId)` → `GET .../workspaces/{workspace_id}/versions`.
2. Note: the store-level scope filter for this route is soft (returns `[]` on tenant/user mismatch rather than 403) — UI cannot distinguish "no history" from "wrong scope" from this response alone; flag to backend owner if this ambiguity matters for the UX.
3. Operator selects a prior version and clicks Rollback. `DashboardChangeLog.onRollback` must call a (to-be-added) `rollbackTradingRoomWorkspace(workspaceId, versionId, { ifMatch, idempotencyKey, body: { expected_version, target_version, reason } })` → `POST .../versions/{version_id}/rollback`.
4. `canRollback` client guard already prevents rolling forward (`selected.version < activeVersion`) — keep this check when wiring the real call.

### Journey G: Capability Not Ready / Conflict

1. Any mutation route returns 428 (missing If-Match), 412 (ETag or content-hash mismatch), or 409 (invalid state transition, e.g. accepting a non-preview proposal, or an idempotency-key replay if the header was supplied).
2. UI must surface the typed diagnostic (reusing the `TradingRoomBffDiagnostic` shape already established in `tradingRoom.ts`/`TradingRoomErrorState`) rather than silently retrying or fabricating a success state.
3. Because `Idempotency-Key` is **optional** on every workspace/proposal/widget/version mutation route (only decision-events/decisions and intent-handoffs/withdraw require it), the frontend should generate and send it on every mutation by default — the components already generate a real `crypto.randomUUID()`-based key (`makeIdempotencyKey()` in `DashboardProposalPreview`/`DashboardChangeLog`); the gap is that nothing currently sends these keys to a real endpoint.

## Frontend Handoff

| UI / client need | Binding guidance |
|---|---|
| BFF client module | Add a new module (e.g. `execute-plans/src/lib/bff-v1/agora/tradingRoomWorkspace.ts`) or extend `tradingRoom.ts` with the ~10 missing functions below. Do not add these to `dashboard.ts` — that file is the separate legacy recipe surface. |
| Suggested client functions | See code block below. |
| Fallback posture | Live strict behavior, matching the existing `tradingRoom.ts` header comment. No local fixture fallback, no synthetic proposal/workspace/version data, no direct service fanout. |
| Idempotency key | Generate via `crypto.randomUUID()` (reuse the existing `makeIdempotencyKey()` pattern already present in `DashboardProposalPreview.tsx`/`DashboardChangeLog.tsx`) on every mutation call, even though the backend currently treats the header as optional on workspace/proposal/widget/version routes. |
| ETag / If-Match | Capture `ETag` from every GET (`proposals/{id}`, `workspaces/{id}`) and every mutation response; forward as `If-Match` on the next mutation for that resource. Missing header → 428; stale value → 412. |
| Mounting | `DashboardProposalPreview`, `WidgetRevisionDrawer`, and `DashboardChangeLog` must be mounted somewhere in the Trading Room workspace view (today none of the three is mounted anywhere). `DashboardGridEditor` is already mounted — replace its three no-op callbacks and the local-state-only `onPlacementsChange` with real BFF-backed handlers. |
| Legacy surface decision | Confirm with parent owner/reviewer whether `getDashboardRecipeById`/`dashboard-recipes/*` continues to back `StrategyRecipeSection`, or whether the page moves entirely to the `workspaces/{workspace_id}` read path. Mixing both without a documented reason risks two divergent sources of "current layout" truth. |
| Degraded state | 409: invalid state transition or idempotency-key replay. 412: ETag or content-hash conflict — refetch and show conflict, do not overwrite. 428: missing If-Match — this indicates a client bug, not a user-facing state, since the client should always send it. 403: cross-user/cross-tenant access — clear the view, do not retry. 404: proposal/workspace/version not found. |

Suggested frontend client methods (new module or extension of `tradingRoom.ts`):

```ts
proposeTradingRoomWorkspace(strategyId: string): Promise<TradingRoomWorkspaceProposal>
getTradingRoomProposal(strategyId: string, proposalId: string): Promise<{ proposal: TradingRoomWorkspaceProposal; etag: string }>
acceptTradingRoomProposal(strategyId: string, proposalId: string, opts: { ifMatch: string; idempotencyKey: string; body: DashboardAcceptRequestBody }): Promise<TradingRoomWorkspace>
getTradingRoomWorkspace(workspaceId: string): Promise<{ workspace: TradingRoomWorkspace; etag: string }>
patchTradingRoomWorkspaceLayout(workspaceId: string, opts: { ifMatch: string; idempotencyKey: string; body: { operations: WorkspaceLayoutOperation[] } }): Promise<{ workspace: TradingRoomWorkspace; etag: string }>
requestWidgetRevision(workspaceId: string, widgetId: string, opts: { ifMatch: string; idempotencyKey: string; body: { instruction: string } }): Promise<WidgetRevisionProposal>
acceptWidgetRevisionProposal(proposalId: string, opts: { ifMatch: string; idempotencyKey: string; body: { action: "apply" | "keep_original_add_modified_copy" } }): Promise<{ workspace: TradingRoomWorkspace; etag: string }>
listTradingRoomWorkspaceVersions(workspaceId: string): Promise<TradingRoomDashboardVersion[]>
rollbackTradingRoomWorkspace(workspaceId: string, versionId: string, opts: { ifMatch: string; idempotencyKey: string; body: DashboardRollbackRequestBody }): Promise<{ workspace: TradingRoomWorkspace; etag: string }>
```

Route path strings for each (from `services/control-plane/openapi/agora_v1_5.openapi.yaml`, cross-checked against `bff/agora/trading_room/router.py`):

```
POST /bff/agora/strategies/{strategy_id}/trading-room/proposals
GET  /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}
POST /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept
GET  /bff/agora/trading-room/workspaces/{workspace_id}
PATCH /bff/agora/trading-room/workspaces/{workspace_id}/layout
POST /bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}/revision-proposals
POST /bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept
GET  /bff/agora/trading-room/workspaces/{workspace_id}/versions
POST /bff/agora/trading-room/workspaces/{workspace_id}/versions/{version_id}/rollback
```

(`apply` and `keep-copy` are body-field enum values on the accept-revision
route, not separate URL paths — do not create separate `/apply` or
`/keep-copy` endpoints on the client side.)

## Suggested Backend Acceptance Checks

| Check | Expected result |
|---|---|
| Schema conformance | Every proposal/workspace/version response validates against `trading_room_workspace.schema.json` definitions (`TradingRoomWorkspaceProposal`, `TradingRoomWorkspace`, `WidgetRevisionProposal`, `TradingRoomDashboardVersion`). |
| Idempotency-Key enforcement gap | Decide and document whether `Idempotency-Key` should become **required** (not optional) on the workspace/proposal/widget/version mutation routes, matching the already-required behavior on decision-events/decisions and intent-handoffs/withdraw — today a client that omits the header gets no de-dup protection at all on these routes. |
| Durability gap | `TradingRoomStore` is in-memory only; confirm whether AG-DYNUI-PROD-005's "no fake success" acceptance criterion is satisfied by an in-memory store, or whether durable persistence is a hosted-proof blocker for this task or a follow-up. |
| Decision-events/intents scope gap | `GET decision-events`, `GET decision-events/{id}`, and `GET trading-intents/{intent_id}` perform no per-record tenant/user check (only role-gated) — confirm whether this is in scope for this task or a pre-existing gap to track separately (it predates this task; the decision-event queue is explicitly out of this task's scope per the parent boundary above). |
| Version-history soft-scope | `GET .../versions` returns `[]` on scope mismatch instead of 403 — confirm whether this should be tightened as part of this task. |
| SSE stub | Confirm `GET /bff/agora/trading-room/stream` remains explicitly out of scope (its docstring already states this) and is not silently marked "done" by this closeout. |
| Widget allowlist | Every widget create/patch/revision-accept path continues to route through `_validate_registry_widget_spec` (shared with the Dashboard v2 router) — do not introduce a second, divergent validator. |
| No-order guard | No route under this surface accepts `place_order`/`submit_order`/`enable_live`/`bind_capital`/`runtime_binding`/`invoke_broker` as an interaction kind — confirmed present today via both the frontend blocklist and backend `_FORBIDDEN_INTERACTIONS`; must remain unchanged. |
| Content-hash concurrency | Widget-revision-proposal acceptance re-checks the widget's content hash against the proposal's `beforeSpec` and 412s on mismatch — confirm frontend tests this path distinctly from the workspace-level ETag conflict. |

## Open Design Notes

### 1. Two distinct BFF surfaces exist for "dashboard/workspace" — do not conflate them

`agora_v1_1`/`agora_v1_2.openapi.yaml` define an older `dashboard-recipes`
surface (backed by `dashboard.ts`, currently the only thing
`StrategyRecipeSection` calls). `agora_v1_5.openapi.yaml` defines the newer
`trading-room/workspaces` + `strategies/{id}/trading-room/proposals` surface
(backed by `trading_room/router.py`, with zero frontend client coverage
today). The parent task's acceptance language ("workspace load," "proposal
generation/accept," "version history," "rollback") matches the **new**
surface. Parent owner should explicitly decide and document: retire the old
surface, keep both in parallel with a clear ownership split, or migrate
`StrategyRecipeSection` onto the new workspace-by-id read path. Silently
building V11 UI on top of the old recipe surface, or building a mixed client
that calls both without a documented boundary, would recreate the same
kind of "two candidate-state-machines" risk flagged in other AG-BE sidecar
packets.

### 2. Idempotency-Key is optional almost everywhere it matters most

Every workspace/proposal/widget/version mutation route accepts but does not
require `Idempotency-Key`. Only the (out-of-scope for this task)
decision-events/decisions and intent-handoffs/withdraw routes hard-require
it. Since this task's acceptance criteria explicitly call for idempotency
testing, the parent owner should decide whether to (a) tighten the backend
to require the header on these routes, matching the decision-event pattern,
or (b) keep it optional server-side but make the frontend client always send
it and document that de-dup is a client-side best-effort, not a
server-enforced guarantee, on this surface.

### 3. `If-Match` on decision-events/decisions and intent-handoffs/withdraw is presence-only

These two out-of-scope routes require the header to be present but never
validate it against a computed ETag (no such ETag helper exists for those
resource types). This is a pre-existing characteristic of the
already-completed decision-event workflow, not something AG-DYNUI-PROD-005
needs to fix — noted here only so the parent owner does not mistake it for
part of this task's optimistic-concurrency scope.

### 4. In-memory store is a durability gap, not a fake-success gap

The store's writes are real relative to its own process (no literal
`return success` without a persisted mutation was found anywhere in the
backend), but nothing survives a BFF restart, including the idempotency-key
dedup table. This is a materially different risk than "fake success" and
should be evaluated by the parent owner/reviewer as a possible hosted-proof
or follow-up-task blocker, separate from the wiring work itself.

### 5. Frontend components are correctly designed as prop-driven shells — the gap is purely in the caller

None of the four dynamic-workflow components (`DashboardProposalPreview`,
`DashboardGridEditor`, `WidgetRevisionDrawer`, `DashboardChangeLog`) need
rework to their own internal logic: idempotency-key generation,
scope-mismatch guards, and rollback-direction guards are already correctly
implemented at the component level. The entire gap is (a) the missing BFF
client functions for the new V11 workspace surface, and (b) the missing
mount points / no-op callbacks in `TradingRoomPage.tsx`. This significantly
narrows the implementation surface for the parent owner.

## Reviewer Handoff

Claude review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, registry/governance, or frontend files changed by this sidecar. |
| Artifact path correction | `WorkspaceProposalPreview.tsx`/`WorkspaceGridEditor.tsx`/`WorkspaceWidgetRevisionDrawer.tsx` genuinely do not exist; the real files are `DashboardProposalPreview.tsx`/`DashboardGridEditor.tsx`/`WidgetRevisionDrawer.tsx`; `trading_room.py` is genuinely a package (`router.py` + `store.py`), not a flat file. |
| Two-surface finding | `dashboard.ts` (legacy `dashboard-recipes`) and the new `trading-room/workspaces`/`trading-room/proposals` surface are genuinely separate OpenAPI-declared contracts with no frontend client overlap; `StrategyRecipeSection` genuinely only calls the legacy surface today. |
| Wiring gap accuracy | `DashboardGridEditor` is genuinely mounted with 3 no-op callbacks + 1 local-state-only callback; the other three components are genuinely never mounted anywhere in the app (confirmed by grep, not just by file existence). |
| Backend completeness claim | All 15 in-scope V11 workspace/proposal/layout/widget-revision/version/rollback routes genuinely exist and are wired into the shared Agora BFF router; idempotency-optional / ETag-required characteristics per route are accurately described. |
| Widget allowlist accuracy | The 42-entry `widget_registry.v1.json` allowlist and the interaction blocklist (`place_order`, `submit_order`, `enable_live`, `bind_capital`, `runtime_binding`, `invoke_broker`) are accurately described as shared/enforced on both frontend and backend. |
| No-order guard | All journeys and acceptance checks correctly exclude broker orders, `RuntimeBinding`, and capital binding. |
| Out-of-scope boundary | Decision-event queue workflow, SSE stream stub, and decision-events/intents scope gaps are correctly marked as pre-existing / out of this task's scope rather than blockers this task must fix. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-DYNUI-PROD-005/AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff packet approved: 記錄了 artifact 路徑與實際檔案不符的更正、V11 trading-room workspace surface 與舊版 dashboard-recipes surface 的雙軌現況、DashboardGridEditor 目前以 no-op/僅本地 state 掛載、其餘三個元件完全未掛載的具體證據、後端 22 個路由的 idempotency/ETag/scope 現況、widget allowlist 雙層防護，以及建議的前端 client 方法與 operator journey，未修改 canonical truth 或 runtime 檔案。" \
  ./scripts/ai-status.sh approve AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF \
  "Support-only AG-DYNUI-PROD-005 BFF/frontend handoff packet approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

## Validation Run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF

git status --short
# ?? .orchestrator/task-briefs/ag_dynui_prod_005_sidecar_bff_handoff.md

AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-004
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-001

find execute-plans/src/agora -maxdepth 3 -type d
find execute-plans/src -iname "*trading*room*" -o -iname "*Workspace*"
ls execute-plans/src/agora/widgets/ execute-plans/src/agora/dashboard/ execute-plans/src/agora/pages/trading-room/

grep -n "^import\|DashboardGridEditor\|ProposalPreview\|RevisionDrawer\|Workspace" execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx
grep -rn "DashboardProposalPreview|WidgetRevisionDrawer|DashboardChangeLog" execute-plans/src --include="*.tsx" | grep -v "\.test\."
grep -rn "trading-room/workspaces|trading-room/proposals|widget-revision-proposals|proposeDashboardRecipe|getTradingRoomWorkspace|workspace_id|workspaceId" execute-plans/src --include="*.ts" --include="*.tsx" | grep -v "\.test\."

ls services/control-plane/specs/agora/
grep -n "onWidgetAdd\|onWidgetRemove\|onWidgetChartChange\|onPlacementsChange\|patchLayout" execute-plans/src/agora/dashboard/DashboardGridEditor.tsx
```

Backend and frontend deep inventories were additionally produced by two
parallel Explore agents against full-file reads (not excerpts) of every file
listed in §"Sources Read" above; their findings are cross-cited throughout
this packet.
