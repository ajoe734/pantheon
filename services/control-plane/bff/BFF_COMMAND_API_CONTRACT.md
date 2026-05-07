# BFF Command API Contract (v1)

Last updated: 2026-05-07
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
| `/bff/v1/commands` | POST | Submit a governed operator command (final contract); returns `CommandResponse<T>`. |
| `/api/v1/operator/commands` | POST | Legacy command submission; returns `CommandSubmissionResponse`. Kept for adapter compatibility. |
| `/api/v1/operator/commands/{command_id}` | GET | Poll command status, result, error, and audit record. |

The status route is a read projection of command state. It is not a retry or
mutation endpoint.

### Final vs Legacy Route

The final contract route `/bff/v1/commands` is the authoritative command surface for new
frontend integrations. The legacy `/api/v1/operator/commands` remains active to avoid
breaking existing adapters and must not be silently removed; use an explicit migration
test when retiring it.

Key differences:

| Dimension | `/bff/v1/commands` (final) | `/api/v1/operator/commands` (legacy) |
|---|---|---|
| Idempotency header | `Idempotency-Key` (canonical); `X-Idempotency-Key` accepted as alias | `X-Idempotency-Key` only |
| Body `idempotencyKey` | Rejected with 400 `INVALID_REQUEST` | Not checked |
| Response shape | `CommandResponse<T>` with `status` and `data` | `CommandSubmissionResponse` with flat `receipt_id` |

## 4. Required Admission Controls

Every accepted command must persist:

| Control | Contract |
|---|---|
| Actor | `actor_ref` from authenticated operator identity; anonymous commands are rejected. |
| Trace | non-empty `trace_id` and `correlation_id`; `X-Trace-Id` may be supplied by caller, otherwise BFF generates one. |
| Idempotency | non-empty idempotency key from header; duplicate key with same request returns the original receipt; same key with different request returns conflict. On final routes, `Idempotency-Key` is canonical and `X-Idempotency-Key` is a temporary compatibility alias; body-level `idempotencyKey` is rejected. |
| RBAC / policy | command-specific validator must produce a policy decision (`allow` or `deny`) tied to actor, target, action, environment, and trace. |
| Audit | non-empty `audit_context.reason`; accepted, denied, validation-failed, and idempotency-conflict commands emit an audit action. |
| Target | typed target reference (`target.type`, `target.id`) matching the command class. |

## 5. Request Shape

Headers (final `/bff/v1/commands` route):

```http
Authorization: Bearer <operator-token>
Idempotency-Key: <stable-client-retry-key>
X-Idempotency-Key: <compatibility alias — accepted when Idempotency-Key is absent>
X-Trace-Id: <optional-trace-id>
X-Correlation-Id: <optional-correlation-id>
X-Request-Id: <optional-request-id>
X-MFA-Token: <required for MFA-gated commands when not already session-bound>
```

`Idempotency-Key` takes precedence over `X-Idempotency-Key` when both are present.
`idempotencyKey` in the request body is rejected with 400 `INVALID_REQUEST` on final routes.

Headers (legacy `/api/v1/operator/commands` route):

```http
Authorization: Bearer <operator-token>
X-Idempotency-Key: <stable-client-retry-key>
X-Trace-Id: <optional-trace-id>
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

Final frontend-facing command routes use:

```json
{
  "status": "accepted",
  "data": {
    "receipt_id": "cmd-...",
    "command": "ApproveDecision"
  },
  "meta": {}
}
```

Contract rules:

- `CommandResponse<T>.data` is required.
- `ActionCommandStatus` is exactly `accepted`, `queued`, or `completed`.
- `requires_approval`, `requires_confirm_token`, and `requires_two_man` are
  not success statuses. Missing preconditions must be returned as non-2xx
  errors.
- The legacy `/api/v1/operator/commands` response remains
  `CommandSubmissionResponse` until that route is explicitly migrated; new
  final-contract routes should use the final `CommandResponse<T>` adapter.

Rejected commands return a non-2xx `BffErrorEnvelope` plus foundation error,
policy decision when applicable, and audit action evidence. Canonical BFF error
codes include:

| Code | Intended Use |
|---|---|
| `CONFIRM_TOKEN_REQUIRED` | Operator confirmation token is missing or expired. |
| `APPROVAL_REQUIRED` | Required approval evidence is absent. |
| `TWO_MAN_REQUIRED` | A second authorized operator decision/signature is required. |
| `IDEMPOTENCY_CONFLICT` | Same idempotency key was reused with a different payload. |
| `SSE_REPLAY_UNAVAILABLE` | Requested SSE replay window is no longer available. |

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

- `Idempotency-Key` header is accepted on `/bff/v1/commands` (final route)
- `X-Idempotency-Key` is accepted as a compatibility alias on the final route
- `Idempotency-Key` takes precedence over `X-Idempotency-Key` when both are present
- `idempotencyKey` in the request body is rejected with 400 `INVALID_REQUEST`
- missing idempotency key returns 400 `INVALID_PARAMS` with `precondition_failed=idempotency_key`
- duplicate idempotency key with identical request replays the original `CommandResponse`
- same key with different body returns 409 `IDEMPOTENCY_CONFLICT`
- `/bff/v1/commands` response shape is `CommandResponse<T>` with `status` and `data`
- legacy `/api/v1/operator/commands` is unaffected and returns `CommandSubmissionResponse`
- runtime, deployment, approval, and incident command classes persist actor,
  trace, idempotency, policy decision, and audit evidence
- the existing committee command path still uses the shared operator command
  facade

---

*End of BFF Command API Contract (v1)*
