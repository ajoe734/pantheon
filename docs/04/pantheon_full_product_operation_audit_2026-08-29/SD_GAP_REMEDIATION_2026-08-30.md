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

## 2. AST Top-Level Symbol Disposition

Every named symbol at top level in `services/control-plane/bff/main.py` is categorized into an explicit disposition:

- **`composition_keep`**: FastAPI app setup, CORS, lifespan, health endpoints (Owner: `OPGAP-BFF-MAIN-ASSEMBLY-20260830`).
- **`move_domain`**: Route handlers and domain-specific helpers moved into the 18 named domain routers.
- **`extract_shared_port`**: Cross-domain store interfaces, error codes, and auth utilities moved to `services/control-plane/bff/ports/` (Owner: `OPGAP-BE-PORT-NAMESPACE-CONSOLIDATION-20260830`).
- **`delete_dead`**: Legacy fallback stores (`read_store.py`, `ReadSurfaceStore`) deleted after zero-caller verification in `OPGAP-BFF-MAIN-ASSEMBLY-20260830`.

---

## 3. Frontend Residual Remediation Design (`execute-plans`)

### 3.1 Strict Live Mode & Dead Code Elimination
All residual mock fallbacks, seed references, and speculative write overlays in `execute-plans` at baseline commit `bd03c863e3c2c1c64b9b7797f27cefaf84df17c1` are eliminated across 4 disjoint task owners:
1. `OPGAP-FE-BUNDLE-CLEANUP-20260830`: Removes 37 residual files across locales, libraries, and mock utilities.
2. `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830`: Disables unsupported generic CRUD mutations and provides typed postmortem views.
3. `OPGAP-FE-AGORA-WORKSHOP-20260830`: Connects Agora strategy workshop and trading room to live backend contracts.
4. `OPGAP-FE-INTEGRATION-ASSEMBLY-20260830`: Assembles `App.tsx`, `ManagementLayout.tsx`, and `bff-v1/index.ts`.

---

## 4. Command Caller Cutover & Retirement Design

1. **Script Cutover** (`OPGAP-BE-COMMAND-CALLER-CUTOVER-20260830`):
   - `scripts/audit_management_bff_list_responses.py`
   - `scripts/bff_route_manifest_backend.py`
   - `scripts/run_command_idempotency_regression.py`
   All updated to call typed domain router modules directly.

2. **Command Plane Retirement** (`OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830`):
   - Deletes `command_executor.py`, `command_adapters/base.py`, `contract_snapshots/report_execute_plans_bff_coverage.py`, `reproduce_sse_gap.py`, `smoke_test.py`, `smoke_test_incident.py`.
   - Proves zero executable callers across the codebase.
