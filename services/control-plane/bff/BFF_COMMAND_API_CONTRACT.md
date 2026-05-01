# BFF Command API Contract (v1)

Last updated: 2026-05-01
Status: canonical - governed BFF command facade contract for P0 command admission
Tier: L2 Planning & Execution (paired with `BFF_API_CONTRACT.md`)
Scope: command admission, receipt, idempotency, RBAC/policy, trace, and audit requirements for BFF-routed operator commands
Owner: Codex2
Reviewer: Claude
Task-ID: P0-BFF-CMD-001

---

## 1. Purpose

This document separates the BFF command facade from the read-oriented BFF API
contract.

The read contract remains GET-only and is defined in `BFF_API_CONTRACT.md`.
This command contract defines the narrow write-facing facade used by operator
surfaces to request governed actions. The BFF accepts command envelopes, runs
admission controls, records command/audit/idempotency state, and dispatches to
canonical control-plane authorities. The BFF does not become a canonical store.

## 2. Boundary Rules

1. Read surfaces must not be reused as pseudo-write channels.
2. Command admission must not silently mutate UI-only state as canonical truth.
3. Runtime, deployment, approval, rollback, kill-switch, incident, and evolution
   actions must enter through a command envelope or a documented secondary
   control path.
4. BFF command records are command/audit records. Domain state transitions remain
   owned by the downstream governance, runtime-manager, incident, or execution
   authority.
5. Control-plane degradation does not relax RBAC, idempotency, MFA, or audit
   requirements.

## 3. Routes

| Route | Method | Purpose |
|---|---:|---|
| `/api/v1/operator/commands` | POST | Submit a governed operator command and receive a command receipt. |
| `/api/v1/operator/commands/{command_id}` | GET | Poll command status, result, error, and audit record. |

The status route is a read projection of command state. It is not a retry or
mutation endpoint.

## 4. Required Admission Controls

Every accepted command must persist:

| Control | Contract |
|---|---|
| Actor | `actor_ref` from authenticated operator identity; anonymous commands are rejected. |
| Trace | non-empty `trace_id` and `correlation_id`; `X-Trace-Id` may be supplied by caller, otherwise BFF generates one. |
| Idempotency | non-empty `X-Idempotency-Key`; duplicate key with same request returns the original receipt; same key with different request returns conflict. |
| RBAC / policy | command-specific validator must produce a policy decision (`allow` or `deny`) tied to actor, target, action, environment, and trace. |
| Audit | non-empty `audit_context.reason`; accepted, denied, validation-failed, and idempotency-conflict commands emit an audit action. |
| Target | typed target reference (`target.type`, `target.id`) matching the command class. |

## 5. Request Shape

Headers:

```http
Authorization: Bearer <operator-token>
X-Idempotency-Key: <stable-client-retry-key>
X-Trace-Id: <optional-trace-id>
X-Correlation-Id: <optional-correlation-id>
X-Request-Id: <optional-request-id>
X-MFA-Token: <required for MFA-gated commands when not already session-bound>
```

Body:

```json
{
  "command": "ApproveDecision",
  "target": {
    "type": "ApprovalDecision",
    "id": "appr-001"
  },
  "action": "approve",
  "params": {
    "decision_id": "appr-001"
  },
  "audit_context": {
    "reason": "Policy checks passed",
    "incident_id": null
  }
}
```

The BFF persists a foundation command context containing:

```json
{
  "command_envelope": {
    "command_id": "cmd-...",
    "command_type": "ApproveDecision",
    "actor_ref": { "actor_type": "user", "actor_id": "op-6", "roles": ["approver"] },
    "idempotency_key": "idmp-...",
    "trace": { "trace_id": "trace-...", "correlation_id": "trace-..." }
  },
  "idempotency_record": { "idempotency_key": "idmp-...", "status": "succeeded" },
  "policy_decision": { "decision": "allow" },
  "audit_action": { "action_type": "bff.command.accepted" }
}
```

## 6. Response Shape

Accepted commands return HTTP 202:

```json
{
  "receipt_id": "cmd-...",
  "command": "ApproveDecision",
  "status": "accepted",
  "accepted_at": "2026-05-01T00:00:00Z",
  "routing_path": "direct",
  "expected_completion_at": "2026-05-01T00:00:02Z"
}
```

Rejected commands return the standard BFF error envelope plus foundation error,
policy decision when applicable, and audit action evidence.

## 7. Command Classes

| Class | Commands | Minimum Admission Contract |
|---|---|---|
| Deployment | `ApproveDeployment`, `EscalateDiff` | approver/admin or governance operator role; deployment target; audit reason; idempotency key. |
| Approval | `ApproveDecision`, `RejectDecision`, `RequestApprovalRevision`, `ApproveRollback`, `RejectRollback` | approver/admin role; approval or rollback target; policy decision and audit action. |
| Runtime | `PauseRuntime`, `PauseExecution`, `ExecuteRollback`, `HardRollback` | operator/admin/approver role per action; runtime or runtime-binding target; live broker scope fail-closed when disabled. |
| Incident / kill switch | `IssueRiskOff`, `LiquidateAll`, `IssueSafeMode`, `ActivateKillSwitch` | operator/admin role per action; admin+MFA for destructive commands; audit reason and command receipt. |
| Evolution / governance | `ApproveEvolutionDecision`, `ExecuteEvolutionAction`, `ApproveMutation`, `RejectMutation`, `RecordSponsorDecision` | policy-gated governance role; target state checked against read projection before dispatch. |

## 8. Verification

Focused regression evidence:

```bash
python3 -m pytest services/control-plane/bff/test_governance_command_submission.py services/control-plane/bff/test_cw03_committee_board_contract.py -q
```

This test set verifies:

- missing `X-Idempotency-Key` is rejected with foundation error and audit action
- duplicate idempotency key with identical request replays the original receipt
- runtime, deployment, approval, and incident command classes persist actor,
  trace, idempotency, policy decision, and audit evidence
- the existing committee command path still uses the shared operator command
  facade

---

*End of BFF Command API Contract (v1)*
