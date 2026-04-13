# Qwen Readout — Blueprint Gap Convergence (Round 2)

## Lane

- Agent: Qwen
- Capability focus: Audit schema and object formalization for GAP-01, GAP-03, and GAP-06, especially canonical object boundaries and acceptance surface coverage.

## Canonical Sources Read

- **L0**: `AI_COLLABORATION_GUIDE.md`, `ai-status.json`, `current-work.md`
- **L1**: `TARGET_ARCHITECTURE.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`, `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`, `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, `PERSONA_RUNTIME_MODEL.md`
- **L2**: `Pantheon_Blueprint_Gap_Review_v1.md`, `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md`, `CANONICAL_DOCUMENT_MAP.md`, `ROADMAP.md`, `DEVELOPMENT_WORKBREAKDOWN.md`, `docs/02-architecture/consensus/phase2/planning-session.json`, `docs/02-architecture/consensus/phase2/README.md`, `docs/02-architecture/consensus/phase2/gap-response-matrix.md`, `docs/02-architecture/consensus/phase2/execution-materialization.md`
- **Schema evidence (21 `.schema.json` files across `services/`)**:
  - Governance: `approval_decision`, `capital_pool`, `deployment_plan`, `evolution_decision`, `persona_capital_binding`
  - Persona: `capability_snapshot`, `prompt_bundle`, `persona_registry`, `session_persona`
  - Specs: `strategy_spec`, `workflow_handoff`
  - Evaluation: `critic_result`, `evaluator_result`, `optimizer_result`
  - Execution: `runtime_binding`
  - Feedback: `execution_telemetry_event`, `trader_feedback_event`
  - Incident: `incident_case`, `postmortem`
  - Registry: `promoted_artifact_metadata`
  - Telemetry: `telemetry_event`
- **BFF/operator surfaces**: `services/control-plane/bff/BFF_SURFACE_INVENTORY.md`, `services/control-plane/bff/BFF_API_CONTRACT.md`, `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md`

## Working Interpretation

- **Architecture summary**: Pantheon's governance/execution/telemetry backbone (Phases 0–6) is materially complete. 21 JSON schemas exist across `services/`, covering the post-decision plane (`ApprovalDecision`, `DeploymentPlan`, `RuntimeBinding`, `EvolutionDecision`), persona/session objects, telemetry, incident, and feedback. The **Data Plane** (GAP-01) and **Decision Plane early stages** (GAP-03) lack any schema-level definitions. The **Operator Acceptance Matrix** (GAP-06) lacks a unified sign-off artifact despite individual BFF/API/degraded-path docs being thorough.
- **Delivery order**: P0 gaps (GAP-00, GAP-01, GAP-03, GAP-05) must close first. BG-005 (golden replay) depends on BG-000, BG-001, and BG-003, making it the P0 acceptance gate. P1 (BG-002, BG-006) and P2 (BG-004, BG-007) follow. The `gap-response-matrix.md` and `execution-materialization.md` already reflect this ordering correctly.
- **Ownership boundaries**: Per `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`, single-table single-write-owner is canonical. Data-plane objects (`SecurityMaster`, `ContractMaster`, etc.) should be owned by `registry-core-svc`. Decision-front objects (`RegimeState`, `UniverseSelection`, etc.) should be owned by the decision-registry or registry-core subdomain. The BFF acceptance matrix (GAP-06) is a documentation/coordination task that reuses existing BFF, API, and degraded-path sources.

## Risks / Contradictions

- **Risk 1 (GAP-01 — confirmed real gap)**: Grep search for `SecurityMaster|ContractMaster|MarketCalendarSession|RawDataset|NormalizedDataset|FeatureDataset|DatasetVersion` under `services/` returns **zero hits**. None of these seven Data Plane objects exist as `.schema.json` files, Python models, or any other code artifact in Pantheon-owned code. The `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md` §6 defines their required field shapes (specification-level only), but no schema implementation exists. The existing `TelemetryEvent` schema carries binding, deployment, rollback, and artifact identity, but carries **no data-plane references** (no `dataset_version_id`, no `security_id`, no `contract_id`). This confirms GAP-01 is a genuine P0 gap, not an evidence visibility issue. (`Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:354-447,472-533`; grep zero-hit evidence; `services/telemetry/telemetry_event.schema.json`)

- **Risk 2 (GAP-03 — confirmed real gap)**: Grep search for `RegimeState|UniverseSelection|SignalInference|AllocationDecision|RiskAdjudication` under `services/` returns **zero hits**. None of these five decision-front objects exist as Pantheon-native schemas. The existing backbone has `ApprovalDecision`, `DeploymentPlan`, `RuntimeBinding`, and `EvolutionDecision`, but the **pre-decision chain** (regime → universe → signal → allocation → risk) has no first-class object definitions. The `StrategySpec.market_scope` block uses free-form `symbols[]`, `asset_classes[]`, `venues[]` arrays and should eventually reference `SecurityMaster`/`ContractMaster` by ID. This confirms GAP-03 is a genuine P0 gap. (`Pantheon_Blueprint_Gap_Review_v1.md:376-451`; `services/control-plane/specs/strategy_spec.schema.json:38-95`; grep zero-hit evidence)

- **Risk 3 (GAP-06 — documentation/coordination gap, not implementation gap)**: The BFF surface inventory enumerates 33 canonical surfaces and their degraded behavior (`BFF_SURFACE_INVENTORY.md`). The BFF API contract codifies read-orientation, composed-view staleness metadata, and the "never show none" rule (`BFF_API_CONTRACT.md`). The degraded operator path defines 5-tier fallback (Fresh → Read-replica → Cache → Reconstructed → Unavailable) and secondary control paths (`DEGRADED_OPERATOR_PATH.md`). `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` defines HA principles and backup control paths. What is **missing** is a single **Operator Acceptance Matrix** that maps surface → authoritative/composed/fallback → degraded behavior → permissions → test status → drill status. All individual source docs exist; this is a consolidation/packaging gap. (`Pantheon_Blueprint_Gap_Review_v1.md:601-665`; `BFF_SURFACE_INVENTORY.md`; `BFF_API_CONTRACT.md`; `DEGRADED_OPERATOR_PATH.md`; `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`)

- **Risk 4 (StrategySpec backward compatibility)**: Once `SecurityMaster` and `ContractMaster` are defined, `StrategySpec.market_scope` should transition from free-form symbol lists to typed references (`security_master_id`, `contract_master_id`, or `dataset_version_id`). The BG-001 task must address this migration path explicitly — either via a v1.1 StrategySpec extension or an aliasing layer. (`services/control-plane/specs/strategy_spec.schema.json:38-69`)

- **Risk 5 (Telemetry schema data-plane extension)**: The `TelemetryEvent` schema is the strongest evidence of backbone maturity — it carries `binding_id`, `runtime_id`, `capital_pool_id`, `artifact_id`, `deployment_stage`, `plan_id`, `persona_capital_binding_id`, and rollback lineage. When GAP-01 objects are defined, the telemetry schema should gain an **optional** `data_refs[]` field to support data-layer provenance queries (e.g., "show all events produced from dataset version X"). This is not a blocker for GAP-01 schema definition, but is a downstream dependency for BG-005 (golden replay). (`services/telemetry/telemetry_event.schema.json`)

- **Risk 6 (Lineage edge table missing data-plane edges)**: `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` defines 19 canonical lineage edges. None reference data-plane objects (`SecurityMaster`, `DatasetVersion`, `NormalizedDataset`, etc.). When BG-001 delivers these objects, the lineage edge table must be extended to include data-plane edges (e.g., `DatasetVersion → NormalizedDataset`, `SecurityMaster → StrategySpec`). This is a BG-001 follow-on concern, not a blocker. (`LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`)

## Suggested Task Slices

### Slice 1 (GAP-01 — P0, BG-001): Define Data Plane canonical object schemas
- **Deliverable**: 7 `.schema.json` files — `SecurityMaster`, `ContractMaster`, `MarketCalendarSession`, `RawDataset`, `NormalizedDataset`, `FeatureDataset`, `DatasetVersion`
- **Location**: `services/registry-core/data-domain/` (per `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` ownership)
- **Field source**: `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md` §6 as starting field list; add `created_at`, `version`, `checksum`, `source_class` for provenance
- **Replay contract**: Define `event_time` vs `available_time` vs `ingest_time` semantics; `DatasetVersion` must carry a `replay_key` that uniquely pins market inputs at a point in time
- **Acceptance**: Each schema validates against JSON Schema draft-07; ≥1 example payload per schema; cross-reference integrity (e.g., `NormalizedDataset.parent_raw_dataset_id` → `RawDataset.dataset_id`); `DatasetVersion.replay_key` is uniquely constraintable
- **Owner**: Qwen; **Reviewer**: Codex

### Slice 2 (GAP-03 — P0, BG-003): Define Decision Plane early-stage object schemas
- **Deliverable**: 5 `.schema.json` files — `RegimeState`, `UniverseSelection`, `SignalInference`, `AllocationDecision`, `RiskAdjudication`
- **Location**: `services/registry-core/decision-domain/`
- **Field requirements**: Each must include `strategy_id`, `artifact_id`, `version`, `evaluated_at`, `input_refs[]` (upstream dataset/experiment refs), `output_refs[]` (downstream allocation/deployment refs), and a `decision_reasoning` or `model_ref` field for provenance
- **Acceptance**: Each schema validates; ≥1 strategy family can produce a full five-stage chain `RegimeState → UniverseSelection → SignalInference → AllocationDecision → RiskAdjudication` using example payloads; chain links to existing `ApprovalDecision` downstream
- **Owner**: Qwen; **Reviewer**: Claude

### Slice 3 (GAP-06 — P1, BG-006): Draft Operator Acceptance Matrix
- **Deliverable**: `OPERATOR_ACCEPTANCE_MATRIX.md` mapping every surface (BFF, internal API, CLI, fallback, support-only) to: canonical object, authoritative/composed/fallback classification, degraded behavior, required permissions, test status, operator drill status
- **Location**: `docs/03-operations/` or root level
- **Source reuse**: Must cite existing `BFF_SURFACE_INVENTORY.md`, `BFF_API_CONTRACT.md`, `DEGRADED_OPERATOR_PATH.md`, and `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` — must not redefine surfaces or invent shadow objects
- **Acceptance**: Covers all APP-002 wave surfaces; includes degraded-mode drill confirmation; cites existing test evidence per surface
- **Owner**: Qwen; **Reviewer**: Claude

### Slice 4 (GAP-01 extension — P0, BG-001 subtask): Define availability-time discipline spec
- **Deliverable**: `AVAILABILITY_TIME_DISCIPLINE.md` defining `event_time`, `available_time`, `ingest_time` semantics across all data classes; explicit point-in-time correctness and look-ahead-leakage prevention rules
- **Location**: `docs/01-design-decisions/` or alongside data-domain schemas
- **Acceptance**: Referenced by `RawDataset`, `NormalizedDataset`, and `FeatureDataset` schemas; ≥1 dataset pipeline demonstrates the discipline end-to-end
- **Owner**: Qwen; **Reviewer**: Codex

## Cross-Review Notes

- **Agreement with gap-response-matrix.md**: The matrix's gap assessments for GAP-01, GAP-03, and GAP-06 are all confirmed by my independent grep-based evidence search. Zero hits for all target object names under `services/` is definitive.
- **BG-005 priority correction**: I agree with the gap-response-matrix that BG-005 should be treated as P0 acceptance closure, not P2 as the original session seed labeled it. The golden replay is the production sign-off gate.
- **BG-006 scope clarification**: BG-006 is a consolidation/packaging task, not a reimplementation. All source material already exists in BFF docs. The matrix should be a single-page sign-off artifact, not a redesign.
- **BG-002 ownership note**: `planning-session.json` lists BG-002 owner as Copilot, but the execution-materialization.md also lists Copilot. Since Copilot's planning lane was waived (DISC-COPILOT-PLANNING), BG-002 may need reassignment or the matrix-only scope decision must be confirmed by the facilitator.

## Citations

- [Pantheon_Blueprint_Gap_Review_v1.md §GAP-01] Data Plane requires raw/normalized/feature-ready three layers, dataset version object, availability-time discipline, and dataset replay contract. Grep confirms zero code-level implementations.
- [Pantheon_Blueprint_Gap_Review_v1.md §GAP-03] Decision Plane requires RegimeState, UniverseSelection, SignalInference, AllocationDecision, RiskAdjudication. Grep confirms no Pantheon-native implementations.
- [Pantheon_Blueprint_Gap_Review_v1.md §GAP-06] Operator surfaces are complete but lack production acceptance language.
- [Pantheon_Market_Data_Scope_and_Source_Plan_v1.md §6] Defines required field shapes for all seven Data Plane objects — specification-level only, not schema files.
- [services/telemetry/telemetry_event.schema.json] Canonical telemetry v2 envelope with full binding/deployment/rollback lineage. No data-plane refs yet.
- [services/control-plane/specs/strategy_spec.schema.json] StrategySpec has free-form `market_scope` with `symbols[]`, `asset_classes[]`, `venues[]`. Should reference SecurityMaster/ContractMaster by ID once defined.
- [services/control-plane/bff/BFF_SURFACE_INVENTORY.md] 33 canonical surfaces enumerated with L1 source references.
- [services/control-plane/bff/BFF_API_CONTRACT.md] Read-oriented API with staleness model, RBAC, and composed views.
- [services/control-plane/bff/DEGRADED_OPERATOR_PATH.md] 5-tier fallback model and secondary control path specification.
- [BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md] HA principles, degraded-mode policies, and backup control paths.
- [DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md] Single-table single-write-owner principle; `registry-core-svc` owns data-domain schemas.
- [LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md] 19 canonical lineage edges defined; none reference data-plane objects yet.
- [docs/02-architecture/consensus/phase2/planning-session.json] BG-001 and BG-003 proposed as P0 tasks owned by Qwen; BG-006 proposed as P1 task owned by Qwen.
- [docs/02-architecture/consensus/phase2/gap-response-matrix.md] Codex repo-evidence pass confirms all gap claims; BG-005 priority corrected to P0.
- [docs/02-architecture/consensus/phase2/execution-materialization.md] Wave ordering confirmed: P0 (BG-000, BG-001, BG-003, BG-005) → P1 (BG-002, BG-006) → P2 (BG-004, BG-007).
