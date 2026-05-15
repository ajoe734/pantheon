# P0-BFF-CMD-001 Sidecar BFF Handoff Packet

Task: `P0-BFF-CMD-001-SIDECAR-BFF-HANDOFF`
Parent task: `P0-BFF-CMD-001`
Owner: Codex2
Reviewer: Codex
Status: review approved; finalized by owner
Scope: support artifact only; no canonical truth or runtime implementation changes

## 1. Purpose

This packet supports `P0-BFF-CMD-001` by separating the material already present in the BFF implementation from the contract gaps the parent owner must resolve.

Parent acceptance target:

- read contract remains GET-only
- runtime/deployment/approval/incident commands require `actor_ref`, `trace_id`, `idempotency_key`, RBAC/policy, and audit

This sidecar does not promote new contract truth. It is a handoff checklist for the parent owner and frontend/BFF implementers.

## 2. Evidence Read

Read for this sidecar:

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/p0_bff_cmd_001_sidecar_bff_handoff.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `ai-status.json`
- `services/control-plane/bff/BFF_API_CONTRACT.md`
- `services/control-plane/bff/BFF_SURFACE_INVENTORY.md`
- `docs/04/pantheon_sa/SA-13_contract_schema_gap_analysis.md`
- `docs/04/pantheon_sa/SA-15_governance_boundary_gap_analysis.md`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/models.py`
- `services/control-plane/bff/command_queue.py`
- `services/control-plane/bff/command_executor.py`
- `services/control-plane/bff/test_governance_command_submission.py`
- `docs/bff/PKT-006-approval-queue.md`
- `docs/bff/PKT-010-runtime-state-board.md`
- `docs/bff/PKT-013-operator-home.md`
- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`

## 3. Current BFF Command Material

Observed implementation material already exists for a command facade:

- `services/control-plane/bff/main.py` exposes `POST /api/v1/operator/commands` and `GET /api/v1/operator/commands/{command_id}`.
- `models.py` defines `OperatorCommand`, `AuditContext`, `CommandSubmissionResponse`, `CommandStatusResponse`, `CommandType`, and `OperatorIdentity`.
- `main.py` accepts command metadata through headers:
  - `Authorization`
  - `X-Trace-Id`
  - `X-Correlation-Id`
  - `X-Idempotency-Key`
- `main.py` builds foundation context with command envelope, trace context, idempotency record, policy decision, and audit action.
- `command_queue.py` persists command records and can replay by foundation `idempotency_key`.
- `command_executor.py` dispatches supported command types to internal runtime/governance APIs instead of directly mutating canonical state in the BFF.
- `test_governance_command_submission.py` verifies idempotency replay, policy denial, validation error envelopes, and live broker scope denial.

Supported command enum values currently include:

- deployment and approval: `ApproveDeployment`, `ApproveDecision`, `RejectDecision`, `RequestApprovalRevision`
- runtime and incident-adjacent control: `PauseRuntime`, `PauseExecution`, `IssueRiskOff`, `LiquidateAll`, `HardRollback`, `IssueSafeMode`, `ActivateKillSwitch`
- rollback: `ExecuteRollback`, `ApproveRollback`, `RejectRollback`
- evolution and mutation: `ApproveEvolutionDecision`, `ExecuteEvolutionAction`, `ApproveMutation`, `RejectMutation`, `RecordSponsorDecision`

## 4. Contract Gap Summary

The principal gap is not absence of all command code. The gap is contract layering and publication:

| Area | Current material | Parent task gap |
|---|---|---|
| Read API | `BFF_API_CONTRACT.md` is explicitly read-oriented and has GET/list/detail/composed view envelopes. | Preserve this as GET-only read truth. Do not mix command semantics into read route sections. |
| Command API | `POST /api/v1/operator/commands` exists with command queue/executor support. | Publish a separate command contract or section that does not weaken the read-only BFF read contract. |
| Actor | Runtime foundation context builds `actor_ref` from operator identity. | Contract must require actor identity and role source for every command, including OpenClaw-triggered actions. |
| Trace | Headers support trace and correlation IDs. | Contract must make trace semantics mandatory for command submission, replay, denial, and audit. |
| Idempotency | Header-backed idempotency replay exists. | Contract must define conflict behavior and required key scope for deploy, rollback, kill switch, and approval commands. |
| RBAC/policy | Tests verify denial for insufficient roles and live broker scope. | Contract must identify policy gates per command family and assert frontend CTAs consume backend-owned allowed actions. |
| Audit | Foundation audit action and command audit records exist. | Contract must require reason, actor, trace, policy decision ref, target ref, status/result, and downstream verification outcome. |
| Canonical writes | Executor dispatches to internal APIs. | Contract must say BFF is command facade only; canonical writes remain owned by target services. |

## 5. Recommended Parent Contract Shape

Recommended split for `P0-BFF-CMD-001`:

1. Keep `BFF_API_CONTRACT.md` read API sections GET-only.
2. Add a distinct command contract surface, either as a new section or separate support-to-canonical document chosen by the parent owner.
3. Define one command submission endpoint:

```text
POST /api/v1/operator/commands
GET  /api/v1/operator/commands/{command_id}
```

4. Require command submission headers:

```text
Authorization: Bearer <operator token>
X-Trace-Id: <trace id>
X-Correlation-Id: <correlation id>
X-Idempotency-Key: <stable idempotency key>
```

5. Require command body:

```json
{
  "command": "ApproveDecision",
  "target": { "type": "ApprovalDecision", "id": "appr-001" },
  "action": "approve",
  "params": {},
  "audit_context": {
    "reason": "operator rationale",
    "timestamp": "RFC3339",
    "incident_id": null
  }
}
```

6. Require command status response to expose:

```text
command_id
type
target
submitted_at
status: submitted | processing | executed | failed | timeout
result
error
audit
```

## 6. Operator Journey Handoff

Frontend should treat read and command paths differently:

- Read screens consume GET routes and backend-owned `meta.surfaces` degradation state.
- Command CTAs submit to `POST /api/v1/operator/commands`.
- Command CTA visibility and enablement should be backend-owned where a page packet provides `allowedActions`.
- The UI should never infer approval authority, rollback safety, live broker enablement, or kill-switch eligibility from local role strings alone.
- After submission, the UI should render the receipt and poll `GET /api/v1/operator/commands/{command_id}` or use a future event feed. It should not mutate canonical-looking state optimistically.
- If a read surface is degraded or unavailable, the UI should disable high-risk CTAs unless the BFF explicitly returns a command-safe degraded-path allowance.

Primary journeys that need parent-owner alignment:

| Journey | Read surface | Command family |
|---|---|---|
| Approval queue | `GET /api/v1/operator/governance/approval-queue` | `ApproveDecision`, `RejectDecision`, `RequestApprovalRevision` |
| Runtime state board | `GET /api/v1/operator/runtime-state` | `PauseRuntime`, `PauseExecution`, `IssueSafeMode`, `HardRollback` |
| Incident response | incident detail / operator home / kill-switch status | `IssueRiskOff`, `LiquidateAll`, `ActivateKillSwitch`, `ExecuteRollback` |
| Evolution review | evolution decision surfaces | `ApproveEvolutionDecision`, `ExecuteEvolutionAction`, `ApproveMutation`, `RejectMutation` |
| Governance sponsor flow | committee board surfaces | `RecordSponsorDecision` |

## 7. Frontend Acceptance Notes

Useful frontend checks for the parent owner:

- All command submissions must include `X-Trace-Id` and `X-Idempotency-Key`.
- Every command body must include `audit_context.reason`.
- CTAs must bind to BFF-supplied target refs or row IDs, not reconstructed local IDs.
- Approval, rollback, kill switch, live broker, and evolution actions must surface BFF denial envelopes rather than translating them into generic failures.
- Degraded read surfaces must not render empty healthy state.
- Submitted command receipts must not be treated as executed state.

## 8. Parent Owner Open Questions

The parent owner should decide these before canonicalizing:

- Whether the command contract belongs inside `services/control-plane/bff/BFF_API_CONTRACT.md` as a separate command chapter or in a separate command contract artifact.
- Whether `X-Trace-Id` and `X-Idempotency-Key` should be hard-required at the HTTP boundary or may be generated server-side for low-risk commands.
- Whether command status polling is enough for P0, or whether command lifecycle events must be attached to the existing SSE path.
- How to map incident commands to the non-BFF backup control path required by `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`.
- Which command families require MFA, explicit second approval, or admin-only role.

## 9. Verification

This sidecar performed read-only inspection plus artifact creation. No runtime tests were run because no runtime code was changed.

Focused commands used:

```bash
rg -n "P0-BFF-CMD-001" ai-status.json
rg --files | rg 'P0-BFF-CMD-001|BFF|Frontend|frontend|bff|command'
rg -n "@(app\\.)?(post|put|patch|delete)|POST|Command|idempotency|actor_ref|trace_id|audit|rbac|policy" services/control-plane/bff/main.py services/control-plane/bff/command_queue.py services/control-plane/bff/command_executor.py services/control-plane/bff/test_command_executor.py services/control-plane/bff/test_governance_command_submission.py
rg -n "class CommandType|class OperatorCommand|class CommandSubmissionResponse|class CommandStatusResponse|class AuditContext|class OperatorIdentity" services/control-plane/bff/models.py
```

## 10. Reviewer Handoff

Reviewer `Codex` should check:

- The packet stays support-only and does not alter canonical truth.
- The command/read split is aligned with `P0-BFF-CMD-001` acceptance.
- The frontend handoff does not authorize client-side policy derivation.
- The parent task can absorb or reject recommendations without depending on this sidecar as canonical source.

Review outcome: approved by `Codex` on 2026-05-01. The reviewer confirmed this packet remains support-only, aligns with the parent read/command split acceptance, and can be finalized by owner `Codex2`.

## 11. Owner Closeout

Owner closeout by `Codex2` kept the deliverable limited to this support handoff packet. No canonical truth, runtime implementation, registry, governance, or contract source files were changed for this sidecar finalization.

Additional closeout verification:

```bash
sed -n '1,260p' support/sidecars/P0-BFF-CMD-001/P0-BFF-CMD-001-SIDECAR-BFF-HANDOFF.md
jq '.tasks[] | select(.id=="P0-BFF-CMD-001-SIDECAR-BFF-HANDOFF")' ai-status.json
git status --short
git diff --check -- support/sidecars/P0-BFF-CMD-001/P0-BFF-CMD-001-SIDECAR-BFF-HANDOFF.md
```
