# Target System Design for Operation Gap Remediation (2026-08-30)

## 1. Domain Router Specifications & Exact Route Breakdown

The monolithic `services/control-plane/bff/main.py` contains exactly **441 HTTP route decorators** across **421 unique route handlers**. In the target design, all 441 decorators and 421 handlers are partitioned into 18 domain routers under `services/control-plane/bff/`:

| Domain Router | Module Path | Decorator Count | Handler Count | Primary Owner Task | Key Responsibilities |
|---|---|---:|---:|---|---|
| **Core & Auth** | `services/control-plane/bff/auth/router.py` | 30 | 29 | `OPGAP-BE-BFF-CORE-20260830` | Session tokens, dev login, health probes, CORS middleware, provider decoupling. |
| **Persona Management** | `services/control-plane/bff/personas/router.py` | 49 | 45 | `OPGAP-BE-PERSONA-ROUTER-20260830` | Persona registry, durable listing, parameter schema definitions, profiles. |
| **Training & FinRL** | `services/control-plane/bff/training/router.py` | 14 | 14 | `OPGAP-BE-TRAINING-ROUTER-20260830` | Training pipeline jobs, checkpoints, FinRL experiment lineage. |
| **Agora Research** | `services/control-plane/bff/agora/router.py` | 38 | 26 | `OPGAP-BE-AGORA-ROUTER-20260830` | Suggestion lifecycle, candidate truth gating, parameter search, trading data. |
| **Research & Synthesis** | `services/control-plane/bff/research/router.py` | 47 | 45 | `OPGAP-BE-RESEARCH-ROUTER-20260830` | Research datasets, analyst feeds, multi-persona synthesis logs. |
| **Governance & Approvals** | `services/control-plane/bff/governance/router.py` | 35 | 34 | `OPGAP-BE-GOVERNANCE-ROUTER-20260830` | Approval journal, operator review decisions, policy rules, voting records. |
| **Evolution Engine** | `services/control-plane/bff/evolution/router.py` | 13 | 13 | `OPGAP-BE-EVOLUTION-ROUTER-20260830` | Evolution program runs, fitness evaluations, mutation candidate metrics. |
| **Capital Allocation** | `services/control-plane/bff/capital/router.py` | 25 | 25 | `OPGAP-BE-CAPITAL-ROUTER-20260830` | Capital pool balances, risk limits, portfolio rebalances, sleeve allocations. |
| **Strategy & Ranking** | `services/control-plane/bff/strategies/router.py` | 31 | 31 | `OPGAP-BE-STRATEGY-RANKING-20260830` | Strategy registry, ranking formulas, leaderboard read models. |
| **Management System** | `services/control-plane/bff/management_read_models/router.py` | 17 | 17 | `OPGAP-BE-MANAGEMENT-ROUTER-20260830` | Portfolio book, positions, execution orders, natural language query proxy. |
| **Postmortem System** | `services/control-plane/bff/postmortems/router.py` | 2 | 2 | `OPGAP-BE-POSTMORTEM-ROUTER-20260830` | Root cause diagnostics, postmortem catalog, incident linkage. |
| **Incident Response** | `services/control-plane/bff/incidents/router.py` | 27 | 23 | `OPGAP-BE-INCIDENT-ROUTER-20260830` | Active incident alerts, severity transitions, mitigation tracking. |
| **Events & Bus** | `services/control-plane/bff/events/router.py` | 14 | 14 | `OPGAP-BE-EVENTS-ROUTER-20260830` | SSE channel subscriptions, outbox event streaming, notification dispatch. |
| **Tools & Integrations** | `services/control-plane/bff/integrations/router.py` | 35 | 33 | `OPGAP-BE-TOOLS-INTEGRATIONS-20260830` | MCP server/tool facades, system diagnostics, third-party connectors. |
| **Control Loops** | `services/control-plane/bff/control_loops/router.py` | 24 | 21 | `OPGAP-BE-CONTROL-LOOPS-20260830` | Loop trigger execution, schedule definitions, cross-plane dispatch. |
| **Command Adapters** | `services/control-plane/bff/command_adapters/router.py` | 11 | 11 | `OPGAP-BE-COMMAND-ADAPTERS-20260830` | Action catalog, operator command endpoints, typed execution adapters. |
| **Runtime Binding** | `services/control-plane/bff/runtime/router.py` | 17 | 17 | `OPGAP-BE-RUNTIME-BINDING-20260830` | Environment configs, market policy projections, loader runtime bindings. |
| **Deployments & Rollback** | `services/control-plane/bff/deployment/router.py` | 12 | 12 | `OPGAP-DEPLOY-RELIABILITY-20260830` | Release manifest read models, environment health, sealed rollback triggers. |
| **Total / Decomposed** | *18 Domain Routers* | **441** | **421** | — | *Note: 1 generic handler (`sem_final_generic_read_alias`) decomposes into 3 typed domain handlers across Governance & Research, assembled by `OPGAP-BFF-MAIN-ASSEMBLY-20260830`.* |

---

## 2. Minimal Composition Root Allowlist & AST-Level Migration

All **2,271 top-level AST body nodes** in `services/control-plane/bff/main.py` are mapped in `EXECUTION_TASK_CATALOG_2026-08-30.json` with cryptographic AST digests (`ast_digest`), source segment hashes (`source_segment_hash`), 100% non-empty rationales, and edge-level cutover mappings for all consuming tasks:

1. **Minimal Composition Root Allowlist (`composition_keep`)**:
   - Explicit minimal allowlist governing `main.py` assembly under `OPGAP-BFF-MAIN-ASSEMBLY-20260830`:
     - Framework setup (FastAPI app instance initialization)
     - Lifespan context manager (`@asynccontextmanager async def lifespan(app: FastAPI)`) containing startup DB initialization, directory creation (relocated **Node 118** `os.makedirs(BFF_DATA_DIR, exist_ok=True)`), and graceful shutdown logic
     - CORS middleware, trace ID injection, and bearer auth token verification middlewares
     - Explicit inclusion of all 18 domain routers (`app.include_router(auth_router)`, etc.)
     - Root composition logger (`logging.getLogger("pantheon.bff.main")`)
   - **Target Invariant**: Zero inline route handlers, zero side effects outside lifespan, and zero reverse imports of `main.py`. Domain routers instantiate composition-local loggers (`logging.getLogger(__name__)`) and never import `main.py`'s `log`.
2. **Domain Movement (`move_domain`)**:
   - Relocated into their respective domain routers or ports modules (`ports/`).
   - Shared symbols (e.g. `_resolve_param`, `_REPO_ROOT`, `_CRON_SERVICE_DIR`, `_stable_json_hash`, `_snapshot_meta`) have explicit `consumer_cutover_mapping` entries specifying the exact target import path for each consuming task.
   - Standard library imports are never classified as `extract_shared_port`.
3. **Legacy Action Adapter Cluster**: **9 nodes** (Nodes 764, 765, 769, 2018, 2019, 2020, 2021, 2022, 2027)
   - Live AST call edges connect this cluster internally:
     - `_action_adapter_spec` (2019) calls `_normalize_action_adapter_entity_type` (2018)
     - `_action_adapter_command_payload` (2021) calls `_normalize_action_adapter_entity_type` (2018), `_action_adapter_spec` (2019), `_action_adapter_audit_event` (2020)
     - `_submit_canonical_action_command` (2027) calls `_legacy_action_deprecation_notice` (764), `_apply_legacy_action_deprecation_headers` (765), `_action_adapter_command_payload` (2021)
   - Proven 0 production root callers across all 18 domain routers and production scripts; typed domain commands supersede this legacy path.
   - Retired and eliminated from `main.py` during composition root assembly in `OPGAP-BFF-MAIN-ASSEMBLY-20260830`.

---

## 3. Scoped External Reverse-Main Import Inventory (269 Qualified Instances, 94 Exclusions)

Across the repository, exactly **269 qualified external import instances** spanning **214 unique caller files** import directly from BFF `main.py`. The catalog maps every instance to its target port module or domain router:

- **Command Contracts & Constants (`ports/command_contracts.py`)**: `CommandType`, `ErrorCode`, `ObjectType`, `STATUS_CLAIMED`, `STATUS_DEGRADED`, `STATUS_FAILED`, `STATUS_PROCESSED`, `STATUS_PROPOSED`, `TargetObject`, `_dry_run_success_response`, `_reject_body_idempotency_key`, `_request_dry_run_requested`, `_resolve_final_idempotency_key`, `_sem_list_payload`.
- **Storage & State Contracts (`ports/storage.py`)**: `_read_surface_meta`, `_stable_json_hash`, `get_store`, `inbox_store`, `outbox_store`, `store`.
- **Authentication & Tokens (`ports/auth.py` & `auth/router.py`)**: `controller_token`, `load_controller_token`, `CONTROLLER_TOKEN_PATH`, `_extract_identity`, `_extract_identity_jwt`, `_extract_identity_stub`, `_require_operator_role`.
- **Configuration Constants (`ports/config.py`)**: `_REPO_ROOT`, `_CRON_SERVICE_DIR`, `CONTROLLER_TOKEN_PATH`.
- **Domain-Specific Helpers (`agora/router.py`, `incidents/router.py`, `postmortems/router.py`, `personas/router.py`, `capital/router.py`, `integrations/router.py`, `events/router.py`)**: `_agora_action_command`, `_agora_ask_deterministic_fallback`, `_agora_core_idempotency_check`, `_agora_get_insight`, `_agora_list_response`, `_agora_required_text`, `_sem_agora_inbox_payload`, `process_incidents_outbox`, `reconcile_incidents_outbox`, `process_postmortems_outbox`, `reconcile_postmortems_outbox`, `_build_persona_health_items`, `_trading_performance_delta`, `_assistant_ask_enabled`, `_assistant_build_context_pack`, `_publish_event`.

**Context-Aware Exclusions**: Exactly 94 unrelated import instances across 55 files (importing other service main modules such as `services.search.main`, `services.incidents.main`, `services.consultation.main`, `services.source_ingestion.main`, or local service `main.py` files) are explicitly classified as non-BFF exclusions with detailed rationales in `external_reverse_main_symbol_inventory.ambiguous_and_unrelated_exclusions`.

All qualified caller sites are updated in Batch B and Batch C tasks, completely freeing `main.py` from reverse dependencies before final assembly.

---

## 4. Port Namespace Consolidation & Classified Caller Inventory (191 Imported-Symbol Rows across 22 Unique Files)

1. **Sole Namespace**: `services/control-plane/bff/ports/` is the sole public and implementation interface for all BFF shared capabilities (`telemetry.py`, `storage.py`, `auth.py`, `command_contracts.py`, `param_utils.py`, `config.py`).
2. **Classified Caller Inventory**:
   - **Executable Production Callers (129 imported-symbol rows across 7 unique files)**: Domain routers, ports modules, and background workers importing domain ports are migrated to `ports/`.
   - **Automated Test Callers (62 imported-symbol rows across 15 unique files)**: Unit and contract tests (e.g. `test_cw01_*.py`, `test_cw03_*.py`, `test_bff_b3_*.py`, `test_lifecycle_*.py`) are updated to import from `ports/`.
3. **Permanent Deletion**: The entire directory `services/control-plane/bff/domain_ports/` (6 files: `lifecycle_telemetry_governance.py`, `ooda_management.py`, `operations_consultation.py`, `persona_capital_runtime.py`, `persona_training.py`, `research_knowledge_source.py`) is deleted in `OPGAP-BE-PORT-NAMESPACE-CONSOLIDATION-20260830`.
4. **Zero-Shim Rule**: Forwarding shims and backward-compatibility aliases are strictly forbidden. Rollback procedures never restore deleted shims.

---

## 5. Frontend Residual Cleanup & Bundle Dependency Graph Gates

Residual frontend files are categorized according to graph reachability from `src/main.tsx` in `ajoe734/execute-plans`:

1. **`retain_and_clean` (Live Reachable Files)**: Live assets such as `en-US.ts`, `zh-TW.ts`, `client.ts`, `liveTransport.ts`, `personas.ts`, `capitalPools.ts`, `lists.ts`, `management.ts`, `managementConsoleReads.ts`, `managementDataSources.ts`, `tradeJournal.ts`, `v5.ts`, `writes.ts`, `createEntity.ts`, `ObjectListPage.tsx`, `PersonaOnboarding.tsx`, `index.ts` are retained and cleaned of mock/overlay reachability.
2. **`delete_after_zero_reachability` (Unreachable Mock Files)**: Truly dead mock files (`src/mocks/strictLiveFixtureUnavailable.ts`, `src/lib/bff-v1/mocks/adapters.ts`, `src/lib/bff-v1/mocks/registry.ts`) are deleted once bundle isolation is proven.
3. **`move_test_only`**: `src/mocks/seed.ts` is isolated exclusively to test runners.
4. **`already_absent_delivered_paths`**: 17 obsolete paths delivered in previous PRs are recorded as verified absent.
5. **Bundle Dependency Graph Gate**: Automated analyzer `scripts/check_bundle_mock_reachability.mjs` enforces zero reachability from production entrypoint to mocks or `writeOverlay`.

---

## 6. Command Plane Retirement & Central Command Authority

1. **Authoritative Dispatcher**: `command_executor.py` is retained as the authoritative production operator command dispatcher. Its reverse imports from `main.py` are replaced with imports from `ports/`.
2. **Caller Cutover**: All scripts and background workers calling legacy unrouted command endpoints are migrated to canonical domain endpoints in `OPGAP-BE-COMMAND-CALLER-CUTOVER-20260830`.
3. **Dead Shim Deletion**: Dead action adapter base files (`command_adapters/base.py`, `report_execute_plans_bff_coverage.py`, `reproduce_sse_gap.py`, `smoke_test.py`, `smoke_test_incident.py`) are deleted in `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830`.
4. **Clean Non-Overlapping Ownership**: `services/control-plane/bff/main.py` and `read_store.py` are exclusively owned and assembled by `OPGAP-BFF-MAIN-ASSEMBLY-20260830`.

---

## 7. Implementation-Ready Contract Dimensions

Every child task in `EXECUTION_TASK_CATALOG_2026-08-30.json` adheres to strict structural contracts across nine dimensions:

1. **Owned Code Surfaces (`owned_code_surfaces`)**: Exclusive repository file and directory boundaries declared per task to prevent overlapping write collisions. Zero duplicate owned surfaces across all 30 tasks.
2. **Contract Specification (`contract`)**: Interface, schema, API endpoint, and data-flow specifications governing the task's functional delivery.
3. **State Transitions (`state_transitions`)**: Explicit operational and lifecycle phase progressions enacted by the task.
4. **Focused Test Suites (`tests`)**: Concrete automated verification commands and test files verifying the unit's acceptance.
5. **Migration Operations (`migration`)**: Precise caller cutover, symbol relocation, and schema evolution steps.
6. **Zero-Caller Deletion (`deletion`)**: Exhaustive non-empty inventory of removed dead adapters, obsolete shims, and legacy inline `main.py` handlers/helpers backed by AST caller proofs.
7. **Fail-Closed Rollback (`rollback`)**: Explicit automated and manual forward repair or previous release artifact rollback procedures, never restoring deleted shims or duplicate handlers.
8. **Durable Readback Boundaries (`durable_readback`)**: Cryptographic hashes, entity IDs, and projection checkpoints verifying end-to-end truth persistence.
9. **Canonical Task Spec Hash Binding (`spec_hash`)**: Post-bootstrap tasks explicitly bind `acceptance`, `artifacts`, `delivery_repository`, `dependency_tracks`, `depends_on`, `execution_resources`, `id`, `owner`, `phase`, `reviewer`, `summary`, `target_repo`, `task_class`, and `title`.
