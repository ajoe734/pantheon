# AG-BE-DYNUI-001 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-DYNUI-001` — Trading Room workspace proposal contracts, schemas, persistence, and routes |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Prepared by | `Claude2` |
| Reviewer | `Claude` |
| Date | 2026-06-28 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
frontend code. It summarizes the BFF query gaps, operator journey, and frontend
handoff boundaries for `AG-BE-DYNUI-001`; the parent owner decides whether and
how to absorb it into the main implementation.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_be_dynui_001_sidecar_bff_handoff.md` | Sidecar is support-only: BFF query gap, operator journey, frontend handoff materials; no canonical truth changes. |
| `docs/04/agora_design_pack_dynui_2026-06-28/README.md` | Design pack requires dynamic workspace proposal system (V11). AG-BE-DYNUI-001 owns proposal contracts, workspace schemas, persistence, and routes. Dependencies: AG-DYNUI-SRC-001, AG-BE-TR-001, AG-BE-DB-001. |
| `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md` | Gap map confirms: `TradingRoomWorkspaceProposal`, `TradingRoomWorkspace`, `TradingRoomViewSpec`, `TradingRoomWidgetSpec`, and workspace proposal routes are all missing from the current implementation. |
| `/tmp/ai-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md` §5, §12, §13 | Canonical workspace proposal TypeScript contract shapes and §13 BFF route family. |
| `services/control-plane/bff/agora/trading_room/router.py` | Implements `agora.trading.v1`: trading-room aggregate, decision-events, stream, trading-intents, governed-intent handoffs. No V11 proposal or workspace routes. |
| `services/control-plane/bff/agora/dashboard/router.py` | Implements `agora.dashboard.v2`: dashboard-recipe proposals (DashboardRecipeV2 shape), accept, get, layout patch, rollback, feedback, versions, widget validate. Does not implement V11 `TradingRoomWorkspaceProposal` shape or per-workspace/per-view/per-widget resource lifecycle. |
| `services/control-plane/specs/agora/v2/dashboard_recipe_v2.schema.json` | `DashboardRecipeV2` v2.0: per-user/per-strategy append-only version schema with views/placements/widgets, propose/active/archive/rolled_back lifecycle. Does not include V11 proposal-level rationale, per-view thumbnails, data availability, warnings, personalization summary, or active workspace state fields. |
| `services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json` | `TradingRoomAggregate` v1.0: read-only snapshot of strategies, queue summary, and risk summary for the operator scope. Contains `dashboard_recipe_id` link but no workspace proposal or workspace lifecycle fields. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | Routes through `agora_v1_3`: trading-room aggregate, strategies, decision-events, stream, workshops, patch-proposals, version-comparisons. No V11 workspace proposal, workspace CRUD, view mutation, widget mutation, widget revision proposal, workspace versions, or rollback routes. |
| `services/control-plane/specs/agora/v2/widget_spec_v2.schema.json`, `services/control-plane/specs/agora/widget_registry.v1.json` | 42 active registry entries (Winner Branch widgets) with safe `WidgetSpecV2`, `ChartSpecV1`, blocked interaction policy. Strong allowlist foundation for V11 `TradingRoomWidgetSpec` but missing V11 `purpose`, `whyIncluded`, `placement` min/preferred/max dimension fields, and widget-context request payload for servant adjustment. |
| `services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json` | `dashboard_recipe_id` presence on strategy entries confirms the existing dashboard recipe link is intended as the bridge to a trading workspace. V11 replaces this with the full workspace proposal lifecycle. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Current BFF State Observed In This Worktree

| Surface | Observed state | Handoff meaning |
|---|---|---|
| `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals` | Not implemented. | AG-BE-DYNUI-001 must add this route to trigger servant workspace proposal generation and return a `TradingRoomWorkspaceProposal`. |
| `GET /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}` | Not implemented. | AG-BE-DYNUI-001 must return the full proposal with views, thumbnails, widget counts, rationale, data availability, warnings, and personalization applied. |
| `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept` | Not implemented. | AG-BE-DYNUI-001 must accept a proposal and materialize a `TradingRoomWorkspace` as the active workspace. |
| `GET /bff/agora/trading-room/workspaces/{workspace_id}` | Not implemented. | AG-BE-DYNUI-001 must return the full workspace state including views, widgets, and version metadata. |
| `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/layout` | Not implemented. | AG-BE-DYNUI-001 must apply controlled layout operations (drag, resize) through the widget registry validator. Must use optimistic concurrency (ETag). |
| `POST /bff/agora/trading-room/workspaces/{workspace_id}/views` | Not implemented. | AG-BE-DYNUI-001 must add a new `TradingRoomViewSpec` to the active workspace. |
| `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/views/{view_id}` | Not implemented. | AG-BE-DYNUI-001 must update an existing view's layout or metadata. |
| `POST /bff/agora/trading-room/workspaces/{workspace_id}/widgets` | Not implemented. | AG-BE-DYNUI-001 must add a widget to a view using only registry-validated `TradingRoomWidgetSpec` instances. |
| `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}` | Not implemented. | AG-BE-DYNUI-001 must update a widget's controlled properties (query, chart spec, placement). No direct mutation from servant; mutation requires a WidgetRevisionProposal (AG-BE-DYNUI-002 owns revision proposals). |
| `services/control-plane/specs/agora/trading_room_workspace.schema.json` | File does not exist. | AG-BE-DYNUI-001 must create this schema to represent the `TradingRoomWorkspace`, `TradingRoomWorkspaceProposal`, `TradingRoomViewSpec`, and `TradingRoomWidgetSpec` resource shapes. |

Widget revision proposals, workspace versions, rollback, and change log are scoped to `AG-BE-DYNUI-002` (see § Parent Scope Boundary).

## Parent Scope Boundary

`AG-BE-DYNUI-001` owns:

- `TradingRoomWorkspaceProposal` schema and generation trigger route: `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals`.
- Proposal detail and preview: `GET /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}`.
- Proposal accept (materializes workspace): `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept`.
- Active workspace read: `GET /bff/agora/trading-room/workspaces/{workspace_id}`.
- Controlled layout PATCH with ETag/optimistic concurrency: `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/layout`.
- View mutation routes: `POST` / `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/views[/{view_id}]`.
- Widget add and update routes (registry-validated): `POST` / `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/widgets[/{widget_id}]`.
- `trading_room_workspace.schema.json` (new schema file) covering proposal, workspace, view, and widget spec shapes.
- Scope isolation tests: one trader cannot read another trader's workspace or proposal.
- Safety invariant: all new routes must preserve the D1 no-order-route boundary (see § Safety Invariants below).

`AG-BE-DYNUI-001` does **not** own:

- Widget revision proposals, accept, keep-original-copy, cancel lifecycle (`AG-BE-DYNUI-002` owns this).
- Workspace version history, change log, and rollback routes (`AG-BE-DYNUI-002` owns this).
- Servant workspace generator and safe widget/chart validator integration (`AG-BE-DYNUI-003` owns this).
- OpenAPI schema drift and generated frontend type sync (`AG-XR-DYNUI-001` owns this).
- Frontend page, grid editor, and widget renderer runtime (`AG-FE-DYNUI-001` through `AG-FE-DYNUI-005` own this).
- `TradingDecisionEvent`, `GovernedIntentHandoff`, or any live order path (`AG-BE-TR-001`/`AG-BE-TR-002` own this).
- `RuntimeBinding`, capital binding, broker order routing, or Management-plane operations.

Dependencies:
- `AG-DYNUI-SRC-001`: source map and gap map (done — this packet references it).
- `AG-BE-TR-001`: Trading Room aggregate and decision-event queue (must be done before AG-BE-DYNUI-001 touches the trading-room routes namespace to avoid routing conflicts).
- `AG-BE-DB-001`: database ownership; proposal and workspace records need confirmed storage ownership before persistence implementation.

## BFF Query Gap Matrix

| Gap | Needed BFF surface | Parent disposition |
|---|---|---|
| Workspace proposal generation is missing | `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals` — triggers servant generator (AG-BE-DYNUI-003) and returns a pending `TradingRoomWorkspaceProposal` with all V11 fields. | `AG-BE-DYNUI-001` primary. Gated on AG-BE-DYNUI-003 for the servant generator integration; initial implementation may return a status-only pending response while generator is wired. |
| Workspace proposal detail is missing | `GET /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}` — returns full proposal including all views, thumbnail refs or equivalent previews, widget counts, rationale, data availability flags, warnings, and personalization applied. | `AG-BE-DYNUI-001` primary. |
| Workspace proposal accept is missing | `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept` — transitions proposal to accepted and creates or activates a `TradingRoomWorkspace` for the trader. Must scope the workspace to `(user_id, strategy_id, strategy_version)`. | `AG-BE-DYNUI-001` primary. |
| Active workspace read is missing | `GET /bff/agora/trading-room/workspaces/{workspace_id}` — returns `TradingRoomWorkspace` with views, widgets, placement data, status, `generatedBy`, version, and staleness indicators. | `AG-BE-DYNUI-001` primary. |
| Workspace layout PATCH is missing | `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/layout` — applies controlled layout operations (drag, resize, remove, add, replace chart) validated through widget registry. Must reject forbidden interactions. Must use ETag/optimistic concurrency to prevent stale writes. | `AG-BE-DYNUI-001` primary. The existing `dashboard/router.py` layout PATCH pattern (§657) is the closest reference model. |
| View creation and mutation routes are missing | `POST /bff/agora/trading-room/workspaces/{workspace_id}/views` and `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/views/{view_id}` — adds or updates a `TradingRoomViewSpec` in the workspace. | `AG-BE-DYNUI-001` primary. |
| Widget add and update routes are missing | `POST /bff/agora/trading-room/workspaces/{workspace_id}/widgets` and `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}` — adds or updates a widget using registry-validated `TradingRoomWidgetSpec`. Direct servant mutation is not allowed; servant must go through a `WidgetRevisionProposal` (AG-BE-DYNUI-002). | `AG-BE-DYNUI-001` primary for the widget resource endpoints; revision proposal lifecycle is AG-BE-DYNUI-002. |
| `trading_room_workspace.schema.json` is missing | New JSON Schema file under `services/control-plane/specs/agora/` covering `TradingRoomWorkspaceProposal`, `TradingRoomWorkspace`, `TradingRoomViewSpec`, and `TradingRoomWidgetSpec` shapes derived from V11 §5/§12 TypeScript contracts. | `AG-BE-DYNUI-001` primary. Schema must have `additionalProperties: false` at proposal, workspace, view, and widget levels. Parent owner should verify with SD/design that the V11 TypeScript shapes are the authoritative schema reference before creating the JSON Schema. |
| `TradingRoomWidgetSpec` V11 fields are missing from existing `widget_spec_v2.schema.json` | `widget_spec_v2.schema.json` has `WidgetSpecV2` for dashboard recipes. V11 `TradingRoomWidgetSpec` adds: `purpose`, `whyIncluded`, placement `minSize`/`maxSize`, widget-context envelope for servant adjustment. | Design team must confirm whether V11 `TradingRoomWidgetSpec` extends or replaces `WidgetSpecV2` — parent owner should not create new fields without design confirmation. |
| `DashboardRecipeV2` vs `TradingRoomWorkspaceProposal` boundary | Existing `dashboard/router.py` has a proposal-based recipe create/accept pattern. V11 requires a distinct `TradingRoomWorkspaceProposal` shape with per-view thumbnails/counts, data availability, warnings, and personalization summary that the `DashboardRecipeV2` schema does not provide. | `AG-BE-DYNUI-001` must create the V11 proposal shape as a separate resource. It must not re-use the `DashboardRecipeV2` accept/rollback surface for V11 workspace proposals without first resolving with design whether the recipe and workspace resource models are intended to merge or remain separate. |
| Frontend workspace client is missing | TypeScript client methods in `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` (or a new `workspace.ts`) for proposal generation, proposal detail, accept, workspace read, layout patch, view mutation, and widget add/update. | `AG-FE-DYNUI-002` and `AG-FE-DYNUI-003` after `AG-XR-DYNUI-001` lands the OpenAPI-generated types. |

## Operator Journey

### Journey A: Join Trading Room and Receive Workspace Proposal

1. Operator completes strategy workshop and readiness gates pass (`highest_ready_gate === "trading_room"`).
2. Operator clicks "Join Trading Room" in the Strategy Workshop.
3. Frontend calls `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals` with `strategy_version` and any personalization hints.
4. BFF queues the workspace proposal generation (via servant generator in AG-BE-DYNUI-003) and returns `proposal_id` with `status: "generating"`.
5. Frontend polls or subscribes to SSE for proposal status transitions: `generating → preview`.
6. When `status: "preview"`, frontend calls `GET /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}`.
7. BFF returns a full `TradingRoomWorkspaceProposal` including all views, per-view thumbnail refs or equivalent previews, widget counts per view, rationale for each view, data availability flags, warnings (inferred/estimated data badges), and personalization applied.
8. Operator reviews the proposal — all required views for a Winner Branch strategy: strategy overview, candidates/entry, winner branch intelligence, related-party/flow migration, event lead, positions/add/reduce/exit, evidence/monitoring rules.
9. Operator can accept the whole proposal or cancel.

### Journey B: Accept Proposal and Activate Workspace

1. Operator accepts the proposal.
2. Frontend calls `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept`.
3. BFF validates the proposal is in `preview` state, creates a `TradingRoomWorkspace` scoped to `(user_id, strategy_id, strategy_version)`, and transitions workspace to `status: "active"`.
4. BFF returns the `workspace_id` and initial workspace state.
5. Frontend transitions to the Trading Room workspace view using `GET /bff/agora/trading-room/workspaces/{workspace_id}`.
6. Operator must not land in an empty dashboard; the workspace must have all views and widgets from the accepted proposal.

### Journey C: Drag, Resize, and Reorganize Widgets

1. Operator enters layout edit mode in the Trading Room.
2. Operator drags or resizes a widget.
3. Frontend calls `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/layout` with an array of controlled layout operations (`move_widget`, `resize_widget`) and the current ETag.
4. BFF validates: each widget referenced must be in the workspace; operations must not include forbidden interactions (`place_order`, `enable_live`, `change_capital_binding`, `invoke_broker`, `write_runtime_binding`, `open_management_route`); widget registry allowlist is respected.
5. BFF applies the operations, generates a new workspace version, and returns the updated workspace with new ETag.
6. If ETag is stale (concurrent edit), BFF returns `412 Precondition Failed`; frontend re-reads the workspace and re-applies.

### Journey D: Remove a Widget and Restore It

1. Operator removes a widget from a view.
2. Frontend calls `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/layout` with a `remove_widget` operation.
3. BFF marks the widget as removed from the current layout but retains the widget spec in the workspace record (hidden, restorable).
4. Frontend shows "removed widget" in the Widget Library panel, from which the operator can restore it.
5. Restoration calls `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/layout` with an `add_registered_widget` operation referencing the hidden widget ID.

### Journey E: Add a New Widget From the Registry

1. Operator opens the Add Widget panel and selects a widget type.
2. Frontend calls `POST /bff/agora/trading-room/workspaces/{workspace_id}/widgets` with the desired `widget_type`, `query` parameters, and target `view_id`.
3. BFF validates the `widget_type` against the widget registry; rejects types not in the active registry or using forbidden interactions.
4. BFF adds the widget to the workspace view and returns the new `TradingRoomWidgetSpec` with assigned `widget_id`.
5. Frontend adds the widget to the grid at a default or operator-specified position.

### Journey F: View That Workspace Data Is Scoped Correctly

1. Trader A calls `GET /bff/agora/trading-room/workspaces/{workspace_id_of_trader_B}`.
2. BFF must reject with `403 Forbidden` — workspaces are scoped per trader.
3. A trader cannot see another trader's workspace even if they share the same strategy.

### Journey G: Servant Cannot Directly Mutate a Widget

1. Servant generates a widget modification suggestion.
2. Servant calls `POST /bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}/revision-proposals` (AG-BE-DYNUI-002 scope).
3. BFF creates a `WidgetRevisionProposal` with `beforeSpec` (current) and `proposedSpec` (suggested).
4. Servant must not call `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}` directly to apply a mutation.
5. Only the operator's explicit accept action (AG-BE-DYNUI-002) may apply the proposed spec.

### Journey H: Workspace Not Yet Available (Capability Gate)

1. Frontend calls any workspace route when `AG-BE-DYNUI-001` routes are not yet deployed.
2. BFF returns `501 Not Implemented` with a typed capability-not-ready response.
3. Frontend must not silently substitute fixture data, static mock workspace, or a `DashboardRecipeV2` accept response as a proxy for the V11 workspace proposal.

## Safety Invariants

All AG-BE-DYNUI-001 routes must preserve the D1 no-order-route boundary:

| Invariant | How to enforce |
|---|---|
| No live order routing | Workspace proposal, accept, and layout routes must never invoke broker, create `RuntimeBinding`, or write a capital binding. |
| No Management-plane exposure | Workspace and proposal responses must not include Management, RuntimeBinding, ArtifactState, or other backend-engineering-internal terms. |
| Widget allowlist enforced | All widget types in the workspace must pass the widget registry validator. Forbidden interactions (`place_order`, `enable_live`, `change_capital_binding`, `invoke_broker`, `write_runtime_binding`, `open_management_route`) are blocked at BFF even if sent by the frontend. |
| No arbitrary code injection | BFF must not accept, store, or return raw JavaScript, React, HTML, external scripts, unsupported renderers, or arbitrary code in widget specs. |
| Scope isolation | All workspace and proposal resources are scoped to the authenticated trader's identity. Cross-user reads are `403`. |

## Frontend Handoff

| UI / client need | Binding guidance |
|---|---|
| BFF client | Add typed methods to `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` (extend) or a new `workspace.ts` module. Frontend pages must not call the dashboard-recipe API as a substitute for V11 workspace proposal routes. |
| Fallback posture | Live strict behavior. Do not add local fixture fallback, synthetic workspace data, or direct service fanout. Return `501` capability gate when routes are not yet live. |
| Proposal generation | `generateWorkspaceProposal(strategyId, strategyVersion, personalizationHints?)` → poll or SSE subscribe for `status: "generating" → "preview"`. |
| Proposal preview | `getWorkspaceProposal(strategyId, proposalId)` → bind all views, thumbnails, widget counts, rationale, data availability, warnings, personalizationApplied. Must render all 7 Winner Branch views. |
| Proposal accept | `acceptWorkspaceProposal(strategyId, proposalId)` → transition to workspace, navigate to workspace view. |
| Workspace read | `getWorkspace(workspaceId)` → bind `TradingRoomWorkspace.views[]` and `TradingRoomViewSpec.widgets[]`; show `status`, `generatedBy`, version. |
| Layout PATCH | `patchWorkspaceLayout(workspaceId, operations[], etag)` → handle `412 Precondition Failed` by re-reading workspace; handle `400 ValidationFailed` by showing which operation failed registry check. |
| View mutation | `addView(workspaceId, viewSpec)` / `updateView(workspaceId, viewId, patch)` → update the view selector/tabs. |
| Widget add | `addWidget(workspaceId, widgetSpec)` → validate `widget_type` against local registry copy; show Preview before sending. |
| Widget update | `updateWidget(workspaceId, widgetId, patch)` → only for operator-initiated direct property edits (query, chart spec). Servant must use revision proposal path. |
| Scope error handling | `403`: forbidden (cross-user access attempt — clear view, show scoping error). `404`: workspace not found (clear stale navigation). `412`: ETag mismatch (re-read workspace, show stale-write warning). `422`: validation failed (show blocked operation reason). `501`: capability not ready (show "workspace feature not yet available" with no fixture fallback). |
| No-order guard | No workspace proposal or layout action must expose "Place order", "Execute trade", "Enable live", or any broker-facing control. |

Suggested BFF client methods (all to be placed in `tradingRoom.ts` or `workspace.ts`):

```ts
generateWorkspaceProposal(
  strategyId: string,
  strategyVersion: string,
  hints?: WorkspacePersonalizationHints,
): Promise<{ proposalId: string; status: "generating" | "preview" }>

getWorkspaceProposal(
  strategyId: string,
  proposalId: string,
): Promise<TradingRoomWorkspaceProposal>

acceptWorkspaceProposal(
  strategyId: string,
  proposalId: string,
): Promise<{ workspaceId: string; workspace: TradingRoomWorkspace }>

getWorkspace(workspaceId: string): Promise<TradingRoomWorkspace>

patchWorkspaceLayout(
  workspaceId: string,
  operations: WorkspaceLayoutOperation[],
  etag: string,
): Promise<TradingRoomWorkspace>

addView(workspaceId: string, viewSpec: TradingRoomViewSpec): Promise<TradingRoomWorkspace>

updateView(
  workspaceId: string,
  viewId: string,
  patch: Partial<TradingRoomViewSpec>,
): Promise<TradingRoomWorkspace>

addWidget(
  workspaceId: string,
  widgetSpec: TradingRoomWidgetSpec,
): Promise<TradingRoomWorkspace>

updateWidget(
  workspaceId: string,
  widgetId: string,
  patch: Partial<TradingRoomWidgetSpec>,
): Promise<TradingRoomWorkspace>
```

`WorkspaceLayoutOperation.kind` type:
`"move_widget" | "resize_widget" | "remove_widget" | "add_registered_widget" | "replace_chart_spec" | "update_widget_query"`

## Suggested Backend Acceptance Checks

| Check | Expected result |
|---|---|
| Schema conformance | Every `TradingRoomWorkspaceProposal` response validates against the (to-be-created) `trading_room_workspace.schema.json`. |
| Proposal required fields | `proposal_id`, `strategy_id`, `strategy_version`, `generated_at`, `status`, `views[]`, `rationale`, `data_availability`, `warnings`, `personalization_applied` all present. |
| View required fields | Each `TradingRoomViewSpec` includes `id`, `title`, `purpose`, `order`, `layout_template`, and `widgets[]`. |
| Widget required fields | Each `TradingRoomWidgetSpec` includes `id`, `widget_type`, `title`, `purpose`, `why_included`, `data_source`, `query`, `chart_spec`, `interactions`, `placement`, `min_size`. |
| Winner Branch view completeness | A Winner Branch strategy proposal must include at least 7 views: strategy overview, candidates/entry, winner branch intelligence, related-party/flow migration, event lead, positions/add/reduce/exit, evidence/monitoring rules. |
| Proposal → Workspace transition | Accepting a `preview` proposal creates a `TradingRoomWorkspace` with `status: "active"` and all views/widgets materialized. |
| Scope isolation | `GET /bff/agora/trading-room/workspaces/{workspace_id}` returns `403` for a workspace not owned by the authenticated user. |
| Widget registry enforcement | Any layout PATCH with an unregistered `widget_type` or a forbidden interaction returns `400 ValidationFailed` with the blocked operation reason. |
| ETag optimistic concurrency | A layout PATCH with a stale ETag returns `412 Precondition Failed`. |
| Remove-but-retain | A `remove_widget` layout operation marks the widget as hidden but does not delete the spec from the workspace record; the widget is restorable. |
| No-order route | No workspace or proposal BFF endpoint routes a broker order, writes a `RuntimeBinding`, or creates a capital binding. |
| No code injection | Widget specs with arbitrary JavaScript, React, HTML, or unsupported renderer references are rejected at the BFF validation layer. |
| Scope-by-strategy-version | Two proposals for the same strategy but different strategy versions produce separate `TradingRoomWorkspace` instances. |

## Open Design Notes

### 1. `trading_room_workspace.schema.json` must be created before implementation

No JSON Schema currently exists for `TradingRoomWorkspaceProposal`, `TradingRoomWorkspace`, `TradingRoomViewSpec`, or `TradingRoomWidgetSpec`. The V11 design document provides TypeScript shape contracts (§5 and §12). Parent owner must:

- Create `services/control-plane/specs/agora/trading_room_workspace.schema.json` (or a versioned path such as `v5/`).
- Set `additionalProperties: false` at proposal, workspace, view, and widget levels.
- Verify with SD/design that the TypeScript shapes in V11 §5 and §12 are the authoritative reference and no design-closure amendments are pending.

### 2. `TradingRoomWidgetSpec` vs `WidgetSpecV2` — design boundary must be confirmed

`services/control-plane/specs/agora/v2/widget_spec_v2.schema.json` defines `WidgetSpecV2` for `DashboardRecipeV2`. V11 `TradingRoomWidgetSpec` adds `purpose`, `whyIncluded`, placement dimension constraints, and a widget-context adjustment request envelope. Parent owner must confirm with SD/design whether:

- V11 `TradingRoomWidgetSpec` extends `WidgetSpecV2` (additive), or
- V11 `TradingRoomWidgetSpec` is a parallel but independent schema.

Parent owner must not self-add new fields to `widget_spec_v2.schema.json` due to `additionalProperties: false`. A schema extension or sibling schema is required from design.

### 3. `DashboardRecipeV2` coexistence boundary

The existing `dashboard/router.py` (`agora.dashboard.v2`) has a proposal/accept lifecycle for `DashboardRecipeV2`. V11 introduces `TradingRoomWorkspaceProposal` with different fields (per-view thumbnails, data availability, warnings, personalization summary). Parent owner must:

- Confirm with SD/design whether `DashboardRecipeV2` is deprecated by V11 workspace proposals for the Trading Room, or whether both co-exist for different purposes.
- Not silently re-route V11 proposal accepts through the `DashboardRecipeV2` accept handler, as the response shapes are incompatible.

### 4. AG-BE-TR-001 and AG-BE-DB-001 dependency gating

The task brief lists `AG-BE-TR-001` and `AG-BE-DB-001` as dependencies. Before implementing the workspace proposal routes:

- Verify `AG-BE-TR-001` status: the trading-room namespace currently has `router.py` routes for aggregate, decision-events, and stream; AG-BE-DYNUI-001 must not conflict with these routes.
- Verify `AG-BE-DB-001` status: proposal and workspace persistence require a storage backing. If `AG-BE-DB-001` has not defined storage ownership for workspace records, implementation should raise a blocker rather than using in-memory store.

### 5. Servant generator integration boundary

AG-BE-DYNUI-001 owns the proposal routes but not the servant generator implementation (AG-BE-DYNUI-003). The proposal generation route may return `status: "generating"` with a pending `proposal_id` if the servant generator is not yet integrated. Parent owner should design the proposal status machine to allow a phased integration:

- Phase 1 (AG-BE-DYNUI-001): proposal resources, accept, workspace CRUD — stub generator with a seeded proposal for testing.
- Phase 2 (AG-BE-DYNUI-003): real servant generator that populates the proposal with Winner Branch views.

The stub must not be accepted as the live delivery; it is scaffolding for frontend integration only.

## Reviewer Handoff

`Claude` review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, registry/governance, or frontend files changed by this sidecar. |
| Factual alignment | `AG-BE-DYNUI-001` is `todo` (owner `Claude`, reviewer `Claude2`); the V11 workspace proposal schema does not yet exist; no workspace proposal or workspace CRUD routes exist in any OpenAPI or BFF router file; `dashboard_recipe_v2.schema.json` is not a drop-in replacement for the V11 shape. |
| Gap coverage | All 5 proposal-and-workspace-level gaps (generate, get, accept, workspace read, layout PATCH) and the 4 view/widget mutation gaps are correctly identified as absent from the current codebase. |
| Scope boundary accuracy | Workspace version history, change log, rollback, and widget revision proposals are correctly assigned to AG-BE-DYNUI-002. Servant generator integration is correctly assigned to AG-BE-DYNUI-003. Frontend runtime is correctly assigned to AG-FE-DYNUI-002/003/004. |
| Safety invariant completeness | No-order-route, widget allowlist, scope isolation, and no-code-injection invariants are correctly documented. |
| Open design notes accuracy | Schema creation requirement, WidgetSpecV2 boundary, DashboardRecipeV2 coexistence, and dependency gating are each a genuine blocker that requires design confirmation before implementation. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-BE-DYNUI-001/AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff packet approved: 記錄 TradingRoomWorkspaceProposal/Workspace/ViewSpec/WidgetSpec BFF gap surfaces、V11 §13 route family、operator journeys、frontend client boundaries、no-order-route/widget-allowlist/scope isolation 守衛、schema extension requirement、DashboardRecipeV2 coexistence boundary 與 AG-BE-DYNUI-002/003 dependency 邊界，不修改 canonical truth 或 runtime 檔案。" \
  ./scripts/ai-status.sh approve AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF \
  "Support-only AG-BE-DYNUI-001 BFF/frontend handoff packet approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

## Validation Run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF

git status --short
# ?? .orchestrator/task-briefs/ag_be_dynui_001_sidecar_bff_handoff.md

ls services/control-plane/bff/agora/trading_room/
# router.py  store.py  test_trading_room.py  __init__.py

grep -n "workspace\|proposal" services/control-plane/bff/agora/trading_room/router.py
# Line 289: action_proposal (GovernedActionProposal — unrelated to workspace proposals)

ls services/control-plane/specs/agora/
# No trading_room_workspace.schema.json present

ls services/control-plane/openapi/agora_v1_3.openapi.yaml
# Grep for "workspace" or "trading-room/proposals" returns no matches

cat docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md
# Confirmed: TradingRoomWorkspaceProposal, workspace CRUD, view/widget mutation routes are absent

cat /tmp/ai-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md §13
# Confirmed §13 BFF route family as documented above
```
