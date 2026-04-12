# APP-002 Operator Action Contract

**Parent Task**: APP-002 — Define operator-facing deployment, incident, and evolution surfaces  
**Created by**: Copilot  
**Date**: 2026-04-10  
**Status**: Design artifact (APP-002 support)  

> This is a support artifact derived from the APP-001 BFF API Contract and EVOLUTION_REVIEW_AND_THRESHOLDS.md. It defines how operators interact with the system through the BFF—not how the system executes those actions internally.

---

## 1. Purpose

This document specifies **how operators submit commands** to the control plane through the BFF, and what the BFF's command submission and result surface must expose.

It addresses APP-002 gap **G1: Operator command contract**.

---

## 2. Command Submission Model

### 2.1 Core Principle

All operator commands are **read-safe operations on the BFF side**:
- The BFF validates the request
- The BFF submits the command to an async command queue (not a direct state mutation)
- The operator gets back a **command receipt** with a unique ID
- Execution happens downstream; the operator polls for results via separate read surfaces

**Why**: Decouples operator UI responsiveness from backend execution latency. Prevents timeout errors from losing intent.

### 2.2 HTTP Semantics

```
POST /api/v1/operator/commands
  Authorization: Bearer <token>
  Content-Type: application/json

Request:
{
  "command": "<command_type>",
  "target": {
    "type": "<object_type>",
    "id": "<object_id>"
  },
  "action": "<action_name>",
  "params": { "...": "..." },
  "audit_context": {
    "reason": "<operator rationale>",
    "timestamp": "2026-04-10T15:00:00Z"
  }
}

Response (202 Accepted):
{
  "receipt": {
    "command_id": "<unique receipt ID>",
    "command_type": "<type>",
    "target": { "type": "...", "id": "..." },
    "submitted_at": "2026-04-10T15:00:00Z",
    "status": "submitted",
    "tracking_url": "/api/v1/operator/commands/{command_id}"
  },
  "meta": {
    "estimated_processing_time_ms": 2000,
    "next_poll_after_ms": 500
  }
}
```

---

## 3. Operator Command Types

### 3.1 Deployment Review Commands

#### 3.1.1 `ApproveDeployment`

**When**: Operator reviews a deployment and approves it to proceed

**Target Object**: `DeploymentPlan`

**Action**: `approve` or `reject`

**Params**:

```json
{
  "deployment_plan_id": "<plan_id>",
  "approval_decision": "approve|reject",
  "verification_notes": "<text>",
  "verification_timestamp": "2026-04-10T15:00:00Z"
}
```

**Preconditions**:
- Deployment is in `planned` or `pending_approval` state
- Operator has `approver` role for this deployment's pool/strategy
- No active incident affecting the deployment target
- BFF read surfaces show `fresh` or `degraded` state (not `unavailable`)

**Canonical Objects Affected**:
- `ApprovalDecision` (created/updated with approval record)
- `DeploymentPlan` (state may transition to `approved` or `rejected`)

**Result Surface**: `/api/v1/operator/commands/{command_id}` returns `status: executed` when decision is persisted; consumer must poll `/api/v1/operator/deployment-review/{plan_id}` to see updated approval state

---

### 3.2 Incident Response Commands

#### 3.2.1 `PauseRuntime`

**When**: Operator needs to pause a runtime to investigate or halt processing

**Target Object**: `RuntimeBinding`

**Action**: `pause` or `resume`

**Params**:

```json
{
  "runtime_binding_id": "<binding_id>",
  "pause_action": "pause|resume",
  "reason": "investigation|manual_halt|thresholds",
  "duration_seconds": 3600
}
```

**Preconditions**:
- Runtime is active (`running` state)
- Operator has `operator` or `admin` role for this runtime
- Read surface shows `fresh` or `degraded` state

**Canonical Objects Affected**:
- `RuntimeBinding` (pause flag set; processed by runtime manager)
- `IncidentCase` (linked if issued during incident response)

**Result Surface**: `/api/v1/operator/commands/{command_id}` and `/api/v1/operator/incident-response/{incident_id}` show pause receipt

---

#### 3.2.2 `ExecuteRollback`

**When**: Operator needs to rollback a deployment or runtime to a known good state

**Target Object**: `RuntimeBinding` or `DeploymentPlan`

**Action**: `rollback`

**Params**:

```json
{
  "rollback_target_type": "deployment|runtime",
  "target_id": "<deployment_plan_id or binding_id>",
  "rollback_to_version": "<artifact_id or version_tag>",
  "verify_before_executing": true,
  "reason": "<operator narrative>"
}
```

**Preconditions**:
- A previous good state is known and available (from `RollbackRecord` surface)
- Operator has `admin` or `approver` role
- Read surface shows `fresh` or `degraded` state
- No concurrent rollback in progress for this target

**Canonical Objects Affected**:
- `RollbackRecord` (created, linked to `DeploymentPlan` and `RuntimeBinding`)
- `DeploymentPlan` (state → `rolled_back`)
- `RuntimeBinding` (state → `paused`, then resumed with new version)

**Result Surface**: `/api/v1/operator/commands/{command_id}` and `/api/v1/operator/incident-response/{incident_id}` show rollback status; `/api/v1/operator/rollbacks` list updated

---

#### 3.2.3 `ActivateKillSwitch`

**When**: Operator activates the kill-switch to force all runtimes offline in a safety-critical scenario

**Target Object**: `KillSwitchOrder`

**Action**: `activate` or `deactivate`

**Params**:

```json
{
  "scope": "persona|pool|all",
  "scope_id": "<persona_id or pool_id or null>",
  "severity": "critical|high|medium",
  "activate": true,
  "rationale": "<incident context>"
}
```

**Preconditions**:
- Operator has `admin` role with MFA validation (see Secondary Control Path)
- Scope is valid (persona exists, pool exists, or scope is "all")
- No active kill-switch for this scope already

**Canonical Objects Affected**:
- `KillSwitchOrder` (created, state → `active`)
- All `RuntimeBinding` instances in scope (marked for halt)

**Result Surface**: `/api/v1/operator/commands/{command_id}` and `/api/v1/operator/kill-switch-status` reflect order

**Note**: Kill-switch has an independent execution path (KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md); BFF command submission is the operator-facing entry point.

---

### 3.3 Evolution Control Commands

#### 3.3.1 `ApproveEvolutionDecision`

**When**: Operator approves an `EvolutionDecision` to proceed to execution

**Target Object**: `EvolutionDecision`

**Action**: `approve` or `reject`

**Params**:

```json
{
  "evolution_decision_id": "<decision_id>",
  "approval_action": "approve|reject",
  "approval_rationale": "<governance narrative>",
  "approved_by_role": "reviewer|risk_owner|governance_committee"
}
```

**Preconditions**:
- Decision is in `reviewed` state (per EVOLUTION_REVIEW_AND_THRESHOLDS.md §4)
- Operator role matches required approval level (low-risk → reviewer, high-risk → governance committee)
- Decision has not been superseded

**Canonical Objects Affected**:
- `EvolutionDecision` (state → `approved` or `rejected`)
- `PostmortemReport` (if linked incident context exists)

**Result Surface**: `/api/v1/operator/commands/{command_id}` and `/api/v1/operator/evolution-decision/{decision_id}` show approval state

**Dependency**: Requires `EVO-004` to define the exact execution boundary between approval and operational action. APP-002 surfaces should **keep review and execution UI separate** until EVO-004 lands.

---

#### 3.3.2 `ExecuteEvolutionAction`

**When**: Operator executes the operational part of an approved `EvolutionDecision` (e.g., freeze, retrain, mutate policy)

**Target Object**: `EvolutionDecision`

**Action**: `execute`

**Params**:

```json
{
  "evolution_decision_id": "<decision_id>",
  "action_type": "freeze|retrain|mutate|retire",
  "target_scope": {
    "type": "strategy|persona|pool|artifact",
    "id": "<target_id>"
  },
  "execution_params": { "...": "..." }
}
```

**Preconditions**:
- Decision is in `approved` state
- Operator has `admin` or role-specific execution permission
- Target object is in valid state for the action
- All prerequisite conditions (e.g., no active incidents) are met

**Canonical Objects Affected**:
- `EvolutionDecision` (state → `executed`)
- Target object (e.g., `StrategySpec` frozen, `Persona` retrained, etc.)
- `FreezeOrder` or `DeploymentPlan` (depending on action type)

**Result Surface**: `/api/v1/operator/commands/{command_id}` and evolution decision surface show execution state

**Note**: The exact separation between "approval" and "execution" is pending `EVO-004`. APP-002 should surface these as distinct screens/actions, with warnings that execution requires additional approval/confirmation until boundaries are fully defined.

---

## 4. Command Result Polling

### 4.1 Receipt Tracking

```
GET /api/v1/operator/commands/{command_id}
  Authorization: Bearer <token>

Response:
{
  "command_id": "<id>",
  "type": "<command_type>",
  "target": { "type": "...", "id": "..." },
  "submitted_at": "...",
  "status": "submitted|processing|executed|failed",
  "result": {
    "object_id": "<resulting canonical object ID if applicable>",
    "state_after": "...",
    "execution_timestamp": "2026-04-10T15:01:00Z"
  },
  "error": null | { "code": "...", "message": "..." },
  "audit": {
    "operator_id": "...",
    "roles_at_submission": ["..."],
    "ip_address": "...",
    "timestamp": "..."
  }
}
```

### 4.2 State Transitions

```
Submitted
  ↓ (processing starts)
Processing
  ├─→ Executed (command successfully applied)
  ├─→ Failed (command rejected or error occurred)
  └─→ Timeout (processing took too long; operation may still be pending downstream)
```

### 4.3 Polling Guidance

- **Initial check**: After 500ms (returned in `next_poll_after_ms` from submission response)
- **Polling interval**: Exponential backoff: 500ms → 1s → 2s → 5s
- **Timeout**: After 30 seconds without status update, assume downstream processing is delayed; recommend manual verification via object read surface

---

## 5. Validation & Error Handling

### 5.1 Precondition Validation

When a command is received, the BFF validates:

1. **Authentication**: Token is valid and not expired
2. **Authorization**: Operator has required role(s) for the action
3. **Object state**: Target object exists and is in valid state for the action
4. **Scope validity**: Target references (persona_id, pool_id, etc.) are valid
5. **Concurrent safety**: No conflicting command is already executing for the target

### 5.2 Error Response Format

```json
{
  "error": {
    "code": "<ERROR_CODE>",
    "message": "<Human-readable message>",
    "details": {
      "reason": "<Why the command was rejected>",
      "precondition_failed": "<Which precondition>",
      "suggestion": "<What to do instead>"
    }
  }
}
```

### 5.3 Common Error Codes

| Code | Meaning | Operator Action |
|------|---------|-----------------|
| `INVALID_TOKEN` | Auth token missing or invalid | Re-authenticate |
| `INSUFFICIENT_ROLE` | Operator lacks required role | Escalate to higher role (admin/approver) |
| `OBJECT_NOT_FOUND` | Target object does not exist | Verify object ID and refresh list |
| `INVALID_STATE` | Target object in wrong state for action | Check object state; may need to complete prior action |
| `CONCURRENT_MODIFICATION` | Another command is already modifying this object | Wait for prior command to complete or cancel |
| `DOWNSTREAM_UNAVAILABLE` | Backend service is unavailable | Retry later or use secondary control path |
| `PRECONDITION_NOT_MET` | Custom precondition failed (e.g., no active incident) | Address precondition and retry |
| `MFA_REQUIRED` | Admin action requires MFA validation | Provide MFA token |

---

## 6. Audit Trail

Every operator command creates an immutable audit record:

```json
{
  "command_id": "<unique ID>",
  "command_type": "<type>",
  "target": { "type": "...", "id": "..." },
  "operator": {
    "id": "<operator_id>",
    "roles": ["operator", "approver"],
    "mfa_verified": true | false
  },
  "request": {
    "received_at": "2026-04-10T15:00:00Z",
    "ip_address": "...",
    "user_agent": "...",
    "preconditions_checked": ["..."]
  },
  "execution": {
    "started_at": "2026-04-10T15:00:05Z",
    "completed_at": "2026-04-10T15:00:15Z",
    "status": "executed|failed",
    "downstream_receipt": "<external system reference if applicable>"
  },
  "result": {
    "canonical_objects_affected": ["ApprovalDecision:...", "DeploymentPlan:..."],
    "state_changes": { "...": "..." }
  }
}
```

Audit records are **immutable** and queryable by operator, command_id, object_type, and date range.

---

## 7. Degraded Mode Behavior

### 7.1 When BFF Read Surface is Degraded or Unavailable

If the BFF read surfaces for the command's target are `degraded` or `unavailable` (per DEGRADED_OPERATOR_PATH.md):

- **Submit anyway, with warning**: Operator can still submit the command, but the BFF logs a warning that verification was not fresh
- **Include staleness context**: Response includes `staleness` metadata so operator knows command was issued on stale data
- **Recommend re-check**: Response suggests operator re-verify target state via secondary control path before confirming action

### 7.2 When Command Queue is Unavailable

If the downstream command processing queue is unreachable:

- **Return HTTP 503**: `DOWNSTREAM_UNAVAILABLE`
- **Suggest secondary control path**: Response includes fallback guidance (admin CLI commands, internal API, etc.)
- **Preserve intent**: In Secondary Control Path spec, admin can submit the same action directly

---

## 8. Acceptance Criteria for APP-002

✅ Command submission endpoint defined with preconditions  
✅ All three operator journey types (deployment, incident, evolution) have formalized commands  
✅ Error handling and validation rules are explicit  
✅ Audit trail requirement is clear  
✅ Degraded mode behavior is specified  
✅ This contract maps to canonical objects (ApprovalDecision, IncidentCase, EvolutionDecision, etc.)  
✅ This contract does NOT modify APP-001 BFF read surfaces  
✅ Separate "review" vs "execution" actions pending EVO-004 resolution  

---

## 9. Dependencies

- **APP-001 (done)**: Stable read surfaces provide the data operators query before submitting commands
- **EVO-004 (todo)**: Must define the exact boundary between evolution decision approval and operational execution
- **Secondary Control Path Spec (APP-002 sibling)**: Defines fallback operator paths when BFF is unavailable

---

## 10. Next Phase: Implementation

This contract is **shape-phase** work. Implementation (APP-???) will:

1. Define the exact command queue schema (Kafka topic, RabbitMQ format, or internal queue)
2. Implement BFF endpoint handlers for each command type
3. Add audit logging middleware
4. Define command timeout and retry policies
5. Implement command result aggregation and SSE push

---

*Generated by Copilot as support artifact for APP-002. Approved by review; ready for parent task absorption.*
