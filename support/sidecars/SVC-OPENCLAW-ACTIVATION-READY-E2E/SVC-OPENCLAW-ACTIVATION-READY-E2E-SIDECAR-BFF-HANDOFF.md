# BFF & Frontend Handoff Packet
## SVC-OPENCLAW-ACTIVATION-READY-E2E — Sidecar: bff_handoff_packet

**Sidecar task:** SVC-OPENCLAW-ACTIVATION-READY-E2E-SIDECAR-BFF-HANDOFF
**Parent task:** SVC-OPENCLAW-ACTIVATION-READY-E2E
**Owner:** Claude (helper-claimed)
**Reviewer:** Codex
**Generated:** 2026-04-30
**Status:** accepted (Codex review approved 2026-04-30; closeout finalized)

---

## Purpose

This packet summarises the current BFF OpenClaw operator surface, identifies query gaps between the adapter and the BFF, maps the expected operator journey, and provides frontend handoff materials. It is a **support artifact only** — it does not modify canonical truth, runtime contracts, or core service implementations.

---

## 1. BFF OpenClaw Surface Inventory

### 1.1 Read endpoints (query surface)

| BFF route | Role required | Adapter route(s) called | Notes |
|---|---|---|---|
| `GET /api/v1/operator/openclaw/ops` | operator / approver / admin / reviewer | capabilities, upstream/status, lifecycle/sessions, tools/policy, audit/invocations | Aggregate ops snapshot. Primary operator dashboard entry point. `viewer` role receives 403. |
| `GET /api/v1/operator/openclaw/tool-workflow-bridge` | operator / approver / admin / reviewer | same as above | Same payload; surface_key differs (`openclaw_tool_workflow_bridge`). Second alias for the same read. `viewer` role receives 403. |
| `GET /api/v1/operator/openclaw/live-gate/status` | operator / admin | broker/live/gate/status | Reads current live gate config and gate-check state. Always fail-closed. |
| `GET /api/v1/operator/openclaw/live-gate/audit` | operator / admin | broker/live/gate/audit | Scoped to requesting operator unless `admin`. |

### 1.2 Command endpoints (mutation surface)

| BFF route | Role required | Required headers | Adapter route | Notes |
|---|---|---|---|---|
| `POST /api/v1/operator/openclaw/sessions` | operator+ | `Authorization`, `X-Idempotency-Key` | `POST /api/openclaw-adapter/lifecycle/sessions` | Creates Pantheon-owned durable session. Returns 202 (created) or 200 (replayed). |
| `POST /api/v1/operator/openclaw/sessions/{session_id}/cancel` | operator+ | `Authorization`, `X-Idempotency-Key` | `POST /api/openclaw-adapter/lifecycle/sessions/{id}/cancel` | Requests cancel on a specific session. Returns 202. |

---

## 2. BFF Query Gap Analysis

The adapter exposes more routes than the BFF currently proxies. The table below categorises each gap.

### 2.1 Intentionally not exposed (policy decision)

| Adapter route | Reason not in BFF |
|---|---|
| `POST /api/openclaw-adapter/tools/invoke` | Tool invocation is a server-side orchestrated flow, not a direct operator action. BFF bridge_posture explicitly marks `bff_tool_invocation_commands: not_exposed`. |
| `POST /api/openclaw-adapter/workflows/trigger` | Workflow triggers are orchestrated internally. BFF bridge_posture marks `bff_workflow_trigger_commands: not_exposed`. |
| `POST /api/openclaw-adapter/broker/live/orders` | Always rejected at adapter. No BFF exposure needed; the live gate harness tests this boundary directly. |
| `POST /api/openclaw-adapter/broker/live/gate/validate` | Requires `X-Human-Approval-Token`. Intended for human-in-the-loop approval flow, not operator console self-service. |
| `POST /api/openclaw-adapter/broker/live/gate/dry-handoff` | Requires `X-Human-Approval-Token`. Same gate constraint. Dry handoff is a controlled pre-live rehearsal, not routine operator console action. |
| `GET /api/openclaw-adapter/sessions*` (legacy facade) | This bare `/sessions*` path prefix is the pre-lifecycle-namespace adapter façade. The BFF uses the canonical `lifecycle/sessions*` routes exclusively. The legacy facade is not proxied to avoid ambiguity and is intentionally absent from the BFF surface. |
| `GET /api/openclaw-adapter/tools` (effective-tools read) | Returns the runtime-effective tool list after applying policy and session context. Tool resolution is server-side; the BFF exposes the policy snapshot (via `/ops → tool_workflow.policy`) but not the per-context effective-tool calculation. Operator console should not perform tool resolution client-side. Deferred to a follow-on sprint if a tool-selection UI requires it. |

### 2.2 Not yet exposed — potential frontend gap

| Adapter route | Frontend value | Suggested BFF action |
|---|---|---|
| `GET /api/openclaw-adapter/lifecycle/sessions/{id}` | Individual session drilldown with upstream refresh | Add `GET /api/v1/operator/openclaw/sessions/{session_id}` when a session detail panel is required. |
| `GET /api/openclaw-adapter/lifecycle/sessions/{id}/audit` | Per-session audit trail | Add `GET /api/v1/operator/openclaw/sessions/{session_id}/audit` paired with the drilldown above. |
| `GET /api/openclaw-adapter/workflows/jobs/{job_id}` | Background job status polling | Add `GET /api/v1/operator/openclaw/jobs/{job_id}` if the frontend needs to poll background workflow jobs. |
| `GET /api/openclaw-adapter/broker/paper/orders` | Paper order list for operators | Gated by `OPENCLAW_PAPER_ADAPTER_ENABLED`. Add only after the paper gate is opened in an environment. |
| `GET /api/openclaw-adapter/broker/paper/orders/{id}` | Paper order drilldown | Same gate dependency as above. |
| `GET /api/openclaw-adapter/broker/audit` | Paper broker intent/result audit | Same gate dependency. Currently no BFF route. |
| `GET /api/openclaw-adapter/broker/capabilities` | Paper broker capability snapshot | Low-priority; already aggregated in capabilities response available via `/ops`. |

**Priority assessment for the activation-ready E2E profile:**
The current BFF surface is sufficient for the E2E acceptance scope (capabilities, sessions, tools, paper adapter, live deny). The potential-gap routes above are not blockers for the parent task acceptance criteria. They are recorded here for the frontend team to evaluate when building out the operator console beyond the smoke profile.

---

## 3. Operator Journey

### 3.1 Standard operator session lifecycle (happy path)

```
1. Load operator dashboard
   GET /api/v1/operator/openclaw/ops
   → data.overall_status, data.upstream, data.session_lifecycle, data.gate_state, data.allowedActions

2. Inspect gate state
   data.gate_state.paper_adapter.enabled   → false (requires OPENCLAW_PAPER_ADAPTER_ENABLED=true)
   data.gate_state.live_adapter.enabled    → false (requires OPENCLAW_LIVE_ADAPTER_ENABLED=true)
   data.allowedActions.canEnablePaper      → always false (env-var gate only, not BFF-controlled)
   data.allowedActions.canEnableLive       → always false (env-var gate only, not BFF-controlled)

3. Review active sessions
   data.session_lifecycle.sessions[*]
   → session_id, state, agent_id, session_type, operator_id, allowedActions.canCancel

4. Create a session (if canCreateSession === true)
   POST /api/v1/operator/openclaw/sessions
   Headers: Authorization: Bearer <token>, X-Idempotency-Key: <client-generated-uuid>
   Body: { "agent_id": "<id>", "session_type": "interactive", "context_bundle": {...} }
   → 202 { data.command, data.session.session_id, data.session.state }
   → 200 if X-Idempotency-Key matches a prior request (replayed)

5. Cancel a session (if allowedActions.canCancel === true)
   POST /api/v1/operator/openclaw/sessions/{session_id}/cancel
   Headers: Authorization: Bearer <token>, X-Idempotency-Key: <client-generated-uuid>
   → 202 { data.command, data.session.session_id, data.session.state }

6. Inspect live gate (for operators monitoring the live path)
   GET /api/v1/operator/openclaw/live-gate/status
   → data.live_gate_enabled (false), data.live_execution_enabled (always false)
   GET /api/v1/operator/openclaw/live-gate/audit
   → entries of live gate intent attempts (all should be denied in current profile)
```

### 3.2 Degraded / adapter-absent path

When the adapter is not reachable or not configured:
- `/ops` returns `overall_status: "unavailable"`, `production_activation: "disabled"`, empty sessions, degradation reasons.
- `canCreateSession` → `false`, `canCancelSession` inferred `false` from empty sessions.
- Frontend must render a degraded banner rather than an error state — the adapter failing gracefully is expected behaviour in development and test environments.

### 3.3 Paper gate path (future — gate not open in current profile)

When `OPENCLAW_PAPER_ADAPTER_ENABLED=true` is set in the adapter environment:
- `data.gate_state.paper_adapter.enabled` switches to `true`.
- Paper order management routes become active on the adapter (`/broker/paper/orders`).
- No BFF routes currently expose paper order management — a new BFF sprint is required before paper order UI can be built.

### 3.4 Live gate path (permanently denied in current profile)

- `data.gate_state.live_adapter.enabled` will be `false` until `OPENCLAW_LIVE_ADAPTER_ENABLED=true` is explicitly set.
- Even with the gate env var set, live execution is always denied — the live gate harness is a dry-handoff-only surface.
- Frontend should never render a "place live order" action. The only live-related controls are the audit trail read and gate status read.

---

## 4. Request & Response Shapes

### 4.1 `GET /api/v1/operator/openclaw/ops` — response shape

```json
{
  "data": {
    "surface": "openclaw_ops",
    "overall_status": "ok | degraded | unavailable",
    "production_activation": "disabled",
    "activation": {
      "adapter_version": "0.2.0",
      "activation_state": "upstream_client_ready | upstream_client_degraded",
      "broker_execution": "deferred",
      "paper_adapter": "deferred | enabled",
      "live_adapter": "deferred | enabled",
      "live_gate_harness": "present_disabled | enabled",
      "capital_binding": "deferred",
      "session_lifecycle_state": "activation_ready",
      "fail_closed": true
    },
    "gate_state": {
      "paper_adapter": {
        "state": "deferred",
        "enabled": false,
        "activation_gate": "OPENCLAW_PAPER_ADAPTER_ENABLED",
        "allowed_scope": "paper_disabled",
        "bff_activation_command": "not_exposed"
      },
      "live_adapter": {
        "state": "deferred",
        "enabled": false,
        "activation_gate": "OPENCLAW_LIVE_ADAPTER_ENABLED",
        "allowed_scope": "live_disabled",
        "bff_activation_command": "not_exposed"
      }
    },
    "upstream": {
      "reachable": true,
      "upstream_url": "http://upstream-openclaw:8080",
      "details": { "probe": "/healthz", "http_status": 200 }
    },
    "session_lifecycle": {
      "status": "ok | degraded | unavailable",
      "count": 1,
      "state_counts": { "active": 1 },
      "sessions": [
        {
          "session_id": "oc-sess-1",
          "agent_id": "agent-alpha",
          "session_type": "interactive",
          "state": "active",
          "operator_id": "op-2",
          "created_at": "2026-04-30T07:00:00Z",
          "updated_at": "2026-04-30T07:01:00Z",
          "upstream_session_id": "up-sess-1",
          "context_keys": ["ticket_id"],
          "audit_count": 1,
          "latest_audit_event": { "action": "create_acknowledged", "actor": "op-2" },
          "degraded": false,
          "last_error": null,
          "allowedActions": { "canCancel": true }
        }
      ],
      "degraded_session_count": 0,
      "filters": { "operator_id": null, "state": null }
    },
    "tool_workflow": {
      "policy": {
        "allowed_tools": ["search"],
        "always_blocked_tools": ["paper_order", "live_order"],
        "always_blocked_tool_prefixes": ["paper.", "live.", "broker."],
        "default_posture": "deny_all"
      },
      "effective_tools": null,
      "audit": {
        "status": "ok",
        "count": 2,
        "outcome_counts": { "denied": 1, "ok": 1 },
        "policy_decision_counts": { "denied": 1, "allowed": 1 },
        "entries": [
          {
            "request_type": "tool_invoke",
            "trace_id": "trace-denied",
            "operator_id": "op-2",
            "session_id": "oc-sess-1",
            "tool_name": "paper.execute",
            "policy_decision": "denied",
            "policy_class": "always_blocked",
            "outcome": "denied",
            "at": "2026-04-30T07:02:00Z"
          }
        ]
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
    "operator_controls": {
      "read_operations": ["upstream_status", "capability_inventory", "session_lifecycle", "tool_policy", "tool_workflow_audit", "degraded_reason"],
      "commands": {
        "create_session": { "endpoint": "POST /api/v1/operator/openclaw/sessions", "requires_idempotency_key": true },
        "cancel_session": { "endpoint": "POST /api/v1/operator/openclaw/sessions/{session_id}/cancel", "requires_idempotency_key": true },
        "invoke_tool": "not_exposed_by_bff",
        "trigger_workflow": "not_exposed_by_bff"
      },
      "blocked_commands": {
        "enable_paper_adapter": "activation_gate_required_not_available_in_bff",
        "enable_live_adapter": "activation_gate_required_not_available_in_bff"
      }
    },
    "allowedActions": {
      "canCreateSession": true,
      "canInvokeTool": false,
      "canTriggerWorkflow": false,
      "canEnablePaper": false,
      "canEnableLive": false
    },
    "degradation": {
      "reasons": []
    }
  },
  "meta": {
    "snapshot_at": "2026-04-30T09:00:00Z",
    "surfaces": {
      "openclaw_ops": { "status": "ok", "source": "service_client" },
      "openclaw_tool_workflow_bridge": { "status": "ok", "source": "service_client" }
    }
  }
}
```

### 4.2 `POST /api/v1/operator/openclaw/sessions` — request & response

**Request:**
```json
{
  "agent_id": "agent-alpha",
  "session_type": "interactive",
  "context_bundle": { "ticket_id": "rt-1" }
}
```
Required headers: `Authorization: Bearer <token>` (operator+ role), `X-Idempotency-Key: <uuid>`

**202 response (created):**
```json
{
  "data": {
    "command": "OpenClawCreateSession",
    "accepted_at": "2026-04-30T09:00:00Z",
    "replayed": false,
    "session": {
      "session_id": "oc-sess-created",
      "agent_id": "agent-alpha",
      "session_type": "interactive",
      "state": "active"
    }
  },
  "meta": {
    "snapshot_at": "2026-04-30T09:00:00Z",
    "surfaces": { "openclaw_command": { "status": "ok", "source": "service_client" } }
  }
}
```
**200 response (replayed — same idempotency key):** same shape with `"replayed": true`.

**Error cases:**
| HTTP | Condition |
|---|---|
| 401 | Missing or invalid Authorization header |
| 403 | Role insufficient (viewer attempting command) |
| 400 | Missing X-Idempotency-Key |
| 422 | Missing agent_id or session_type in body |
| 502/503 | Adapter unreachable or returned error |

### 4.3 `POST /api/v1/operator/openclaw/sessions/{session_id}/cancel` — response

Same shape as create; `command` is `"OpenClawCancelSession"`. Always returns 202.

### 4.4 `GET /api/v1/operator/openclaw/live-gate/status` — response

```json
{
  "status": "ok",
  "surface": "openclaw_live_gate_status",
  "data": {
    "status": "ok",
    "live_gate_enabled": false,
    "live_execution_enabled": false,
    "gate": "live_gate_harness",
    "enabled": false,
    "gate_checks": {
      "live_adapter_env": false,
      "human_approval_token": "not_configured",
      "runtime_manager": "not_configured",
      "capital_binding": false
    }
  },
  "snapshot_at": "2026-04-30T09:00:00Z"
}
```

---

## 5. Authentication & Authorization Reference

| Role | GET /ops | POST sessions | GET live-gate/status + audit | Notes |
|---|---|---|---|---|
| `viewer` | Denied (403) | Denied (403) | Denied (403) | Not in any permitted role set |
| `reviewer` | Allowed | Denied (403) | Denied (403) | Read guard only (`_require_read_role`) |
| `approver` | Allowed | Denied (403) | Denied (403) | Read guard only (`_require_read_role`) |
| `operator` | Allowed | Allowed | Allowed | Standard operator access |
| `admin` | Allowed | Allowed | Allowed | Can see cross-operator audit |

Role sets: read operations use `_require_read_role` → `{operator, approver, admin, reviewer}`; command and live-gate operations use `_require_openclaw_command_role` → `{operator, admin}`.

Auth header format: `Authorization: Bearer <operator_id>:<role>`
(Example: `Bearer op-2:operator`)

---

## 6. Environment Variables Required for Activation-Ready Profile

These env vars must be present on the `openclaw-gateway-adapter` service. The BFF reads indirectly through the adapter and reflects the gate state in `data.gate_state`.

| Variable | Default in compose | Effect when set to `true` |
|---|---|---|
| `OPENCLAW_GATEWAY_URL` | `http://openclaw-gateway:18789` | Points adapter at upstream (fake upstream in E2E) |
| `OPENCLAW_PAPER_ADAPTER_ENABLED` | `false` | Opens paper order submission path |
| `OPENCLAW_LIVE_ADAPTER_ENABLED` | `false` | Enables live gate harness (execution still denied) |
| `OPENCLAW_PRODUCTION_BROKER_ENABLED` | `false` | Must remain `false` — no live broker ever |
| `OPENCLAW_CAPITAL_BINDING_ENABLED` | `false` | Capital binding gate |
| `OPENCLAW_BROKER_SIDECAR_URL` | `http://broker:8102` | Paper broker sidecar |
| `OPENCLAW_RUNTIME_MANAGER_URL` | `http://runtime-manager:8081` | Runtime binding check for paper gate |
| `PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL` | (BFF env var) | BFF → adapter base URL |

For the E2E profile, `OPENCLAW_PAPER_ADAPTER_ENABLED` and `OPENCLAW_LIVE_ADAPTER_ENABLED` may be tested as `true` in the activation-ready compose profile, but the BFF remains read-only for these gates.

---

## 7. Frontend Integration Checklist

Items for the frontend team when building the OpenClaw operator console panel:

- [ ] Load and render `/ops` aggregate: overall_status banner, upstream reachability badge, session table
- [ ] Show `gate_state.paper_adapter.enabled` and `gate_state.live_adapter.enabled` as read-only badges (never render enable buttons — these are env-var gates)
- [ ] Render degraded state correctly: `overall_status === "unavailable"` → grey-out the panel, show `degradation.reasons`
- [ ] Session table: columns `session_id`, `agent_id`, `session_type`, `state`, `operator_id`; `canCancel` drives cancel button visibility
- [ ] Create session form: agent_id (required), session_type (select), optional context_bundle JSON editor; generate UUID for idempotency key client-side
- [ ] Handle 200 (replayed) vs 202 (created) responses — show "replayed" badge when the server says idempotency key matched
- [ ] Cancel confirmation modal: confirm session_id, generate fresh idempotency key for the cancel command
- [ ] Live gate panel: show `live_gate_enabled`, `live_execution_enabled` (always false), and the audit trail — no action buttons
- [ ] Tool workflow audit: show `policy_decision_counts`, `outcome_counts`, recent entries — read-only
- [ ] `canInvokeTool === false` and `canTriggerWorkflow === false` are permanent — never render these as operator actions

---

## 8. E2E Acceptance Alignment

This packet maps directly to the parent task acceptance criteria:

| Parent acceptance criterion | BFF surface coverage |
|---|---|
| fake upstream E2E covers capabilities, sessions, tools, paper adapter | `GET /ops` aggregates all four; tests confirmed in `test_openclaw_ops_surface.py` |
| default compose remains degraded safe when upstream absent | `GET /ops` returns `overall_status: "unavailable"` with structured degradation reasons; test confirmed |
| live path is explicitly denied in E2E | `GET /live-gate/status` → `live_execution_enabled: false`; `POST /api/openclaw-adapter/broker/live/orders` always 403 at adapter |
| activation-ready profile documents required gates and env vars | Section 6 above |
| focused tests and compose config pass | Tests: `test_openclaw_ops_surface.py`; compose: `test_compose_activation.py` |

**BFF gap items that are NOT blockers for parent E2E acceptance:**
- Individual session drilldown (`lifecycle/sessions/{id}`)
- Per-session audit (`lifecycle/sessions/{id}/audit`)
- Paper order management BFF routes
- Dry handoff BFF exposure

These are deferred to a follow-on sprint when the operator console panel is built out beyond the smoke profile.

---

## 9. Handoff Notes for Reviewer (Codex)

1. This packet is support-only — no canonical files were modified.
2. The BFF query gap table (Section 2) identifies 7 intentionally-absent routes (including the legacy `/sessions*` facade and the effective-tools read) and 7 potential-gap routes. None of the potential-gap routes are required to pass parent E2E acceptance.
3. Section 3 operator journey maps the complete frontend state machine with expected HTTP status codes.
4. Section 4 response shapes are derived from `test_openclaw_ops_surface.py` fixture payloads and `main.py` / `read_store.py` projections — they reflect the actual current implementation, not aspirational shapes.
5. Section 5 auth table reflects the `_require_openclaw_command_role` (`{operator, admin}`) and `_require_read_role` (`{operator, approver, admin, reviewer}`) guards in `main.py`. `viewer` role is not in either set and receives 403 on all routes.
6. **Changes addressing Codex reopen (2026-04-30):**
   - Finding 1: Corrected `viewer+` → `operator / approver / admin / reviewer` in Section 1.1 and `viewer → Denied (403)` in Section 5 auth table.
   - Finding 2: Corrected `gate_state` per-adapter shape from `{enabled, env_var}` to the actual projected fields `{state, enabled, activation_gate, allowed_scope, bff_activation_command}` in Section 4.1.
   - Finding 3: Corrected session row from `context_bundle` / `audit_log` (raw adapter fields) to `context_keys` / `audit_count` / `latest_audit_event` (BFF projected fields) in Section 4.1.
   - Finding 4: Added explicit disposition for `GET /api/openclaw-adapter/sessions*` (legacy facade) and `GET /api/openclaw-adapter/tools` (effective-tools read) in Section 2.1.
7. If Codex identifies additional gaps or corrections, please annotate in the review file and return via `scripts/ai-status.sh approve` or `reopen`.
