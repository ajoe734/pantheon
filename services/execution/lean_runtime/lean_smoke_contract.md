# LEAN-ALGO-001 Algorithm Smoke Contract

Status: implementation artifact for LEAN-ALGO-001

## Scope

This smoke covers the CPU-only algorithm-level path that was deferred after
EX-003:

1. Build a governed `RuntimeBootstrapRequest` from DeploymentPlan-like and
   RuntimeBinding-like records.
2. Materialize an approved paper execution artifact into LEAN Object Store
   keys through `services.execution.artifact_loader`.
3. Start the minimal LEAN Python algorithm at
   `lean/Algorithm.Python/pantheon_algo/smoke_loader_test.py`.
4. Load the approved artifact with `ArtifactLoader.from_runtime(self)`.
5. Drive five deterministic daily OHLCV bars through `OnData`.
6. Execute the loaded artifact signal once and record one simulated fill.

The smoke deliberately does not start a live broker, read broker credentials,
or call network services.

## Canonical Fields

The Object Store metadata must include:

- `artifact_state: approved`
- `deployment_stage: paper`
- `promotion_state: paper` as a legacy read alias
- `runtime_binding_id` and `deployment_plan_id` for audit linkage

The runtime bootstrap env must include:

- `PANTHEON_RUNTIME_BINDING_ID`
- `PANTHEON_DEPLOYMENT_PLAN_ID`
- `PANTHEON_DEPLOYMENT_STAGE=paper`
- `PANTHEON_LIVE_BROKER_ENABLED=false`

## Deterministic Inputs

The local harness in `smoke_algorithm.py` uses five trading days:

- 2026-01-05
- 2026-01-06
- 2026-01-07
- 2026-01-08
- 2026-01-09

The artifact payload carries one signal:

- `signal_id: sig-lean-smoke-001`
- `symbol: AAPL.US`
- `action: BUY`
- `direction: LONG`
- `quantity: 7`
- `quantity_type: SHARES`

## Acceptance Checks

`services/execution/lean_runtime/test_algorithm_smoke.py` asserts:

- the synthetic OHLCV set contains exactly five trading days
- the algorithm receives five raw `OnData` calls but executes the artifact
  signal during one eligible callback
- exactly one simulated fill is recorded
- the fill references the loaded artifact signal
- the loaded metadata is `artifact_state=approved` and `deployment_stage=paper`
- bootstrap context reaches the algorithm as `runtime_binding_id` and
  `deployment_plan_id`
- `BROKER_PRODUCTION_LIVE_ENABLED` is scoped to `false` and restored after the
  smoke

## Verification

Focused command:

```bash
python3 -m pytest -q services/execution/lean_runtime/test_algorithm_smoke.py
```

Wider execution-plane regression command:

```bash
python3 -m pytest -q services/execution/lean_runtime/test_algorithm_smoke.py services/execution/test_artifact_loader.py services/execution/lean_runtime/test_bootstrap_contract.py services/execution/lean_runtime/test_runtime_context.py
```
