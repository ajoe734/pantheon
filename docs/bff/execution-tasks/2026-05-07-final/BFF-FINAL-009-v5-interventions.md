# BFF-FINAL-009 - v5 Interventions Contract

Priority: P0

Area: HIQ Sentinel remediation, two-man command semantics

## Goal

Implement the `/bff/v5/interventions` read surface and the `RemediateSentinelIntervention`
command with full two-man precondition enforcement.

## Contract

- `GET /bff/v5/interventions` — list HIQ Sentinel intervention records (SSE approval-channel resync surface)
- `GET /bff/approvals` — list pending approval-queue items (companion SSE resync surface)
- `POST /bff/v5/interventions/{intervention_id}/remediate` — two-man-gated remediation command
- `POST /bff/v1/commands` with `command: RemediateSentinelIntervention` — alternate command path

Two-man enforcement: `twoManSignatureId`, `two_man_signature_id`, `secondOperatorId`, or
`second_operator_id` must be present in the payload or params.  Missing signature returns
HTTP 409 `TWO_MAN_REQUIRED`.  Approval evidence and confirm token are also required (CRITICAL
risk level).

## Acceptance Criteria

- `canonical v5 route present`: `GET /bff/v5/interventions` returns 200 with `InterventionListResponse`.
- `remediation guarded`: `POST /bff/v5/interventions/{id}/remediate` returns 409 when two-man, approval, or confirm token precondition is missing.
- `two-man semantics tested`: `test_v5_interventions.py` passes all 14 tests including both dedicated route and `/bff/v1/commands` path.

## Implementation

Files changed:

- `services/control-plane/bff/models.py`
  - Added `CommandType.REMEDIATE_SENTINEL_INTERVENTION`
  - Added `ObjectType.SENTINEL_INTERVENTION`
  - Added `InterventionStatus`, `InterventionKind`, `InterventionRecord`, `InterventionListResponse`

- `services/control-plane/bff/action_catalog.py`
  - Added `RemediateSentinelIntervention` catalog entry:
    `risk_level=critical`, `requires_two_man=True`, `requires_approval=True`, `requires_confirm_token=True`

- `services/control-plane/bff/command_executor.py`
  - Added `_execute_remediate_sentinel_intervention()` dispatcher
  - Registered in `_EXECUTORS` dispatch table

- `services/control-plane/bff/main.py`
  - Imported new Intervention models
  - Added `GET /bff/approvals` SSE resync endpoint
  - Added `GET /bff/v5/interventions` read endpoint with status/kind query filters
  - Added `POST /bff/v5/interventions/{intervention_id}/remediate` two-man gated command endpoint
  - `_V5_INTERVENTIONS_STORE` in-memory stub store for dev/paper environments

- `services/control-plane/bff/test_v5_interventions.py`
  - 14 new tests covering: route presence, seeded records, status filtering, auth enforcement,
    two-man gate, approval gate, confirm token gate, full happy-path 202, v1/commands path gate,
    SSE resync route metadata, action catalog entry, and model validation.

## Verification

```bash
python3 -m pytest services/control-plane/bff/test_v5_interventions.py services/control-plane/bff/test_command_executor.py services/control-plane/bff/test_governance_command_submission.py services/control-plane/bff/test_final_precondition_errors.py services/control-plane/bff/test_action_catalog.py -q
python3 -m pytest services/control-plane/bff -q
```

Results (post-R2, commit 11dd738f):
- 82 focused tests passed (v5 interventions, command executor, governance submission, precondition errors, action catalog)
- 457 total BFF tests passed (36 existing deprecation warnings from read_store.py)

R1 fixes (commit 32574279): approver role gate + fail-closed executor (no stub fallback)
R2 fixes (commit 11dd738f): top-level two-man alias normalization into stored params for RemediateSentinelIntervention

Reviewer: Codex — review_approved 2026-05-08T02:54:01Z
