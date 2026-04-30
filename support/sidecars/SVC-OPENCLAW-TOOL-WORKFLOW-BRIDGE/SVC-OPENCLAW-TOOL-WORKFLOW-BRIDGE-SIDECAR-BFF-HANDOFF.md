# SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE` - Bridge OpenClaw tools and workflows safely  
**Parent Owner**: `Claude`  
**Parent Reviewer**: `Codex`  
**Parent Status at packet refresh time**: `review_approved`  
**Sidecar Task**: `SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE-SIDECAR-BFF-HANDOFF`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Codex2`  
**Helper Kind**: `bff_handoff_packet`  
**Generated/Refreshed**: `2026-04-30`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not change L1 truth, OpenClaw runtime policy, BFF route contracts, registry/governance behavior, or runtime-manager implementation. It packages the current evidence and handoff gaps for the parent owner to accept, revise, or ignore while implementing the canonical task.

## 1. Executive Summary

The parent task has now added and passed review for a safe OpenClaw tool/workflow bridge across:

- allowed tool and workflow policy enforcement
- Pantheon operator identity and request context mapping
- request/response audit trail
- fail-closed handling for unknown or disallowed tools
- no broker, paper, live, or capital-binding activation

Current repo truth now provides:

- `openclaw-gateway-adapter` has typed upstream capabilities and session calls.
- The adapter has Pantheon-owned lifecycle session routes with operator headers, idempotency, degraded recovery, and audit logs.
- The adapter has tool/workflow bridge routes for policy reads, effective tool listing, tool invocation, workflow triggering, workflow job status, and invocation audit.
- BFF has an OpenClaw operator ops projection at `/api/v1/operator/openclaw/ops` plus `/api/v1/operator/openclaw/tool-workflow-bridge` alias.
- BFF exposes authenticated, idempotent session create/cancel facades through the Pantheon-owned lifecycle adapter routes.
- BFF does not expose browser-facing tool invocation or workflow trigger commands.

The remaining BFF/frontend gap is therefore not "call OpenClaw directly from the frontend." The gap is frontend adoption and any future narrow command contract for tool invocation/workflow trigger, while preserving BFF-owned auth, idempotency, redaction, degradation semantics, and fail-closed policy projection.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable owner/reviewer/status truth for parent and sidecar tasks |
| `.orchestrator/task-briefs/svc_openclaw_tool_workflow_bridge_sidecar_bff_handoff.md` | Sidecar scope and artifact target |
| `.orchestrator/reviews/SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE-review-codex.md` | Parent review disposition and focused verification evidence |
| `.orchestrator/reviews/SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE-SIDECAR-BFF-HANDOFF-review-codex2.md` | Prior sidecar review finding that requested route snapshot refresh |
| `OPENCLAW_RUNTIME_CONTRACT.md` | L1 boundary: tool resolution, workflow/cron/hooks, error model, and no execution-kernel semantics |
| `services/openclaw-gateway-adapter/main.py` | Current adapter routes, fail-closed capability snapshot, typed upstream client, lifecycle route wiring, and tool/workflow bridge route wiring |
| `services/openclaw-gateway-adapter/tool_workflow_bridge.py` | Current policy engine, request/context mapping, bridge audit behavior, and fail-closed denial classes |
| `services/openclaw-gateway-adapter/lifecycle_client.py` | Downstream client for lifecycle create/get/list/cancel/audit without bypassing the adapter |
| `services/openclaw-gateway-adapter/session_lifecycle.py` | Durable Pantheon-owned session state machine and audit behavior |
| `services/openclaw-gateway-adapter/test_tool_workflow_bridge.py` | Test evidence for deny-by-default policy, always-blocked prefixes, operator context, and audit writes |
| `services/openclaw-gateway-adapter/test_session_lifecycle.py` | Evidence for idempotent lifecycle, operator ownership, audit metadata, and degraded recovery |
| `services/control-plane/bff/read_store.py` | Current BFF OpenClaw ops projection for capabilities, upstream status, lifecycle sessions, tool policy, effective tools, invocation audit, gate state, and degradation |
| `services/control-plane/bff/openclaw_ops_client.py` | BFF service client for adapter-backed OpenClaw ops, lifecycle session commands, policy, effective tools, and invocation audit |
| `services/control-plane/bff/main.py` | Current BFF OpenClaw ops read routes and authenticated idempotent session command facades |
| `services/control-plane/bff/test_openclaw_ops_surface.py` | Test evidence for BFF OpenClaw ops projection, degradation, session create/cancel auth, and idempotency |
| `services/control-plane/bff/test_research_oss_preactivation_contract.py` | Current BFF test evidence that OpenClaw remains fail-closed in OSS activation-ready views |

## 3. Current Surface Snapshot

### 3.1 Adapter Routes Already Available

| Route | Current role | Frontend guidance |
|---|---|---|
| `GET /api/openclaw-adapter/capabilities` | Static fail-closed adapter capability snapshot plus optional upstream capabilities | BFF may read this; frontend should not call it directly |
| `GET /api/openclaw-adapter/upstream/status` | OpenClaw upstream reachability | BFF may compose degradation state from it |
| `GET /api/openclaw-adapter/sessions` | Typed upstream session list, degraded when upstream absent | Low-level adapter route; not a frontend contract |
| `GET /api/openclaw-adapter/sessions/{session_id}` | Typed upstream session read | Low-level adapter route; not a frontend contract |
| `POST /api/openclaw-adapter/sessions` | Typed upstream session create; broker paths disabled | Parent task must preserve governance/policy gate before any BFF command exposes this |
| `POST /api/openclaw-adapter/sessions/{session_id}/cancel` | Typed upstream cancel | Parent task must preserve operator/audit context |
| `GET /api/openclaw-adapter/lifecycle/sessions` | Pantheon-owned durable session list | Strong candidate source for BFF operator read views |
| `GET /api/openclaw-adapter/lifecycle/sessions/{session_id}` | Pantheon-owned session record, optionally refreshed from upstream | Strong candidate source for BFF session detail |
| `POST /api/openclaw-adapter/lifecycle/sessions` | Idempotent create with `X-Operator-Id` and optional `X-Idempotency-Key` | Prefer this over raw upstream session create if BFF exposes session-backed commands |
| `POST /api/openclaw-adapter/lifecycle/sessions/{session_id}/cancel` | Operator-owned cancel preserving local state on upstream degradation | Candidate command path for safe operator cancel only |
| `GET /api/openclaw-adapter/lifecycle/sessions/{session_id}/audit` | Append-only audit trail for one session | Candidate source for BFF audit drawer/timeline |
| `GET /api/openclaw-adapter/tools/policy` | Current adapter policy snapshot: allowed tools/workflows, always-blocked tools/prefixes, deny-all default | Candidate source for BFF bridge posture; frontend should consume only BFF projection |
| `GET /api/openclaw-adapter/tools` | Effective tool list for `agent_id`/optional `session_id`, requiring `X-Operator-Id`; returns policy allowlist intersected with upstream tools when reachable | Candidate source for BFF allowed-action/schema projection, not a browser contract |
| `POST /api/openclaw-adapter/tools/invoke` | Policy-checked tool invocation requiring `X-Operator-Id`; writes audit for allowed, denied, pending, upstream error, and success outcomes | Candidate BFF command target after BFF-owned auth/idempotency handling |
| `POST /api/openclaw-adapter/workflows/trigger` | Policy-checked workflow trigger requiring `X-Operator-Id`; writes audit and maps operator context upstream | Candidate BFF command target; UI must treat response as submitted/job-backed, not live execution |
| `GET /api/openclaw-adapter/workflows/jobs/{job_id}` | Upstream workflow job status read | Candidate BFF read source for submitted workflow status |
| `GET /api/openclaw-adapter/audit/invocations` | Bridge invocation audit list filtered by `session_id`/`operator_id` with `limit` | Candidate source for BFF invocation timeline after redaction/projection |

### 3.2 BFF Routes Currently Available

| Route | OpenClaw content today | BFF/frontend gap |
|---|---|---|
| `GET /api/v1/operator/research/oss-activation-ready` | Includes OpenClaw adapter capability/upstream status as fail-closed backend inventory | Does not expose tool/workflow bridge attempts, policy decisions, or lifecycle audit |
| `GET /api/v1/operator/research/oss-preactivation` | Alias for the same read-only surface | Same gap as canonical route |
| `GET /api/v1/operator/openclaw/ops` | BFF-composed OpenClaw ops snapshot: capabilities, upstream reachability, gate state, lifecycle sessions, tool policy, effective tools when requested, invocation audit, allowedActions, and degradation | Frontend can adopt this as the operator read model; tool/workflow invoke commands still absent |
| `GET /api/v1/operator/openclaw/tool-workflow-bridge` | Alias for the same OpenClaw ops snapshot, with bridge-oriented route name | Same read model; no separate browser authority over adapter internals |
| `POST /api/v1/operator/openclaw/sessions` | Authenticated, role-checked, idempotent BFF facade to `POST /api/openclaw-adapter/lifecycle/sessions` | Safe session command exists; frontend must supply idempotency and use returned accepted/replayed state |
| `POST /api/v1/operator/openclaw/sessions/{session_id}/cancel` | Authenticated, role-checked, idempotent BFF facade to adapter lifecycle cancel | Safe cancel command exists; frontend should only render from BFF `allowedActions` |

## 4. BFF Operator Surface

This packet does not define canonical truth. Current worktree already contains a BFF operator view; the guidance below records the support-slice handoff shape for frontend adoption and future parent-owner decisions.

### 4.1 Current Read Routes

`GET /api/v1/operator/openclaw/ops`

`GET /api/v1/operator/openclaw/tool-workflow-bridge`

Current query parameters:

| Parameter | Default | Bounds | Purpose |
|---|---:|---:|---|
| `session_limit` | `25` | `1..100` | Bound lifecycle/session rows |
| `audit_limit` | `20` | `1..100` | Bound per-session audit excerpts |
| `state` | absent | backend enum | Optional lifecycle state filter |
| `operator_id` | absent | opaque | Optional server-authorized operator filter, not a trust source |
| `agent_id` | absent | opaque | Optional effective-tool lookup key |
| `session_id` | absent | opaque | Optional session context for effective-tool lookup |

Current response shape:

```json
{
  "data": {
    "surface": "openclaw_ops",
    "surface_aliases": ["openclaw_tool_workflow_bridge"],
    "overall_status": "ok | degraded | unavailable",
    "activation": {},
    "gate_state": {},
    "production_activation": "disabled",
    "upstream": {},
    "session_lifecycle": {"sessions": [], "state_counts": {}},
    "tool_workflow": {
      "policy": {},
      "effective_tools": {},
      "audit": {"entries": [], "outcome_counts": {}, "policy_decision_counts": {}},
      "bridge_posture": {
        "policy_state": "adapter_enforcing | degraded",
        "unknown_tools": "fail_closed",
        "disallowed_tools": "fail_closed",
        "workflow_triggers": "adapter_policy_checked",
        "bff_tool_invocation_commands": "not_exposed",
        "bff_workflow_trigger_commands": "not_exposed"
      }
    },
    "operator_controls": {},
    "allowedActions": {"canCreateSession": true, "canInvokeTool": false, "canTriggerWorkflow": false},
    "degradation": {"status": "ok | degraded | unavailable", "reasons": []},
    "service_status": {}
  },
  "meta": {
    "snapshot_at": "2026-04-30T00:00:00Z",
    "surfaces": {
      "openclaw_ops": {"status": "ok | degraded | unavailable"},
      "openclaw_tool_workflow_bridge": {"status": "ok | degraded | unavailable"}
    }
  }
}
```

### 4.2 Current And Candidate Command Boundaries

Commands should stay absent from the browser until the BFF owns auth, idempotency, projection, and redaction around the adapter bridge. Current BFF session lifecycle commands meet that shape; tool invocation and workflow trigger commands remain deliberately absent.

| Command | Current status | Required backend guarantees | Frontend handling |
|---|---|---|
| Start bridge-backed session | Implemented at `POST /api/v1/operator/openclaw/sessions` | Operator role required, server-derived operator identity, idempotency key required, adapter lifecycle audit preserved | Use only when `allowedActions.canCreateSession = true`; treat 202 as accepted and 200 as replayed |
| Cancel bridge-backed session | Implemented at `POST /api/v1/operator/openclaw/sessions/{session_id}/cancel` | Operator role required, server-derived operator identity, idempotency key required, lifecycle record preserved if upstream degraded | Use only when the row marks `allowedActions.canCancel = true` |
| Invoke allowed tool | Not exposed by BFF | BFF derives operator identity server-side, forwards through `POST /api/openclaw-adapter/tools/invoke`, preserves trace id, and projects audit/error results | Do not construct raw tool args outside BFF-supplied schema |
| Trigger allowed workflow | Not exposed by BFF | BFF derives operator identity server-side, forwards through `POST /api/openclaw-adapter/workflows/trigger`, preserves trace id, and exposes job status separately | Treat as submitted/job-backed, not completed |

Do not expose browser calls to:

- raw `POST /api/openclaw-adapter/sessions`
- raw `POST /api/openclaw-adapter/tools/invoke`
- raw `POST /api/openclaw-adapter/workflows/trigger`
- raw upstream `/api/sessions`
- raw upstream tool/workflow endpoints
- broker, paper, live, or capital-binding paths
- registry or governance mutation paths

## 5. Frontend Handoff Materials

### 5.1 Operator Journey

1. Operator opens the OpenClaw tool/workflow bridge page.
2. UI reads the BFF composed route, not adapter internals.
3. UI renders activation posture first: broker, paper, live, and capital binding remain deferred/fail-closed.
4. UI renders bridge posture: whether policy enforcement is available, degraded, or not implemented.
5. UI renders lifecycle sessions and recent invocation rows from BFF-composed fields.
6. UI opens an audit drawer using BFF-provided audit excerpts or a BFF detail route.
7. UI renders command buttons only from `allowedActions`; unknown or missing actions are disabled.
8. UI treats workflow trigger responses as async acceptance records, not execution success.

### 5.2 Suggested Screen Regions

| Region | Data source | Notes |
|---|---|---|
| Activation posture strip | `activation` | Must keep production/broker/capital states visibly disabled |
| Bridge policy panel | `tool_workflow.bridge_posture`, `tool_workflow.audit.policy_decision_counts` | Show fail-closed decisions distinctly from transport degradation |
| Session table | `session_lifecycle.sessions[]` | Include session id, agent id, type, state, operator, created/updated, last error |
| Invocation timeline | `tool_workflow.audit.entries[]` | Include tool/workflow name, decision, status, retryability, trace/request ids |
| Audit drawer | `tool_workflow.audit` or future per-session audit route | Show create/cancel/policy/invoke events in append order |
| Degradation banner | `meta.surfaces`, `degradation` | Do not show empty success when adapter/upstream is unavailable |

### 5.3 UI Rules

- Use BFF-provided `allowedActions` only; do not infer permissions from role labels, session state, or capability text.
- Unknown `tool_name`, `workflow_ref`, or missing policy decision means disabled/fail-closed.
- Display `retryable` from backend error envelopes; do not invent retry policy in the browser.
- Keep raw `context_bundle`, secrets, credentials, and upstream payload internals out of visible UI unless the BFF explicitly redacts and exposes them.
- Show `trace_id`, `request_id`, and `session_id` as support/debug identifiers when provided.

## 6. BFF Query Gap Matrix

| Area | Current state | BFF/frontend gap |
|---|---|---|
| Bridge overview route | BFF exposes `/api/v1/operator/openclaw/ops` and `/api/v1/operator/openclaw/tool-workflow-bridge` | Frontend adoption and visual treatment remain downstream work |
| Tool policy decisions | Adapter exposes `GET /api/openclaw-adapter/tools/policy`; BFF projects it under `tool_workflow.policy` | UI must render backend decisions only and keep unknowns disabled |
| Workflow policy decisions | Adapter exposes workflow allowlist policy and `POST /api/openclaw-adapter/workflows/trigger`; BFF projects policy posture but no trigger command | Add trigger command only if BFF owns auth/idempotency/schema/redaction |
| Invocation audit | Adapter exposes `GET /api/openclaw-adapter/audit/invocations`; BFF projects bounded entries and counts | UI needs timeline/adjudication treatment without exposing raw args/context |
| Effective tools | Adapter exposes `GET /api/openclaw-adapter/tools`; BFF fetches it only when `agent_id` and authenticated operator context are present | UI schemas remain a future projection concern |
| Session state | Adapter lifecycle routes exist; BFF projects bounded sessions and degradation | Per-session detail/audit drawer route may still be useful if the frontend needs drill-in |
| Error model | Adapter maps upstream errors; BFF projects unavailable/degraded surfaces and retryability in audit entries | UI should preserve backend retryability and trace/request identifiers |
| Degradation | BFF distinguishes surface failures, upstream unreachable state, capability degradation, and degraded sessions | UI should avoid empty-success states when any OpenClaw surface is unavailable |
| Commands | BFF exposes session create/cancel only; tool invocation/workflow trigger commands are not exposed | Keep invoke/trigger disabled until BFF command contract exists |

## 7. Parent Reviewer Checklist

For parent implementation review (`Codex`, already approved after follow-up) and downstream BFF review:

- Confirm the parent implementation enforces allowed tool/workflow policy server-side.
- Confirm operator identity and request context are forwarded from BFF/runtime-manager into the adapter without trusting client-supplied identity fields.
- Confirm request/response audit includes at least session id, trace/request id, actor/operator, tool/workflow ref, decision, status, and error code when applicable.
- Confirm unknown tools, disallowed tools, unknown workflows, and unsupported upstream responses fail closed.
- Confirm broker, paper, live, registry, governance, and capital-binding writes are not activated by the bridge.
- Confirm BFF does not expose raw adapter internals as a frontend contract.
- Confirm BFF session commands preserve server-side operator identity and idempotency.
- Confirm BFF still does not expose tool invocation or workflow trigger commands unless the command contract is explicitly added and reviewed.

For the sidecar reviewer `Codex2`:

- Confirm this packet is support-only and does not edit canonical truth.
- Confirm current-route statements match the adapter/BFF code at review time, including the current OpenClaw ops route and session command facades.
- Confirm current route/field guidance is support-only and does not promote canonical truth by itself.
- Confirm frontend guidance preserves fail-closed behavior and avoids client-side policy inference.

## 8. Verification Notes

Sidecar refresh verification performed by Codex after Codex2 requested changes:

- Read task-scoped brief (`svc_openclaw_tool_workflow_bridge_sidecar_bff_handoff.md`), closeout rule, and current `ai-status.json` task assignment to confirm sidecar owner=Codex, reviewer=Codex2, status=review; parent task status is `review_approved`.
- Read `.orchestrator/reviews/SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE-review-codex.md`: parent review is approved after follow-up, with bridge tests 56/56 and adapter suite 119/119 recorded by the parent reviewer.
- Read `.orchestrator/reviews/SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE-SIDECAR-BFF-HANDOFF-review-codex2.md`: sidecar change request was stale adapter route evidence.
- Inspected `OPENCLAW_RUNTIME_CONTRACT.md` §2.2 and §4.3/§4.6: tool/workflow bridge surfaces are now listed as repo truth; broker/paper/live/capital-binding remain deferred.
- Inspected `services/openclaw-gateway-adapter/main.py` (922 lines): confirmed adapter routes listed in §3.1 include `/tools/policy`, `/tools`, `/tools/invoke`, `/workflows/trigger`, `/workflows/jobs/{job_id}`, and `/audit/invocations`.
- Inspected `services/openclaw-gateway-adapter/tool_workflow_bridge.py`: confirmed deny-by-default allowlists, always-blocked broker/live/paper/canary/capital/lean prefixes, operator context mapping, and append-only invocation audit behavior.
- Confirmed `services/control-plane/bff/read_store.py` now projects `get_openclaw_ops_snapshot(...)` with capabilities, upstream status, lifecycle sessions, tool policy, effective tools when `agent_id` and authenticated operator context are present, invocation audit, gate state, allowedActions, and degradation.
- Confirmed `services/control-plane/bff/openclaw_ops_client.py` calls the Pantheon-owned adapter routes for capabilities, upstream status, lifecycle session list/create/cancel, tool policy, effective tools, and invocation audit.
- Confirmed `services/control-plane/bff/main.py` exposes `GET /api/v1/operator/openclaw/ops`, `GET /api/v1/operator/openclaw/tool-workflow-bridge`, `POST /api/v1/operator/openclaw/sessions`, and `POST /api/v1/operator/openclaw/sessions/{session_id}/cancel`.
- Confirmed `services/control-plane/bff/BFF_API_CONTRACT.md §10.1` records the OpenClaw ops composed view and bridge alias; session commands are implementation routes and do not activate broker/paper/live/capital paths.
- Confirmed `services/control-plane/bff/test_openclaw_ops_surface.py` covers BFF OpenClaw ops aggregation/degradation and session command auth/idempotency forwarding.
- Ran `python3 -m pytest services/control-plane/bff/test_openclaw_ops_surface.py -q`: 4 passed.
- Refreshed this packet to distinguish implemented adapter bridge routes, implemented BFF read/session facades, and still-missing BFF tool/workflow invocation commands plus frontend adoption.

No runtime, registry, governance, BFF implementation, L1 canonical document, or frontend implementation files were edited by this sidecar.

## 9. Sidecar Scope Check

| Check | Result |
|---|---|
| Only support artifact changed | Yes |
| L1 canonical truth changed | No |
| Runtime/registry/governance implementation changed | No |
| BFF implementation changed | No |
| Frontend implementation changed | No |
| Parent owner must decide whether to absorb guidance | Yes |
