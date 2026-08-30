# Full Product Operation Audit & Remediation Plan (2026-08-30)

## Executive Summary

This document package provides the complete, root-cause System Architecture (SA), System Design (SD), and parallel Execution Task Catalog for remediating all 20 identified product operation gaps (**OP-G01** through **OP-G20**) across the **Pantheon** control plane and **execute-plans** desktop frontend repositories.

### Baseline Provenance
- **Pantheon Baseline Commit**: `d2bca5bc70bfae897e1ef3ca736ad3680a587679` (`origin/dev`)
- **Execute-Plans Baseline Commit**: `bd03c863e3c2c1c64b9b7797f27cefaf84df17c1` (`origin/dev`)
- **Hosted Environment**: Pair ID `6899d0daadb3dea2dbc3ae93456cf5818675dbd9a5c4284f676b80b5ce59c1a1`, Backend `e7f010dccee33185bc260d06048f09e6d2125f28`, Frontend `bd03c863e3c2c1c64b9b7797f27cefaf84df17c1`, Status `accepted` (accepted at `2026-08-30T06:28:46Z`).
- **Governed Command Runtime SHA**: `954caefa519ab89827b4d3030a511f2f7c73138a`

---

## Package Structure

1. **[CURRENT_GAP_DISPOSITION_2026-08-30.md](./CURRENT_GAP_DISPOSITION_2026-08-30.md)**
   Exhaustive disposition of all 20 operational gaps (OP-G01 to OP-G20) preserving original audit semantics, reconciling active, verify, closed, and in_progress states with direct code/deployment evidence, merging Finding F21 into OP-G08 and F24 into OP-G10, and documenting F22/F23/F25 unresolved exclusions.
2. **[SA_GAP_REMEDIATION_2026-08-30.md](./SA_GAP_REMEDIATION_2026-08-30.md)**
   Target System Architecture defining bounded context domain routing, single-namespace port consolidation (`ports/`), reverse import elimination, command executor retention, single-stimulus Source contract, and strict separation of development tooling vs product runtime.
3. **[SD_GAP_REMEDIATION_2026-08-30.md](./SD_GAP_REMEDIATION_2026-08-30.md)**
   Detailed System Design for all 18 domain routers (441 HTTP decorators across 421 unique route handlers), inventory of all 2,271 `main.py` top-level AST nodes with no Core catch-all, frontend residual cleanup, command caller cutover, and command plane retirement.
4. **[EXECUTION_DAG_2026-08-30.md](./EXECUTION_DAG_2026-08-30.md)**
   Acyclic multi-wave dependency graph, materialization batches (A, B, C, D), worker capability assignments, predecessor reconciliation (`AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830` terminal `done`), and capacity constraints (including capacity-1 `pantheon-dev` host).
5. **[EXECUTION_TASK_CATALOG_2026-08-30.json](./EXECUTION_TASK_CATALOG_2026-08-30.json)**
   Machine-checkable authoritative JSON catalog containing the 29 child tasks, route migration matrix, top-level AST node inventory, prior delivery dispositions, live-derived capacity, and embedded dynamic validation rules.

---

## Core Planning Rules & Guarantees

1. **Exact AST-Level Route & Symbol Migration**: All 2,271 top-level AST body nodes (1,719 function definitions across 438 async and 1,281 sync functions, 437 assignments across 375 Assign and 62 AnnAssign nodes, 80 imports across 66 ImportFrom and 14 Import nodes, 21 Expr nodes, 7 ClassDef nodes, 5 If nodes, and 2 Try nodes) in `main.py` are mapped to concrete domain owners or designated as pure composition root (`composition_keep`) with no Core catch-all.
2. **Zero Reverse Imports of `main.py`**: Reverse imports across BFF routers and production scripts (including `command_executor.py`, `identity`, and `personalization`) are eliminated by extracting shared contracts to `services/control-plane/bff/ports/`.
3. **Port Namespace Consolidation**: `services/control-plane/bff/ports/` is the sole public and implementation namespace. All 22 direct callers of `domain_ports` are migrated, the 6 `domain_ports/*.py` files are deleted, and no third compatibility namespace is introduced.
4. **Command Executor Preservation**: `command_executor.py` is retained as the production operator command executor, eliminating its reverse-main dependency while deleting dead legacy unrouted action adapters.
5. **Source Contract**: Strict `reconcile_only` default in development; live stimulus bounded to a single receipt contract (`source_proof_receipt_id`) binding `connectorId` + `ingestRunId` + `sourceId` + `snapshotId` pre-switch and read-only reuse post-switch.
6. **Clean Materialization Batches**:
   - **Batch A (Bootstrap)**: 1 task (`OPGAP-DEVTOOL-TARGET-REPO-BRIDGE-20260830`)
   - **Batch B (Domain Routers & Ports)**: 14 tasks
   - **Batch C (Support, Frontend & Controls)**: 9 tasks
   - **Batch D (Assembly, Cutover, Retirement & Promotion)**: 5 tasks
