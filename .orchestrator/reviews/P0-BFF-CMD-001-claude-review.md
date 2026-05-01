# P0-BFF-CMD-001 — Review: Claude

Date: 2026-05-01
Reviewer: Claude
Owner: Codex2
Status: **approved**

---

## Acceptance Criteria Verdict

| Criterion | Verdict | Evidence |
|---|---|---|
| Read contract remains GET-only | ✅ PASS | `BFF_API_CONTRACT.md` title, §1, §13, and verification checklist updated to "read API contract" with companion `BFF_COMMAND_API_CONTRACT.md` reference; §2 Principle #6 added for Read/command split |
| Runtime/deployment/approval/incident commands require actor_ref, trace_id, idempotency_key, RBAC/policy, and audit | ✅ PASS | `BFF_COMMAND_API_CONTRACT.md` §4 specifies all six required controls; `test_runtime_deployment_approval_incident_commands_record_foundation_controls` verifies all four command classes record the full foundation context |

---

## Evidence Review

### BFF_API_CONTRACT.md
- Correctly scoped from "governed BFF API contract" to "governed BFF **read** API contract"
- Companion reference to `BFF_COMMAND_API_CONTRACT.md` added in header and §13
- New Architectural Principle #6 ("Read/command split") explicitly separates read surfaces from command facade
- §13 Read-Only Guarantee updated to cite `BFF_COMMAND_API_CONTRACT.md` as the governing document for command paths
- Verification checklist updated with command split evidence row

### BFF_COMMAND_API_CONTRACT.md (new)
- Defines the two routes: `POST /api/v1/operator/commands` (admission) and `GET /api/v1/operator/commands/{command_id}` (read projection only)
- §4 Required Admission Controls table: Actor, Trace, Idempotency, RBAC/policy, Audit, Target — all six present
- §7 Command Classes table covers deployment, approval, runtime, incident/kill-switch, evolution/governance with minimum admission contract per class
- Verification command matches the evidence

### main.py
- `_require_operator_command_idempotency_key` enforced at command admission; rejects with structured foundation error when header is absent
- Live broker scope gate (`live_broker_scope` precondition) still present and correctly fail-closed

### test_governance_command_submission.py
- `test_submit_command_rejects_missing_idempotency_key_with_foundation_audit`: missing `X-Idempotency-Key` → HTTP 400 with `precondition_failed=idempotency_key`, foundation error audit action recorded
- `test_submit_command_records_foundation_context_and_replays_idempotency`: duplicate idempotency key replays original receipt; store has exactly one record; all six foundation fields verified
- `test_submit_command_policy_denial_returns_foundation_error_envelope`: operator-without-approver-role → 403 with policy_decision.decision=deny and audit action
- `test_runtime_deployment_approval_incident_commands_record_foundation_controls`: deployment/approval/runtime/incident cases all record actor_ref, trace_id, idempotency_key, policy_decision (allow), audit_action with correct cross-reference

### SA-13 §4.5 & SA-15 §6.6
- Both disposition sections correctly summarize the split: `BFF_API_CONTRACT.md` = GET-only read; `BFF_COMMAND_API_CONTRACT.md` = governed command facade
- Idempotency, actor identity, RBAC, and audit requirements stated

### Test run
```
python3 -m pytest services/control-plane/bff/test_governance_command_submission.py \
                  services/control-plane/bff/test_cw03_committee_board_contract.py -q
19 passed in 10.22s
```

---

## No Blocking Issues

The implementation cleanly closes the BFF read/command layering gap for the P0 paper-loop baseline:

- Read API boundary is formally stated and GET-only
- Command admission requires all six controls (actor, trace, idempotency, policy/RBAC, audit, target)
- All four command classes (deployment, approval, runtime, incident) are regression-covered
- Existing committee command path continues to use the shared command facade

---

## Returned to Codex2 for closeout

Owner should commit the task-scoped files (`BFF_API_CONTRACT.md`, `BFF_COMMAND_API_CONTRACT.md`, `main.py`, `test_governance_command_submission.py`, `test_cw03_committee_board_contract.py`, `SA-13`, `SA-15`) with the required commit metadata and run `scripts/ai-status.sh done`.
