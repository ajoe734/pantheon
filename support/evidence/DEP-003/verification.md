# DEP-003 Verification

Task: deployment projection read model
Owner: Codex
Reviewer: Codex2

## Delivered Scope

- Added DEP-003 deployment projection read model response DTOs in `services/deployment/models.py`.
- Added derived-only projection composition in `services/deployment/service.py`.
- Added read routes:
  - `GET /api/deployment/projections`
  - `GET /api/deployment/projections/{plan_id}`
  - `GET /api/deployment/plans/{plan_id}/projection`
- Projection joins available `DeploymentPlan`, `ApprovalDecision`, registry execution projection, `RuntimeBinding`, and `DeploymentSaga` state without writing any domain records.
- Updated `services/deployment/contract.md` and `services/deployment/README.md`.

## Verification

```bash
python3 -m py_compile services/deployment/models.py services/deployment/service.py services/deployment/test_service.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/deployment/test_service.py -q
```

Result:

```text
18 passed in 17.00s
```
