# PFG-BE-CONSOLIDATE-20260820: Backend Consolidation Evidence

Task: `PFG-BE-CONSOLIDATE-20260820`
Program: `pantheon-product-functional-closure-20260820`
Owner: `Antigravity2`
Reviewer: `Codex`

## Summary

This task performs the backend disposition closeout across Source Ingestion, Paper Execution, Agora / Policy Learning, and Loop Health Truth:

1. **Source Ingestion**:
   - Retired and deleted `services/source_ingestion/scheduler_worker.py` and its dedicated test `services/source_ingestion/tests/test_scheduler_worker.py`.
   - Verified that `services/source_ingestion/controller_worker.py` (the canonical desired-state/schedule reconciler) incorporates `run_schedule_tick` directly.
   - Retained `scripts/source_ingest_scheduler_once.py` as the canonical bounded one-shot CLI entrypoint.
   - Retained `docker-compose.yml::source-ingest-scheduler` service running `controller_worker.py`.

2. **Agora / Policy Learning**:
   - Audited `services/policy-learning/agora_dataset_authority.py`: confirmed exact-ref dataset version registration and lookup are retained for durable handoff admission and candidate resolution.
   - Verified that automatic scheduled direct DB discovery is retired in favor of durable Agora handoff intake cycle (`agora_handoff_drainer.py`).
   - Retained `services/policy-learning/candidate_experiment_handoff.py` as the active HTTP-only candidate handoff client.

3. **Paper Execution / Lean Runtime**:
   - Retained `services/execution/runtime-manager/paper_fleet_reconciler.py` as the dynamic fleet reconciler.
   - Retained `services/execution/lean_runtime/` active modules and `BoundedPaperStrategy` smoke fixture.
   - Deferred static paper runtime profile cleanup in `docker-compose.exec.yml` until split topology is unified.

4. **Loop Health Truth**:
   - Retained `docs/deployment/loop-catalog.registry.json` for stable loop specification, ownership, and controller contracts.
   - Retired static maturity/truth claims and execution task references from `docs/deployment/loop-catalog.registry.json` and `loop-catalog.schema.json`.
   - Verified that `services/control-plane/bff/loop_inventory.py` and `/bff/v5/loop-health` derive live runtime maturity solely from current qualified controller records.
   - Migrated `services/control-plane/bff/test_current_twelve_owner_truth.py` to the stable owner/controller contract and runtime projection.

## Validation Results

- `pytest -q services/source_ingestion/`: 845 passed, 2 skipped
- `pytest -q services/policy-learning/tests/`: 129 passed, 5 skipped
- Focused consolidation suite: 104 passed across:
  - `scripts/tests/test_source_ingest_scheduler_once.py`
  - `services/source_ingestion/tests/test_controller_worker_manual_once.py`
  - `services/policy-learning/tests/test_current_agora_handoff_cutover.py`
  - `services/policy-learning/tests/test_agora_handoff_drainer.py`
  - `services/policy-learning/tests/test_current_imitation_entrypoint.py`
  - `tests/test_loop_catalog_registry.py`
  - `services/control-plane/bff/test_loop_inventory_read_model_contract.py`
  - `services/control-plane/bff/test_loop_health_read_model_contract.py`
  - `services/control-plane/bff/test_current_twelve_owner_truth.py`
  - `scripts/test_paper_runtime_topology_contract.py`
  - `tests/integration/test_product_functional_compose_contract.py`
