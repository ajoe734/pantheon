# Gap Response Matrix

Session: `phase2-2026-04-12-blueprint-gap-convergence`
Status: draft — Codex repo-evidence refresh aligned to current phase2 machine state

Convergence rule:

- Task IDs, owners, reviewers, and hard dependencies come from `planning-session.json:191-308`.
- The canonical machine state already reports `current_round = 1` and `consensus_status = ready_for_human`; this matrix is therefore a repo-evidence refresh, not a fresh round-0 seed. (`planning-session.json:92-95`; `current-work.md:24-31`)
- Priority and wave order follow the blueprint gap table in `Pantheon_Blueprint_Gap_Review_v1.md:714-726`.
- Scoped `rg` searches on 2026-04-12 under `services/**`, `integrations/**`, and `support/**` returned no hits for `SecurityMaster`, `ContractMaster`, `MarketCalendarSession`, `RawDataset`, `NormalizedDataset`, `FeatureDataset`, `DatasetVersion`, `RegimeState`, `UniverseSelection`, `SignalInference`, `AllocationDecision`, `RiskAdjudication`, memory-layer keywords, or golden replay artifacts.

---

## GAP-00

- Gap statement: Market scope, supported instruments, and source-class boundaries are not yet formalized as repo-local policy, so the Data Plane still lacks a canonical input universe.
- Repo evidence: The blueprint and market-data brief both require `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md`, `DATA_SOURCE_SCOPE_MATRIX.md`, and `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md`, but `rg --files` finds none of them. Current `StrategySpec` still relies on free-form `symbols`, `asset_classes`, `venues`, and `frequency`, which is insufficient for v1 market truth. (`Pantheon_Blueprint_Gap_Review_v1.md:160-181`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:19-99,303-343,609-638`; `services/control-plane/specs/strategy_spec.schema.json:38-69`)
- What is already done: Downstream governance/runtime/telemetry/operator work is already closed, so this is an upstream truth-model gap rather than a platform-core rewrite. (`ai-status.json:1468-2022`)
- Decision: Confirmed real P0 gap. `BG-000` is the closure task and remains Wave 0. (`planning-session.json:203-215`; `Pantheon_Blueprint_Gap_Review_v1.md:714-718`)
- Next action: Publish the v1 market scope, instrument scope, per-market data classes, source-class matrix, and stage-eligibility policy package required by the market-data brief. (`Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:566-570,609-638`)
- Citations: `Pantheon_Blueprint_Gap_Review_v1.md:94-183`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:19-99,136-343,609-638`; `services/control-plane/specs/strategy_spec.schema.json:38-69`; `planning-session.json:203-215`

---

## GAP-01

- Gap statement: Pantheon still lacks the first-class Data Plane objects needed to pin market truth, replay truth, and dataset availability discipline.
- Repo evidence: The market-data brief defines `SecurityMaster`, `ContractMaster`, `MarketCalendarSession`, `RawDataset`, `NormalizedDataset`, `FeatureDataset`, and `DatasetVersion`, plus `event_time`, `available_time`, and `ingest_time` replay discipline. Scoped repo search found no Pantheon-owned implementations for those objects. `TelemetryEvent` proves runtime/deployment truth, but it does not carry dataset-version or market-master identity. (`Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:358-447,472-533`; `services/telemetry/telemetry_event.schema.json:7-117`)
- What is already done: Research ingestion, `StrategySpec` normalization, telemetry ingest, and lineage projection are complete, but they are downstream of the missing Data Plane truth model. (`ai-status.json:1668-1791`; `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md:52-98,143-225`)
- Decision: Confirmed real P0 gap. `BG-001` is the correct schema-first closure task. (`planning-session.json:217-228`; `Pantheon_Blueprint_Gap_Review_v1.md:714-718`)
- Next action: Formalize the seven Data Plane objects, dataset replay contract, symbol/contract truth boundaries, and availability-time discipline before any golden replay work begins. (`Pantheon_Blueprint_Gap_Review_v1.md:248-272`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:572-583`)
- Citations: `Pantheon_Blueprint_Gap_Review_v1.md:189-274`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:358-447,472-533,572-583`; `services/telemetry/telemetry_event.schema.json:7-117`; `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md:52-98,143-225`; `planning-session.json:217-228`

---

## GAP-02

- Gap statement: The Research Plane has meaningful implementations, but no single maturity matrix says which backends are production research paths versus smoke-tested or still deferred.
- Repo evidence: The target architecture already names current and deferred framework roles. The regrade report shows DSPy, imitation, and MLflow are implemented and `smoke-tested`, while other paths remain deferred or criteria-defined. What is missing is the cross-backend maturity matrix and production-path mapping. (`TARGET_ARCHITECTURE.md:147-155`; `integrations/oss-002/regrade_report.md:22-30,33-77,80-121,125-171,187-224`)
- What is already done: Research ingestion and normalization are live, and the implemented adapters are not blank stubs. This makes GAP-02 a maturity-packaging gap rather than a missing-research-plane gap. (`TARGET_ARCHITECTURE.md:65-75`; `ai-status.json:1961-2022`)
- Decision: Confirmed real P1 gap. `BG-002` closes it, but the scope stops at the matrix + production-path mapping rather than checklist-format doc hardening. (`planning-session.json:185-189,230-241`)
- Next action: Publish a backend maturity matrix that classifies production research path, smoke-tested, deferred, and criteria-defined backends using the canonical checklist vocabulary. (`Pantheon_Blueprint_Gap_Review_v1.md:342-369`; `OSS_INTEGRATION_CHECKLIST.md:21-46`)
- Citations: `Pantheon_Blueprint_Gap_Review_v1.md:280-370`; `TARGET_ARCHITECTURE.md:147-155`; `integrations/oss-002/regrade_report.md:22-30,33-77,80-121,125-171,187-224`; `OSS_INTEGRATION_CHECKLIST.md:21-46`; `planning-session.json:185-189,230-241`

---

## GAP-03

- Gap statement: The back half of the decision chain is formalized, but the first half of the five-stage decision chain still lacks first-class auditable objects.
- Repo evidence: The backbone already formalizes `ApprovalDecision`, `DeploymentPlan`, `RuntimeBinding`, and `EvolutionDecision`, and the optimizer path already handles aggregated allocation truth. But scoped repo search found no Pantheon-owned definitions for `RegimeState`, `UniverseSelection`, `SignalInference`, `AllocationDecision`, or `RiskAdjudication`. `StrategySpec` remains a research-normalization object rather than a decision-front provenance chain. (`TARGET_ARCHITECTURE.md:117-145`; `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md:31-45,108-145`; `services/control-plane/specs/strategy_spec.schema.json:38-95`)
- What is already done: Portfolio/risk/governance/runtime boundaries are already strong enough to host later decision-front objects without reopening the existing backbone. (`ai-status.json:1564-1865`; `EVOLUTION_REVIEW_AND_THRESHOLDS.md:299-383`)
- Decision: Confirmed real P0 gap. `BG-003` is the closure task. (`planning-session.json:243-254`; `Pantheon_Blueprint_Gap_Review_v1.md:714-718`)
- Next action: Publish a decision-layer object map and formal schemas for the five-stage chain so replay can attribute failures before portfolio/risk/runtime layers. (`Pantheon_Blueprint_Gap_Review_v1.md:430-447`)
- Citations: `Pantheon_Blueprint_Gap_Review_v1.md:376-449`; `TARGET_ARCHITECTURE.md:117-145`; `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md:31-45,108-145`; `services/control-plane/specs/strategy_spec.schema.json:38-95`; `planning-session.json:243-254`

---

## GAP-04

- Gap statement: Registry, lineage, incident, evolution, and persona/session contracts exist, but there is still no actual memory layer for persona-specific or institutional memory, retrieval, or write-back.
- Repo evidence: `SessionPersona` already carries runtime audit fields, which proves lifecycle/session modeling exists, but scoped repo search found no Pantheon-owned memory objects, retrieval contracts, or memory write-back artifacts. The blueprint explicitly asks for persona memory and institutional memory as distinct objects. (`services/control-plane/persona/session_persona.schema.json:66-130`; `Pantheon_Blueprint_Gap_Review_v1.md:460-519`)
- What is already done: Persona/session/runtime boundaries, incident/postmortem, evolution, telemetry, and lineage are already in place and can serve as future memory inputs. (`ai-status.json:1717-1958`; `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md:143-225`)
- Decision: Confirmed real P2 gap. `BG-004` should stay design-note-first and should not delay P0 acceptance closure. (`planning-session.json:256-267`; `Pantheon_Blueprint_Gap_Review_v1.md:724-726`)
- Next action: Publish a memory-layer design note covering object schemas, retrieval contract, write-back triggers, and read/write authority. (`Pantheon_Blueprint_Gap_Review_v1.md:506-519`)
- Citations: `Pantheon_Blueprint_Gap_Review_v1.md:455-521`; `services/control-plane/persona/session_persona.schema.json:66-130`; `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md:143-225`; `planning-session.json:256-267`

---

## GAP-05

- Gap statement: Pantheon still lacks one acceptance-grade scenario that replays the full chain from data truth through runtime/telemetry/evolution evidence.
- Repo evidence: Telemetry ingest, lineage projection, rollback lineage, and evolution routing are already formalized and replay-friendly, but scoped repo search found no `dataset_version_id`, golden replay scenario, replay runbook, or expected-output manifest in Pantheon-owned code/docs under `services/**`, `integrations/**`, or `support/**`. The market-data brief explicitly requires replay pinned to exact dataset and master versions. (`TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md:216-234`; `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md:70-98,219-225`; `ROLLBACK_AND_POSITION_SEMANTICS.md:168-219`; `EVOLUTION_REVIEW_AND_THRESHOLDS.md:299-383`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:526-533`)
- What is already done: Approval, deployment planning, runtime binding, telemetry ingest, lineage, incident, and evolution are already complete enough to support replay once the upstream data and decision objects exist. (`ai-status.json:1468-1958`)
- Decision: Confirmed real P0 gap and the primary production-signoff artifact. `BG-005` depends on `BG-000`, `BG-001`, and `BG-003`, but it belongs in the P0 acceptance wave. (`planning-session.json:269-282`; `Pantheon_Blueprint_Gap_Review_v1.md:714-718`)
- Implementation Constraints:
  - **LEAN Execution Gap**: `EX-001` (Artifact Loader) defers "algorithm-level smoke coverage inside a real LEAN run", so the Wave 1 replay might rely on synthetic/mocked execution for the LEAN segment. (`services/execution/artifact-loader/contract.md` §6)
  - **Telemetry Durability**: The in-memory buffer is a "dev/research shim" only; full-chain replay should verify event persistence in Postgres/Redis to ensure repeatability. (`services/telemetry/BUFFER_CHOICE_ADR.md` L111)
- Next action: Define the golden replay scenario and runbook, pinning at least one equities path and one derivatives-aware path to real dataset/master/decision refs and expected telemetry/incident/evolution outputs. (`Pantheon_Blueprint_Gap_Review_v1.md:578-594`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:626-638`)
- Citations: `Pantheon_Blueprint_Gap_Review_v1.md:527-597,714-718`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:520-533,626-638`; `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md:216-234`; `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md:70-98,219-225`; `ROLLBACK_AND_POSITION_SEMANTICS.md:168-219`; `EVOLUTION_REVIEW_AND_THRESHOLDS.md:299-383`; `planning-session.json:269-282`; `services/execution/artifact-loader/contract.md` §6; `services/telemetry/BUFFER_CHOICE_ADR.md` L111

---

## GAP-06

- Gap statement: Operator surfaces are materially in place, but there is still no single acceptance matrix that classifies each surface by authority, fallback path, permissions, and drill status.
- Repo evidence: The BFF inventory already enumerates canonical surfaces and degraded behavior; the API contract codifies read-orientation, composed-view staleness, and the "never show none" rule; the degraded operator path already defines CLI/internal API fallback and admin operations. What is missing is one operator acceptance matrix that consolidates those facts into a sign-off artifact. `rg --files` also finds no dedicated operator acceptance matrix doc. (`services/control-plane/bff/BFF_SURFACE_INVENTORY.md:13-24,222-267`; `services/control-plane/bff/BFF_API_CONTRACT.md:15-37,219-290`; `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md:15-23,99-149`; `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md:81-149`)
- What is already done: `APP-001` and `APP-002` are both marked done, so this is a packaging/acceptance-language gap rather than missing operator capability. (`ai-status.json:1905-1958`)
- Decision: Confirmed real P1 gap. `BG-006` is the closure task. (`planning-session.json:284-295`; `Pantheon_Blueprint_Gap_Review_v1.md:720-722`)
- Next action: Publish the operator acceptance matrix by reusing the existing BFF inventory, API contract, degraded path, and drill expectations rather than inventing new surfaces or shadow objects. (`Pantheon_Blueprint_Gap_Review_v1.md:645-661`)
- Citations: `Pantheon_Blueprint_Gap_Review_v1.md:601-665`; `services/control-plane/bff/BFF_SURFACE_INVENTORY.md:13-24,222-267`; `services/control-plane/bff/BFF_API_CONTRACT.md:15-37,219-290`; `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md:15-23,99-149`; `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md:81-149`; `ai-status.json:1905-1958`; `planning-session.json:284-295`

---

## GAP-07

- Gap statement: Pantheon still lacks a product/operator language pack that translates engineering objects, stages, and actions into final acceptance language.
- Repo evidence: The canonical docs define exact lifecycle, stage, and object terms, and the BFF contract exposes them in read-oriented API surfaces, but `rg --files` finds no product glossary, action→object mapping, or stage/status language pack in the repo. (`Pantheon_Blueprint_Gap_Review_v1.md:674-708`; `TARGET_ARCHITECTURE.md:17-45,117-145`; `services/control-plane/bff/BFF_API_CONTRACT.md:15-37`)
- What is already done: Engineering truth is already stable enough to translate; this is not a schema or runtime blocker.
- Decision: Confirmed real P2 gap. `BG-007` should follow the operator acceptance matrix so it can reuse the agreed surface vocabulary. (`planning-session.json:297-308`; `Pantheon_Blueprint_Gap_Review_v1.md:724-726`)
- Next action: Publish the product-facing glossary, action→object map, and stage/status wording pack on top of the canonical object and surface language already stabilized by `BG-006`. (`Pantheon_Blueprint_Gap_Review_v1.md:701-708`)
- Citations: `Pantheon_Blueprint_Gap_Review_v1.md:669-708,724-726`; `TARGET_ARCHITECTURE.md:17-45,117-145`; `services/control-plane/bff/BFF_API_CONTRACT.md:15-37`; `planning-session.json:297-308`

---

## Summary Table

| Gap | Real Gap? | Proposed Task | P-level | Key Blocker |
|---|---|---|---|---|
| GAP-00 | Yes | `BG-000` | P0 | Upstream market/data scope truth |
| GAP-01 | Yes | `BG-001` | P0 | Missing Data Plane objects and replay identifiers |
| GAP-02 | Yes, but mostly maturity packaging | `BG-002` | P1 | No production-path matrix |
| GAP-03 | Yes | `BG-003` | P0 | Missing decision-front provenance objects |
| GAP-04 | Yes | `BG-004` | P2 | Memory layer absent but non-blocking for P0 sign-off |
| GAP-05 | Yes | `BG-005` | P0 | Production sign-off gate; depends on `BG-000`, `BG-001`, `BG-003` |
| GAP-06 | Yes, but mostly acceptance packaging | `BG-006` | P1 | No single operator acceptance matrix |
| GAP-07 | Yes | `BG-007` | P2 | No product/operator language pack |

Critical path to production sign-off: `BG-000` → (`BG-001` ∥ `BG-003`) → `BG-005`

Tracking note:

- The only remaining item in the canonical session file is the waived Copilot lane (`DISC-COPILOT-PLANNING`); no unresolved semantic planning conflict remains. (`planning-session.json:164-189`)
