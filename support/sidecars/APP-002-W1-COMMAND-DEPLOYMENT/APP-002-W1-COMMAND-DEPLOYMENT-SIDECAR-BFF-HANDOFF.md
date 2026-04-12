# APP-002-W1-COMMAND-DEPLOYMENT BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-002-W1-COMMAND-DEPLOYMENT` -- Harden deployment command execution for Wave 1
**Parent Owner**: Codex
**Parent Reviewer**: Qwen
**Parent Status**: `todo`
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Claude
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-11

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime/registry/governance implementations. It summarizes the current command pipeline and identifies the concrete gaps needed to make Promotion Review commands authoritative.

---

## 1. Parent Task Summary

Wave 1 command deployment is scoped to the Promotion Review action path. The goal is to replace any stub command execution with a protected, authoritative execution path so that command receipts and polling reflect real downstream outcomes.

**Acceptance criteria (from ai-status)**:
- `deployment_commands_not_stub`
- `authoritative_command_status`
- `audit_and_failure_paths_truthful`

**Primary artifacts in scope**:
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/command_executor.py`
- `services/control-plane/bff/command_queue.py`
- `services/control-plane/bff/models.py`
- `services/control_plane/internal_api.py`
- `tools/pantheon_admin/cli.py`

---

## 2. Current Command Surfaces (BFF)

### 2.1 Command Submission

`POST /api/v1/operator/commands` (FastAPI)

- Auth: `Authorization: Bearer <operator_id>:<comma_roles>[:mfa]`
- Validates per-command role + parameter requirements
- Enforces concurrent modification guard per target
- Emits `staleness_warning` when `BFF_READ_SURFACE_STATE != fresh`
- Persists command record in `CommandStore` (JSONL under `BFF_DATA_DIR`)
- Queues async worker (`_process_command_stub`, now aliasing real executor)

Response (202):
- `receipt` with `command_id`, `command_type`, `tracking_url`
- `meta.estimated_processing_time_ms` and `meta.next_poll_after_ms`
- Optional `staleness_warning`

### 2.2 Command Polling

`GET /api/v1/operator/commands/{command_id}`

- Auth required
- Returns command status plus `result`, `error`, and `audit` as recorded in `CommandStore`

### 2.3 Command Types (as implemented)

| Command | Required Roles | Required Params | Notes |
|---|---|---|---|
| `ApproveDeployment` | `approver` or `admin` | `deployment_plan_id`, `approval_decision` | Promotion Review action (Wave 1 focus) |
| `PauseRuntime` | `operator` or `admin` | `runtime_binding_id`, `pause_action` | Incident-related (Wave 2 focus) |
| `ExecuteRollback` | `admin` or `approver` | `rollback_target_type`, `target_id`, `rollback_to_version` | Incident-related (Wave 2 focus) |
| `ActivateKillSwitch` | `admin` + MFA | `scope`, `activate`, optional `severity` | Kill-switch (Wave 2+ focus) |
| `ApproveEvolutionDecision` | `reviewer` or `approver` or `admin` | `evolution_decision_id`, `approval_action` | Evolution review (Wave 3 focus) |
| `ExecuteEvolutionAction` | `admin` or `approver` | `evolution_decision_id`, `action_type` | Evolution execution (Wave 3 focus) |

---

## 3. Execution Path (Current Behavior)

### 3.1 Worker Pipeline

`submit_command()` -> background task -> `_process_command()`

Pipeline in `services/control-plane/bff/main.py`:
1. Load command record from `CommandStore`
2. Set status = `processing`
3. Call `execute_command_with_status()`
4. Update status = `executed` / `failed` / `timeout`
5. Persist `result` or `error` to command record

### 3.2 Executor Dispatch Table

`services/control-plane/bff/command_executor.py`:

| Command | Execution Target | Internal API Path | Notes |
|---|---|---|---|
| `ApproveDeployment` | internal API | `POST /api/internal/v1/deployments/{plan_id}/approve` | Sends approval decision and verification timestamp |
| `PauseRuntime` | internal API | `POST /api/internal/v1/runtimes/{binding_id}/pause` | Uses `binding_id` param (see gap) |
| `ExecuteRollback` | internal API | `POST /api/internal/v1/rollbacks/execute` | Passes rollback target type + version |
| `ActivateKillSwitch` | internal API | `POST /api/internal/v1/kill-switch` | Always sets `action=activate` |
| `ApproveEvolutionDecision` | local stub | None | Writes local result only |
| `ExecuteEvolutionAction` | local stub | None | Writes local result only |

### 3.3 Internal API Scaffold

`services/control_plane/internal_api.py` provides minimal Flask endpoints for deployment approval, runtime pause, rollback, and kill-switch. It enforces:
- Bearer token required on all endpoints
- MFA token format checks when `X-MFA-Token` is provided
- Placeholder JSON responses only (no real persistence)

---

## 4. Frontend Command Handoff (Promotion Review)

Promotion Review (F-042) uses the composed read surface plus one write action:

### 4.1 Approve Deployment Request

```
POST /api/v1/operator/commands
Authorization: Bearer op-42:approver
Content-Type: application/json

{
  "command": "ApproveDeployment",
  "target": {"type": "DeploymentPlan", "id": "plan-123"},
  "action": "approve",
  "params": {
    "deployment_plan_id": "plan-123",
    "approval_decision": "approve",
    "verification_timestamp": "2026-04-11T12:00:00Z"
  },
  "audit_context": {"reason": "reviewed runtime + pool state"}
}
```

### 4.2 Response and Polling

```
202 Accepted
{
  "receipt": {
    "command_id": "...",
    "command_type": "ApproveDeployment",
    "status": "submitted",
    "tracking_url": "/api/v1/operator/commands/<id>"
  },
  "meta": {"estimated_processing_time_ms": 2000, "next_poll_after_ms": 500},
  "staleness_warning": null
}
```

Frontend should poll `tracking_url` until status is `executed`, `failed`, or `timeout`.

### 4.3 UI Gating Rules

- If `staleness_warning` present, show a warning banner and require confirmation.
- If command status is `failed` or `timeout`, display error details and surface the secondary control path.
- If `BFF_READ_SURFACE_STATE == unavailable`, UI should block the CTA and instruct operator to use fallback paths.

---

## 5. Known Gaps vs Wave 1 Acceptance

These are the concrete gaps to close for `APP-002-W1-COMMAND-DEPLOYMENT`:

1. **Internal API auth propagation missing**
   - `command_executor` does not send `Authorization` or MFA headers.
   - `internal_api` requires Bearer token, so calls currently return 401.

2. **PauseRuntime param mismatch**
   - Validator requires `runtime_binding_id` but executor reads `binding_id`.
   - This yields `None` in the internal API URL unless both params are supplied.

3. **Approval payload mismatch**
   - Operator contract allows `verification_notes`; executor does not forward it.
   - Internal API only consumes `approval_decision` + `verification_timestamp`.

4. **Stub execution for evolution commands**
   - `ApproveEvolutionDecision` and `ExecuteEvolutionAction` are still local-only.
   - Acceptable for Wave 1, but must remain clearly non-authoritative.

5. **Authoritative status not yet tied to downstream truth**
   - Executor treats 202 responses as `executed` immediately.
   - No follow-up verification or downstream polling exists yet.

---

## 6. Suggested Implementation Checklist (Parent Task)

Use this list to close the acceptance criteria without touching L1 truth:

1. **Add service-to-service auth for internal API calls**
   - Introduce a service token (env var) or propagate operator JWT to internal API.
   - Forward `X-MFA-Token` when command requires MFA.

2. **Align parameter names**
   - Standardize `runtime_binding_id` vs `binding_id` across validator + executor.
   - Ensure approval payload supports `verification_notes` if required.

3. **Make command status authoritative**
   - Do not mark `executed` until downstream confirms execution.
   - If internal API is async, return `submitted` and poll or reconcile.

4. **Harden audit trail**
   - Record downstream receipt IDs, timestamps, and failure reasons from internal API.
   - Ensure error payloads map to `CommandStatusResponse.error` fields.

5. **Smoke tests**
   - Add a test that validates internal API integration for ApproveDeployment.
   - Ensure command submission still passes when read surfaces are degraded.

---

## 7. Downstream Consumers

| Consumer | What to Use | Notes |
|---|---|---|
| Frontend (F-042) | `/api/v1/operator/commands` + poll | Requires ApproveDeployment to be authoritative |
| APP-002-W2-CLI-FALLBACK | Internal API + CLI | Should match the same command semantics |
| APP-002-W2-READ-INCIDENT | Command status surface | Will depend on truthful command results |

---

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | Only this sidecar file created |
| No canonical truth edited | PASS | References existing files only |
| Handoff anchored to code | PASS | Based on `main.py`, `command_executor.py`, `internal_api.py` |
| Gaps clearly identified | PASS | Section 5 lists actionable gaps |

---

## 9. Handoff to Reviewer (Claude)

This packet enumerates the current command pipeline and the concrete gaps that prevent Promotion Review commands from being authoritative.

Recommended next step:
- Confirm the gap list against `services/control-plane/bff/main.py` and `command_executor.py`
- If accurate, approve this sidecar and hand it to the parent owner (Codex) to drive the Wave 1 command hardening work

---

*Generated by Codex as a sidecar `bff_handoff_packet` helper for APP-002-W1-COMMAND-DEPLOYMENT. This file is a support artifact and does not modify canonical truth.*
