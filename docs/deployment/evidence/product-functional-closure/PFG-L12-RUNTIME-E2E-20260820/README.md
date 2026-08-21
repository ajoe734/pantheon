# PFG-L12-RUNTIME-E2E-20260820: Loops 8 through 12 deployed runtime E2E evidence

Task: `PFG-L12-RUNTIME-E2E-20260820`
Program: `pantheon-product-functional-closure-20260820`
Phase: `W4-deployed-journey`

This directory records the task-scoped review evidence for proving deployed Loops 8 through 12 across normal approval, deployment, paper execution, telemetry, reconciliation, evolution, and BFF health readback.

## Scope and Delivered Chain

The deployed integration suite `tests/integration/l12/test_current_runtime_loops_deployed_e2e.py` proves the complete product chain:

```text
Approved Registry Artifact (Loop 8)
  -> Governance Approval Decision
  -> DeploymentPlan & Outbox Consumer Saga
  -> Active RuntimeBinding in Runtime Manager
  -> Paper Fleet Reconciler Admission (Loop 9)
  -> Paper Signal Producer & CurrentArtifactStrategy
  -> Paper Runtime Simulated Broker Fill
  -> Telemetry Ingest & Runtime Summary
  -> Reconciliation-Drift Service DriftReport (Loop 10)
  -> IncidentCase Lineage Creation
  -> Evolution Service Threshold Evaluation & Daily Sweep (Loop 11)
  -> Proposal-Only EvolutionDecision (No Unsolicited Runtime Mutation)
  -> Operator BFF Typed Health Reporting (Loop 12)
```

## Fail-Closed & Boundary Cases

1. **Migration / Retirement Prerequisite (`migration_invalid_bindings_prerequisite`)**:
   - Pre-existing invalid/non-executable bindings lacking required execution fields or artifact checksums are canonically retired via `POST /api/runtime-bindings/{id}/retire` prior to fleet admission.
   - Verified that `paper-fleet-reconciler` desired state excludes all retired/invalid bindings.

2. **Negative Artifact Checksum (`negative_missing_artifact_checksum`)**:
   - An active RuntimeBinding lacking artifact checksum projection is rejected/degraded by `PaperSignalProducer`.
   - Zero signals are enqueued to the binding-scoped queue (`pantheon:signals:pending:<binding_id>`).

3. **Negative Typed Worker Failure (`negative_typed_worker_failure`)**:
   - Stopping `paper-fleet-reconciler` causes operator BFF `/bff/v5/downstream-health` to report `ok: false` for the worker target while `runtime-manager` API remains `ok: true`.
   - Restarting the container restores typed worker health without masking API readiness.

4. **Bounded Outbox Cursor & Resource Limits (`bounded_lifecycle_cursor_and_resources`)**:
   - Verified that `paper_runtime.py` lifecycle telemetry outbox uses durable cursor compaction (`ack_cursor`) avoiding full-history rescans.
   - Verified that non-executable bindings are rejected by `paper_fleet_reconciler.py:validate_executable_binding` without spawning fleet children.
   - Verified strict 1:1 running worker and port bounds matching active valid bindings.

## Code Disposition Audit

| Component | Canonical Owner / Path | Status & Purpose |
| --- | --- | --- |
| Deployment & Saga | `services/deployment/`, `services/execution/runtime-manager/` | Retained as canonical deployment outbox & plan authority. |
| Paper Fleet Reconciler | `services/execution/runtime-manager/paper_fleet_reconciler.py` | Retained as sole default paper runtime reconciler; strictly validates executable bindings. |
| Paper Runtime & Producer | `services/execution/lean_runtime/` | Retained; produces signal from canonical Source snapshot via `market_data_policy` and emits simulated fill. |
| Source Ingest | `services/source_ingestion/` | Retained; provides canonical stored normalized market snapshot for runtime strategies. |
| Static Paper Runtime | `docker-compose.yml` profile `static-paper-runtime` | Explicit compatibility/test profile only; never default in dev. |
| Smoke / Bounded Strategy | `paper_signal_producer.py:SmokeStrategy` | Explicit smoke profile only (`PAPER_SIGNAL_STRATEGY=smoke`); CurrentArtifactStrategy is default. |
| Telemetry & Reconciliation | `services/telemetry/`, `services/reconciliation-drift/`, `services/incidents/` | Retained as canonical telemetry and drift/incident owners. |
| Evolution | `services/evolution/` | Retained; produces proposal-only evolution decisions with strict incident lineage. |
| BFF Typed Health | `services/control-plane/bff/` | Retained as typed multi-target health projection. |

## Verification & Isolated-Compose Harness

Run from the repository root:

```bash
python3 scripts/dev/provision_python_distribution.py
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
"$PANTHEON_PY" -m py_compile \
  tests/integration/l12/test_current_runtime_loops_deployed_e2e.py \
  scripts/run_isolated_l12_runtime_e2e.py
"$PANTHEON_PY" -m pytest -q \
  tests/integration/l12/test_current_runtime_loops_deployed_e2e.py \
  services/execution/runtime-manager/test_paper_fleet_reconciler.py \
  services/execution/lean_runtime/test_paper_signal_producer.py \
  services/execution/lean_runtime/test_paper_runtime.py \
  services/deployment/test_l12_mfc_r4_deploy_001_contract.py
```

To run the reproducible isolated-Compose harness:

```bash
python3 scripts/run_isolated_l12_runtime_e2e.py \
  --compose-project l12currentruntimee2e \
  --sync-evidence
```

## Deployed Execution & Evidence Output

The complete 9-case deployed suite was executed in an isolated Compose environment running all 18 production services. The atomic result artifact:
- `docs/deployment/evidence/product-functional-closure/PFG-L12-RUNTIME-E2E-20260820/run-report.json`
- `docs/deployment/evidence/product-functional-closure/PFG-L12-RUNTIME-E2E-20260820/run-report.sha256`

contains all case readbacks, exact service container identities, authority proofs, and exact commit SHA. Reviewer metadata is assigned to `Codex2`.

