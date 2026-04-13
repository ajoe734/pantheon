# Starter Draft

Current rule: only `Codex` edits this file directly.

## Shared Draft

- Canonical state note: the machine-readable session file and derived planning snapshot already place this session at round 1 with `ready_for_human`; this draft is a Codex repo-evidence refresh, not a fresh round-0 bootstrap. (`planning-session.json:92-95`; `current-work.md:24-31`)
- Objective: converge a cited draft that keeps the accepted governance/runtime/telemetry/persona/BFF backbone intact and focuses the next delivery wave on blueprint gaps that still lack repo-local truth objects, policy docs, or acceptance artifacts. (`TARGET_ARCHITECTURE.md:77-145`; `ai-status.json:1468-2022`)
- Scope boundary: this round does not reopen the completed backbone tasks from `REG-004` through `OSS-003`; it only materializes the next wave around market/data truth, decision-front truth, replay acceptance, and packaging gaps. (`ai-status.json:1468-2022`; `DEVELOPMENT_WORKBREAKDOWN.md:11-114`)
- Repo-verified baseline:
  - The repo already formalizes `ApprovalDecision`, `DeploymentPlan`, `PersonaCapitalBinding`, `RuntimeBinding`, `TelemetryEvent`, lineage projection, incident/postmortem, `EvolutionDecision`, `SessionPersona`, BFF degraded/fallback behavior, and OSS regrade status. (`BINDING_AND_DEPLOYMENT_SEMANTICS.md:24-57,171-223,464-505`; `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md:143-225,372-397`; `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md:25-65,69-120`; `EVOLUTION_REVIEW_AND_THRESHOLDS.md:299-383`; `ai-status.json:1468-2022`)
  - `StrategySpec` still carries free-form `symbols`, `asset_classes`, `venues`, and generic `data_dependencies[]`, and RS-002 still emits research sentinel placeholders instead of master-backed market truth. (`services/control-plane/specs/strategy_spec.schema.json:38-95`; `services/research/strategy_spec/README.md:27-31`)
  - Scoped `rg` searches on 2026-04-12 under `services/**`, `integrations/**`, and `support/**` returned no hits for `SecurityMaster`, `ContractMaster`, `MarketCalendarSession`, `RawDataset`, `NormalizedDataset`, `FeatureDataset`, `DatasetVersion`, `RegimeState`, `UniverseSelection`, `SignalInference`, `AllocationDecision`, `RiskAdjudication`, memory-layer keywords, or golden replay artifacts.
  - `rg --files` returned no `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md`, `DATA_SOURCE_SCOPE_MATRIX.md`, `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md`, or `DATASET_VERSION_AND_REPLAY_POLICY.md`, even though the market-data brief names them as required acceptance artifacts. (`Pantheon_Blueprint_Gap_Review_v1.md:160-181`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:609-638`)
- Accepted wave order:
  1. Wave 0 / P0 foundations: `BG-000`, `BG-001`, `BG-003`
  2. Wave 1 / P0 acceptance closure: `BG-005`
  3. Wave 2 / P1 packaging: `BG-002`, `BG-006`
  4. Wave 3 / P2 convergence tail: `BG-004`, `BG-007`
  Priority truth: `Pantheon_Blueprint_Gap_Review_v1.md:714-726`; task identity truth: `planning-session.json:191-308`
- Gap-response seed:
  - `BG-000`: publish the market scope, supported instruments, per-market data classes, source-class matrix, and stage eligibility package required by the blueprint and market-data brief. (`Pantheon_Blueprint_Gap_Review_v1.md:101-181`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:19-99,299-343,609-638`)
  - `BG-001`: formalize `SecurityMaster`, `ContractMaster`, `MarketCalendarSession`, `RawDataset`, `NormalizedDataset`, `FeatureDataset`, and `DatasetVersion`, plus `event_time` / `available_time` / `ingest_time` discipline and replay identifiers. (`Pantheon_Blueprint_Gap_Review_v1.md:194-272`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:358-447,472-533,572-583`)
  - `BG-003`: formalize the five-stage decision-front chain so replay can attribute failure to regime, universe, signal, allocation, or risk adjudication instead of treating everything before deployment as opaque research logic. (`Pantheon_Blueprint_Gap_Review_v1.md:381-447`)
  - `BG-005`: build one scriptable golden replay scenario only after the upstream data and decision objects exist; it must pin real `DatasetVersion` refs and real decision-chain refs. (`Pantheon_Blueprint_Gap_Review_v1.md:546-597`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:526-533,626-638`)
  - `BG-002`: publish a research backend maturity matrix and production-path mapping; do not expand this task into checklist-format doc hardening. (`Pantheon_Blueprint_Gap_Review_v1.md:317-370`; `integrations/oss-002/regrade_report.md:187-224`)
  - `BG-006`: package the already-written BFF/API/degraded-path contracts into one operator acceptance matrix with authority, fallback, permissions, and drill status. (`Pantheon_Blueprint_Gap_Review_v1.md:620-665`; `services/control-plane/bff/BFF_SURFACE_INVENTORY.md:255-267`; `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md:99-149`)
  - `BG-004`: design the missing memory layer rather than pretending lineage/session contracts already satisfy persona or institutional memory. (`Pantheon_Blueprint_Gap_Review_v1.md:478-519`; `services/control-plane/persona/session_persona.schema.json:66-130`)
  - `BG-007`: package canonical object/stage/action language into a product/operator glossary only after `BG-006` stabilizes the surface vocabulary. (`Pantheon_Blueprint_Gap_Review_v1.md:674-708`)
- Resolved items reflected in this seed:
  - `BG-005` is a P0 acceptance gate, not a P2 tail item. (`Pantheon_Blueprint_Gap_Review_v1.md:714-718`; `planning-session.json:269-279`)
  - `BG-005` requires real data and decision anchors, not a synthetic telemetry-only replay. (`Pantheon_Blueprint_Gap_Review_v1.md:549-594`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:526-533`)
  - `BG-002` scope is matrix + production-path mapping only; documentation hardening is follow-on work. (`planning-session.json:185-189,230-241`; `integrations/oss-002/regrade_report.md:218-224`)
  - `BG-004` and `BG-007` remain P2 convergence-tail work. (`Pantheon_Blueprint_Gap_Review_v1.md:724-726`; `planning-session.json:256-308`)
- Remaining tracking item:
  - `DISC-COPILOT-PLANNING` remains a low-severity waived lane, but no unresolved semantic conflict remains in the canonical session file. (`planning-session.json:164-189`)
