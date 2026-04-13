# Gemini Readout: Phase 2 Blueprint Gap Convergence

## Lane

- Agent: Gemini
- Capability focus: Evaluate runtime, replay, and tooling feasibility for GAP-02 and GAP-05; report blockers with cited implementation constraints.

## Canonical Sources Read

- L0: `ai-status.json`, `current-work.md`, `baton-log.md`
- L1: `Pantheon_Blueprint_Gap_Review_v1.md`, `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md`, `TARGET_ARCHITECTURE.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, `PERSONA_RUNTIME_MODEL.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `PAPER_CANARY_LIVE_POLICY.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`, `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`, `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`, `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`, `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `CANONICAL_DOCUMENT_MAP.md`
- L2: `ROADMAP.md`, `DEVELOPMENT_WORKBREAKDOWN.md`, `OSS_INTEGRATION_CHECKLIST.md`

## Working Interpretation

- **Architecture Summary**:
  The Pantheon platform backbone (Governance, Execution, Telemetry, Lineage) is robust and converged. However, the **Research Plane** (GAP-02) and **Cross-Plane Replay** (GAP-05) lack formalization and integrated evidence. Research backends are implemented but not categorized by production readiness. Replay exists in isolated segments (Telemetry, SSE) but lacks a "Golden Scenario" that pins end-to-end truth from Data to Feedback.

- **Feasibility Evaluation**:
  - **GAP-02 (Research Maturity)**: High feasibility for current backends (MLflow, DSPy, Imitation) to reach "governed" status if documentation debt is cleared. Tooling for experiment tracking and registry handoff is already in place.
  - **GAP-05 (Golden Replay)**: Feasible but blocked by **GAP-01** (Data Plane objects). Without `DatasetVersion` and `ContractMaster`, replay remains unanchored to truth. Full "Execution" replay is constrained by the deferral of algorithm-level smoke coverage in LEAN (`EX-001`).

- **Delivery Order**:
  1. **Data Plane Objects (BG-001)**: Must precede Golden Replay to provide the replay anchor.
  2. **Research Maturity Matrix (BG-002)**: Formalize readiness levels to guide strategy family adoption.
  3. **Golden Replay Scenario (BG-005)**: Implement as a P0 acceptance gate, initially using synthetic/mocked execution for LEAN segments where real-run coverage is deferred.

## Risks / Contradictions

- **Risk 1: LEAN Execution Replay Gap (GAP-05)**: Since `EX-001` (Artifact Loader) still defers "algorithm-level smoke coverage inside a real LEAN run", the Golden Replay Scenario might not be able to verify real execution feedback loops for the first wave. It should focus on the Data -> Research -> Decision -> Artifact handoff first.
- **Risk 2: Telemetry Buffer Durability (GAP-05)**: The current in-memory telemetry buffer is a "dev/research shim" (`BUFFER_CHOICE_ADR.md`). A reliable, repeatable Golden Replay should verify that events are persisted in Postgres/Redis to avoid data loss during the replay cycle.

## Suggested Task Slices

- **Slice 1: GAP-02-MATURITY (Research Maturity Matrix)**: Publish a matrix classifying OpenClaw, Qlib, vectorbt, etc. by role and readiness (smoke-tested vs. production-path) to satisfy `OSS_INTEGRATION_CHECKLIST.md`.
- **Slice 2: GAP-05-GOLDEN (Golden Replay Scenario)**: Define a script that drives a full cycle: pin a `DatasetVersion`, generate a `StrategySpec`, produce an `Artifact`, trigger an `ApprovalDecision`, and verify `TelemetryEvent` lineage.

## Citations

- [Pantheon_Blueprint_Gap_Review_v1.md] L527: "GAP-05：Data → Research → Decision → Execution → Feedback 的跨 Plane replay 證據仍不足"
- [services/execution/artifact-loader/contract.md] §6: "algorithm-level smoke coverage inside a real LEAN run... Still deferred"
- [services/telemetry/BUFFER_CHOICE_ADR.md] L111: "in-memory buffer只適合dev/research shim... 部署需要crash recovery時必須切到Redis Streams"
- [Pantheon_Market_Data_Scope_and_Source_Plan_v1.md] L526: "replay pinned to exact dataset and master versions"
