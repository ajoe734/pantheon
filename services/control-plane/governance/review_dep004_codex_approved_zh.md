# DEP-004 Codex Review

Status: approved
Reviewer: Codex
Task: DEP-004
Reviewed at: 2026-05-18

## Scope

- `services/control-plane/governance/pool_runtime_compat.py`
- `services/control-plane/governance/test_pool_runtime_compat.py`
- `services/control-plane/governance/pool_runtime_compat_contract.md`
- `services/control-plane/cron/service.py`
- `services/control-plane/cron/test_cron.py`

The requested task brief path `.orchestrator/task-briefs/dep_004.md` was not
present in this worktree. Reviewer scope used the active `ai-status` DEP-004
acceptance criteria and the task artifacts above.

## Verification

- `pytest -q services/control-plane/governance/test_pool_runtime_compat.py services/control-plane/cron/test_cron.py -q`
  - Result: `21 passed`

## Findings

No blocking findings.

The implementation satisfies the DEP-004 acceptance surface:

- `check_compatibility(capital_pool_id, deployment_plan_id, ...)` returns a
  serializable `CompatibilityResult` dictionary with `passed` and
  `rejection_reasons`.
- The guard rejects inactive pools, insufficient pool risk budget,
  jurisdiction mismatch, runtime mode / deployment stage mismatch, and missing
  or inactive persona-capital binding.
- `CronOrchestrator._run_deploy()` calls `enforce_compatibility()` before
  execution projection and saga bootstrap when deploy payloads provide
  pool/runtime compatibility context.
- The cron hook raises `DeploymentPlanError` on failed compatibility and records
  the passed compatibility result in the deployment request.
- The module is read-only and does not invoke live broker or capital side
  effects.

## Verdict

DEP-004 is approved for owner closeout.
