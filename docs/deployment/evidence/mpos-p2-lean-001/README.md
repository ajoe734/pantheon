# MPOS-P2-LEAN-001 Evidence

Task: Harden LEAN runtime adapter contract for approved artifact only execution

Date: 2026-06-09

## Contract hardening

- `RuntimeBootstrapRequest` materialization now fails closed unless launch evidence includes:
  - `artifact_state=approved`
  - approved runtime config evidence (`runtime_config_status=approved` or `runtime_config.approved=true`)
  - `risk_policy_ref` plus an allowed `risk_policy_evaluation` for the same capital pool
- Paper runtime signal drain now requires a resolved `RuntimeBinding` before simulated execution can change positions or PnL.
- Paper runtime state, order, and drain HTTP endpoints reject mismatched `capital_pool_id` / `pool_id` query scope before exposing runtime state, credential refs, positions, or PnL-derived state.
- The paper runtime adapter still does not expose kill-switch dispatch; kill-switch execution remains in runtime-manager, whose audit/idempotency suite was re-run.

## Validation

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.execution.lean_runtime.test_bootstrap_contract services.execution.lean_runtime.test_runtime_context services.execution.lean_runtime.test_paper_runtime services.execution.lean_runtime.test_paper_runtime_smoke services.execution.lean_runtime.test_algorithm_smoke
Result: 52 tests passed.

PYTHONDONTWRITEBYTECODE=1 python3 services/control-plane/governance/test_paper_runtime_binding.py
Result: 37 PASS, 0 FAIL.

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.control-plane.bff.test_p0_paper_operating_loop_smoke
Result: 1 test passed.

PYTHONDONTWRITEBYTECODE=1 python3 services/runtime-manager/test_runtime_manager.py
Result: 54 tests passed.
```
