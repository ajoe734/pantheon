# Execution Materialization

Status: draft — Codex materialization refresh

Source rules:

- Owners, reviewers, and hard dependencies come from `planning-session.json:191-308`.
- Wave order follows the blueprint priority table in `Pantheon_Blueprint_Gap_Review_v1.md:714-726`.
- The baseline assumption is that the governance/runtime/telemetry/persona/BFF/OSS backbone is already done and stays out of scope for this wave. (`ai-status.json:1468-2022`)

## Session Bootstrap

| Task ID | Owner | Reviewer | Depends On | Purpose |
|---|---|---|---|---|
| `PLAN-002` | Codex | Claude | - | Generalize discussion planning into a reusable session-driven runtime before the blueprint-gap task family is materialized into long-lived execution work. (`planning-session.json:193-202`) |

## Wave 0 / P0 Foundations

| Task ID | Owner | Reviewer | Depends On | Materialize As | Acceptance focus |
|---|---|---|---|---|---|
| `BG-000` | Codex | Gemini | `PLAN-002` | Market scope and source-policy package | Publish `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md`, `DATA_SOURCE_SCOPE_MATRIX.md`, and `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md`; answer the 10 market-data questions and define per-market paper/canary/live eligibility. (`planning-session.json:203-215`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:609-638`) |
| `BG-001` | Qwen | Codex | `PLAN-002` | Data Plane schemas and replay identifiers | Formalize `SecurityMaster`, `ContractMaster`, `MarketCalendarSession`, `RawDataset`, `NormalizedDataset`, `FeatureDataset`, and `DatasetVersion`, plus `event_time` / `available_time` / `ingest_time` discipline and dataset replay contract. (`planning-session.json:217-228`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:358-447,472-533`) |
| `BG-003` | Qwen | Claude | `PLAN-002` | Decision-front provenance schemas | Formalize `RegimeState`, `UniverseSelection`, `SignalInference`, `AllocationDecision`, and `RiskAdjudication`, and make explicit which current logic remains research input versus first-class decision provenance. (`planning-session.json:243-254`; `Pantheon_Blueprint_Gap_Review_v1.md:430-447`) |

## Wave 1 / P0 Acceptance Gate

| Task ID | Owner | Reviewer | Depends On | Materialize As | Acceptance focus |
|---|---|---|---|---|---|
| `BG-005` | Codex | Qwen | `BG-000`, `BG-001`, `BG-003` | Golden replay scenario + runbook | Pin real dataset/master/decision refs, fixed `DeploymentPlan`/`RuntimeBinding`, and expected telemetry/incident/evolution outputs. Acceptance must include at least one equities path and one derivatives-aware path. (`planning-session.json:269-282`; `Pantheon_Blueprint_Gap_Review_v1.md:578-594`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:626-638`) |

## Wave 2 / P1 Packaging

| Task ID | Owner | Reviewer | Depends On | Materialize As | Acceptance focus |
|---|---|---|---|---|---|
| `BG-002` | Qwen | Gemini | `PLAN-002` | Research backend maturity matrix | Publish a backend × role × maturity × owner × strategy-family matrix that distinguishes production research path from smoke-tested or deferred paths. Do not expand scope into checklist-format doc hardening. (`planning-session.json:230-241`; `integrations/oss-002/regrade_report.md:187-224`) |
| `BG-006` | Qwen | Claude | `PLAN-002` | Operator acceptance matrix | Consolidate the existing BFF inventory, API contract, degraded path, permissions, and drill status into one sign-off artifact without inventing new surfaces or shadow state. (`planning-session.json:284-295`; `services/control-plane/bff/BFF_SURFACE_INVENTORY.md:255-267`; `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md:99-149`) |

## Wave 3 / P2 Convergence Tail

| Task ID | Owner | Reviewer | Depends On | Materialize As | Acceptance focus |
|---|---|---|---|---|---|
| `BG-004` | Claude | Codex | `PLAN-002` | Memory-layer design note | Define persona memory, institutional memory, retrieval contract, and write-back triggers on top of the existing persona/lineage/incident backbone. (`planning-session.json:256-267`; `Pantheon_Blueprint_Gap_Review_v1.md:506-519`) |
| `BG-007` | Codex | Claude | `PLAN-002` | Product/operator language pack | Publish glossary, action→object mapping, and stage/status wording on top of the canonical object and surface language already stabilized by `BG-006`. (`planning-session.json:297-308`; `Pantheon_Blueprint_Gap_Review_v1.md:701-708`) |

## Materialization Rules

- `BG-000`, `BG-001`, and `BG-003` are parallelizable only after `PLAN-002` exists as the authoritative planning-runtime precondition. Drafting work can start earlier, but execution materialization should preserve the hard dependency declared in `planning-session.json:203-254`.
- `BG-001` and `BG-003` example payloads must consume `BG-000` vocabulary once it exists; they should not lock in new free-form symbol strings or market enums that bypass the market-scope policy. (`services/control-plane/specs/strategy_spec.schema.json:38-69`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:482-533`)
- `BG-005` must not start as a telemetry-only replay. It only closes GAP-05 if it pins real `DatasetVersion` refs and real decision-front object refs. Due to `EX-001` deferral, the LEAN execution segment of the replay may rely on synthetic/mocked feedback for the first wave. The replay must also verify event persistence in the durable storage layer (Postgres/Redis) to ensure reproducibility. (`Pantheon_Blueprint_Gap_Review_v1.md:549-594`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:526-533`; `services/execution/artifact-loader/contract.md` §6)
- `BG-002` ends at the matrix + production-path mapping boundary; documentation hardening remains explicit follow-on work after the matrix closes. (`planning-session.json:185-189`; `integrations/oss-002/regrade_report.md:218-224`)
- `BG-006` must package existing BFF/operator evidence rather than redesign operator surfaces that are already marked done. (`ai-status.json:1905-1958`; `services/control-plane/bff/BFF_API_CONTRACT.md:15-37,219-290`)
- After human acceptance, convert the proposed wave into execution tasks via `scripts/planning-state.sh materialize`, as required by the phase2 README. (`docs/02-architecture/consensus/phase2/README.md:45-60`)
