# AG-FE-DYNUI-002 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-FE-DYNUI-002-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-DYNUI-002` - V11 Trading Room proposal preview and workspace shell |
| Parent owner / reviewer | `Claude2` / `Codex` |
| Prepared by | `Codex2` |
| Reviewer | `Codex` |
| Date | 2026-06-28 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
frontend code. It summarizes the BFF query gap, operator journey, and frontend
handoff boundaries for `AG-FE-DYNUI-002`; the parent owner decides whether and
how to absorb it into the main frontend implementation.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates task ownership; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_dynui_002_sidecar_bff_handoff.md` | Sidecar scope is support-only: BFF query gap, operator journey, and frontend handoff materials; no canonical truth changes. |
| `ai-status.json` through `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DYNUI-002-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`, owner `Codex2`, reviewer `Codex`, helper parent `AG-FE-DYNUI-002`, artifact path is this file. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DYNUI-002` | Parent task is `todo`, owner `Claude2`, reviewer `Codex`; AG-FE-DYNUI-002 owns V11 generation progress, workspace proposal preview, view thumbnails/counts, and accept-to-workspace shell. |
| `docs/04/agora_design_pack_dynui_2026-06-28/README.md` | Task graph routes V11 proposal preview and generated workspace shell to AG-FE-DYNUI-002. Static screenshots, hardcoded mock state, and empty dashboard fallbacks are explicitly non-goals. |
| `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md` | Gap routing confirms AG-FE-DYNUI-002 must cover V11 generation progress, workspace proposal preview, view thumbnails/counts, and accept-to-workspace shell. |
| `/tmp/ai-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md` sections 5, 12, 13, 14, 16, 17 | V11 requires `TradingRoomWorkspaceProposal`, all generated views/widgets before accept, per-view preview metadata, `TradingRoomWorkspace`, BFF workspace/proposal routes, no empty dashboard, no arbitrary frontend code injection, and no Management/RuntimeBinding/backend-engineering terms. |
| `support/sidecars/AG-BE-DYNUI-001/AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF.md` | Backend sidecar already identifies the missing V11 proposal/workspace route family and warns that `DashboardRecipeV2` is not a drop-in replacement for V11 `TradingRoomWorkspaceProposal`. |
| `execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx` | Current Trading Room frontend reads `getTradingRoom`, `listDecisionEvents`, and, for a selected strategy, `getDashboardRecipeById(strategy.dashboard_recipe_id)`; it renders `DashboardGridEditor` from a `DashboardRecipeV2`, not a V11 workspace proposal or workspace. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Current client exposes Trading Room aggregate, strategy detail, decision events, and decision-event writes only. No `TradingRoomWorkspaceProposal` or `TradingRoomWorkspace` types or methods exist. |
| `execute-plans/src/lib/bff-v1/agora/dashboard.ts` | Current dashboard client exposes `getDashboardRecipeById` and widget validation. It does not expose proposal generation, proposal detail, proposal accept, or workspace read. |
| `execute-plans/src/agora/pages/trading-room/TradingRoomPage.test.tsx` | Current tests assert `getDashboardRecipeById("recipe-001")` and `strategy-recipe-workspace`; they do not cover join-to-generation, V11 proposal preview, all view thumbnails/counts, accept, or strict no-fixture fallback. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Generated Agora types include `DashboardRecipeV2` and `WidgetSpecV2`; no generated `TradingRoomWorkspaceProposal`, `TradingRoomWorkspace`, `TradingRoomViewSpec`, or V11 `TradingRoomWidgetSpec` type is present. |
| `services/control-plane/openapi/*`, `services/control-plane/bff/agora/dashboard/router.py` | Existing backend exposes `/bff/agora/dashboard-recipes/*`; the V11 `/trading-room/proposals` and `/trading-room/workspaces` route family is not present in the inspected OpenAPI/route surfaces. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Current Frontend State Observed

| Surface | Observed state | Handoff meaning for AG-FE-DYNUI-002 |
|---|---|---|
| Trading Room entry | `TradingRoomPage` initially calls `getTradingRoom()` and `listDecisionEvents()`. | This is still the aggregate/decision-room surface; it does not start V11 workspace generation. |
| Strategy workspace | When `strategyId` is selected, `StrategyWorkspaceView` reads `strategy.dashboard_recipe_id` and calls `getDashboardRecipeById(recipeId)`. | This is a compatibility path for `DashboardRecipeV2`; parent task must replace or gate it for V11 join-to-proposal flow. |
| Workspace shell | `StrategyRecipeSection` renders recipe `views[]` through `DashboardGridEditor`. | It can inform grid rendering, but it is not the V11 generated workspace shell because it lacks `TradingRoomWorkspace.status`, `generatedBy`, `activeViewId`, proposal preview metadata, and workspace lifecycle state. |
| Proposal preview | Existing `DashboardProposalPreview` previews `DashboardRecipeV2` deltas. | Do not reuse it as-is for V11 unless wrapped/adapted around `TradingRoomWorkspaceProposal` fields; V11 requires per-view thumbnails, widget counts, rationale, data availability, warnings, and personalization applied. |
| BFF client | `tradingRoom.ts` exposes aggregate and decision-event methods only. | Parent task needs typed V11 client methods after AG-XR-DYNUI-001 supplies generated types or an explicit temporary local contract is approved. |
| Type model | `types.ts` has generated `DashboardRecipeV2`, `WidgetSpecV2`, and `ChartSpecV1`; no V11 workspace/proposal types. | Parent task should not invent durable fields in app code. If generated types are unavailable, open a blocker or use a task-local adapter with clearly disposable scope. |
| Tests | Current page tests verify aggregate view, decision events, recipe lookup, and recipe unavailability. | Parent task needs new tests for generation progress, proposal preview, accept-to-workspace, no empty fallback, and 501/capability-not-ready handling. |

## BFF Query Gap Matrix For Frontend

| Frontend need | Expected BFF surface | Current gap / required behavior |
|---|---|---|
| Start generated workspace proposal after Strategy Workshop readiness | `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals` | Missing from current frontend client. UI needs `generateWorkspaceProposal(strategyId, strategyVersion, hints?)` and a generation state machine. |
| Poll or read proposal preview | `GET /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}` | Missing from current frontend client. UI must bind full `TradingRoomWorkspaceProposal`, not `DashboardRecipeV2`. |
| Accept proposal | `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept` | Missing from current frontend client. Accept must return or lead to a `TradingRoomWorkspace`; the UI must not navigate to an empty dashboard. |
| Read active workspace | `GET /bff/agora/trading-room/workspaces/{workspace_id}` | Missing from current frontend client. Shell needs `activeViewId`, `views[]`, `status`, `generatedBy`, `dashboardVersion`, and timestamps. |
| View all generated proposal views | Proposal `views[]` | UI must show all Winner Branch views as thumbnails/cards with widget counts and purpose before accept. |
| Show data availability and warnings | Proposal `dataAvailability`, view/widget warnings, inferred/partial/unavailable flags | Current recipe workspace does not show these V11 preview badges. |
| Show personalization applied | Proposal `personalizationApplied` | Current recipe workspace does not distinguish generated default vs personalized result. |
| Capability-not-ready gate | Typed `501 Not Implemented` or equivalent capability response | UI must show a real BFF-gap/capability state and must not substitute fixtures, static mock cards, or `DashboardRecipeV2` as V11 data. |
| Scope isolation handling | `403 Forbidden` on cross-user proposal/workspace reads | UI must clear stale workspace state and show scoped error; do not keep rendering previous user's proposal/workspace. |
| Stale write handling for later editor tasks | `412 Precondition Failed` with ETag on workspace mutations | AG-FE-DYNUI-002 only needs to preserve ETag handoff state for AG-FE-DYNUI-003; it should not implement grid mutation semantics unless parent expands scope. |

## Parent Scope Boundary

`AG-FE-DYNUI-002` owns:

- Join-to-generation screen after Strategy Workshop readiness is satisfied.
- Generation progress for the V11 Trading Room workspace proposal.
- `WorkspaceProposalPreview` UI that renders all proposal views before accept.
- Per-view preview cards/thumbnails showing title, purpose, order, widget count,
  data availability, warnings, and personalization applied.
- Accept-to-workspace transition that creates/loads a `TradingRoomWorkspace` and
  lands on a non-empty active view shell.
- Typed client calls in `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` or a
  sibling module, aligned with generated AG-XR-DYNUI-001 types.
- Strict fallback posture: no fixture fallback, no empty dashboard, no silent
  `DashboardRecipeV2` substitution.
- Focused tests covering generation progress, proposal preview, accept, and
  capability-not-ready behavior.

`AG-FE-DYNUI-002` does **not** own:

- Backend proposal/workspace schemas, persistence, or route implementation
  (`AG-BE-DYNUI-001`).
- Widget revision proposals, keep-original-copy, version history, change log,
  and rollback (`AG-BE-DYNUI-002`, later FE tasks).
- Servant workspace generator and widget/chart validator integration
  (`AG-BE-DYNUI-003`).
- OpenAPI generated type drift closure (`AG-XR-DYNUI-001`).
- Full grid editor mutation runtime: drag, resize, remove, restore, add,
  duplicate, save/discard, personalization events (`AG-FE-DYNUI-003`).
- Widget adjustment drawer and before/after revision flow (`AG-FE-DYNUI-004`).
- Design-pack visual parity polish after dynamic foundations exist
  (`AG-FE-DYNUI-005`).
- Direct order routing, capital binding, RuntimeBinding, Management-plane
  operations, or broker controls.

## Operator Journey To Implement

### Journey A: Join Trading Room Starts Generation

1. Strategy Workshop reports readiness to join the Trading Room.
2. Operator clicks the join action.
3. Frontend calls `generateWorkspaceProposal(strategyId, strategyVersion, hints?)`.
4. UI shows a generation progress state tied to the BFF proposal status.
5. If BFF returns capability-not-ready, UI shows a typed unavailable state and
   stops. It must not render mock proposal cards or route to an empty workspace.

### Journey B: Proposal Preview Before Workspace

1. Frontend reads the full `TradingRoomWorkspaceProposal`.
2. UI shows all generated views before accept.
3. Each view preview shows title, purpose, order, widget count, data completeness,
   warning badges, and personalization applied.
4. Winner Branch preview must include at least the seven V11-required views:
   strategy overview, candidates/entry, winner branch intelligence,
   related-party/flow migration, event lead, positions/add/reduce/exit, and
   evidence/monitoring rules.
5. Operator can accept or cancel. AG-FE-DYNUI-002 does not need to implement
   per-widget editing from this preview.

### Journey C: Accept Proposal To Workspace Shell

1. Operator accepts the proposal.
2. Frontend calls `acceptWorkspaceProposal(strategyId, proposalId)`.
3. BFF returns a `workspaceId` and/or workspace state.
4. Frontend loads `getWorkspace(workspaceId)` when needed and enters a generated
   workspace shell with an active view.
5. Shell renders view tabs and widget placeholders/renderers from the accepted
   workspace. It must not be blank if the proposal contained views/widgets.

### Journey D: Error And Scope Handling

1. `403`: clear proposal/workspace state and show scope error.
2. `404`: clear stale navigation and show missing proposal/workspace.
3. `409` or invalid proposal state: re-read proposal and show stale workflow
   state; do not accept twice.
4. `412`: preserve for downstream edit tasks; re-read workspace when mutation
   routes are used.
5. `422`: show validation failure details without inventing client-side
   replacements.
6. `501`: show capability-not-ready; do not substitute local fixtures or
   `DashboardRecipeV2`.

## Suggested Frontend Client Surface

Add these methods only after generated types are available or the parent owner
records an explicit temporary adapter decision:

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
): Promise<{ workspaceId: string; workspace?: TradingRoomWorkspace }>

getWorkspace(workspaceId: string): Promise<TradingRoomWorkspace>
```

Implementation guidance:

- Put these methods in `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` if
  the module remains the Trading Room boundary; otherwise create a narrow
  sibling such as `workspace.ts` and re-export it intentionally.
- Use live strict BFF calls with `credentials: "include"` and
  `Accept: "application/json"`, matching the existing client style.
- For accept, include idempotency/request headers if the backend contract
  requires them. Do not guess header requirements if AG-BE-DYNUI-001/AG-XR-DYNUI-001
  have not published the contract.
- Parse typed error envelopes so `403`, `404`, `409`, `412`, `422`, and `501`
  become distinct UI states.
- Do not call `/bff/agora/dashboard-recipes/*` as a substitute for V11
  workspace proposal routes.

## Suggested UI Composition

| UI piece | Suggested behavior |
|---|---|
| Generation progress | A task-state view shown immediately after join; reflects BFF status, not a timer-only animation. |
| `WorkspaceProposalPreview` | New component under `execute-plans/src/agora/trading-room/WorkspaceProposalPreview.tsx` that accepts `TradingRoomWorkspaceProposal` and renders all views. |
| View preview card | Shows title, purpose, widget count, data completeness, warnings, personalization applied, and a compact thumbnail or structured layout preview. |
| Accept footer | Shows accept/cancel actions and blocked state if proposal is not `preview`. |
| Workspace shell | Replaces `strategy-recipe-workspace` with V11 workspace status once accepted; tabs come from `TradingRoomWorkspace.views[]`, active tab from `activeViewId`. |
| Existing `DashboardGridEditor` | May be a rendering reference, but AG-FE-DYNUI-002 should not treat its `DashboardRecipeV2` placement model as canonical for V11 unless generated types confirm compatibility. |
| Empty state | Empty workspace is an error unless BFF explicitly returns no views. The normal V11 Winner Branch path must render a full generated workspace. |

## Safety Invariants For Parent Implementation

| Invariant | Frontend guard |
|---|---|
| No direct order route | Do not add "Place order", "Execute trade", "Enable live", broker, capital binding, or RuntimeBinding controls. |
| No arbitrary code injection | Treat widget specs as data; never execute raw HTML, React, JavaScript, external script URLs, or unsupported renderers returned by a servant. |
| No internal terminology | Do not surface Management, RuntimeBinding, ArtifactState, or backend implementation terms in the Agora operator UI. |
| Strict BFF dependency | No local fixtures, generated mock workspace, or `DashboardRecipeV2` fallback in live strict mode. |
| User scope isolation | Clear state on `403`; never keep displaying a previous proposal/workspace after a scope failure. |
| Parent/dependent task separation | Do not implement AG-FE-DYNUI-003/004 editor/revision behavior inside the AG-FE-DYNUI-002 preview task except for shell handoff placeholders. |

## Suggested Frontend Acceptance Checks

| Check | Expected result |
|---|---|
| Join-to-generation | Clicking join calls the V11 proposal generation client and shows generation progress. |
| Capability gate | `501` from proposal generation shows capability-not-ready and does not render fixtures, recipe workspace, or an empty dashboard. |
| Proposal preview completeness | Preview renders all proposal `views[]`; each view shows title, purpose, widget count, data availability, warnings, and personalization applied. |
| Winner Branch minimum views | A Winner Branch proposal with the seven V11 views renders all seven in order. |
| Accept transition | Accept calls the proposal accept route, then loads/renders a non-empty `TradingRoomWorkspace` shell with the active view selected. |
| DashboardRecipe isolation | Tests fail if `getDashboardRecipeById` is used as the V11 proposal source. |
| Scope error | `403` clears proposal/workspace state and shows scoped error. |
| Missing workspace | `404` clears stale navigation. |
| No-order-route UI | No proposal, preview, or workspace shell exposes order execution, broker, capital binding, RuntimeBinding, or Management controls. |
| No code execution | Widget render path treats specs as declarative data and rejects unsupported renderer/code fields. |

## Open Coordination Notes

1. `AG-XR-DYNUI-001` should land generated types for `TradingRoomWorkspaceProposal`,
   `TradingRoomWorkspace`, `TradingRoomViewSpec`, and V11 `TradingRoomWidgetSpec`
   before AG-FE-DYNUI-002 hardens the client surface.
2. `AG-BE-DYNUI-001` must define exact response envelopes and error codes for
   generation, proposal read, proposal accept, and workspace read. FE should not
   guess if the route family is not published.
3. Existing `DashboardRecipeV2` code can remain available for older dashboard
   flows, but AG-FE-DYNUI-002 should make the V11 path visibly separate to avoid
   accidental static/recipe fallback.
4. Parent owner should decide whether the workspace shell initially reuses
   existing widget renderers for read-only rendering or lands a thinner V11
   placeholder renderer until AG-FE-DYNUI-003 owns edit behavior.

## Reviewer Handoff

`Codex` review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, registry/governance, or frontend files changed by this sidecar. |
| Factual alignment | Parent AG-FE-DYNUI-002 is `todo`; current frontend uses `DashboardRecipeV2` for selected strategy workspace; V11 proposal/workspace client methods and types are absent. |
| Gap coverage | Packet covers generation, proposal read, proposal accept, workspace read, strict capability-not-ready, view preview metadata, and accept-to-workspace shell. |
| Boundary accuracy | AG-FE-DYNUI-003/004 editor and revision work, AG-BE-DYNUI-001/002/003 backend work, and AG-XR-DYNUI-001 generated types are not assigned to this sidecar. |
| Safety invariants | No-order-route, no arbitrary code injection, no internal terminology, strict BFF dependency, and scope isolation are documented. |

Recommended reviewer approval command:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/AG-FE-DYNUI-002/AG-FE-DYNUI-002-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only AG-FE-DYNUI-002 handoff packet approved: 記錄目前 TradingRoomPage 仍以 DashboardRecipeV2/dashboard_recipe_id 作為策略 workspace，相對於 V11 TradingRoomWorkspaceProposal/Workspace route family 的 BFF/client/UI gap；涵蓋 join-to-generation、proposal preview、all-view thumbnails/counts、accept-to-workspace shell、strict 501/capability gate、403 scope handling、no fixture fallback、no-order-route 與 AG-FE-DYNUI-003/004/AG-BE/AG-XR 邊界，不修改 canonical truth 或 runtime/front code。" \
  ./scripts/ai-status.sh approve AG-FE-DYNUI-002-SIDECAR-BFF-HANDOFF \
  "Support-only AG-FE-DYNUI-002 BFF/frontend handoff packet approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Codex ./scripts/ai-status.sh reopen AG-FE-DYNUI-002-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

## Validation Run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-FE-DYNUI-002-SIDECAR-BFF-HANDOFF

git status -sb
# ## task/AG-FE-DYNUI-002-SIDECAR-BFF-HANDOFF...origin/dev
# ?? .orchestrator/task-briefs/ag_fe_dynui_002_sidecar_bff_handoff.md

AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DYNUI-002-SIDECAR-BFF-HANDOFF
# Confirmed sidecar owner Codex2, reviewer Codex, status in_progress,
# helper_parent AG-FE-DYNUI-002, mutates_canonical false.

AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DYNUI-002
# Confirmed parent owner Claude2, reviewer Codex, status todo, and
# acceptance focused on V11 proposal preview, all generated views/counts,
# accept-to-workspace shell, no empty/static fallback.

rg -n "DashboardRecipeV2|dashboard_recipe_id|WorkspaceProposal|TradingRoomWorkspace|strategy-recipe|DashboardGridEditor|workspace" \
  execute-plans/src/agora execute-plans/src/lib/bff-v1/agora
# Confirmed current TradingRoomPage selected-strategy path uses
# dashboard_recipe_id/getDashboardRecipeById and generated types do not include
# V11 TradingRoomWorkspaceProposal/Workspace types.

rg -n "dashboard-recipes|trading-room/proposals|workspaces|revision-proposals" \
  services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora
# Confirmed existing dashboard recipe route family is present; V11
# trading-room proposals/workspaces route family is not present in inspected
# backend/OpenAPI/spec surfaces.

sed -n '520,620p' /tmp/ai-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md
sed -n '940,1032p' /tmp/ai-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md
sed -n '1032,1165p' /tmp/ai-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md
# Confirmed V11 proposal/workspace model, route family, required artboards,
# do/do-not constraints, and acceptance checklist.
```
