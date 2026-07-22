# AG-BE-DYNUI-001 Sidecar Review Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-DYNUI-001-SIDECAR-REVIEW` |
| Helper kind | `review_packet` |
| Parent task | `AG-BE-DYNUI-001` |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Prepared by | `Codex` |
| Reviewer | `Codex2` |
| Date | 2026-06-29 |
| Mutates canonical truth | `false` |
| Status | Review approved; owner closeout in progress |

This is a support-only review packet for `AG-BE-DYNUI-001`. It does not change
L1 canonical truth, OpenAPI, JSON Schema, BFF runtime, widget registry,
governance logic, persistence code, or frontend code. It summarizes the parent
implementation evidence, focused verification, residual scope boundaries, and
handoff questions for the assigned sidecar reviewer.

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Current Task State Observed

| Source | Observation |
|---|---|
| `.orchestrator/task-briefs/ag_be_dynui_001_sidecar_review.md` | This sidecar may create support material only and must not modify canonical truth or runtime implementation. |
| Central `PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon` L0 state | `AG-BE-DYNUI-001-SIDECAR-REVIEW` is `review_approved`, owner `Codex`, reviewer `Codex2`, artifact `support/sidecars/AG-BE-DYNUI-001/AG-BE-DYNUI-001-SIDECAR-REVIEW.md`. |
| Central `PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon` archive | Parent `AG-BE-DYNUI-001` is `done`; closeout notes say implementation PR #2577 and closeout PR #2579 merged to `dev`, with focused validation `37 + 18 + 2` passed. |
| Current task branch | `task/AG-BE-DYNUI-001-SIDECAR-REVIEW`, based on parent closeout merge `eac485c90360a93545b5bf023e9324ca50c1b342` (`Merge pull request #2579 from ajoe734/task/AG-BE-DYNUI-001`). |

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; support packets do not override canonical product or architecture truth. |
| `.orchestrator/skills/worker-anchor-commit.md` | Support progress must be preserved through scoped commits and must not sweep unrelated worktree changes. |
| `.orchestrator/skills/task-closeout-finalization.md` | Closeout requires scoped artifacts, validation, commit/PR flow, reviewer approval, and `done` only after merge. |
| `support/sidecars/AG-BE-DYNUI-001/AG-BE-DYNUI-001-IMPLEMENTATION-EVIDENCE.md` | Parent evidence records delivered schema, store, routes, and focused test results. |
| `support/sidecars/AG-BE-DYNUI-001/AG-BE-DYNUI-001-SIDECAR-BFF-HANDOFF*.md` | Prior packets were pre-implementation gap/handoff material. They are useful for scope boundaries but are superseded by the implemented surfaces. |
| `services/control-plane/specs/agora/trading_room_workspace.schema.json` | Defines V11 proposal/workspace/view/widget/layout operation contracts with `additionalProperties: false` on primary resources. |
| `services/control-plane/bff/agora/trading_room/store.py` | Adds tenant/user-scoped in-process records for workspace proposals and workspaces. |
| `services/control-plane/bff/agora/trading_room/router.py` | Adds proposal create/get/accept, workspace read, layout patch, view mutation, and widget mutation routes. |
| `services/control-plane/bff/agora/trading_room/test_trading_room.py` | Covers complete V11 view set, schema validity, accept-to-workspace, ETag layout mutation, stale-write rejection, remove/restore, registry validation, servant direct patch rejection, code injection rejection, and cross-user read denial. |

## Parent Implementation Evidence Summary

| Surface | Evidence | Review note |
|---|---|---|
| V11 schema | `trading_room_workspace.schema.json` defines `TradingRoomWorkspaceProposal`, `TradingRoomWorkspace`, `TradingRoomViewSpec`, `TradingRoomWidgetSpec`, `WidgetPlacement`, `WorkspaceLayoutOperation`, and `WorkspaceError`. | Contract exists as a distinct Trading Room workspace resource, not a `DashboardRecipeV2` substitute. |
| Proposal lifecycle | Router exposes `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals`, `GET /.../proposals/{proposal_id}`, and `POST /.../accept`. | Create returns a complete preview proposal; accept requires `preview` status and materializes an active workspace. |
| Workspace lifecycle | Router exposes `GET /bff/agora/trading-room/workspaces/{workspace_id}` and `PATCH /.../layout`. | Workspace read returns an ETag; layout mutation requires `If-Match` and rejects stale writes with `412`. |
| View/widget mutation | Router exposes `POST/PATCH /views[/{view_id}]` and `POST/PATCH /widgets[/{widget_id}]`. | Operator-controlled mutations are schema/registry validated; servant-originated direct widget patch is rejected. |
| Scope isolation | Store records include `tenant_id` and `user_id`; loader checks record visibility before returning resources. | Focused test confirms cross-user workspace read returns `403`. |
| Safety posture | Widget validation maps V11 widgets into registry `WidgetSpecV2` validation and checks forbidden interactions. | Tests confirm non-allowlisted widgets and script-like chart options are rejected. |

## Focused Verification Run

Commands run from this task worktree:

```bash
python3 -m py_compile services/control-plane/bff/agora/trading_room/router.py services/control-plane/bff/agora/trading_room/store.py services/control-plane/bff/agora/trading_room/test_trading_room.py
python3 -m pytest services/control-plane/bff/agora/trading_room/test_trading_room.py -q
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py -q
python3 -m pytest services/control-plane/bff/test_route_resolution_no_shadowing.py -q
```

Observed result:

| Command | Result |
|---|---|
| `py_compile` | Passed. |
| `test_trading_room.py -q` | `37 passed in 9.89s`. |
| `test_agora_router.py -q` | `18 passed in 8.37s`; existing FastAPI `on_event` deprecation warnings only. |
| `test_route_resolution_no_shadowing.py -q` | `2 passed in 5.08s`; existing FastAPI `on_event` deprecation warnings only. |

## Review Findings

No blocking issue was found within the implemented `AG-BE-DYNUI-001` parent
scope.

The implementation satisfies the support packet's expected parent surfaces:
schema, proposal lifecycle, active workspace read, ETag-protected layout patch,
view/widget mutation, registry validation, no direct servant widget mutation,
code-injection rejection, and cross-user scope isolation.

## Reviewer Approval Observed

Central L0 state records Codex2 review approval for this sidecar packet:

| Review note | Closeout interpretation |
|---|---|
| PR #2580 diff is support-only: task brief plus sidecar review packet. | No canonical truth, L1 policy, runtime, registry, or governance implementation change is introduced by this sidecar. |
| Branch CI gates are green and focused local checks passed: `py_compile`, `trading_room` 37 passed, `agora_router` 18 passed, and route resolution 2 passed. | The evidence packet has sufficient reviewer-approved validation for its support role. |
| Store-level BFF evidence is clear and does not overclaim durable DB persistence or downstream `AG-BE-DYNUI-002` / `AG-BE-DYNUI-003` work. | Residual boundaries are explicit enough for downstream parent-owner routing. |

Owner closeout must still wait for PR #2580 to merge into `dev` before
running `AI_NAME=Codex ./scripts/ai-status.sh done`.

## Scope Boundaries To Keep

| Boundary | Current disposition |
|---|---|
| Durable DB-backed workspace persistence | Not proven by this sidecar. The evidence supports BFF store-level, tenant/user-scoped records in `TradingRoomStore`; do not overclaim durable database persistence without a follow-up. |
| Real servant workspace generator | Out of parent scope and remains `AG-BE-DYNUI-003`; current route returns a complete contract-level preview proposal. |
| Widget revision proposals, workspace versions, change log, rollback | Out of parent scope and remain `AG-BE-DYNUI-002`. |
| OpenAPI/generated frontend type sync | Out of parent scope and remains `AG-XR-DYNUI-001`. |
| RuntimeBinding, broker order routing, capital binding, Management-plane operations | Not introduced by this implementation and must remain out of Trading Room workspace routes. |

## Reviewer Handoff

Reviewer `Codex2` has approved the packet in central L0 state. No further
reviewer action is pending unless PR #2580 receives a new correction request or
CI regresses before merge. Parent implementation changes remain outside this
sidecar.

## Handoff Summary

`AG-BE-DYNUI-001` is review-ready from this sidecar's perspective. The parent
implementation has focused passing tests and no sidecar-blocking issue was
found. Downstream tasks should consume the implemented route/schema surfaces
while preserving the boundaries listed above.
