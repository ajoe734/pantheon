# P0-APP-001 Review — POST /bff/approvals/{id}/decide

**Reviewer**: Claude2
**Owner**: Claude
**Commit reviewed**: e8d18e92
**Branch**: bff-luv-fe-006-dev-deploy
**Review date**: 2026-05-16

## Outcome: APPROVED

## Verification

```
cd services/control-plane/bff
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest test_bff_approvals_decide_contract.py -v
# → 15 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest test_execute_plans_final_live_wiring_contract.py -v
# → 7 passed, 1 pre-existing failure (P0-CAP-001 fail-closed 503 on /bff/capital-pools/pool_001)
```

## Checklist

- [x] **Role gate**: `approver` or `admin` required; `operator` returns 403; anonymous returns 401/403
- [x] **Decision routing**: `approve` → `APPROVE_DECISION`, `reject` → `REJECT_DECISION`, `request_revision` → `REQUEST_APPROVAL_REVISION`; `escalate`/`freeze` pass through as `APPROVE_DECISION` pending dedicated types; unknown value → 422
- [x] **rejection_reason validation**: missing or empty → 422 for `reject` decision
- [x] **revision_notes validation**: missing or empty → 422 for `request_revision` decision
- [x] **Empty body inference**: absent `decision` field infers command from body fields, defaults to `APPROVE_DECISION`
- [x] **404 on unknown id**: `read_store.get_approval_decision(id) is None` when dataset source is not "missing" → 404 with `OBJECT_NOT_FOUND`
- [x] **Idempotency-Key enforcement**: `_resolve_final_idempotency_key` inside `_sem_command_response` raises 400 if both `Idempotency-Key` and `X-Idempotency-Key` are absent
- [x] **Idempotency replay**: same key + same payload → same `command_id` in response; confirmed by idempotency replay test
- [x] **202 envelope**: standard command receipt envelope with `data.command_id` confirmed
- [x] **CommandType target**: `ObjectType.APPROVAL_DECISION` passed correctly
- [x] **Removed from generic stub**: `/bff/approvals/{id}/decide` removed from `sem_final_generic_id_command_alias` decorator stack

## Cross-task notes

- The 1 live wiring failure (`test_execute_plans_final_stub_auth_smoke_avoids_server_errors`) is the P0-CAP-001 fail-closed 503 on `/bff/capital-pools/pool_001`, documented in P0-REG-001 review notes. Not introduced by P0-APP-001.
- Commit e8d18e92 also contains P0-AUD-001 `main.py` hunk (`/bff/audit` dedicated handler) and incidental fixes. These were in the concurrent dirty worktree and are already covered by their respective task reviews (P0-AUD-001 approved separately).
