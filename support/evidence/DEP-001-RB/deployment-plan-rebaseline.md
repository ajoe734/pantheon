# DEP-001-RB DeploymentPlan Rebaseline Evidence

Task: `DEP-001-RB` - DeploymentPlan contract + service (rebaseline)
Owner: `Codex`
Reviewer: `Claude`
Date: 2026-05-16

## Scope

This rebaseline verifies the existing `DeploymentPlan` contract and deployable
service against the 2026-05-15 governance -> deployment -> runtime acceptance.
No canonical L1 truth was changed. The task adds focused service coverage and
records the evidence for review.

## Contract And Service Surface

Authoritative contract and implementation remain:

- `services/control-plane/governance/deployment_plan.contract.md`
- `services/control-plane/governance/deployment_plan.schema.json`
- `services/control-plane/governance/deployment_plan.py`
- `services/deployment/contract.md`
- `services/deployment/service.py`

The deployable service owns:

- `POST /api/deployment/plans`
- `POST /api/deployment/plans/validate`
- `GET /api/deployment/plans`
- `GET /api/deployment/plans/{plan_id}`
- `POST /api/deployment/plans/{plan_id}/status`

## Rebaseline Acceptance

The new test file `services/deployment/test_dep001_rebaseline_service.py`
pins these acceptance points:

- `artifact_state=approved` can create `DeploymentPlan` records through the
  deployable service.
- Supported target stages are `paper`, `canary`, `live`, and `frozen`.
- Service-created plans preserve first-class `approval_decision_id`,
  `capital_pool_id`, `current_stage`, `target_stage`, `transition_type`,
  `runtime_action`, status, and policy scale fields.
- `paper`, `canary`, and `live` active targets require rollback linkage.
- Non-approved artifact states are rejected before plan creation.
- An ApprovalDecision must be `decision_state=decided` before it can govern a
  plan.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/deployment/test_dep001_rebaseline_service.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/deployment/test_dep001_rebaseline_service.py services/deployment/test_service.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s services/control-plane/governance -p 'test_deployment_plan.py'
```

Results:

```text
6 passed in 8.63s
24 passed in 26.86s
Ran 26 tests in 0.041s - OK
```

Owner closeout rerun on 2026-05-16:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/deployment/test_dep001_rebaseline_service.py -q
6 passed in 19.64s

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/deployment/test_dep001_rebaseline_service.py services/deployment/test_service.py -q
27 passed in 52.53s

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s services/control-plane/governance -p 'test_deployment_plan.py'
Ran 26 tests in 0.036s - OK
```

## Review Notes

The working tree already contained unrelated DEP-003 projection changes in
`services/deployment/README.md`, `contract.md`, `models.py`, `service.py`, and
`test_service.py` before DEP-001-RB edits. This task-owned change is isolated to
the new DEP-001-RB test file and this evidence packet.
