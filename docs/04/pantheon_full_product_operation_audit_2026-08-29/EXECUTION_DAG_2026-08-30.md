# Execution DAG & Materialization Batches (2026-08-30)

## 1. Materialization Batch Strategy

```mermaid
graph TD
    subgraph Wave 1 [Batch A: Bootstrap]
        DEVTOOL[OPGAP-DEVTOOL-TARGET-REPO-BRIDGE-20260830]
    end

    subgraph Wave 2 [Batch B: Backend Domain Routers & Port Consolidation]
        PORT[OPGAP-BE-PORT-NAMESPACE-CONSOLIDATION-20260830]
        CORE[OPGAP-BE-BFF-CORE-20260830]
        PERS[OPGAP-BE-PERSONA-ROUTER-20260830]
        TRAIN[OPGAP-BE-TRAINING-ROUTER-20260830]
        AGORA[OPGAP-BE-AGORA-ROUTER-20260830]
        RES[OPGAP-BE-RESEARCH-ROUTER-20260830]
        GOV[OPGAP-BE-GOVERNANCE-ROUTER-20260830]
        EVO[OPGAP-BE-EVOLUTION-ROUTER-20260830]
        CAP[OPGAP-BE-CAPITAL-ROUTER-20260830]
        STRAT[OPGAP-BE-STRATEGY-RANKING-20260830]
        MGMT[OPGAP-BE-MANAGEMENT-ROUTER-20260830]
        POST[OPGAP-BE-POSTMORTEM-ROUTER-20260830]
        INC[OPGAP-BE-INCIDENT-ROUTER-20260830]
        EVT[OPGAP-BE-EVENTS-ROUTER-20260830]
    end

    subgraph Wave 2 & 3 [Batch C: Support, Controls & Frontend]
        TOOL[OPGAP-BE-TOOLS-INTEGRATIONS-20260830]
        LOOP[OPGAP-BE-CONTROL-LOOPS-20260830]
        CMD[OPGAP-BE-COMMAND-ADAPTERS-20260830]
        RUN[OPGAP-BE-RUNTIME-BINDING-20260830]
        DEP[OPGAP-DEPLOY-RELIABILITY-20260830]
        FE_CLN[OPGAP-FE-BUNDLE-CLEANUP-20260830]
        FE_MGMT[OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830]
        FE_AGO[OPGAP-FE-AGORA-WORKSHOP-20260830]
        FE_ASM[OPGAP-FE-INTEGRATION-ASSEMBLY-20260830]
    end

    subgraph Wave 4, 5, 6 [Batch D: Assembly, Retirement & Promotion]
        MAIN_ASM[OPGAP-BFF-MAIN-ASSEMBLY-20260830]
        CALLER[OPGAP-BE-COMMAND-CALLER-CUTOVER-20260830]
        RETIRE[OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830]
        PROMO[OPGAP-HOSTED-DEV-PROMOTION-20260830]
        ACCEPT[OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830]
    end

    DEVTOOL --> PORT
    DEVTOOL --> CORE
    DEVTOOL --> PERS
    DEVTOOL --> TRAIN
    DEVTOOL --> AGORA
    DEVTOOL --> RES
    DEVTOOL --> GOV
    DEVTOOL --> EVO
    DEVTOOL --> CAP
    DEVTOOL --> STRAT
    DEVTOOL --> MGMT
    DEVTOOL --> POST
    DEVTOOL --> INC
    DEVTOOL --> EVT

    DEVTOOL --> TOOL
    DEVTOOL --> LOOP
    DEVTOOL --> CMD
    DEVTOOL --> RUN
    DEVTOOL --> DEP
    DEVTOOL --> FE_CLN
    DEVTOOL --> FE_MGMT
    DEVTOOL --> FE_AGO

    FE_CLN --> FE_ASM
    FE_MGMT --> FE_ASM
    FE_AGO --> FE_ASM

    PORT --> MAIN_ASM
    CORE --> MAIN_ASM
    PERS --> MAIN_ASM
    TRAIN --> MAIN_ASM
    AGORA --> MAIN_ASM
    RES --> MAIN_ASM
    GOV --> MAIN_ASM
    EVO --> MAIN_ASM
    CAP --> MAIN_ASM
    STRAT --> MAIN_ASM
    MGMT --> MAIN_ASM
    POST --> MAIN_ASM
    INC --> MAIN_ASM
    EVT --> MAIN_ASM
    TOOL --> MAIN_ASM
    LOOP --> MAIN_ASM
    CMD --> MAIN_ASM
    RUN --> MAIN_ASM
    DEP --> MAIN_ASM

    MAIN_ASM --> CALLER
    CALLER --> RETIRE
    MAIN_ASM --> PROMO
    RETIRE --> PROMO
    FE_ASM --> PROMO
    PROMO --> ACCEPT
```

---

## 2. Batch Composition & Execution Rules

### Batch A: Bootstrap (1 Task)
- `OPGAP-DEVTOOL-TARGET-REPO-BRIDGE-20260830` (Owner: Codex, Reviewer: Antigravity2)
- Establishes signed target repository persistence in `.orchestrator/development_bridge/`.

### Batch B: Domain Routers & Port Consolidation (14 Tasks)
- Runs immediately in parallel after Batch A.
- Decouples all domain routes and consolidates `ports/`.

### Batch C: Support, Controls & Frontend (9 Tasks)
- Runs in parallel with Batch B.
- Cleans frontend residuals and prepares desktop views.
- `OPGAP-FE-INTEGRATION-ASSEMBLY-20260830` merges once frontend domain tasks are done.

### Batch D: Assembly, Retirement & Promotion (5 Tasks)
- Requires completion of all Batch B and Batch C backend tasks.
- Executes `main.py` assembly, command plane deletion, and hosted dev deployment/acceptance.

---

## 3. Predecessor Reconciliation Truth

1. **`AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830`**:
   - Canonical status: `done` (terminal done, merged to Pantheon dev `d2bca5bc70bfae897e1ef3ca736ad3680a587679` via PR #5427).
   - Unblocks Batch D `main.py` assembly from active predecessor constraints.
2. **`AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829`**:
   - Canonical status: `in_progress` (Agora-only authentic hosted demo).
   - Reused as the sole owner for `OP-G14` in Agora scope; no duplicate OPGAP FE acceptance task is materialized.

---

## 4. Resource & Agent Capacity Constraints

1. **Host Capacity**: `pantheon-dev` has strict capacity = 1. Only hosted promotion and acceptance tasks (`OPGAP-HOSTED-DEV-PROMOTION-20260830`, `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830`, `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829`) acquire this resource.
2. **Agent Capability Lanes**: Every child task has distinct owner and reviewer from the live agent pool (`Codex`, `Codex2`, `Antigravity`, `Antigravity2`, `Claude`, `Claude2`, `Gemini`, `Gemini2`, `Copilot`). Capacity is dynamically derived from live configuration at materialization.

---

## 5. Reproducible Dynamic Validation Command

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