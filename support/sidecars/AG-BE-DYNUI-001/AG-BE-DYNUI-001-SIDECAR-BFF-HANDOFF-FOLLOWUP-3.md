# AG-BE-DYNUI-001 BFF Handoff Follow-Up 3

| Field | Value |
|---|---|
| Task ID | `AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-DYNUI-001` |
| Prepared by | `Codex` |
| Reviewer | `Claude` |
| Date | 2026-06-28 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support-only follow-up for the parent owner. It does not edit
L1 canonical truth, OpenAPI, JSON Schema, BFF runtime, widget registry,
governance logic, persistence, or frontend code. It converts the already
identified V11 Trading Room workspace gaps into an implementation handoff card
that `AG-BE-DYNUI-001` can absorb or reject in the main task.

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates task ownership; support packets do not override product or architecture truth. |
| `.orchestrator/task-briefs/ag_be_dynui_001_sidecar_bff_handoff_followup_3.md` | This sidecar may create support material only and must not modify canonical truth or runtime implementation. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned doc progress must be preserved with explicit scoped commits when it reaches a handoff state. |
| `.orchestrator/skills/task-closeout-finalization.md` | This task is not `review_approved`; the correct next lifecycle move is reviewer handoff, not `done`. |
| `support/sidecars/AG-BE-DYNUI-001/AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF.md` | Baseline packet already lists the missing V11 proposal/workspace route family, operator journey, frontend bindings, and no-order guards. |
| `support/sidecars/AG-BE-DYNUI-001/AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Follow-up 2 narrows the absorption order: schema first, proposal lifecycle, workspace read/layout, view/widget mutation, then OpenAPI/type sync. |
| `docs/04/agora_design_pack_dynui_2026-06-28/README.md` | Joining the Trading Room must create a complete `TradingRoomWorkspaceProposal`, not an empty dashboard. |
| `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md` | Gap routing assigns missing workspace proposal, workspace, view, widget specs, and workspace proposal routes to `AG-BE-DYNUI-001`. |
| `/tmp/ai-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md` | V11 names the `TradingRoomWorkspaceProposal`, `TradingRoomWorkspace`, `TradingRoomViewSpec`, `TradingRoomWidgetSpec`, `WidgetRevisionProposal`, and BFF route family. |
| `services/control-plane/bff/agora/trading_room/router.py` | Current `agora.trading.v1` router implements aggregate, per-strategy detail, decision events, SSE stub, and governed intent handoff/withdraw. It does not implement workspace proposal or workspace CRUD routes. |
| `services/control-plane/bff/agora/dashboard/router.py` | Existing dashboard recipe v2 router has useful ETag/layout/versioning patterns, but its resource identity is `DashboardRecipeV2`, not V11 Trading Room workspace. |
| `services/control-plane/openapi/agora_v1_2.openapi.yaml` | Dashboard recipe routes exist under `/dashboard-recipes`; they are not the V11 Trading Room workspace route family. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | Trading Room OpenAPI exposes aggregate, strategy detail, decision events, decisions, and stream routes only. |
| `services/control-plane/specs/agora/v2/dashboard_recipe_v2.schema.json` | Useful reference for structured views/widgets and lifecycle fields, but missing V11 proposal-level rationale, warnings, data availability, personalization, and active workspace state. |
| `services/control-plane/specs/agora/v2/widget_spec_v2.schema.json` and `services/control-plane/specs/agora/widget_registry.v1.json` | Useful validator substrate for allowlisted widgets and chart specs; V11 workspace-specific fields still require an explicit contract. |

## Current Worktree Read

| Surface checked | Result | Parent meaning |
|---|---|---|
| `services/control-plane/specs/agora/trading_room_workspace.schema.json` | Not present. | Parent should start with the V11 workspace contract before route claims. |
| `services/control-plane/bff/agora/trading_room/router.py` | No `TradingRoomWorkspaceProposal`, `TradingRoomWorkspace`, `/trading-room/proposals`, or `/trading-room/workspaces` route implementations. | Parent must add a distinct workspace route family. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | Has `/bff/agora/trading-room`, `/strategies/{strategy_id}`, `/decision-events`, `/decision-events/{id}`, `/decisions`, and `/stream`. | Current OpenAPI represents decision support only. |
| `services/control-plane/openapi/agora_v1_2.openapi.yaml` | Has `/dashboard-recipes` proposal, accept, layout, rollback, versions, widget validate, feedback, and plugin routes. | Borrow concurrency/validation patterns only; do not substitute this resource for V11. |
| `services/control-plane/bff/agora/dashboard/router.py` | Uses `If-Match`, ETag checks, versioned layout patch, rollback, and widget validation. | Good implementation reference for workspace layout safety and stale-write handling. |

## Parent Absorption Card

Recommended `AG-BE-DYNUI-001` implementation order:

1. Add `services/control-plane/specs/agora/trading_room_workspace.schema.json`
   with these top-level definitions: `TradingRoomWorkspaceProposal`,
   `TradingRoomWorkspace`, `TradingRoomViewSpec`, `TradingRoomWidgetSpec`,
   `WidgetPlacement`, `WorkspaceLayoutOperation`, and typed error envelopes.
2. Add proposal lifecycle routes under
   `/bff/agora/strategies/{strategy_id}/trading-room/proposals[...]`:
   create/enqueue proposal, get proposal detail, and accept a preview proposal
   into an active workspace.
3. Add active workspace read and layout patch:
   `GET /bff/agora/trading-room/workspaces/{workspace_id}` and
   `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/layout`.
   Layout PATCH must require `If-Match`/ETag and return `412` on stale writes.
4. Add operator-scoped view and widget mutation routes:
   `POST/PATCH /views[/{view_id}]` and `POST/PATCH /widgets[/{widget_id}]`.
   These endpoints apply only operator-controlled edits. Servant changes remain
   proposal-based and outside this parent task.
5. Keep generator integration behind `AG-BE-DYNUI-003`. `AG-BE-DYNUI-001` can
   define the route, persistence shape, status envelope, and capability gate
   before the real servant generator is wired.
6. Leave widget revision proposal lifecycle, workspace versions, change log,
   and rollback to `AG-BE-DYNUI-002` unless the parent owner explicitly expands
   scope after review.
7. After schema and runtime routes land, hand OpenAPI/type drift to
   `AG-XR-DYNUI-001`; frontend tasks should not hand-write fixture clients to
   bypass missing BFF capability.

## Query Gap Ledger For Backend Owner

| Backend question | Required answer before implementation is called complete |
|---|---|
| What is the stable identity key for proposals? | Proposal records must be scoped by authenticated user, strategy id, strategy version, and proposal id. |
| Can a generating proposal be accepted? | No. `accept` must require a complete `preview` proposal. |
| What does accept return? | At minimum `workspace_id`, active workspace state, status, version/ETag metadata, and links for workspace read/layout. |
| How are empty dashboards prevented? | Proposal detail and accepted workspace must include all required generated views and widgets; capability-not-ready is preferable to a static empty shell. |
| How are layout races handled? | Require `If-Match` or equivalent ETag on layout, view, and widget mutations; return `412` with latest workspace link on mismatch. |
| Can removed widgets be restored? | Yes. A remove operation hides/removes from current view placement but retains restorable widget state/history. |
| Can servants directly patch widgets? | No. Servants must create revision proposals; direct widget PATCH is operator-initiated only. |
| What is the safe fallback while routes are absent? | Typed `501` or capability-not-ready response; no local fixtures, dashboard recipe substitution, or static mock workspace. |
| What should be rejected at BFF validation? | Raw JavaScript, React, HTML, external scripts, unsupported renderers, raw prompts, arbitrary data-source URLs, cross-user data, order actions, broker controls, capital binding, RuntimeBinding, and Management-plane terms. |

## Frontend Handoff Contract

Frontend work should bind only to the V11 workspace contract once the backend
has a real capability surface.

| Frontend need | Backend dependency | Required fallback posture |
|---|---|---|
| Join Trading Room | Proposal `POST` route with user/strategy/version scope. | Show capability-not-ready; do not navigate to an empty dashboard. |
| Generation progress | Proposal status or stream/poll hints from the create response. | Do not invent local progress from fixture state. |
| Proposal preview | Proposal `GET` returning views, thumbnail refs or previews, widget counts, rationale, data availability, warnings, and personalization applied. | Do not render `DashboardRecipeV2` as the V11 proposal. |
| Accept proposal | Proposal `accept` returning `workspace_id` and active workspace state. | Do not treat a dashboard recipe accept as workspace activation. |
| Workspace shell | Workspace `GET` returning `views[]`, active view id, widgets, status, generatedBy, version/ETag, and timestamps. | Empty state is an error/capability state, not a successful Trading Room. |
| Grid edit | Workspace layout `PATCH` with ETag. | Handle `412` by re-reading workspace and re-applying user intent. |
| Add/remove/restore widget | Workspace widget/layout routes with registry validation and retained hidden widget state. | Do not mutate local-only widget arrays as if persisted. |
| Widget adjustment drawer | `AG-BE-DYNUI-002` revision proposal routes, not direct widget PATCH by servant. | Keep servant adjustment disabled/capability-gated until revision routes exist. |

Suggested client methods remain:

```ts
generateWorkspaceProposal(strategyId, strategyVersion, hints?)
getWorkspaceProposal(strategyId, proposalId)
acceptWorkspaceProposal(strategyId, proposalId)
getWorkspace(workspaceId)
patchWorkspaceLayout(workspaceId, operations, etag)
addView(workspaceId, viewSpec)
updateView(workspaceId, viewId, patch)
addWidget(workspaceId, widgetSpec)
updateWidget(workspaceId, widgetId, patch)
```

## Review Checklist For Claude

| Check | Expected result |
|---|---|
| Sidecar boundary | This packet adds support material only and does not change runtime, schemas, OpenAPI, registry, governance logic, frontend code, or canonical truth. |
| Parent routing | `AG-BE-DYNUI-001` remains the owner for V11 proposal, workspace, view, widget schema and operator route contracts. |
| Neighbor routing | `AG-BE-DYNUI-002` remains owner for widget revision proposals, workspace versions, change log, and rollback. `AG-BE-DYNUI-003` remains owner for servant generator/validator integration. |
| Safety posture | No-order/no-capital/no-Management/no-runtime-binding boundary is preserved. |
| Frontend posture | Capability-not-ready is preferred over static fixtures, dashboard recipe substitution, or empty workspace navigation. |

## Focused Validation Run

Commands run from the task worktree:

```bash
git status -sb
git branch --show-current
git remote -v
jq '.tasks[]? | select(.id=="AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3")' ai-status.json
rg --files support/sidecars/AG-BE-DYNUI-001
rg -n "AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3|ag-be-dynui-001-sidecar-bff-handoff-followup-3" /home/lupin/code/pantheon/ai-status.json
jq '.tasks[]? | select(.id=="AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3")' /home/lupin/code/pantheon/ai-status.json
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh progress AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 "Verified task branch and support-only scope; preparing follow-up 3 handoff packet without canonical/runtime changes."
rg -n "TradingRoomWorkspace|WorkspaceProposal|workspace proposal|trading-room/proposals|trading-room/workspaces|proposal_id|workspace_id|dashboard_recipe_id" services/control-plane/bff services/control-plane/specs services/control-plane/openapi docs/04 -g '!node_modules' -g '!*.jsonl'
find services/control-plane/specs/agora -maxdepth 3 -type f
rg -n "^  /bff/agora/(strategies/.*/trading-room/proposals|trading-room/workspaces|trading-room|dashboard-recipes|strategies/.*/dashboard-recipes|widgets)" services/control-plane/openapi/agora_v1_2.openapi.yaml services/control-plane/openapi/agora_v1_3.openapi.yaml services/control-plane/openapi/agora_v1_4.openapi.yaml
rg -n "@router\.|def .*trading|dashboard_recipe_id|decision-events|stream|intent|workspace|proposal" services/control-plane/bff/agora/trading_room/router.py
rg -n "@router\.|If-Match|ETag|etag|layout|dashboard-recipes|widgets/validate|proposal|accept|rollback" services/control-plane/bff/agora/dashboard/router.py
rg -n "TradingRoomWorkspaceProposal|TradingRoomWorkspace|TradingRoomViewSpec|TradingRoomWidgetSpec|/bff/agora|WidgetRevisionProposal|workspace proposal|views|thumbnails|generation" /tmp/ai-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md
```

Validation conclusion:

- Branch is the expected `task/AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`.
- Supervisor root L0 state has this task as `in_progress`, owner `Codex`,
  reviewer `Claude`, and artifact
  `support/sidecars/AG-BE-DYNUI-001/AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`.
- Current BFF/OpenAPI/schema surfaces still lack V11 workspace proposal and
  workspace CRUD contracts.
- Existing dashboard recipe surfaces are useful references but insufficient
  substitutes.
- This sidecar produced only support/handoff material.
