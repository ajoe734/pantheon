# Evidence: PRODUCT-V2-RESEARCH-ALPHA-R3-20260813

## Summary
Delivered durable strategy-to-alpha replication execution. Verified that promoted `StrategySpec` entries consumed from repository service boundaries are enqueued into `AlphaReplicationQueue`, processed through `AlphaRevalidationWorker` and `ReplicationGate`, and produce durable terminal `ExperimentRun` records registered with `ExperimentAuthority`.

## Key Verification Results
1. **Service Boundary Input Consumption**: `ReplicationController` queries the registry service boundary (`/api/registry/strategies/{strategy_id}/strategy-specs?artifact_state=approved`), normalizes entries, and enqueues them into `AlphaReplicationQueue`.
2. **Durable Revalidation to Terminal Result**: `AlphaRevalidationWorker` evaluates the candidate spec through `ReplicationGate` and registers schema-backed `ExperimentTask` and `ExperimentRun` records with `ExperimentAuthority` (`ensure_task` & `ensure_run`).
3. **Downstream Readback via Stable Identifier**: `AlphaReplicationQueue.get_entry(tenant_id, strategy_spec_id)` exposes the terminal state and authority/experiment identifiers (`authority_task_id`, `authority_run_ids`, `experiment_task_id`, `experiment_run_ids`) to downstream product surfaces.
4. **Idempotency & Lease Recovery**: Verified that duplicate submissions return `None` without creating duplicate queue entries, and expired leases are safely reclaimed while fencing tokens (`claim_token`) prevent stale worker overwrites.

## Verification Commands & Output
```bash
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
"$PANTHEON_PY" -m pytest -v services/research/alpha_replication/test_product_v2_research_alpha_r3.py
```

Result: `2 passed in 3.86s`
