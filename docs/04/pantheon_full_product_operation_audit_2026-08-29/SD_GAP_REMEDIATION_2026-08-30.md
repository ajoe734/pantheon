# Target System Design for Operation Gap Remediation (2026-08-30)

## 1. Frozen Domain Router Specifications & Exact Route Breakdown

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

All **2,272 top-level AST body nodes** in `services/control-plane/bff/main.py` are mapped in `EXECUTION_TASK_CATALOG_2026-08-30.json` with cryptographic AST digests (`ast_digest`), source segment hashes (`source_segment_hash`), 100% non-empty rationales, and edge-level cutover mappings for all consuming tasks:

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

## 3. Scoped External Reverse-Main Import Inventory (270 Qualified Instances, 94 Exclusions)

Across the repository, exactly **270 qualified external import instances** spanning **215 unique caller files** import directly from BFF `main.py`. The catalog maps every instance to its target port module or domain router:

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

---

## 8. Dev-Bridge Dispatcher Allowlist Design (Errata, 2026-08-30)

Design note for `.orchestrator/development_bridge/dev_bridge_dispatcher.py`, not a product BFF/frontend component.

**Observed defect**: the dispatcher's repository allowlist check is `Packet constraint allowedRepos contains unconfigured repositories: <repo>`, sourced from an operator-supplied `PANTHEON_ASSISTANT_DEV_BRIDGE_ALLOWED_REPOS` environment value rather than the live supervisor's `coordination.repositories` registry. Two further admission checks in the same dispatch path also require exact operator/runtime identity: `PANTHEON_TASK_STATE_STORE_MODE=authoritative is required for dev bridge dispatch` and `PANTHEON_COMMAND_RUNTIME_SHA is required with PANTHEON_COMMAND_ROOT`.

**Concrete effect on Batch B/C materialization** (exact evidence in [EXECUTION_REPLACEMENT_LEDGER_2026-08-30.json](./EXECUTION_REPLACEMENT_LEDGER_2026-08-30.json) `fail_closed_evidence`):
- Batch C's first drain attempt (packet `...51d9f9ef...`) was rejected outright for the unconfigured `execute-plans` repository.
- Two subsequent attempts (`...a7d3f578...`, `...82e9abb1...`) failed the command-runtime identity checks before the allowlist was ever reached.
- The fourth attempt (`...ead79e62...`) succeeded once both the allowlist and the runtime identity bindings were corrected, admitting all 9 Batch C tasks in one packet.
- Because 14 Batch B task rows had already materialized against the stale allowlist state, and canonical task rows cannot be amended in place, each was retired via `supersede` and replaced one-to-one under a `-V2-20260830` id bound to the corrected `OPGAP-DEVTOOL-TARGET-REPO-BRIDGE-V2-20260830` dependency.

**Delivered design and evidence-contract lineage**: PR #5459 implemented direct `coordination.repositories` derivation and alias normalization. The implementation's V3 task lacked an allowed immutable evidence-manifest artifact; `OPGAP-DEVTOOL-BRIDGE-REPO-ALLOWLIST-V4-20260830` preserved the functional scope, added that contract, and merged as `5b0d02196acfc9c3ef956ae4c47865601bc43da6` in PR #5473. The dispatcher still fail-closed rejects a repository absent from the live registry, and mixed-repository packets require a promoted command runtime containing V4.

---

## 9. Post-Freeze Execution Design Addendum

### 9.1 Evidence-Contract Replacement Lineage

The following replacements correct immutable task contracts without changing functional design:

| Superseded task | Governed replacement | Functional change | Evidence completion |
|---|---|---|---|
| `OPGAP-BE-TRAINING-ROUTER-V2-20260830` | `OPGAP-BE-TRAINING-ROUTER-V3-20260830` | None | PR #5474 head `6541c4cbedad8451602291707a236b62962075f5`, merge `fb55131864957a5ede398164e6ee060da1e0dead`, immutable V3 manifest |
| `OPGAP-DEVTOOL-BRIDGE-REPO-ALLOWLIST-V3-20260830` | `OPGAP-DEVTOOL-BRIDGE-REPO-ALLOWLIST-V4-20260830` | None | PR #5473 head `f0e1481a1b07c95ec0800a9f93ac99da2ccbe46b`, merge `5b0d02196acfc9c3ef956ae4c47865601bc43da6`, immutable V4 manifest |

The original nine Batch C ids also have one-to-one `-V2-20260830` governed replacements. The V2 ledger's direct-materialization entries remain historical admission truth; the post-freeze replacement array is the current execution truth.

### 9.2 Persona Provisioning Reconciliation Mutation Port (OP-G21)

`ReadSurfacePorts` exposes reads only. It must not declare, dynamically delegate, or receive calls to `update_persona`. Reconciliation receives two independently injected capabilities:

1. a read port that loads the current Persona identity/version and verifies projection readback;
2. a typed mutation/command port backed by the authoritative Persona store that persists only explicit terminal transitions (`provisioning`, `provisioning_failed`) with expected-version, correlation, and failure-reason fields.

The mutation returns a same-ID/version receipt. Reconciliation then reads through the read port and fails closed on identity or version mismatch. Existing lifecycle compensation, cron cleanup, and overlay presentation may consume the resulting projection but cannot become alternate write authorities. Focused tests must first reproduce `ReadSurfacePorts object has no attribute update_persona`, then prove mutation-port persistence and zero startup warning.

### 9.3 OpenClaw Provider Readiness Fallback (OP-G22)

The adapter state machine is:

```text
configured candidates -> partitioned exact-sentinel readiness probes
                      -> retain first proven active model
                      -> one single-attempt invoke
                      -> completed | typed fail-closed outcome
```

Required behavior:

- normalize the readiness reply and require exact equality with `PANTHEON_PROVIDER_READY`;
- reserve bounded time for every remaining configured candidate instead of allowing the primary to consume the whole budget;
- retain sanitized `primary_unavailable` evidence such as `OPENCLAW_GATEWAY_TIMEOUT` without secrets;
- use the already-probed active model for the next default-agent invoke while preserving per-agent routing when no model override was requested;
- never retry invoke on unproven auth errors, timeouts, cancellations, post-execution errors, or generic invocation failures;
- keep OpenResponses streaming on the upstream `model=openclaw` contract;
- leave credentials, secrets, provider priority, and operator login repair outside source authority.

### 9.4 Exact Read-Only Deployment and Product-Proof Gate

The post-freeze release evidence binds these immutable identities:

| Evidence | Outcome | Bound identity |
|---|---|---|
| Pantheon controller run `33332882810` | `failure` | BFF `cbf4e0a7303de1c0e9a51614c99ac2d8ddd96cfe`; failed OpenClaw smoke |
| execute-plans integration gate `33334694659` | `success` | FE `7d30e78476be61222af63a089e7ab141aa43b809`; candidate `9122d0fecd5cf9d5ae574c4c5e802df1d336dd2fd778a54019d2ad4995a2843d` |
| execute-plans switch `33335314834` | `success` | pair `b33741b326b82dea85a647d812bf75880cf7e7e97f0a012cc5375e84ea2f5f21`; profile `read-only`; real/stub writes false |

The switch workflow skipped both the bounded authenticated Persona proof and independent same-pair restore jobs. The accepted design therefore treats this as read-only deployment evidence only. Full hosted product proof requires a parent-bound Firebase/BFF mutation window with `proof_window_ack`, an armed watchdog, immutable child receipts, and an independently verified restore of the same pair to read-only.
