# APP-002-W2-CONTROL-INCIDENT BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-002-W2-CONTROL-INCIDENT` — Harden incident control-path execution
**Parent Owner**: Qwen
**Parent Reviewer**: Codex
**Parent Status**: `done`
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Qwen
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-11
**Last Updated**: 2026-04-11
**Review Status**: Approved (2026-04-11, Qwen)

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime/registry/governance implementations. It summarizes the incident control-path command flow, highlights operator journey expectations, and provides frontend handoff notes for pause/rollback/kill-switch actions.

---

## 1. Parent Task Summary

Wave 2 incident control hardens the **write path** for pause, rollback, and kill-switch so operator actions are executed via authoritative control paths (RuntimeBinding state machine + KillSwitchController) with full audit trails and degraded-mode guidance.

**Acceptance criteria (from ai-status)**:
- `pause_rollback_killswitch_authoritative`
- `incident_actions_audited`
- `degraded_control_guidance_present`

**Primary artifacts in scope**:
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/command_executor.py`
- `services/control-plane/bff/models.py`
- `services/control_plane/internal_api.py`
- `services/execution/runtime-manager/`
- `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md`

---

## 2. Current Implementation Snapshot (Code-Backed)

### 2.1 BFF Command Surfaces

| Surface | Endpoint | Purpose | Status |
|---|---|---|---|
| Command submit | `POST /api/v1/operator/commands` | Submit incident control actions for async execution | Implemented |
| Command status | `GET /api/v1/operator/commands/{command_id}` | Poll status/result/error/audit | Implemented |
| Degraded guidance | `GET /api/v1/operator/degraded-control-guidance` | Secondary path instructions + fallback endpoints | Implemented (206 when degraded) |

**Core behavior**:
- Validates per-command params + role requirements.
- Enforces concurrent modification guard per target.
- Emits `staleness_warning` when `BFF_READ_SURFACE_STATE != fresh`.
- Stores full audit context (operator_id, roles, MFA, staleness warning).
- Background worker dispatches to `services/control_plane/internal_api.py` via `command_executor.py`.

### 2.2 Incident Control Command Types (BFF)

| Command | Required Roles | Required Params | Notes |
|---|---|---|---|
| `PauseRuntime` | `operator` or `admin` | `runtime_binding_id`, `pause_action` | `pause_action` = `pause` or `resume`; optional `duration_seconds`, `reason` |
| `ExecuteRollback` | `admin` or `approver` | `rollback_target_type`, `target_id`, `rollback_to_version` | Optional `rollback_action_type` supported by internal API but not forwarded by BFF executor (defaults to `replace`) |
| `ActivateKillSwitch` | `admin` + MFA | `scope`, `activate` | `activate` is required by validator; internal API always dispatches `action=activate` |

### 2.3 Protected Internal API (Authoritative Execution)

| Internal API | Endpoint | Authoritative Path | Notes |
|---|---|---|---|
| Pause/Resume | `POST /api/internal/v1/runtimes/{binding_id}/pause` | RuntimeBinding state machine transitions | Handles `pause_action`, `duration_seconds`, `reason` |
| Rollback | `POST /api/internal/v1/rollbacks/execute` | RuntimeBinding rollback action matrix | Supports `rollback_action_type` (replace/pause_then_replace/liquidate_then_replace) |
| Kill-switch | `POST /api/internal/v1/kill-switch` | KillSwitchController fast path | Returns `safe_mode_after`, `audit_id`, `emergency_class` |

**Audit persistence**:
- Internal API records every command to `/tmp/pantheon/internal_api/commands.json`.
- BFF command store (`/tmp/pantheon/bff/commands.jsonl`) persists submission + execution results and enriched audit metadata.

---

## 3. Operator Journey (Incident Control)

1. **Load incident context**
   - `GET /api/v1/operator/incident-response/{incident_id}`
   - Render `meta.surfaces` and honor degraded/unavailable flags.
2. **Submit action** (pause/rollback/kill-switch)
   - `POST /api/v1/operator/commands`
3. **Poll status**
   - `GET /api/v1/operator/commands/{command_id}` until `executed`, `failed`, or `timeout`.
4. **Degraded workflow**
   - If `staleness_warning` present or `BFF_READ_SURFACE_STATE != fresh`, show the fallback instructions from:
     `GET /api/v1/operator/degraded-control-guidance`.

---

## 4. Frontend Handoff Notes (Control Path)

### 4.1 Pause Runtime (Example)

```http
POST /api/v1/operator/commands
Authorization: Bearer op-42:operator
Content-Type: application/json

{
  "command": "PauseRuntime",
  "target": {"type": "RuntimeBinding", "id": "binding-042"},
  "action": "pause",
  "params": {
    "runtime_binding_id": "binding-042",
    "pause_action": "pause",
    "duration_seconds": 3600,
    "reason": "investigating anomalous drawdown"
  },
  "audit_context": {"reason": "incident response"}
}
```

### 4.2 Execute Rollback (Example)

```http
POST /api/v1/operator/commands
Authorization: Bearer op-42:approver
Content-Type: application/json

{
  "command": "ExecuteRollback",
  "target": {"type": "RuntimeBinding", "id": "binding-042"},
  "action": "rollback",
  "params": {
    "rollback_target_type": "runtime",
    "target_id": "binding-042",
    "rollback_to_version": "artifact-2026-04-10-001"
  },
  "audit_context": {"reason": "critical regression detected"}
}
```

### 4.3 Activate Kill-Switch (Example)

```http
POST /api/v1/operator/commands
Authorization: Bearer op-42:admin:mfa
X-MFA-Token: 123456
Content-Type: application/json

{
  "command": "ActivateKillSwitch",
  "target": {"type": "KillSwitchOrder", "id": "scope:all"},
  "action": "activate",
  "params": {
    "scope": "all",
    "activate": true,
    "severity": "critical",
    "reason": "uncontrolled drawdown"
  },
  "audit_context": {"reason": "emergency halt"}
}
```

### 4.4 Command Receipt + Polling

```json
{
  "receipt": {
    "command_id": "cmd-123",
    "command_type": "ActivateKillSwitch",
    "status": "submitted",
    "tracking_url": "/api/v1/operator/commands/cmd-123"
  },
  "meta": {"estimated_processing_time_ms": 2000, "next_poll_after_ms": 500},
  "staleness_warning": null
}
```

### 4.5 UI Gating Rules

- If `staleness_warning` is present, show a blocking banner and require explicit confirmation.
- If command status is `failed` or `timeout`, display `error` details and link to secondary path guidance.
- If `BFF_READ_SURFACE_STATE == unavailable`, hide action CTAs and route operators to the fallback path.
- Kill-switch actions require admin role + MFA; surface role errors explicitly (do not silently retry).
- Include `params.activate=true` for `ActivateKillSwitch` or the validator will reject the request.

---

## 5. Gaps / Follow-Ups (Non-blocking)

1. **Rollback action type not forwarded**
   - Internal API supports `rollback_action_type`, but `command_executor.py` does not pass it.
   - Current behavior defaults to `replace`. If UI needs `pause_then_replace` or `liquidate_then_replace`, this needs wiring.

2. **Deployment vs runtime rollback semantics**
   - `rollback_target_type="deployment"` triggers degraded-mode fallback in `internal_api.py` (no binding state check).
   - For authoritative state transitions, use `rollback_target_type="runtime"` with a runtime binding ID.

3. **Secondary control path hardening**
   - CLI fallback (`APP-002-W2-CLI-FALLBACK`) remains the next wave for production-grade admin tooling.

---

## 6. Reviewer Checklist (Qwen)

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This file under `support/sidecars/` only |
| Canonical truth untouched | PASS | No L1 or core runtime files changed |
| Control-path summary matches code | PASS | `main.py`, `command_executor.py`, `internal_api.py` |
| Frontend handoff clear | PASS | Example requests + gating rules |

---

## 7. Handoff Status

Review complete. Qwen approved this packet for correctness against the current control-path implementation. The parent owner can treat this as the frontend handoff reference for incident control actions.
