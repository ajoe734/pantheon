# Execution Sandbox Canary Activation-Ready Packet

Task: `SVC-EXECUTION-SANDBOX-CANARY-ACTIVATION-READY`
Status: review approved; closeout verified
Reviewer: `Claude`

## Scope

This packet makes the execution sandbox/canary entry path activation-ready while
production live remains gated.

It covers:

- broker sandbox/test-key order lifecycle smoke for IBKR, Shioaji, and Kraken
- place, cancel, readback, execution disposition, and reconciliation evidence
- no-real-capital and broker-secret boundary evidence
- runtime-manager forward activation gate for canary/live deployments
- `pantheon/lean` / `ajoe734/pantheon-lean.git` bridge guard preservation

It does not claim:

- production live order submission
- live cancel or fill proof
- first canary/live market execution proof

## Evidence

Broker lifecycle packet:

- `docs/deployment/evidence/execution-sandbox-canary-activation-ready/20260504T045936Z/README.md`

Each provider packet includes:

- `order-lifecycle.json`
- `readback.json`
- `reconciliation.json`
- `no-real-capital-evidence.json`

The provider summaries record `source_task_id =
SVC-EXECUTION-SANDBOX-CANARY-ACTIVATION-READY`, no raw secret material, and no
production-live order/cancel/capital side effects.

## Runtime Gate

Runtime-manager now rejects forward `canary` or `live` `deploy()` calls unless
the request carries explicit promotion-gate evidence:

- `promotion_gate_decision_id`
- `human_gate_packet_ref`
- `broker_sandbox_smoke_ref`
- `risk_owner_approval_ref`
- `operator_approval_ref`
- `capital_scale_pct` and `gross_scale_pct` for canary policy scale
- `canary_observation_ref` for live

Rollback replacement creation may bypass this gate internally because rollback
is a safety action, not forward promotion.

## Verification

Review verification and closeout verification passed:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/lupin/code/pantheon:/home/lupin/code/pantheon/scripts python3 -m unittest scripts.test_run_broker_sandbox_order_smoke scripts.test_run_ep5_canary_readiness
PYTHONDONTWRITEBYTECODE=1 python3 services/runtime-manager/test_runtime_manager.py RuntimeManagerServiceTests RuntimeManagerClientTests
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/run_broker_sandbox_order_smoke.py scripts/run_ep5_canary_readiness.py services/execution/sandbox_order_lifecycle.py services/runtime-manager/service.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/check_execution_bridge.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/check_task_targets.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.execution.lean_runtime.test_bootstrap_contract services.execution.lean_runtime.test_runtime_bootstrap
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/lupin/code/pantheon:/home/lupin/code/pantheon/lean/Algorithm.Python python3 lean/Algorithm.Python/pantheon_algo/test_base.py
docker compose -f docker-compose.exec.yml config
jq -r 'input_filename + " real_capital_used=" + (.real_capital_used|tostring) + " live_order=" + (.production_live_order_submitted|tostring) + " raw_secret=" + (.broker_secret_boundary.raw_secret_material_present|tostring)' docs/deployment/evidence/execution-sandbox-canary-activation-ready/20260504T045936Z/*/no-real-capital-evidence.json
git diff --check -- docker-compose.exec.yml env/canary-exec.env.example scripts/run_broker_sandbox_order_smoke.py scripts/run_ep5_canary_readiness.py scripts/test_run_broker_sandbox_order_smoke.py scripts/test_run_ep5_canary_readiness.py services/execution/runtime-manager/contract.md services/execution/sandbox_order_lifecycle.py services/runtime-manager/service.py services/runtime-manager/smoke_test.py services/runtime-manager/test_runtime_manager.py docs/deployment/ep5-canary-ready/README.md docs/deployment/ep5-canary-ready/operator-approval-checklist.md docs/deployment/execution-sandbox-canary-activation-ready.md
```

Observed environment gap:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.runtime-manager.test_runtime_manager
```

The full runtime-manager HTTP route suite could not complete in this local
environment because `flask` is not installed. The service-layer tests covering
the new gate passed.

## Review Notes

Live remains fail-closed by default:

- the broker sandbox smoke rejects production live modes
- runtime-manager live deploy requires all common gate refs plus
  `canary_observation_ref`
- `docker-compose.exec.yml` carries empty live gate refs by default
- the existing bootstrap contract still rejects `lean-platform` targets and
  requires `pantheon-lean` bridge identity
