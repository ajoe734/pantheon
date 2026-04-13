# Codex Readout

## Lane

- Agent: Codex
- Capability focus: Verify each blueprint gap against repo evidence, own the shared starter draft, and materialize the next delivery wave into execution slices.

## Canonical Sources Read

- L0: `ai-status.json:1468-2022`, `current-work.md:22-31`
- L1: `TARGET_ARCHITECTURE.md:65-155`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md:18-57,171-223,464-505`, `PERSONA_RUNTIME_MODEL.md:59-105,322-346`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md:23-36,70-98,143-225,372-397`, `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md:25-65,69-120,216-277`, `ROLLBACK_AND_POSITION_SEMANTICS.md:40-87,148-219`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md:299-383`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md:22-47,81-149`
- L2: `docs/02-architecture/consensus/phase2/planning-session.json:1-308`, `Pantheon_Blueprint_Gap_Review_v1.md:94-726`, `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:19-99,136-343,358-533,566-638`, `DEVELOPMENT_WORKBREAKDOWN.md:11-114`, `integrations/oss-002/regrade_report.md:22-30,33-77,80-121,125-171,187-224`

## Working Interpretation

- Architecture summary: Pantheon already has the governed backbone the blueprint expected on the governance/runtime/telemetry/persona/BFF side: `ApprovalDecision`, `DeploymentPlan`, `PersonaCapitalBinding`, `RuntimeBinding`, `TelemetryEvent`, lineage projection, incident/postmortem, `EvolutionDecision`, `SessionPersona`, degraded operator path, and OSS regrade artifacts are all already marked done in the active board. (`TARGET_ARCHITECTURE.md:77-145`; `ai-status.json:1468-2022`)
- Delivery order: The next delivery wave should follow the blueprint priority table, not reopen completed backbone work. That means `BG-000`, `BG-001`, and `BG-003` first, then `BG-005` as the first acceptance gate, then `BG-002` and `BG-006`, and finally `BG-004` and `BG-007`. (`Pantheon_Blueprint_Gap_Review_v1.md:714-726`; `planning-session.json:191-308`)
- Ownership boundaries: Task IDs, owners, reviewers, and hard dependencies should stay aligned to `planning-session.json`; only `Codex` should rewrite `starter-draft.md`; other lanes should critique through their own readouts rather than patching the shared draft. (`docs/02-architecture/consensus/phase2/README.md:45-60`; `planning-session.json:53-79,191-308`)

## Risks / Contradictions

- Risk 1: `StrategySpec` still models market/data inputs as free-form `symbols`, `asset_classes`, `venues`, and generic `data_dependencies[]`, while RS-002 explicitly emits research sentinel placeholders for missing market detail. That means the repo still lacks first-class market/data truth even though downstream governance and execution objects are mature. (`services/control-plane/specs/strategy_spec.schema.json:38-95`; `services/research/strategy_spec/README.md:27-31`)
- Risk 2: The invocation header says round 0 / draft, but the canonical machine state already says the session is round 1 and `ready_for_human`. This pass should therefore be treated as a repo-evidence refresh on top of an already-advanced planning state, not a fresh bootstrap. (`planning-session.json:92-95`; `current-work.md:24-31`)
- Risk 3: Existing phase2 outputs already contain useful content, but they drifted on citations and some owner/reviewer assignments. The materialization pass must realign those files to `planning-session.json` before they are used for task creation. (`planning-session.json:191-308`)

## Suggested Task Slices

- Slice 1: `BG-000` publishes the market scope, instrument policy, and source-class package the market-data brief explicitly requires, because none of the required policy files exist in the repo today. (`Pantheon_Blueprint_Gap_Review_v1.md:160-181`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:609-638`)
- Slice 2: `BG-001` formalizes `SecurityMaster`, `ContractMaster`, `MarketCalendarSession`, `RawDataset`, `NormalizedDataset`, `FeatureDataset`, and `DatasetVersion`, plus `event_time` / `available_time` / `ingest_time` discipline and replay identifiers. (`Pantheon_Blueprint_Gap_Review_v1.md:248-272`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:358-447,472-533`)
- Slice 3: `BG-003` formalizes `RegimeState`, `UniverseSelection`, `SignalInference`, `AllocationDecision`, and `RiskAdjudication` so failure provenance can be attributed before portfolio/risk/runtime layers. (`Pantheon_Blueprint_Gap_Review_v1.md:400-447`)
- Slice 4: `BG-005` becomes the first true acceptance artifact only after `BG-000`, `BG-001`, and `BG-003` land, because the repo currently has replay-capable telemetry/lineage infrastructure but no pinned `DatasetVersion` or decision-front object chain. (`Pantheon_Blueprint_Gap_Review_v1.md:546-597`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:526-533,626-638`)
- Slice 5: `BG-002` and `BG-006` should package existing research and operator truth without redefining the canonical backbone; `BG-004` and `BG-007` remain convergence-tail work. (`Pantheon_Blueprint_Gap_Review_v1.md:317-370,620-726`; `integrations/oss-002/regrade_report.md:187-224`; `services/control-plane/bff/BFF_SURFACE_INVENTORY.md:255-267`)

## Citations

- `ai-status.json:1468-2022` shows the governance/runtime/telemetry/evolution/persona/BFF/OSS backbone tasks are already done.
- `services/control-plane/specs/strategy_spec.schema.json:38-95` and `services/research/strategy_spec/README.md:27-31` show current market/data references are still research-first and free-form.
- `services/telemetry/telemetry_event.schema.json:7-117` proves the runtime/telemetry backbone already carries binding, plan, stage, and rollback references.
- `services/control-plane/persona/session_persona.schema.json:66-130` proves persona/session/runtime audit-chain fields already exist, which narrows GAP-04 to memory-layer work rather than persona-lifecycle work.
- `services/execution/runtime-manager/runtime_binding.schema.json:6-77` proves runtime binding authority and rollback lineage are already formalized.
- `services/control-plane/bff/BFF_SURFACE_INVENTORY.md:13-24,255-267`, `services/control-plane/bff/BFF_API_CONTRACT.md:15-37,219-263`, and `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md:15-23,99-149` prove GAP-06 is an acceptance-matrix gap, not a missing-surface gap.
- `integrations/oss-002/regrade_report.md:22-30,187-224` proves GAP-02 is a maturity-packaging gap: DSPy, imitation, and MLflow are implemented and smoke-tested, but not yet packaged as a full matrix or `governed` checklist set.
- Scoped `rg` searches on 2026-04-12 under `services/**`, `integrations/**`, and `support/**` returned no hits for the seven Data Plane objects, the five decision-front objects, memory-layer keywords, or golden replay artifacts, which confirms those gaps remain repo-local absences.
