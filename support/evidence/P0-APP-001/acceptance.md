# P0-APP-001: acceptance evidence — POST /bff/approvals/{id}/decide

**Task**: P0-APP-001 — approval decide endpoint /bff/approvals/{id}/decide
**Owner**: Claude
**Reviewer**: Codex2
**Branch**: bff-luv-fe-006-dev-deploy

## Deliverable

Dedicated `POST /bff/approvals/{id}/decide` handler in
`services/control-plane/bff/main.py` (function `bff_approvals_decide`).

Removed from generic stub `sem_final_generic_id_command_alias`;
replaced with a proper handler that:

- Requires `approver` or `admin` role (403 otherwise)
- Maps `decision` payload field to command type:
  - `approve` → `CommandType.APPROVE_DECISION`
  - `reject` → `CommandType.REJECT_DECISION` (requires non-empty `rejection_reason`)
  - `request_revision` → `CommandType.REQUEST_APPROVAL_REVISION` (requires non-empty `revision_notes`)
  - `escalate` / `freeze` → passthrough as `APPROVE_DECISION` pending dedicated command types
  - missing → inferred from body fields, defaulting to `APPROVE_DECISION`
  - unknown → 422
- Validates `rejection_reason` for reject (422 if absent/empty)
- Validates `revision_notes` for request_revision (422 if absent/empty)
- Looks up `ApprovalDecision` by path id; returns 404 when dataset is available but id unknown
- Requires `Idempotency-Key` (400 if absent)
- Calls `_sem_command_response` → `ObjectType.APPROVAL_DECISION` target
- Returns 202 with command receipt envelope

## Verification commands

```
cd services/control-plane/bff

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest test_bff_approvals_decide_contract.py -v
# → 15 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest test_execute_plans_final_live_wiring_contract.py -v
# → 7 passed, 1 pre-existing failure (test_execute_plans_final_stub_auth_smoke_avoids_server_errors
#   /bff/capital-pools/pool_001 503 = P0-CAP-001 fail-closed, not P0-APP-001 scope)
```

## Cross-task note

`test_execute_plans_final_stub_auth_smoke_avoids_server_errors` 1 failure is the
P0-CAP-001 fail-closed 503 side-effect documented in P0-REG-001 review notes;
not introduced by P0-APP-001.
