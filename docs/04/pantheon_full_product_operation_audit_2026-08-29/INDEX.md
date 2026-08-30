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
   Detailed System Design for all 18 domain routers (441 HTTP decorators across 421 unique route handlers), inventory of all 2,271 `main.py` top-level AST nodes with zero stdlib extract_shared_port, non-empty router deletion inventories, frontend residual cleanup, command caller cutover, and command plane retirement.
4. **[EXECUTION_DAG_2026-08-30.md](./EXECUTION_DAG_2026-08-30.md)**
   Acyclic multi-wave dependency graph, materialization batches (A, B, C, D), worker capability assignments, predecessor reconciliation (`AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830` terminal `done`), live-derived capacity, and capacity-1 `pantheon-dev` host constraint.
5. **[EXECUTION_TASK_CATALOG_2026-08-30.json](./EXECUTION_TASK_CATALOG_2026-08-30.json)**
   Machine-checkable authoritative JSON catalog containing the 29 child tasks with zero duplicate owned surfaces, route migration matrix, top-level AST node inventory, prior delivery dispositions, live-derived capacity, and embedded dynamic validation rules.

---

## Core Planning Rules & Guarantees

1. **Exact AST-Level Route & Symbol Migration**: All 2,271 top-level AST body nodes in `main.py` are mapped to concrete domain owners, pure composition root (`composition_keep`), real port abstractions in `ports/`, or dead deletion (`delete_dead`). Standard library imports are never classified as `extract_shared_port`.
2. **Zero Reverse Imports of `main.py`**: Reverse imports across BFF routers and production scripts (including `command_executor.py`, `identity`, and `personalization`) are eliminated by extracting shared contracts to `services/control-plane/bff/ports/`.
3. **Port Namespace Consolidation**: `services/control-plane/bff/ports/` is the sole public and implementation namespace. All 22 direct callers of `domain_ports` are migrated, the 6 `domain_ports/*.py` files are deleted, and rollback never restores deleted forwarding shims.
4. **Command Executor Preservation**: `command_executor.py` is retained as the production operator command executor, eliminating its reverse-main dependency while deleting dead legacy unrouted action adapters.
5. **Single-Receipt Source Contract**: Strict `reconcile_only` default in development; live stimulus bounded to a single receipt contract (`source_proof_receipt_id`) binding `connectorId` + `ingestRunId` + `sourceId` + `snapshotId` pre-switch and read-only reuse post-switch with zero second egress.
6. **Exclusive Artifact & Surface Ownership**: Zero duplicate `owned_code_surfaces` across all 29 child tasks. Implementation file owners are strictly separated from hosted evidence consumers.
7. **Non-Empty Router Deletion Inventories**: Every router task declares the exact inline `main.py` route handlers, helper functions, and globals being eliminated.
8. **Fail-Closed Forward Rollback**: All tasks specify forward repair or previous release artifact rollback, never restoring shims, duplicate handlers, or in-memory authority.
9. **Clean Materialization Batches**:
   - **Batch A (Bootstrap)**: 1 task (`OPGAP-DEVTOOL-TARGET-REPO-BRIDGE-20260830`)
   - **Batch B (Domain Routers & Ports)**: 14 tasks
   - **Batch C (Support, Frontend & Controls)**: 9 tasks
   - **Batch D (Assembly, Cutover, Retirement & Promotion)**: 5 tasks

---

## Reproducible Dynamic Validation Command

Run this command from repository root to dynamically verify all catalog invariants:

```bash
python3 -c '
import ast, json, sys
tree = ast.parse(open("services/control-plane/bff/main.py").read())
c = json.load(open("docs/04/pantheon_full_product_operation_audit_2026-08-29/EXECUTION_TASK_CATALOG_2026-08-30.json"))
tasks = c["tasks"]
nodes = c["main_ast_node_inventory"]["nodes"]
assert len(nodes) == len(tree.body), f"AST count mismatch: catalog {len(nodes)} != live {len(tree.body)}"
surfaces = {}
for t in tasks:
    for s in t.get("owned_code_surfaces", []):
        surfaces.setdefault(s, []).append(t["id"])
dups = {s: ids for s, ids in surfaces.items() if len(ids) > 1}
assert not dups, f"Duplicate owned surfaces: {dups}"
assert not [n for n in nodes if n.get("disposition") == "extract_shared_port" and n.get("node_type") in ("Import", "ImportFrom")], "Found stdlib extract_shared_port"
assert all(len(t.get("deletion", [])) > 0 for t in tasks), "All tasks must have non-empty deletion inventory"
forbidden_rb = ["restores deleted", "restore domain_ports", "revert to in-memory", "revert to local helper", "preserve legacy"]
for t in tasks:
    tid = t["id"]
    rb = t.get("rollback", "").lower()
    for w in forbidden_rb:
        assert w not in rb, f"Forbidden rollback in {tid}: {rb}"
graph = {t["id"]: set(t.get("depends_on", [])) for t in tasks}
visited, visiting = set(), set()
def dfs(n):
    if n in visiting: return False
    if n in visited: return True
    visiting.add(n)
    for nbr in graph.get(n, []):
        if nbr in graph and not dfs(nbr): return False
    visiting.remove(n); visited.add(n)
    return True
assert all(dfs(t) for t in graph), "Cycle detected in DAG"
sp = c.get("source_proof_contract", {})
assert sp.get("receipt_binding") == "source_proof_receipt_id", "source_proof_contract missing receipt_binding"
assert sp.get("max_ticks") == 1 and sp.get("max_records") == 100, "source_proof_contract bounded limits invalid"
assert sp.get("dev_mode_default") == "reconcile_only", "source_proof_contract dev_mode_default must be reconcile_only"
print("All catalog dynamic validation assertions passed successfully.")
'
```\n