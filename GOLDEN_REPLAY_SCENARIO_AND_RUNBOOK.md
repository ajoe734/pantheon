# Golden Replay Scenario and Acceptance Runbook v1

> **Owner**: Claude (BG-005)
> **Reviewer**: Qwen
> **Task**: BG-005 — Define golden replay scenario and acceptance runbook
> **Phase**: Blueprint Gap P0 / Wave 1
> **Source**: `docs/02-architecture/consensus/phase2/consensus-packet.md`, `docs/02-architecture/consensus/phase2/execution-materialization.md`
> **Closes**: GAP-05 (cross-plane replay evidence)
> **Depends on**: BG-000, BG-001, BG-003 (all done)

---

## 1. Purpose

This document defines the canonical **golden replay scenarios** and **acceptance runbook** for Pantheon v1 production sign-off.

A golden replay is a deterministic, end-to-end re-execution of a pinned historical decision cycle using frozen dataset versions, fixed decision-chain provenance objects, and fixed governance/runtime refs. The replay must reproduce the original decision outputs within tolerance and verify that all events are persisted correctly in the durable storage layer (Postgres/Redis).

This document closes GAP-05 (cross-plane replay evidence) by providing:

1. Two replay scenarios pinning real refs from BG-000 (market scope), BG-001 (data plane objects), and BG-003 (decision-front objects)
2. Fixed `DeploymentPlan` and `RuntimeBinding` refs for each scenario
3. Expected telemetry, incident, and evolution outputs
4. A step-by-step acceptance runbook
5. Durable storage verification requirements

> **EX-001 deferral note**: The LEAN execution segment of each replay uses synthetic/mocked execution feedback for this wave. The replay validates the Data → Research → Decision → Governance chain in full; the execution feedback loop (LEAN fill reports → telemetry → evolution) is replaced with a mocked adapter pending EX-001 completion.

---

## 2. Replay Scenario Inventory

| ID | Name | Market | Path type | Equities / Derivatives |
|---|---|---|---|---|
| `replay-golden-001` | US Equities Momentum — paper stage | US | Equities | Equities |
| `replay-golden-002` | TW TAIFEX Derivatives — paper stage | TW | Derivatives-aware | Derivatives |

Both scenarios must pass before production sign-off proceeds.

---

## 3. Scenario 1 — US Equities Momentum (Paper Stage)

### 3.1 Pinned Data Plane Refs (BG-001)

| Object | ID | Location |
|---|---|---|
| `DatasetVersion` | `dv-20260413-us-equity-universe-v1` | `services/data-plane/schemas/dataset_version.schema.json` |
| `SecurityMaster` | `secmaster-us-large-cap-20260413` | `services/data-plane/schemas/security_master.schema.json` |
| `ContractMaster` | `NULL` (spot-only; no futures/options) | — |
| `MarketCalendarSession` | `cal-nyse-2026-04-13-regular` | `services/data-plane/schemas/market_calendar_session.schema.json` |
| `RawDataset` | `raw-us-equity-ohlcv-20260413` | `services/data-plane/schemas/raw_dataset.schema.json` |
| `NormalizedDataset` | `norm-us-equity-daily-v1` | `services/data-plane/schemas/normalized_dataset.schema.json` |
| `FeatureDataset` | `feat-us-equity-momentum-v1` | `services/data-plane/schemas/feature_dataset.schema.json` |

DatasetVersion manifest ref:
```
dataset_version_id:       dv-20260413-us-equity-universe-v1
market_scope:             US
instrument_scope:         {asset_types: ["equity", "etf"], venues: ["NYSE", "NASDAQ"]}
universe_filter:          {min_market_cap: 1e9, min_avg_volume: 1e5}
raw_dataset_refs:         [raw-us-equity-ohlcv-20260413, raw-us-corp-actions-20260413]
normalized_dataset_refs:  [norm-us-equity-daily-v1, norm-us-equity-intraday-v1]
feature_dataset_refs:     [feat-us-equity-momentum-v1, feat-us-equity-volatility-v1]
symbol_master_ref:        secmaster-us-large-cap-20260413
contract_master_ref:      NULL
calendar_ref:             cal-nyse-2026-04-13-regular
state:                    frozen
```

Available-time gate (per `DATASET_VERSION_AND_REPLAY_POLICY.md §3`):
- Replay point T = `2026-04-13T09:30:00Z`
- All data points must satisfy `available_time <= T`
- EOD bars from 2026-04-12 available after `2026-04-12T21:00:00Z`

### 3.2 Pinned Decision-Chain Refs (BG-003)

Full five-stage chain as defined in `services/registry-core/decision-domain/examples/five_stage_chain.json`:

| Stage | Object | ID |
|---|---|---|
| 1 | `RegimeState` | `regime-20260413-001` |
| 2 | `UniverseSelection` | `universe-20260413-001` |
| 3 | `SignalInference` | `signal-20260413-001` |
| 4 | `AllocationDecision` | `alloc-20260413-001` |
| 5 | `RiskAdjudication` | `risk-20260413-001` |

Chain summary:
- Regime: `trending_up` (HMM confidence 0.78)
- Universe: 4 US large-cap equities (AAPL, NVDA, MSFT, AMZN)
- Signals: 4 long signals; top conviction NVDA (0.91), AAPL (0.82)
- Allocation: 27% gross exposure; largest position 9% (NVDA)
- Risk verdict: `approved` (all hard checks pass; correlation at `warn` level)

### 3.3 Pinned Governance and Runtime Refs (Back Half)

| Object | ID | Schema location |
|---|---|---|
| `ApprovalDecision` | `approval-20260413-001` | `services/control-plane/governance/approval_decision.schema.json` |
| `DeploymentPlan` | `deploy-20260413-001` | `services/control-plane/governance/deployment_plan.schema.json` |
| `RuntimeBinding` | `binding-20260413-001` | `services/runtime_manager/runtime_binding.schema.json` |

DeploymentPlan ref:
```
plan_id:               deploy-20260413-001
approval_decision_id:  approval-20260413-001
artifact_id:           alpha-model-ensemble-v3.1.0
artifact_version:      3.1.0
strategy_id:           strat-equities-momentum-us-001
capital_pool_id:       pool-us-equity-growth
current_stage:         none
target_stage:          paper
transition_type:       initial_deploy
```

RuntimeBinding ref:
```
binding_id:                    binding-20260413-001
runtime_id:                    lean-worker-paper-001
capital_pool_id:               pool-us-equity-growth
artifact_id:                   alpha-model-ensemble-v3.1.0
artifact_version:              3.1.0
deployment_mode:               paper
persona_capital_binding_id:    pcb-senior-quant-us-growth
```

### 3.4 Expected Outputs

#### Telemetry

```json
{
  "event_type": "strategy_cycle_completed",
  "strategy_id": "strat-equities-momentum-us-001",
  "dataset_version_id": "dv-20260413-us-equity-universe-v1",
  "regime_id": "regime-20260413-001",
  "allocation_id": "alloc-20260413-001",
  "risk_adjudication_id": "risk-20260413-001",
  "deployment_plan_id": "deploy-20260413-001",
  "runtime_binding_id": "binding-20260413-001",
  "cycle_at": "2026-04-13T09:30:20Z",
  "verdict": "approved",
  "deployment_mode": "paper",
  "gross_exposure": 0.27,
  "num_positions": 4,
  "execution_feedback": "MOCKED_EX001_DEFERRED"
}
```

#### Lineage Trace

Every data point in the replay must trace back through:
```
raw-us-equity-ohlcv-20260413
  → norm-us-equity-daily-v1
    → feat-us-equity-momentum-v1
      → dataset_version: dv-20260413-us-equity-universe-v1
        → regime_id: regime-20260413-001
          → universe_id: universe-20260413-001
            → signal_id: signal-20260413-001
              → allocation_id: alloc-20260413-001
                → risk_adjudication_id: risk-20260413-001
                  → approval_decision_id: approval-20260413-001
                    → deployment_plan_id: deploy-20260413-001
                      → runtime_binding_id: binding-20260413-001
```

#### Durable Storage Verification

| Store | Key pattern | Expected value |
|---|---|---|
| Postgres `dataset_versions` | `dataset_version_id = dv-20260413-us-equity-universe-v1` | Row present, `state = frozen`, checksum matches manifest |
| Postgres `regime_states` | `regime_id = regime-20260413-001` | Row present, `state = validated` |
| Postgres `allocation_decisions` | `allocation_id = alloc-20260413-001` | Row present, `state = validated` |
| Postgres `risk_adjudications` | `adjudication_id = risk-20260413-001` | Row present, `verdict = approved` |
| Postgres `deployment_plans` | `plan_id = deploy-20260413-001` | Row present, `target_stage = paper` |
| Redis lineage cache | `lineage:binding-20260413-001` | Contains all upstream refs in order |

#### No-Incident Gate

The replay must complete without triggering a `P1` or higher incident. If a telemetry anomaly is raised, the replay is rejected until resolved.

---

## 4. Scenario 2 — TW TAIFEX Derivatives (Paper Stage)

### 4.1 Pinned Data Plane Refs (BG-001)

| Object | ID | Location |
|---|---|---|
| `DatasetVersion` | `dv-20260413-tw-derivs-txo-v1` | `services/data-plane/schemas/dataset_version.schema.json` |
| `SecurityMaster` | `sm-tw-20260413` | `services/data-plane/schemas/security_master.schema.json` |
| `ContractMaster` | `cm-tw-txo-20260413` | `services/data-plane/schemas/contract_master.schema.json` |
| `MarketCalendarSession` | `cal-taifex-2026-04-13-regular` | `services/data-plane/schemas/market_calendar_session.schema.json` |
| `RawDataset` | `raw-txo-chain-20260413` | `services/data-plane/schemas/raw_dataset.schema.json` |
| `NormalizedDataset` | `norm-txo-chain-eod-v1` | `services/data-plane/schemas/normalized_dataset.schema.json` |
| `FeatureDataset` | `feat-txo-iv-surface-v1` | `services/data-plane/schemas/feature_dataset.schema.json` |

DatasetVersion manifest ref:
```
dataset_version_id:       dv-20260413-tw-derivs-txo-v1
market_scope:             TW
instrument_scope:         {asset_types: ["equity_option"], venues: ["TAIFEX"]}
universe_filter:          {underlying_index: "TAIEX", expiry_range: [0, 90]}
raw_dataset_refs:         [raw-txo-chain-20260413, raw-tx-calendar-2026]
normalized_dataset_refs:  [norm-txo-chain-eod-v1, norm-txo-greeks-v1]
feature_dataset_refs:     [feat-txo-iv-surface-v1, feat-txo-oi-change-v1]
symbol_master_ref:        sm-tw-20260413
contract_master_ref:      cm-tw-txo-20260413
calendar_ref:             cal-taifex-2026-04-13-regular
state:                    frozen
```

ContractMaster requirement: At replay point T, `cm-tw-txo-20260413` must reconstruct the full TXO options chain including expired near-month contracts and the active front/back month contracts per `DATASET_VERSION_AND_REPLAY_POLICY.md §5`.

Available-time gate:
- Replay point T = `2026-04-13T13:45:00+08:00` (TAIFEX close)
- All data points: `available_time <= T`
- Options chain snapshot: timestamp < T (end-of-session snapshot)
- TAIFEX-specific: mark settlement price available after `15:00:00+08:00`

### 4.2 Pinned Decision-Chain Refs (BG-003)

| Stage | Object | ID |
|---|---|---|
| 1 | `RegimeState` | `regime-tw-20260413-001` |
| 2 | `UniverseSelection` | `universe-tw-20260413-001` |
| 3 | `SignalInference` | `signal-tw-20260413-001` |
| 4 | `AllocationDecision` | `alloc-tw-20260413-001` |
| 5 | `RiskAdjudication` | `risk-tw-20260413-001` |

Chain summary:
- Regime: volatility regime classification using IV surface features
- Universe: TAIFEX TXO options, front-month and next-month, near-ATM strikes
- Signals: IV surface skew and OI flow signals
- Allocation: delta-hedged position within capital pool
- Risk: includes `max_leverage`, `var_limit`, and `stress_test` with `regime_shift` scenario

Derivatives-specific requirement: `AllocationDecision` must include `contract_master_refs` in `market_scope`, and `UniverseSelection` must include options chains with strike/expiry metadata.

### 4.3 Pinned Governance and Runtime Refs (Back Half)

| Object | ID |
|---|---|
| `ApprovalDecision` | `approval-tw-20260413-001` |
| `DeploymentPlan` | `deploy-tw-20260413-001` |
| `RuntimeBinding` | `binding-tw-20260413-001` |

DeploymentPlan ref:
```
plan_id:               deploy-tw-20260413-001
approval_decision_id:  approval-tw-20260413-001
artifact_id:           txo-iv-surface-strategy-v1.0.0
artifact_version:      1.0.0
strategy_id:           strat-tw-derivs-txo-iv-001
capital_pool_id:       pool-tw-derivs
current_stage:         none
target_stage:          paper
transition_type:       initial_deploy
```

RuntimeBinding ref:
```
binding_id:                    binding-tw-20260413-001
runtime_id:                    lean-worker-paper-tw-001
capital_pool_id:               pool-tw-derivs
artifact_id:                   txo-iv-surface-strategy-v1.0.0
artifact_version:              1.0.0
deployment_mode:               paper
persona_capital_binding_id:    pcb-tw-derivs-001
```

### 4.4 Expected Outputs

#### Telemetry

```json
{
  "event_type": "strategy_cycle_completed",
  "strategy_id": "strat-tw-derivs-txo-iv-001",
  "dataset_version_id": "dv-20260413-tw-derivs-txo-v1",
  "contract_master_id": "cm-tw-txo-20260413",
  "regime_id": "regime-tw-20260413-001",
  "allocation_id": "alloc-tw-20260413-001",
  "risk_adjudication_id": "risk-tw-20260413-001",
  "deployment_plan_id": "deploy-tw-20260413-001",
  "runtime_binding_id": "binding-tw-20260413-001",
  "cycle_at": "2026-04-13T05:45:00Z",
  "verdict": "approved",
  "deployment_mode": "paper",
  "execution_feedback": "MOCKED_EX001_DEFERRED"
}
```

#### Durable Storage Verification

| Store | Key pattern | Expected value |
|---|---|---|
| Postgres `dataset_versions` | `dataset_version_id = dv-20260413-tw-derivs-txo-v1` | Row present, `state = frozen`, checksum matches, `contract_master_ref IS NOT NULL` |
| Postgres `contract_masters` | `contract_master_id = cm-tw-txo-20260413` | Row present with TXO chain at replay point T |
| Postgres `regime_states` | `regime_id = regime-tw-20260413-001` | Row present, `state = validated` |
| Postgres `risk_adjudications` | `adjudication_id = risk-tw-20260413-001` | Row present, `verdict = approved` |
| Postgres `deployment_plans` | `plan_id = deploy-tw-20260413-001` | Row present, `target_stage = paper` |
| Redis lineage cache | `lineage:binding-tw-20260413-001` | Contains all upstream refs including `contract_master_ref` |

---

## 5. Acceptance Runbook

This runbook defines the step-by-step procedure for executing the golden replay and verifying the results. All steps must be completed in order for production sign-off.

### 5.1 Prerequisites

Before running the replay, verify:

1. **Dependency artifacts present**
   - `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` exists and is canonical
   - `DATA_SOURCE_SCOPE_MATRIX.md` exists and is canonical
   - `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` exists and is canonical
   - `DATASET_VERSION_AND_REPLAY_POLICY.md` exists and is canonical
   - All five decision-domain schemas exist and validate: `services/registry-core/decision-domain/`
   - `services/data-plane/schemas/dataset_version.schema.json` exists
   - `services/data-plane/schemas/contract_master.schema.json` exists
   - `services/control-plane/governance/deployment_plan.schema.json` exists
   - `services/runtime_manager/runtime_binding.schema.json` exists

2. **Dataset versions frozen**
   - `dv-20260413-us-equity-universe-v1`: state = frozen, checksum verified
   - `dv-20260413-tw-derivs-txo-v1`: state = frozen, checksum verified, contract_master_ref set

3. **Decision chain objects in store**
   - All five-stage chain objects for both scenarios are persisted in Postgres

4. **Services running**
   - Data Plane ingest service: up
   - Registry Core service: up
   - Governance service: up
   - Execution runtime-manager: up (mock execution adapter active)
   - Telemetry capture service: up
   - Lineage read service: up

### 5.2 Step 1 — Dataset Version Integrity Check

```bash
# Verify frozen state and checksum for Scenario 1
python3 services/data-plane/schemas/__init__.py verify \
  --dataset-version-id dv-20260413-us-equity-universe-v1 \
  --expected-state frozen

# Verify frozen state and checksum for Scenario 2
python3 services/data-plane/schemas/__init__.py verify \
  --dataset-version-id dv-20260413-tw-derivs-txo-v1 \
  --expected-state frozen \
  --require-contract-master
```

Expected: both verifications return `PASS`. Any `FAIL` must be resolved before proceeding.

Checks performed:
- `state = frozen`
- SHA-256 checksum matches stored manifest
- All `raw_dataset_refs` accessible
- All `normalized_dataset_refs` accessible
- All `feature_dataset_refs` accessible
- `available_time <= frozen_at` for a 1% sample of data points
- `symbol_master_ref` resolves to a valid SecurityMaster
- `contract_master_ref` resolves (Scenario 2 only)
- `calendar_ref` resolves and all trading dates in the dataset are valid

### 5.3 Step 2 — Decision Chain Object Validation

```bash
# Validate five-stage chain for Scenario 1
python3 services/registry-core/decision-domain/validate_schemas.py \
  --chain-file services/registry-core/decision-domain/examples/five_stage_chain.json \
  --replay-scenario-id replay-golden-001

# Validate five-stage chain for Scenario 2
python3 services/registry-core/decision-domain/validate_schemas.py \
  --chain-file services/registry-core/decision-domain/examples/five_stage_chain_tw_derivs.json \
  --replay-scenario-id replay-golden-002
```

Expected: all five stages validate against their JSON Schema draft-07 schemas. Each stage must:
- Pass schema validation for its JSON Schema
- Link correctly to the next stage via `output_refs`
- Reference the pinned `DatasetVersion` IDs from Step 1
- For Scenario 2: include `contract_master_refs` in `market_scope`

### 5.4 Step 3 — Governance and Runtime Ref Validation

```bash
# Validate DeploymentPlan and RuntimeBinding for Scenario 1
python3 scripts/validate_replay_refs.py \
  --approval-id approval-20260413-001 \
  --deploy-plan-id deploy-20260413-001 \
  --runtime-binding-id binding-20260413-001

# Validate DeploymentPlan and RuntimeBinding for Scenario 2
python3 scripts/validate_replay_refs.py \
  --approval-id approval-tw-20260413-001 \
  --deploy-plan-id deploy-tw-20260413-001 \
  --runtime-binding-id binding-tw-20260413-001
```

Expected checks:
- `ApprovalDecision` schema validates; links to `RiskAdjudication` via `input_refs`
- `DeploymentPlan` schema validates; `target_stage = paper`; links to `ApprovalDecision`
- `RuntimeBinding` schema validates; `deployment_mode = paper`; links to `DeploymentPlan` via `plan_id`

### 5.5 Step 4 — End-to-End Replay Execution

```bash
# Run Scenario 1 replay
python3 scripts/run_golden_replay.py \
  --scenario replay-golden-001 \
  --dataset-version-id dv-20260413-us-equity-universe-v1 \
  --regime-id regime-20260413-001 \
  --allocation-id alloc-20260413-001 \
  --deploy-plan-id deploy-20260413-001 \
  --runtime-binding-id binding-20260413-001 \
  --execution-mode mock_ex001 \
  --output-dir /tmp/replay-golden-001/

# Run Scenario 2 replay
python3 scripts/run_golden_replay.py \
  --scenario replay-golden-002 \
  --dataset-version-id dv-20260413-tw-derivs-txo-v1 \
  --contract-master-id cm-tw-txo-20260413 \
  --regime-id regime-tw-20260413-001 \
  --allocation-id alloc-tw-20260413-001 \
  --deploy-plan-id deploy-tw-20260413-001 \
  --runtime-binding-id binding-tw-20260413-001 \
  --execution-mode mock_ex001 \
  --output-dir /tmp/replay-golden-002/
```

The replay script drives the following sequence:
1. Load frozen `DatasetVersion` manifest
2. Apply `available_time <= T` filter on all datasets
3. Execute five-stage decision chain from pinned chain objects
4. Submit `AllocationDecision` to risk engine; receive `RiskAdjudication`
5. Submit `RiskAdjudication` to governance; receive `ApprovalDecision`
6. Load `DeploymentPlan` from `ApprovalDecision`
7. Activate `RuntimeBinding` (paper mode)
8. Emit mock execution feedback (EX-001 deferred)
9. Capture telemetry event
10. Write full lineage trace to lineage service

### 5.6 Step 5 — Telemetry and Output Manifest Verification

```bash
# Check telemetry events for Scenario 1
python3 scripts/verify_replay_telemetry.py \
  --scenario replay-golden-001 \
  --output-dir /tmp/replay-golden-001/ \
  --expected-verdict approved \
  --expected-deployment-mode paper

# Check telemetry events for Scenario 2
python3 scripts/verify_replay_telemetry.py \
  --scenario replay-golden-002 \
  --output-dir /tmp/replay-golden-002/ \
  --expected-verdict approved \
  --expected-deployment-mode paper
```

Expected output manifest per scenario (saved to `--output-dir`):

| File | Content |
|---|---|
| `replay_log.jsonl` | Append-only log of all steps and their results |
| `telemetry_events.json` | All telemetry events emitted during replay |
| `lineage_trace.json` | Full lineage chain from RawDataset to RuntimeBinding |
| `durable_store_diff.json` | Before/after row snapshots from Postgres/Redis |
| `verdict.json` | Pass/fail per acceptance criterion |

### 5.7 Step 6 — Durable Storage Verification

```bash
# Verify Postgres persistence for both scenarios
python3 scripts/verify_replay_durable_store.py \
  --scenarios replay-golden-001,replay-golden-002

# Verify Redis lineage cache for both scenarios
python3 scripts/verify_replay_redis.py \
  --binding-ids binding-20260413-001,binding-tw-20260413-001
```

Expected: all rows listed in §3.4 and §4.4 are present with correct state values. Redis lineage cache entries must include all upstream refs in insertion order, enabling deterministic re-query.

### 5.8 Step 7 — No-Incident Gate

```bash
# Check for any P1+ incidents raised during replay
python3 scripts/verify_replay_incident_gate.py \
  --replay-window-start 2026-04-13T09:30:00Z \
  --replay-window-end 2026-04-13T10:00:00Z
```

Expected: zero `P1` or higher incidents in the replay time window. Any incident requires investigation before sign-off.

### 5.9 Step 8 — Regression Check

After the golden replay passes, run the full decision-domain unit test suite to confirm no regressions:

```bash
python3 services/registry-core/decision-domain/validate_schemas.py
python3 -m pytest services/registry-core/decision-domain/ -v
python3 -m pytest services/data-plane/ -v
python3 -m pytest services/control-plane/governance/ -v
```

Expected: all tests pass.

---

## 6. Acceptance Criteria

| # | Criterion | Verification | Required for sign-off |
|---|---|---|---|
| 1 | `dataset_version_frozen` | Both `dv-20260413-us-equity-universe-v1` and `dv-20260413-tw-derivs-txo-v1` are frozen with verified checksums | Yes |
| 2 | `available_time_clean` | 1% sample confirms `available_time <= T` for both datasets | Yes |
| 3 | `equities_chain_validates` | All five decision-chain objects for `replay-golden-001` pass schema validation and link correctly | Yes |
| 4 | `derivatives_chain_validates` | All five decision-chain objects for `replay-golden-002` pass schema validation; `contract_master_refs` present | Yes |
| 5 | `deploy_plan_paper` | Both `DeploymentPlan` objects have `target_stage = paper` | Yes |
| 6 | `runtime_binding_paper` | Both `RuntimeBinding` objects have `deployment_mode = paper` | Yes |
| 7 | `telemetry_emitted` | Both scenarios emit a `strategy_cycle_completed` event with all required fields | Yes |
| 8 | `lineage_trace_complete` | Full lineage from `RawDataset` to `RuntimeBinding` is traceable for both scenarios | Yes |
| 9 | `durable_store_verified` | All Postgres/Redis rows listed in §3.4 and §4.4 are present with correct state | Yes |
| 10 | `no_p1_incident` | No P1+ incidents during replay window | Yes |
| 11 | `regression_tests_pass` | All unit tests in decision-domain, data-plane, and governance pass | Yes |
| 12 | `derivatives_contract_master` | Scenario 2 `DatasetVersion.contract_master_ref` is non-NULL and resolves | Yes |
| 13 | `ex001_mock_recorded` | Execution feedback is recorded as `MOCKED_EX001_DEFERRED` in telemetry (not missing or silent) | Yes |

All 13 criteria must be green before GAP-05 is closed.

---

## 7. What This Replay Does Not Cover (Explicit Scope Exclusions)

| Excluded scope | Reason | Tracked by |
|---|---|---|
| Live LEAN execution fill reports | EX-001 deferral; mock adapter used | EX-001 follow-on task |
| Crypto cross-market replay | Not a P0 gap; no BG-005 dependency from crypto path | Future wave |
| Multi-market MULTI-scope `DatasetVersion` | Incremental; single-market frozen versions are P0 acceptance gate | BG-007 / future wave |
| Evolution decision loop | Requires live execution feedback; deferred with EX-001 | EX-001 follow-on |
| Memory-layer retrieval during replay | BG-004 scope | BG-004 |

---

## 8. Cross-References

| Document | Role |
|---|---|
| `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` | Authoritative market/instrument scope (BG-000) |
| `DATA_SOURCE_SCOPE_MATRIX.md` | Source class boundaries and ingest rules (BG-000) |
| `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` | Identity policy for SecurityMaster/ContractMaster (BG-000) |
| `DATASET_VERSION_AND_REPLAY_POLICY.md` | DatasetVersion schema, lifecycle, available-time discipline (BG-001/BG-000) |
| `services/data-plane/schemas/` | BG-001 schema artifacts |
| `services/registry-core/decision-domain/` | BG-003 schema artifacts and five-stage chain examples |
| `services/control-plane/governance/` | Back-half governance schemas (ApprovalDecision, DeploymentPlan) |
| `services/runtime_manager/runtime_binding.schema.json` | RuntimeBinding schema |
| `Pantheon_Blueprint_Gap_Review_v1.md §GAP-05` | Original gap statement and acceptance evidence list |
| `docs/02-architecture/consensus/phase2/consensus-packet.md` | Planning consensus and delivery wave for BG-005 |
| `docs/02-architecture/consensus/phase2/execution-materialization.md` | Delivery contract for BG-005 |

---

## 9. Acceptance Log

| Version | Date | Change | Author |
|---|---|---|---|
| `1.0` | 2026-04-14 | Initial golden replay scenario and acceptance runbook; closes GAP-05 / BG-005 | Claude |
