# System Design: Full Product Operation Gap Remediation (2026-08-30)

## 1. Route Handler Decomposition & Migration Design

### 1.1 Overview
- **Total `main.py` HTTP Decorators**: 441 decorators
- **Unique Route Handlers**: 421 handlers
- **Handler Migration Dispositions**: 420 `move_as_unit`, 1 `decompose_generic` (`sem_final_generic_read_alias`).

### 1.2 Decomposed Generic Read Handler Design
The legacy catch-all read handler `sem_final_generic_read_alias` is decomposed into 3 strongly typed domain endpoints:
1. `GET /api/v1/governance/approvals/{param}` -> `get_approval_detail` in `services/control-plane/bff/governance/router.py` (Owner: `OPGAP-BE-GOVERNANCE-ROUTER-20260830`)
2. `GET /api/v1/research/artifacts/{param}` -> `get_research_artifact` in `services/control-plane/bff/research/router.py` (Owner: `OPGAP-BE-RESEARCH-ROUTER-20260830`)
3. `GET /api/v1/research/analyses` -> `list_research_analyses` in `services/control-plane/bff/research/router.py` (Owner: `OPGAP-BE-RESEARCH-ROUTER-20260830`)

Assembly dependency: `OPGAP-BFF-MAIN-ASSEMBLY-20260830` deletes `sem_final_generic_read_alias` only after both domain owners have merged their typed implementations.

---

## 2. AST Top-Level Node Inventory (All 2,271 Nodes)

Every top-level AST body node in `services/control-plane/bff/main.py` (2,271 nodes total across 68,304 lines) is inventoried and categorized into an explicit domain owner with no Core catch-all:

- **1,719 Function Definitions**:
  - `FunctionDef` (1,281 nodes): Domain helpers and synchronous route endpoints moved to respective domain routers.
  - `AsyncFunctionDef` (438 nodes): Asynchronous route endpoints and lifespan handlers moved to domain routers or preserved in composition root.
- **437 Assignments**:
  - `Assign` (375 nodes) & `AnnAssign` (62 nodes): Constant definitions, router instantiations, and domain store definitions.
- **80 Imports**:
  - `ImportFrom` (66 nodes) & `Import` (14 nodes): Module imports migrated to domain router files or consolidated in `ports/`. Zero standard library imports classified as `extract_shared_port`.
- **21 Expression Nodes** (`Expr`): Module docstrings and router registrations.
- **7 Class Definitions** (`ClassDef`): Pydantic models and request schemas migrated to domain schemas/ports.
- **5 Conditional Blocks** (`If`): Configuration checks and debug initialization.
- **2 Exception Handlers** (`Try`): Safe startup hooks and optional dependency loading.

---

## 3. Frontend Residual Remediation Design (`execute-plans`)

### 3.1 Strict Live Mode & Dead Code Elimination
All residual mock fallbacks, seed references, and speculative write overlays in `execute-plans` at baseline commit `bd03c863e3c2c1c64b9b7797f27cefaf84df17c1` are eliminated across 4 disjoint task owners:
1. `OPGAP-FE-BUNDLE-CLEANUP-20260830`: Removes 37 residual files across locales, libraries, and mock utilities.
2. `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830`: Disables unsupported generic CRUD mutations and provides typed postmortem views.
3. `OPGAP-FE-AGORA-WORKSHOP-20260830`: Connects Agora strategy workshop and trading room to live backend contracts.
4. `OPGAP-FE-INTEGRATION-ASSEMBLY-20260830`: Assembles `App.tsx`, `ManagementLayout.tsx`, and `bff-v1/index.ts` with exclusive ownership.

---

## 4. Command Caller Cutover & Command Executor Preservation

### 4.1 Command Executor Retention
- `services/control-plane/bff/command_executor.py` is **retained** as the production operator command executor.
- Reverse import of `main.py` (`import main as bff_main`) is removed by migrating shared models and store interfaces to `services/control-plane/bff/ports/` (Owner: `OPGAP-BE-COMMAND-ADAPTERS-20260830`).

### 4.2 Script Cutover (`OPGAP-BE-COMMAND-CALLER-CUTOVER-20260830`)
- `scripts/audit_management_bff_list_responses.py`
- `scripts/bff_route_manifest_backend.py`
- `scripts/run_command_idempotency_regression.py`
- `scripts/benchmark_bff_main_startup.py`
All updated to call typed domain router modules directly without reverse dependencies on `main.py`.

### 4.3 Command Plane Retirement (`OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830`)
- Deletes dead generic action adapter `_execute_bff_action_adapter` and unreferenced legacy compatibility files:
  - `services/control-plane/bff/command_adapters/base.py`
  - `services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py`
  - `services/control-plane/bff/reproduce_sse_gap.py`
  - `services/control-plane/bff/smoke_test.py`
  - `services/control-plane/bff/smoke_test_incident.py`
- Proves zero executable callers across the codebase while retaining `command_executor.py`.

---

## 5. Implementation-Ready SD Units Specification

Every child task in `EXECUTION_TASK_CATALOG_2026-08-30.json` adheres to strict implementation-ready structural contracts across eight dimensions:

1. **Owned Code Surfaces (`owned_code_surfaces`)**: Exclusive repository file and directory boundaries declared per task to prevent overlapping write collisions. Zero duplicate owned surfaces across all 29 tasks.
2. **Contract Specification (`contract`)**: Interface, schema, API endpoint, and data-flow specifications governing the task's functional delivery.
3. **State Transitions (`state_transitions`)**: Explicit operational and lifecycle phase progressions enacted by the task.
4. **Focused Test Suites (`tests`)**: Concrete automated verification commands and test files verifying the unit's acceptance.
5. **Migration Operations (`migration`)**: Precise caller cutover, symbol relocation, and schema evolution steps.
6. **Zero-Caller Deletion (`deletion`)**: Exhaustive non-empty inventory of removed dead adapters, obsolete shims, and legacy inline `main.py` handlers/helpers backed by AST and grep caller proofs.
7. **Fail-Closed Rollback (`rollback`)**: Explicit automated and manual forward repair or previous release artifact rollback procedures, never restoring deleted shims or duplicate handlers.
8. **Durable Readback Boundaries (`durable_readback`)**: Cryptographic hashes, entity IDs, and projection checkpoints verifying end-to-end truth persistence.\n