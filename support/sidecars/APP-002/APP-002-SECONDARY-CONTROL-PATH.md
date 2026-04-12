# APP-002 Secondary Control Path Spec

**Parent Task**: APP-002 — Define operator-facing deployment, incident, and evolution surfaces  
**Created by**: Copilot  
**Date**: 2026-04-10  
**Status**: Design artifact (APP-002 support)  

> This is a support artifact derived from BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §6 and DEGRADED_OPERATOR_PATH.md §4. It specifies the fallback control path operators use when the BFF is unavailable or degraded.

---

## 1. Purpose

This document specifies **how operators directly access the control plane** when the BFF is unavailable, via:
- **Admin CLI**: Command-line interface for local or remote admin access
- **Protected Internal API**: Internal HTTP API with RBAC + MFA enforcement
- **Escalation procedures**: When to use fallback paths and how to reconcile with BFF

It addresses APP-002 gap **G2: Secondary control path spec**.

---

## 2. Design Principles

### 2.1 Why Secondary Control Path Exists

- **BFF is not on the critical path**: Runtime execution is independent of BFF availability
- **Operator must never be stuck**: Even if all BFF replicas fail, operators retain control via CLI/API
- **Safety-critical operations need direct access**: Kill-switch and rollback should be reachable without queuing through BFF
- **Fallback is explicit, not hidden**: Operators see "use this command" guidance, not silent API failures

### 2.2 Access Control

| Path | Transport | Auth | MFA | Use When |
|------|-----------|------|-----|----------|
| **Admin CLI** | Local/SSH | SSH key + RBAC role | Yes, for destructive actions | Local access, scripted deployment ops, no network issues |
| **Protected Internal API** | HTTP(S) | Bearer token + RBAC | Yes, for destructive actions | Remote access, when BFF is down, programmatic escalation |
| **BFF (Primary)** | HTTP(S) | Bearer token + RBAC | No (BFF is read-heavy) | Normal operations, all read queries |

### 2.3 Scope of Secondary Path

The secondary path mirrors the BFF's write operations exactly:
- Deployment approval/rejection
- Runtime pause/resume
- Rollback execution
- Kill-switch activation
- Evolution decision approval/execution

The secondary path does **not**:
- Provide read surfaces (use BFF for that, or SSH into control-plane service for debug)
- Modify L1 canonical objects directly
- Bypass RBAC or approval requirements

---

## 3. Admin CLI Specification

### 3.1 Overview

The admin CLI is a **local command-line tool** installed on control-plane nodes and accessible via SSH to control-plane admins.

```bash
$ pantheon-admin <command> <args>
```

### 3.2 Installation & Access

**Prerequisites**:
- SSH access to control-plane nodes
- Valid SSH key in `~/.ssh/id_pantheon_admin`
- `admin` or `approver` role in RBAC
- Operator's IP whitelisted in control-plane firewall rules (optional, for security)

**Installation**:
```bash
# On control-plane node:
$ which pantheon-admin
/usr/local/bin/pantheon-admin

$ pantheon-admin --version
Pantheon Admin CLI v1.0.0 (control-plane component)
```

### 3.3 Command Structure

#### 3.3.1 `pantheon-admin deployment`

**Approve or reject a deployment plan**:

```bash
pantheon-admin deployment approve <plan_id> \
  --reason "verified runtime binding and capital pool state" \
  --verification-timestamp "2026-04-10T15:00:00Z" \
  [--mfa-token <token>]

pantheon-admin deployment reject <plan_id> \
  --reason "runtime persona mismatch detected" \
  [--mfa-token <token>]
```

**Output**:

```
✓ Deployment plan {plan_id} approved
  Approval decision ID: ad-2026-04-10-12345
  State transition: planned → approved
  Audit record created

Next: Watch for deployment execution or check BFF console at /operator/deployment/{plan_id}
```

**Error cases**:

```
✗ Deployment plan {plan_id} not found
  Check: Is plan_id correct? Was it already executed?

✗ Insufficient role: requires 'approver' or 'admin', you have 'operator'
  Escalate to: Your team's designated approver
  Or contact: platform-ops@example.com
```

**MFA requirement**:
- If operator's role requires MFA, `--mfa-token` is mandatory
- Token format: 6-digit TOTP code from operator's authentication device
- No token → command rejected with `MFA_REQUIRED` error

---

#### 3.3.2 `pantheon-admin runtime`

**Pause, resume, or force-halt a runtime**:

```bash
pantheon-admin runtime pause <binding_id> \
  --reason "investigating anomalous memory usage" \
  --duration 3600 \
  [--mfa-token <token>]

pantheon-admin runtime resume <binding_id> \
  [--mfa-token <token>]

pantheon-admin runtime force-halt <binding_id> \
  --reason "critical safety incident, immediate halt required" \
  [--confirm]  # Prevents accidental execution
  [--mfa-token <token>]
```

**Output**:

```
✓ Runtime binding {binding_id} paused
  Pause command submitted to runtime-manager
  Pause will expire at: 2026-04-10T16:00:00Z (in 1 hour)
  
Next: Monitor runtime state or resume when safe:
  pantheon-admin runtime resume {binding_id}
```

**MFA requirement**:
- `force-halt` always requires MFA (critical action)
- `pause` requires MFA if caller is non-admin
- `resume` requires MFA if operation is being resumed early (before duration expiry)

---

#### 3.3.3 `pantheon-admin rollback`

**Execute a rollback of deployment or runtime**:

```bash
pantheon-admin rollback execute <target_id> \
  --target-type deployment|runtime \
  --rollback-to-version <artifact_id or version_tag> \
  --reason "detected critical performance regression, rolling back deployment v1.2.0 to v1.1.9" \
  --verify-before-executing \
  [--mfa-token <token>]

pantheon-admin rollback list <target_id>  # Show previous rollback records

pantheon-admin rollback abort <rollback_id>  # Cancel an in-progress rollback
  --reason "regression was false alarm, reverting rollback"
  [--mfa-token <token>]
```

**Output**:

```
✓ Rollback executed for deployment plan {target_id}
  Rollback ID: rb-2026-04-10-67890
  Rolling back from: v1.2.0 (artifact-2026-04-10-a123)
           to: v1.1.9 (artifact-2026-04-09-b456)
  
  Verification checks passed:
    ✓ Previous version is available and not corrupted
    ✓ No incompatible schema changes since v1.1.9
    ✓ Rollback path is safe (no circular dependencies)
    
  Rollback started at: 2026-04-10T15:01:00Z
  Estimated time: 45 seconds
  
Next: Monitor via:
  pantheon-admin rollback status {rollback_id}
  Or BFF console: /operator/rollback/{rollback_id}
```

**MFA requirement**: Always required for rollback execution

---

#### 3.3.4 `pantheon-admin kill-switch`

**Activate or deactivate kill-switch**:

```bash
pantheon-admin kill-switch activate \
  --scope all|persona|pool \
  [--scope-id <persona_id or pool_id>] \
  --severity critical|high|medium \
  --rationale "detected compromised credential in persona strategy, disabling all runtimes immediately" \
  --force  # Prevents accidental execution \
  [--mfa-token <token>]

pantheon-admin kill-switch status [--scope all|persona|pool] [--scope-id <id>]

pantheon-admin kill-switch deactivate \
  --scope <scope> \
  [--scope-id <id>] \
  --rationale "incident remediated, resuming normal operations" \
  [--mfa-token <token>]
```

**Output**:

```
✓ Kill-switch activated globally
  Scope: all
  Severity: critical
  Activated at: 2026-04-10T15:00:00Z
  
  Impact:
    - All active runtimes will halt within 30 seconds
    - New deployment attempts will be blocked
    - Fallback mode activated (read-only operations only)
    
  Current status:
    Total runtimes targeted: 47
    Halted: 45
    Still halting: 2
    
Next: Monitor runtime halts and verify system safety:
  pantheon-admin kill-switch status
  
  Once safe, deactivate:
  pantheon-admin kill-switch deactivate --scope all --rationale "..."
```

**MFA requirement**: Always required (kill-switch is safety-critical)

---

#### 3.3.5 `pantheon-admin evolution`

**Approve, reject, or execute evolution decisions**:

```bash
pantheon-admin evolution approve <decision_id> \
  --approval-rationale "drift detector recommendation reviewed, persona strategy retrain is safe" \
  [--mfa-token <token>]

pantheon-admin evolution reject <decision_id> \
  --rejection-rationale "insufficient data to support retrain, waiting for next period" \
  [--mfa-token <token>]

pantheon-admin evolution execute <decision_id> \
  --action-type freeze|retrain|mutate|retire \
  [--action-params <json>] \
  [--mfa-token <token>]
```

**Output**:

```
✓ Evolution decision {decision_id} approved
  Decision type: retrain
  Target: persona-{id}
  Approval by: {operator_id} (reviewer role)
  
  Status progression:
    proposed → reviewed → approved ✓
    
Next: 
  - Decision awaits execution approval (if EVO-004 requires separate step)
  - Or execute immediately: pantheon-admin evolution execute {decision_id}
```

**MFA requirement**:
- `approve` requires MFA if decision risk level is high (per EVOLUTION_REVIEW_AND_THRESHOLDS.md)
- `execute` always requires MFA

---

### 3.4 Global CLI Options

```bash
pantheon-admin [--config <path>] [--log-level debug|info|warn|error] <command> <args>

--config <path>      # Alternative config file (default: ~/.pantheon/cli.conf)
--log-level <level>  # Logging verbosity (default: info)
--output json|text   # Output format (default: text with colors)
--dry-run            # Show what would happen without executing (not all commands support)
--verbose            # Include detailed context in output
```

---

### 3.5 Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Command execution failed (e.g., object not found, invalid state) |
| 2 | Authorization failed (insufficient role, MFA rejected) |
| 3 | CLI usage error (invalid arguments) |
| 4 | Control-plane service unavailable |
| 5 | Partial execution (e.g., half of multi-step operation failed) |

---

## 4. Protected Internal API Specification

### 4.1 Overview

The protected internal API is an **HTTP(S) API** on the control-plane service, accessible only from whitelisted IPs and requiring MFA + RBAC.

**Endpoint base**: `https://control-plane-internal.example.com/api/internal/v1`

### 4.2 Authentication

#### 4.2.1 Bearer Token

```http
Authorization: Bearer <jwt_token>
```

Token must be:
- Issued by the internal auth system
- Signed with control-plane's private key
- Include claims: `sub` (operator ID), `roles` (array), `exp` (expiration)
- Valid for 1 hour (then requires refresh)

#### 4.2.2 MFA Validation

For destructive operations, include MFA token in request header:

```http
X-MFA-Token: <6-digit TOTP or OTP from operator's auth device>
X-MFA-Required: true
```

If MFA validation fails:
```json
{
  "error": {
    "code": "MFA_VALIDATION_FAILED",
    "message": "MFA token expired or incorrect"
  }
}
```

### 4.3 Endpoints

#### 4.3.1 `POST /api/internal/v1/deployments/{plan_id}/approve`

```http
POST /api/internal/v1/deployments/{plan_id}/approve
Authorization: Bearer <token>
Content-Type: application/json

{
  "approval_decision": "approve|reject",
  "verification_notes": "...",
  "verification_timestamp": "2026-04-10T15:00:00Z"
}

Response (202 Accepted):
{
  "approval_decision_id": "ad-...",
  "target_plan_id": "{plan_id}",
  "state_after": "approved|rejected",
  "audit_id": "audit-...",
  "command_id": "cmd-..."  // To track execution
}
```

#### 4.3.2 `POST /api/internal/v1/runtimes/{binding_id}/pause`

```http
POST /api/internal/v1/runtimes/{binding_id}/pause
Authorization: Bearer <token>
X-MFA-Token: <token>  // If operator role requires MFA
Content-Type: application/json

{
  "reason": "...",
  "duration_seconds": 3600
}

Response (202 Accepted):
{
  "command_id": "cmd-...",
  "runtime_binding_id": "{binding_id}",
  "pause_expires_at": "2026-04-10T16:00:00Z",
  "status": "submitted"
}
```

#### 4.3.3 `POST /api/internal/v1/rollbacks/execute`

```http
POST /api/internal/v1/rollbacks/execute
Authorization: Bearer <token>
X-MFA-Token: <token>  // Always required
Content-Type: application/json

{
  "rollback_target_type": "deployment|runtime",
  "target_id": "...",
  "rollback_to_version": "...",
  "reason": "...",
  "verify_before_executing": true
}

Response (202 Accepted):
{
  "rollback_id": "rb-...",
  "command_id": "cmd-...",
  "status": "submitted",
  "tracking_url": "/api/internal/v1/commands/{command_id}"
}
```

#### 4.3.4 `POST /api/internal/v1/kill-switch`

```http
POST /api/internal/v1/kill-switch
Authorization: Bearer <token>
X-MFA-Token: <token>  // Always required
Content-Type: application/json

{
  "action": "activate|deactivate",
  "scope": "all|persona|pool",
  "scope_id": "<id or null>",
  "severity": "critical|high|medium",
  "rationale": "..."
}

Response (202 Accepted):
{
  "kill_switch_order_id": "ks-...",
  "command_id": "cmd-...",
  "action": "activate|deactivate",
  "scope": "...",
  "status": "submitted"
}
```

#### 4.3.5 `GET /api/internal/v1/commands/{command_id}`

```http
GET /api/internal/v1/commands/{command_id}
Authorization: Bearer <token>

Response (200 OK):
{
  "command_id": "{command_id}",
  "type": "...",
  "status": "submitted|processing|executed|failed",
  "submitted_at": "...",
  "result": { "...": "..." },
  "error": null | { "code": "...", "message": "..." }
}
```

---

### 4.4 Error Responses

```json
{
  "error": {
    "code": "<ERROR_CODE>",
    "message": "<Human-readable message>",
    "details": { "...": "..." },
    "trace_id": "<for logging>"
  }
}
```

Common errors:
- `401 Unauthorized`: Token invalid or expired
- `403 Forbidden`: Operator lacks required role
- `404 Not Found`: Object does not exist
- `409 Conflict`: Concurrent modification or invalid state
- `503 Service Unavailable`: Backend service down

---

## 5. Fallback UX Guidance

### 5.1 When to Show Secondary Control Path to Operator

**Scenario 1: BFF is completely unavailable**

```
⚠️  CONTROL PLANE UI UNAVAILABLE
The BFF is currently down. You can still manage operations via:

Admin CLI (SSH):
  ssh control-plane-node.example.com
  pantheon-admin deployment approve <plan_id> --reason "..."

Internal API (curl):
  curl -X POST https://control-plane-internal/api/internal/v1/deployments/{plan_id}/approve \
    -H "Authorization: Bearer <token>" \
    -H "X-MFA-Token: <mfa>" \
    -d '{"approval_decision": "approve"}'

Need help?
  • View control-plane status: https://status.example.com
  • Chat with platform team: #pantheon-support
```

**Scenario 2: BFF read surface is degraded, but command submission is working**

```
⚠️  COMMAND VERIFICATION STATUS: DEGRADED
The data shown below was last verified 5 minutes ago.
Re-verify before approving critical actions.

Suggested workflow:
1. Check current state via BFF when available
2. Or SSH to control-plane and run: pantheon-admin deployment show <plan_id>
3. Then submit your approval via the button below (or CLI if BFF unavailable)
```

### 5.2 Escalation Path Copy

When an operator hits an error, the UI should include actionable escalation text:

```
✗ Your role (operator) cannot approve this deployment.
  
This requires 'approver' role. Escalation options:

1. Ask your team's designated approver to approve this deployment
2. Request temporary 'approver' role via: https://admin.example.com/request-role
3. Contact platform team: #pantheon-support

If urgent and you can't reach an approver:
  • CLI with admin override (requires MFA + audit): 
    pantheon-admin deployment approve <plan_id> --as-role admin --mfa-token <token>
  • This will be logged and may trigger a compliance review
```

---

## 6. Reconciliation Between BFF and Secondary Path

### 6.1 Command Idempotency

All operator commands are **idempotent** — issuing the same command twice produces the same result.

**Example**: If operator submits "approve deployment X" via CLI, then accidentally submits the same via BFF:
- First submission creates ApprovalDecision
- Second submission detects existing ApprovalDecision for same operator, deployment, and timestamp → returns the same receipt ID, no duplicate

### 6.2 Audit Trail Consistency

Both BFF and CLI commands create identical audit records in the central audit log:
- Logged to the same database
- Queryable by operator, command type, target object, and timestamp
- No special markers for "CLI vs BFF" — both are authoritative

### 6.3 SSE Updates Are Source of Truth

Operator sees true state via SSE feeds:
- `/api/v1/operator/deployment-review/{plan_id}` (BFF)
- Real-time updates via SSE show when approvals/rollbacks take effect
- No polling from secondary path needed for state verification

---

## 7. Security Considerations

### 7.1 MFA Enforcement

- **All kill-switch operations**: MFA required
- **All rollbacks**: MFA required  
- **High-risk evolution decisions**: MFA required per EVOLUTION_REVIEW_AND_THRESHOLDS.md
- **Admin actions**: MFA required
- **Approval/rejection of critical deployments**: MFA required if operator role is "approver" and deployment affects more than N runtimes (configurable threshold)

### 7.2 IP Whitelisting

- Admin CLI: SSH key authorization (implicitly localized)
- Internal API: Requester IP must be in control-plane whitelist (prevent off-network access unless VPN)
- Token expiration: 1 hour (no long-lived tokens for internal API)

### 7.3 Audit Logging

Every secondary path action logs:
- Operator ID and roles
- Timestamp and source IP
- Command type and target
- MFA validation status (passed/failed)
- Command result (success/failure)
- Any error details

Logs are retained for compliance and investigation.

---

## 8. Acceptance Criteria for APP-002

✅ Admin CLI command set covers all operator journeys  
✅ Protected Internal API mirrors CLI with HTTP interface  
✅ MFA enforcement rules are clear and tied to action risk levels  
✅ Fallback UX guidance is actionable for operators  
✅ Idempotency and reconciliation are specified  
✅ Audit logging requirement is defined  
✅ This spec does NOT modify canonical objects — only provides alternate access path  

---

## 9. Implementation Notes

This spec is **design-phase** work. Implementation will:

1. Build Admin CLI tool (Go or Python)
2. Add Protected Internal API routes to control-plane service
3. Integrate MFA validation with auth system
4. Implement audit logging middleware
5. Add IP whitelist management
6. Document deployment and access procedures

---

## 10. Dependencies

- **APP-001 (done)**: Stable read surfaces that operators query before using secondary path
- **Operator Action Contract (APP-002 sibling)**: Defines command structure and validation
- **Auth system**: Must support TOTP/OTP MFA for operator accounts

---

*Generated by Copilot as support artifact for APP-002. Ready for parent task absorption.*
