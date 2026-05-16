# DEP-002-RB Evidence

Task: `DEP-002-RB`
Owner: Codex
Reviewer: Claude2
Scope: DeploymentPlan stage planner rebaseline.

## Delivered

- Added `POST /api/deployment/stage-planner/check` as a read-only planner rule
  check that does not require registry or ApprovalDecision payloads.
- The response reports:
  - `ruleset = DEP-002-RB-stage-planner-v1`
  - derived `transition_type`
  - derived `runtime_action`
  - `rollback_required`
  - default and effective scale
  - stage validation errors
- Added focused pytest coverage for allowed paper / canary / live / frozen
  transitions, skipped promotion rejection, missing rollback linkage, and canary
  scale cap enforcement.
- Updated the deployment service README and contract with the new stage planner
  check route.

## Verification

```bash
python3 -m py_compile services/deployment/models.py services/deployment/service.py services/deployment/test_dep002_rebaseline_stage_planner.py
```

Result: passed.

```bash
python3 -m pytest -q services/deployment/test_dep002_rebaseline_stage_planner.py
```

Result: 9 passed.

```bash
python3 -m pytest -q services/deployment/test_dep002_rebaseline_stage_planner.py services/deployment/test_dep001_rebaseline_service.py services/deployment/test_service.py
```

Result: 36 passed.

## Closeout Reverification

Re-run by Codex on 2026-05-16 before finalization:

```bash
python3 -m py_compile services/deployment/models.py services/deployment/service.py services/deployment/test_dep002_rebaseline_stage_planner.py
```

Result: passed.

```bash
python3 -m pytest -q services/deployment/test_dep002_rebaseline_stage_planner.py
```

Result: 9 passed.

```bash
python3 -m pytest -q services/deployment/test_dep002_rebaseline_stage_planner.py services/deployment/test_dep001_rebaseline_service.py services/deployment/test_service.py
```

Result: 36 passed.

```bash
python3 -m pytest -q services/control-plane/governance/test_deployment_plan.py
```

Result: 26 passed, 3 subtests passed.
