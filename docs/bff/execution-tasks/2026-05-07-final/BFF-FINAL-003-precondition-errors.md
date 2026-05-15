# BFF-FINAL-003 - Precondition Errors

Priority: P0

Depends on: BFF-FINAL-001, BFF-FINAL-002

Area: high-risk action admission

## Goal

Return final non-2xx precondition envelopes for missing confirm token, approval, and two-man requirements. These are not successful command statuses.

## Contract Inputs

| Situation | HTTP | ErrorCode |
|---|---:|---|
| Missing confirm token | 428 | `CONFIRM_TOKEN_REQUIRED` |
| Missing approval | 409 | `APPROVAL_REQUIRED` |
| Missing two-man | 409 | `TWO_MAN_REQUIRED` |

## Implementation Scope

Likely files:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/models.py`
- `services/control-plane/bff/command_executor.py`
- `services/control-plane/governance/approval_decision.py`
- `services/control-plane/bff/test_governance_command_submission.py`
- new focused precondition tests

## Steps

1. Add a reusable BFF precondition error builder.
2. Add confirm-token admission checks for high-risk action endpoints.
3. Add approval admission checks for approval-gated actions.
4. Add two-man admission checks for two-person-signoff actions.
5. Ensure missing preconditions do not enqueue or dispatch downstream commands.
6. Include `correlationId` and details:
   - `actionId`
   - `entityType`
   - `entityId`
   - `kind`
   - `reason`
7. Emit audit evidence for rejected precondition attempts where existing policy requires audit.

## Acceptance Criteria

- Missing confirm token returns HTTP 428 and `CONFIRM_TOKEN_REQUIRED`.
- Missing approval returns HTTP 409 and `APPROVAL_REQUIRED`.
- Missing two-man returns HTTP 409 and `TWO_MAN_REQUIRED`.
- No precondition miss returns `status: requires_*`.
- No downstream command is submitted when preconditions fail.

## Verification

```bash
python -m pytest services/control-plane/bff -k "precondition or approval or command" -q
```

## Implementation Notes

- `/bff/v1/commands` now rejects missing final preconditions before command persistence or background dispatch.
- Missing confirm token returns HTTP 428 / `CONFIRM_TOKEN_REQUIRED`.
- Missing approval evidence returns HTTP 409 / `APPROVAL_REQUIRED`.
- Missing two-man evidence returns HTTP 409 / `TWO_MAN_REQUIRED`.
- Rejection envelopes include `correlationId` plus `actionId`, `entityType`, `entityId`, `kind`, and `reason` details, with foundation error and audit action evidence.

## Verification Run

```bash
python3 -m pytest services/control-plane/bff/test_final_precondition_errors.py -q
python3 -m pytest services/control-plane/bff/test_governance_command_submission.py -k "bff_v1_commands" -q
python3 -m pytest services/control-plane/bff/test_final_contract_primitives.py -q
```

Note: the broader requested pattern was started with `python3 -m pytest services/control-plane/bff -k "precondition or approval or command" -q`; it exposed two unrelated BFF-FINAL-007 evidence-redaction failures where `ReadSurfaceStore.redact_evidence_refs()` is missing, so the run was narrowed to BFF-FINAL-003/final-command coverage.

## Closeout Verification

Owner closeout rerun on 2026-05-07:

```bash
python3 -m pytest services/control-plane/bff/test_final_precondition_errors.py -q
python3 -m pytest services/control-plane/bff/test_governance_command_submission.py -k "bff_v1_commands" -q
python3 -m pytest services/control-plane/bff/test_final_contract_primitives.py -q
```

Results: 3 passed; 8 passed, 13 deselected; 5 passed.
