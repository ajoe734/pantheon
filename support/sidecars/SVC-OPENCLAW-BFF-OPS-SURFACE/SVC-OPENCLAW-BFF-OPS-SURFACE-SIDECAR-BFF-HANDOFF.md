# SVC-OPENCLAW-BFF-OPS-SURFACE BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `SVC-OPENCLAW-BFF-OPS-SURFACE` - Expose OpenClaw operations in BFF
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude2`
**Parent Status at packet creation time**: `done`
**Parent Closeout Commit**: `52078b85652d73f9b36356cae645e75142d1243e`
**Sidecar Task**: `SVC-OPENCLAW-BFF-OPS-SURFACE-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Codex2`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-04-30`
**Mutates canonical**: `no`

> This is a support artifact only. It does not change L1 truth, BFF route
> contracts, OpenClaw adapter behavior, registry/governance behavior, runtime
> implementation, or frontend implementation. It packages the current BFF
> operations surface and the remaining handoff guidance for reviewer and
> frontend adoption.

## 1. Executive Summary

The parent task is already complete. Commit `52078b8` added the OpenClaw BFF
operations surface and closed it with reviewer approval. The implemented BFF
surface gives operators a service-client-backed read model for:

- OpenClaw adapter capability and upstream reachability.
- Pantheon-owned lifecycle sessions with bounded rows, state counts, degraded
  markers, and row-level `allowedActions`.
- Tool/workflow bridge policy and bounded invocation audit.
- Explicit paper/live gate state while keeping broker, paper, live, and
  capital-binding activation disabled at the BFF.
- Authenticated, idempotent session create/cancel command facades.

The remaining handoff work is mostly frontend adoption and future-scope guard
rails. The UI should consume the composed BFF route, not the adapter. It should
render degradation and fail-closed decisions as backend-owned state, and it must
not infer activation or policy from labels, local role checks, or adapter internals.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable sidecar owner/reviewer/status truth |
| `ai-task-archive/tasks/SVC-OPENCLAW-BFF-OPS-SURFACE.json` | Parent done record, reviewer approval, closeout commit, and delivery metadata |
| `.orchestrator/task-briefs/svc_openclaw_bff_ops_surface_sidecar_bff_handoff.md` | Sidecar scope and artifact target |
| `services/control-plane/bff/openclaw_ops_client.py` | BFF service client for OpenClaw adapter capability, status, lifecycle, policy, tools, audit, and session commands |
| `services/control-plane/bff/read_store.py` | BFF projection for `get_openclaw_ops_snapshot(...)` |
| `services/control-plane/bff/main.py` | Browser-facing BFF routes and auth/idempotency command guards |
| `services/control-plane/bff/test_openclaw_ops_surface.py` | Focused BFF test evidence for healthy, degraded, auth, idempotency, create, and cancel behavior |
| `services/control-plane/bff/BFF_API_CONTRACT.md` | Current route table for OpenClaw composed view endpoints |
| `docs/pantheon-handoffs/SVC-OPENCLAW-BFF-OPS-SURFACE/FRONTEND_CHANGE_SPEC.md` | Frontend-ready change spec produced by the parent task |
| `support/sidecars/SVC-OPENCLAW-SESSION-LIFECYCLE/SVC-OPENCLAW-SESSION-LIFECYCLE-SIDECAR-BFF-HANDOFF.md` | Upstream lifecycle handoff dependency |
| `support/sidecars/SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE/SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE-SIDECAR-BFF-HANDOFF.md` | Tool/workflow bridge handoff dependency |

## 3. Current BFF Surface Snapshot

### 3.1 Read Routes

| Route | Status | Purpose |
|---|---|---|
| `GET /api/v1/operator/openclaw/ops` | implemented | Canonical BFF-composed OpenClaw operator ops snapshot |
| `GET /api/v1/operator/openclaw/tool-workflow-bridge` | implemented alias | Bridge-oriented route name for the same snapshot |
| `GET /api/v1/operator/research/oss-activation-ready` | implemented | Broader OSS activation-ready view that also includes OpenClaw inventory |
| `GET /api/v1/operator/research/oss-preactivation` | implemented alias | Backward-compatible alias for the broader OSS read-only view |

Supported query parameters on the OpenClaw ops routes:

| Parameter | Default | Bounds | Purpose |
|---|---:|---:|---|
| `session_limit` | `25` | `1..100` | Bound lifecycle session rows |
| `audit_limit` | `20` | `1..100` | Bound invocation audit rows |
| `state` | absent | backend enum | Optional lifecycle state filter |
| `operator_id` | absent | self-only unless admin | Optional operator filter |
| `agent_id` | absent | opaque | Optional effective-tool lookup key |
| `session_id` | absent | opaque | Optional effective-tool session context |

The BFF enforces the cross-operator filter rule: non-admin operators may only
filter by their own `operator_id`.

### 3.2 Command Routes

| Route | Status | Guards | Adapter target |
|---|---|---|---|
| `POST /api/v1/operator/openclaw/sessions` | implemented | `operator` or `admin`, `X-Idempotency-Key`, server-derived operator identity | `POST /api/openclaw-adapter/lifecycle/sessions` |
| `POST /api/v1/operator/openclaw/sessions/{session_id}/cancel` | implemented | `operator` or `admin`, `X-Idempotency-Key`, server-derived operator identity | `POST /api/openclaw-adapter/lifecycle/sessions/{session_id}/cancel` |

Current BFF command envelope returns `data.command`, `data.status`,
`data.accepted_at`, `data.adapter_status`, `data.replayed`, and `data.session`.
Create returns `202` for new acceptance and `200` for adapter replay. Cancel
returns `202`.

Do not add browser calls to adapter internals. The BFF is the browser contract.

## 4. Current Response Model

The read surface returns:

```json
{
  "data": {
    "surface": "openclaw_ops",
    "surface_aliases": ["openclaw_tool_workflow_bridge"],
    "overall_status": "ok | degraded | unavailable",
    "activation": {
      "activation_state": "string",
      "session_lifecycle_state": "string",
      "fail_closed": true,
      "supported_session_types": []
    },
    "gate_state": {
      "paper_adapter": {
        "state": "deferred | enabled | ...",
        "enabled": false,
        "activation_gate": "OPENCLAW_PAPER_ADAPTER_ENABLED",
        "bff_activation_command": "not_exposed"
      },
      "live_adapter": {
        "state": "deferred | enabled | ...",
        "enabled": false,
        "activation_gate": "OPENCLAW_LIVE_ADAPTER_ENABLED",
        "bff_activation_command": "not_exposed"
      }
    },
    "production_activation": "disabled",
    "upstream": {
      "status": "ok | degraded | unavailable",
      "reachable": true,
      "reason": "string | null"
    },
    "session_lifecycle": {
      "status": "ok | degraded | unavailable",
      "count": 0,
      "state_counts": {},
      "sessions": [],
      "degraded_session_count": 0,
      "filters": {"operator_id": null, "state": null}
    },
    "tool_workflow": {
      "policy": {},
      "effective_tools": null,
      "audit": {
        "status": "ok | degraded | unavailable",
        "count": 0,
        "outcome_counts": {},
        "policy_decision_counts": {},
        "entries": []
      },
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
    "allowedActions": {
      "canCreateSession": true,
      "canInvokeTool": false,
      "canTriggerWorkflow": false,
      "canEnablePaper": false,
      "canEnableLive": false
    },
    "degradation": {"status": "ok | degraded | unavailable", "reasons": []},
    "service_status": {}
  },
  "meta": {
    "snapshot_at": "2026-04-30T00:00:00Z",
    "surfaces": {}
  }
}
```

Important UI interpretation:

- `denied` policy rows are healthy fail-closed evidence when the surface is
  otherwise available.
- `overall_status = degraded` means the page should remain visible and read-only
  for unavailable sub-surfaces.
- `overall_status = unavailable` means the UI should show the backend-provided
  unavailable reasons, not an empty table as success.
- Missing or unknown actions are disabled.

## 5. BFF Query Gap Matrix

| Area | Current state | Handoff gap |
|---|---|---|
| OpenClaw ops overview | BFF read route is implemented and documented | Frontend must adopt the BFF route and stop treating adapter internals as a browser contract |
| Session list | BFF projects bounded sessions inside the overview route | No standalone browser-facing session list route exists; use the overview for first UI pass |
| Session detail | BFF overview projects summary fields plus latest audit event | Add a future detail route only if UI needs full `context_bundle`, full upstream payload, or full audit timeline after BFF redaction is designed |
| Session audit | BFF overview includes bounded invocation audit, not per-session lifecycle audit | Add per-session lifecycle audit route only after BFF projection/redaction rules are reviewed |
| Effective tools | BFF fetches effective tools only when `agent_id` and authenticated operator context are present | UI should request this only for a selected agent/session, not for the default page load |
| Tool invocation | Adapter has policy-checked invoke behavior, but BFF command is intentionally not exposed | Keep tool invocation disabled until BFF owns auth, idempotency, schema projection, trace ids, redaction, and audit response shape |
| Workflow trigger | Adapter has policy-checked trigger behavior, but BFF command is intentionally not exposed | Keep workflow trigger disabled until BFF exposes a reviewed async command and job-status read model |
| Paper and live gates | BFF displays gate state and hard-disables activation commands | Do not add paper/live controls to this screen; separate paper/live tasks must own activation gates and tests |
| Broker/capital paths | BFF reports production activation as disabled | Do not expose broker or capital-binding actions through this ops surface |
| Degradation | BFF composes sub-surface status and backend reasons | UI must preserve backend reason text and avoid client-side health inference |

## 6. Operator Journey

1. Operator opens the OpenClaw operations screen.
2. UI calls `GET /api/v1/operator/openclaw/ops` through the shared BFF client.
3. UI renders the activation posture first: production activation disabled,
   paper/live gates visible, and BFF activation commands absent.
4. UI renders upstream reachability and degradation reasons before any session or
   audit tables.
5. UI renders lifecycle sessions using BFF-projected row fields and
   row-level `allowedActions`.
6. UI renders bridge posture and invocation audit counts. Denied rows are shown
   as fail-closed policy decisions, not generic failures.
7. UI enables session create/cancel only when BFF-provided `allowedActions`
   permit the action and the operator can supply a stable idempotency key.
8. After create/cancel, UI refreshes the overview route. It should not mutate
   local session state optimistically beyond a pending affordance.

## 7. Frontend Screen Regions

| Region | Data source | Rendering rule |
|---|---|---|
| Activation posture strip | `data.activation`, `data.gate_state`, `data.production_activation` | Show disabled/deferred gates as intentional backend state |
| Degradation banner | `data.overall_status`, `data.degradation.reasons`, `meta.surfaces` | Show when status is degraded or unavailable; do not invent reasons |
| Upstream status panel | `data.upstream` | Separate adapter transport health from upstream reachability |
| Session table | `data.session_lifecycle.sessions[]` | Include session id, agent id, type, state, operator, created/updated, last error, audit count |
| Session command controls | `data.allowedActions`, row `allowedActions` | Use BFF actions only; require idempotency for create/cancel |
| Bridge policy panel | `data.tool_workflow.policy`, `data.tool_workflow.bridge_posture` | Highlight deny-all/fail-closed posture distinctly from transport errors |
| Invocation timeline | `data.tool_workflow.audit.entries[]` | Include trace id, request type, tool/workflow ref, decision, outcome, retryability, error code |
| Support identifiers | `session_id`, `trace_id`, `operator_id` | Display as copyable support/debug identifiers when present |

## 8. Frontend Guard Rails

- Use the BFF route only; do not call `openclaw-gateway-adapter` directly.
- Use backend `allowedActions` only; do not infer permission from role labels,
  state names, policy text, or gate names.
- Treat unknown tools, workflows, actions, and gates as disabled.
- Keep raw `context_bundle`, upstream payload internals, credentials, and tool
  args out of the UI unless a future BFF route explicitly redacts and exposes
  them.
- Keep tool invocation and workflow trigger buttons absent or disabled until a
  reviewed BFF command exists.
- Keep paper, live, broker, and capital-binding activation controls absent from
  this screen.
- Preserve backend `retryable`, `error_code`, `trace_id`, and `request_id`
  fields when shown in support drawers.

## 9. Reviewer Checklist

For `Codex2` sidecar review:

- Confirm this packet is support-only and only adds this sidecar artifact.
- Confirm parent status and commit reference match the task archive.
- Confirm route statements match `services/control-plane/bff/main.py`.
- Confirm projection statements match `services/control-plane/bff/read_store.py`.
- Confirm client statements match `services/control-plane/bff/openclaw_ops_client.py`.
- Confirm frontend guidance references the existing
  `docs/pantheon-handoffs/SVC-OPENCLAW-BFF-OPS-SURFACE/FRONTEND_CHANGE_SPEC.md`
  without changing it.
- Confirm guidance preserves fail-closed posture and avoids browser calls to
  adapter internals.

## 10. Verification Notes

Sidecar preparation verification performed by Codex:

- Read task-scoped brief, closeout rules, collaboration guide, and current
  `ai-status.json` task entry.
- Confirmed sidecar status is `in_progress`, owner is `Codex`, reviewer is
  `Codex2`, and artifact path is this file.
- Confirmed parent task archive records `SVC-OPENCLAW-BFF-OPS-SURFACE` as
  `done` with closeout commit `52078b85652d73f9b36356cae645e75142d1243e`.
- Inspected `services/control-plane/bff/main.py` for OpenClaw read routes,
  command routes, role checks, operator filter authorization, idempotency
  enforcement, and command envelopes.
- Inspected `services/control-plane/bff/read_store.py` for composed ops
  projection, session row shape, gate state, bridge posture, audit projection,
  allowed actions, and degradation reasons.
- Inspected `services/control-plane/bff/openclaw_ops_client.py` for adapter
  service-client calls and fail-closed error surfaces.
- Inspected `services/control-plane/bff/BFF_API_CONTRACT.md` and
  `docs/pantheon-handoffs/SVC-OPENCLAW-BFF-OPS-SURFACE/FRONTEND_CHANGE_SPEC.md`
  for current documented frontend contract.
- Ran `git diff --check -- support/sidecars/SVC-OPENCLAW-BFF-OPS-SURFACE/SVC-OPENCLAW-BFF-OPS-SURFACE-SIDECAR-BFF-HANDOFF.md`: passed.
- Ran `python3 -m pytest services/control-plane/bff/test_openclaw_ops_surface.py -q`: 4 passed.

No runtime, registry, governance, BFF implementation, L1 canonical document, or
frontend implementation files were edited by this sidecar.
