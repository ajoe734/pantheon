# AG-BE-DYNUI-001 BFF Handoff Follow-Up 2

| Field | Value |
|---|---|
| Task ID | `AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-DYNUI-001` |
| Prepared by | `Codex2` |
| Reviewer | `Codex` |
| Date | 2026-06-28 |
| Mutates canonical truth | `false` |
| Status | Owner closeout finalized for parent absorption |

This follow-up is a support-only delta packet. It does not modify L1 canonical
truth, OpenAPI, JSON schemas, BFF runtime, widget registry, governance logic, or
frontend code. It narrows the already-approved
`AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF.md` into a parent-owner absorption order
and re-checks the current worktree for the V11 Trading Room workspace route and
schema gaps.

Closeout note: PR #2563 merged the original packet into `dev` at
`bc9c24c80de9e8c2633eac3cae82453223800702`. Review was reassigned from
`Claude` to `Codex` in central L0 state because the Claude lane was paused;
`Codex` approved the support-only packet and returned it to `Codex2` for owner
finalization.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; support packets must not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_be_dynui_001_sidecar_bff_handoff_followup_2.md` | This sidecar is support-only: BFF query gaps, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Preserve task-owned doc/support progress with scoped commits; do not sweep unrelated dirty files. |
| `.orchestrator/skills/task-closeout-finalization.md` | Closeout requires task artifacts, focused validation, scoped commit, PR flow, and reviewer approval before done. Central L0 state has this task in `review_approved` for owner finalization. |
| `support/sidecars/AG-BE-DYNUI-001/AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF.md` | Baseline packet already documents V11 workspace proposal gaps, journey, frontend client boundary, and reviewer checklist. |
| `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md` | V11 dynamic invariant: join Trading Room must create a complete `TradingRoomWorkspaceProposal`; `DashboardRecipeV2` alone is insufficient. |
| `/tmp/ai-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md` | V11 §5, §12, and §13 define proposal/workspace/view/widget shapes and route family. |
| `services/control-plane/bff/agora/trading_room/router.py` | Current router is `agora.trading.v1`: aggregate, strategy detail, decision events, SSE stub, governed intent handoff/withdraw. No workspace proposal or workspace CRUD routes. |
| `services/control-plane/bff/agora/dashboard/router.py` | Current dashboard recipe v2 router has useful ETag/layout/widget validation patterns, but its resource model is `DashboardRecipeV2`, not V11 `TradingRoomWorkspaceProposal`. |
| `services/control-plane/openapi/agora_v1_2.openapi.yaml` | Dashboard recipe routes exist under `/dashboard-recipes`; they are not the V11 Trading Room workspace route family. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | Trading Room v1.3 OpenAPI exposes read-only aggregate/decision-support routes only; no `/trading-room/proposals` or `/trading-room/workspaces` routes. |
| `services/control-plane/specs/agora/v2/dashboard_recipe_v2.schema.json` | Defines `DashboardRecipeV2` with views/widgets and proposal/active states, but lacks V11 proposal-level rationale, warnings, personalization summary, workspace state, and revision resources. |
| `services/control-plane/specs/agora/v2/widget_spec_v2.schema.json` | Registry-backed widget schema exists, but V11 `TradingRoomWidgetSpec` still needs explicit `purpose`, `whyIncluded`, placement min/max shape, and widget-context semantics. |
| `services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json` | Aggregate strategies may link to `dashboard_recipe_id`, but there is no workspace proposal/workspace lifecycle schema. |

## Current Verification Delta

| Check | Current result | Parent handoff meaning |
|---|---|---|
| `trading_room_workspace.schema.json` present | Not present under `services/control-plane/specs/agora/`. | `AG-BE-DYNUI-001` still needs to introduce the V11 proposal/workspace/view/widget schema before runtime route claims. |
| Workspace proposal routes present | Not present in `trading_room/router.py` or the Agora OpenAPI bundle. | `POST/GET/accept` proposal routes remain the first backend gap. |
| Workspace CRUD/layout routes present | Not present in `trading_room/router.py` or the Agora OpenAPI bundle. | Active workspace read, layout PATCH, view mutation, and widget mutation remain open. |
| Existing dashboard recipe can substitute | No. It is a separate `/dashboard-recipes` resource family. | Parent owner should borrow patterns, not resource identity or response shapes. |
| Widget registry foundation present | Yes: registry entries and `WidgetSpecV2` validation exist. | Useful validator substrate, but V11 fields and context envelope still require explicit schema design. |
| No-order boundary visible | Yes: v1.3 Trading Room contract and router are decision-support/request-only. | New workspace routes must preserve the same no-order/no-capital/no-Management boundary. |
| Status task entry | Central `PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon` L0 state has `AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` as `review_approved`, owner `Codex2`, reviewer `Codex`, with this packet as `review_file`. | Owner closeout can proceed after confirming the merged PR and preserving this metadata correction. Local worktree snapshots may still be stale. |

## Absorption Order For Parent Owner

1. **Contract first:** add a V11 schema file for `TradingRoomWorkspaceProposal`,
   `TradingRoomWorkspace`, `TradingRoomViewSpec`, `TradingRoomWidgetSpec`, and
   `WorkspaceLayoutOperation`. Keep `additionalProperties: false` on primary
   objects and define typed error envelopes for validation, stale ETag, not
   found, forbidden scope, and capability-not-ready responses.
2. **Proposal lifecycle next:** add `POST`, `GET`, and `accept` proposal routes
   under `/bff/agora/strategies/{strategy_id}/trading-room/proposals[...]`.
   Initial generation may be asynchronous/pending, but `accept` must only
   accept a complete `preview` proposal and must materialize an active workspace.
3. **Workspace read and layout:** add
   `GET /bff/agora/trading-room/workspaces/{workspace_id}` and
   `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/layout` with
   `If-Match`/ETag optimistic concurrency. Borrow the existing dashboard router
   concurrency and widget validation ideas, but persist a workspace resource, not
   a dashboard recipe.
4. **View/widget mutation:** add view `POST/PATCH` and widget `POST/PATCH`
   routes only for operator-initiated controlled edits. Servant-originated
   widget changes remain out of scope here and must route through
   `AG-BE-DYNUI-002` revision proposals.
5. **OpenAPI/type sync:** after schema and routes exist, route drift closure
   belongs to `AG-XR-DYNUI-001`; frontend runtime tasks should not ship local
   fixture clients while backend capability is absent.

## Minimal V11 Route Ledger

`AG-BE-DYNUI-001` should own these route contracts:

| Route | Required behavior | Explicit non-goal |
|---|---|---|
| `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals` | Create or enqueue a user/strategy/version-scoped workspace proposal. Return `proposal_id`, status, and polling/stream hints. | Do not return a static dashboard recipe as the proposal. |
| `GET /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}` | Return a complete `TradingRoomWorkspaceProposal` with views, widgets, rationale, data availability, warnings, and personalization applied. | Do not omit view/widget detail and force the frontend to infer layout. |
| `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept` | Transition a preview proposal into an active `TradingRoomWorkspace`. | Do not accept incomplete/generating proposals. |
| `GET /bff/agora/trading-room/workspaces/{workspace_id}` | Return user-scoped active workspace state, ETag/version, active view, views, widgets, and status. | Do not allow cross-user or cross-strategy reads. |
| `PATCH /bff/agora/trading-room/workspaces/{workspace_id}/layout` | Apply controlled layout ops with registry validation and stale-write protection. | Do not delete removed widgets permanently. |
| `POST/PATCH /bff/agora/trading-room/workspaces/{workspace_id}/views[/{view_id}]` | Create/update view specs and view metadata. | Do not bypass schema validation with arbitrary view payloads. |
| `POST/PATCH /bff/agora/trading-room/workspaces/{workspace_id}/widgets[/{widget_id}]` | Add/update operator-controlled widget specs using registry-validated widget/chart data. | Do not let servants directly mutate widgets; revision proposal lifecycle is separate. |

`AG-BE-DYNUI-002` remains the owner for widget revision proposals, workspace
versions, change log, and rollback. `AG-BE-DYNUI-003` remains the owner for the
real servant workspace generator and validator integration.

## Frontend Handoff Delta

Frontend tasks should treat capability absence as a hard gate:

| Frontend need | Backend contract dependency |
|---|---|
| Join Trading Room button | Requires proposal `POST` route. Until then, show capability-not-ready; do not navigate to an empty dashboard. |
| Generation/proposal preview | Requires proposal status/detail route returning complete V11 proposal fields. |
| Accept-to-workspace transition | Requires proposal `accept` response with `workspace_id` and initial workspace. |
| Workspace shell | Requires workspace `GET` with views/widgets and ETag. |
| Grid edits | Requires workspace layout `PATCH`; handle `412` by re-reading workspace. |
| Add/remove/restore widget | Requires workspace widget/layout routes and remove-but-retain semantics. |
| Servant widget adjustment | Out of this parent task; requires `AG-BE-DYNUI-002` revision proposal routes. |

No frontend task should use `DashboardRecipeV2`, local fixture workspace JSON, or
static prototype state as the live substitute for the V11 workspace proposal
contract.

## Safety Guards To Preserve

| Guard | Required backend posture |
|---|---|
| No order route | Workspace/proposal/layout routes never place broker orders, bind capital, or create `RuntimeBinding`. |
| Scope isolation | Every proposal and workspace is scoped to authenticated user, strategy, and strategy version. Cross-user reads return `403`. |
| Registry allowlist | Every widget type, chart kind, interaction, data source, and sensitivity level is validated against allowlisted specs. |
| No code injection | Reject raw JavaScript, React, HTML, external scripts, unsupported renderers, raw prompts, and arbitrary data-source URLs. |
| Honest capability gate | Return typed `501`/capability-not-ready while backend workspace routes are unavailable; never silently fall back to fixtures. |

## Focused Validation Run

Commands run from this worktree:

```bash
git status -sb
git branch --show-current
git remote -v
rg -n "AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2|ag-be-dynui-001-sidecar-bff-handoff-followup-2|BFF and frontend handoff" ai-status.json
AI_NAME=Codex2 ./scripts/ai-status.sh start AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 "Starting support-only AG-BE-DYNUI-001 follow-up packet; verifying current BFF/schema gaps before writing artifact."
rg -n "TradingRoomWorkspace|WorkspaceProposal|workspace proposal|trading-room/proposals|trading-room/workspaces|proposal_id|workspace_id" services/control-plane/bff services/control-plane/specs services/control-plane/openapi docs/04 -g '!node_modules' -g '!*.jsonl'
find services/control-plane/specs/agora -maxdepth 3 -type f | sort | rg "trading_room|workspace|dashboard_recipe|widget_spec|widget_registry"
rg -n "^  /bff/agora/(strategies/.*/trading-room/proposals|trading-room|dashboard-recipes|strategies/.*/dashboard-recipes|widgets)" services/control-plane/openapi/agora_v1_2.openapi.yaml services/control-plane/openapi/agora_v1_3.openapi.yaml services/control-plane/openapi/agora_v1_4.openapi.yaml
rg -n "TradingRoomWorkspaceProposal|TradingRoomWorkspace|TradingRoomViewSpec|TradingRoomWidgetSpec|/bff/agora|workspace proposal|BFF|WidgetRevisionProposal|views" /tmp/ai-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md
jq '.tasks[] | select(.id=="AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2") | {id,status,owner,reviewer,review_file,review_notes_zh}' /home/lupin/code/pantheon/ai-status.json
gh pr view 2563 --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,title,url,statusCheckRollup
git merge-base --is-ancestor 4ac32253 origin/dev
```

Validation conclusion:

- Branch is the expected per-task branch.
- Current BFF/OpenAPI/schema surfaces still lack V11 workspace proposal and
  workspace CRUD contracts.
- Existing dashboard recipe and widget registry surfaces are useful references
  but not sufficient substitutes.
- Central L0 state records reviewer approval by `Codex`; the local worktree
  snapshot may not include the generated sidecar row.
- PR #2563 is merged into `dev` at
  `bc9c24c80de9e8c2633eac3cae82453223800702`; required checks reported success
  before merge.

## Owner Closeout

Closeout verification:

| Check | Expected result |
|---|---|
| Scope | Only this support packet and the task-scoped brief are changed. |
| No canonical mutation | No BFF runtime, OpenAPI, schema, registry, frontend, or L1/L2 canonical doc changes are present. |
| Delta accuracy | The follow-up accurately states that V11 workspace proposal/workspace routes remain absent in the current worktree. |
| Parent boundary | `AG-BE-DYNUI-001` owns schema/proposal/workspace/view/widget operator routes; `AG-BE-DYNUI-002` owns widget revisions/history/rollback; `AG-BE-DYNUI-003` owns generator integration. |
| Frontend posture | Capability-not-ready is preferred over dashboard recipe substitution, local fixture fallback, or static prototype state. |

No further sidecar implementation is needed. Parent owner may absorb this packet
when sequencing `AG-BE-DYNUI-001`; `AG-BE-DYNUI-002` and `AG-BE-DYNUI-003`
boundaries remain unchanged.
