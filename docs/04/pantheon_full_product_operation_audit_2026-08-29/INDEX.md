# Full Product Operation Audit & Remediation Plan (2026-08-30)

## Executive Summary

This document package provides the complete, root-cause System Architecture (SA), System Design (SD), and parallel Execution Task Catalog for remediating all 20 identified product operation gaps (**OP-G01** through **OP-G20**) across the **Pantheon** control plane and **execute-plans** desktop frontend repositories.

### Baseline Provenance
- **Pantheon Baseline Commit**: `d2bca5bc70bfae897e1ef3ca736ad3680a587679` (`origin/dev`)
- **Execute-Plans Baseline Commit**: `bd03c863e3c2c1c64b9b7797f27cefaf84df17c1` (`origin/dev`)
- **Hosted Environment**: Pair ID `c426db52184193a4063e57e6d8f06b14d8743336db7ab50f06b8325165d5902e`, Backend `d2bca5bc70bfae897e1ef3ca736ad3680a587679`, Frontend `bd03c863e3c2c1c64b9b7797f27cefaf84df17c1`, Status `accepted` (accepted at `2026-08-30T09:28:54Z`).
- **Governed Command Runtime SHA**: `954caefa519ab89827b4d3030a511f2f7c73138a`

---

## 補強「正常運作」定義 (Definition of Normal Operation & Verification Protocol)

「有大量程式碼與測試通過」不等於「全系統正常運作」。為消除重複路徑、假 fallback 成功與偽完成宣稱，完整判定必須同時滿足以下 11 項獨立標準與驗證閘門：

1. **Natural Non-Stub Callers**: 所有 production entrypoint 具備真實 upstream caller；無 stub/mock 冒充 production runtime。
2. **Single Write Authority**: 每一類 mutation 只有唯一 write authority；read models 與 projections 嚴格由其衍生。
3. **Same-ID & Version Durable Readback**: 寫入成功必須具備 same-ID/version durable readback，重啟與多副本後仍一致。
4. **Fail-Closed Fault Semantics**: 重試、併發競爭、依賴故障、SSE replay 與 rollback 遵循 fail-closed 語意，無狀態污染。
5. **Authentic Test Topology**: 測試必須在真實多 process / DB 拓撲執行。跳過測試、逾時或缺少服務依賴者嚴格判定為 `NOT_EXECUTED` 或 `UNVERIFIED`，絕不可作為通過證據；執行斷言失敗者為 `FAIL`。
6. **Formal Governance Validation**: 安全與資金關鍵流程經由正式治理路徑驗證，不依賴 test fixture bypass。
7. **Exact Immutable Release Binding**: CI、deployment manifest、container images 與 exact FE/BFF SHAs 嚴格不可變綁定；缺 gate 即阻擋。
8. **Atomic Caller Cutover & Zero-Shim Deletion**: cutover 完成後，舊 implementation、forwarding shims、mounts 與專屬 tests 同步刪除。
9. **Explicit Observability & Correlation Receipts**: 每個狀態變更均產生唯一 trace ID、correlation receipt ID 與 journal sequence，支援跨 plane 追蹤。
10. **Governed CI Workflow Verification**: 所有 PR 與 release workflows 均有實際 job 啟動並通過；0-job 假綠流程視為治理未驗證並阻擋發布。
11. **Single Truth Reconciliation**: 系統狀態於 canonical task store、Git HEAD、部署 manifest 與 live caller wiring 四者間保持嚴格單一真相，消除漂移。

---

## Package Structure

1. **[CURRENT_GAP_DISPOSITION_2026-08-30.md](./CURRENT_GAP_DISPOSITION_2026-08-30.md)**
   Exhaustive disposition of all 20 operational gaps (OP-G01 to OP-G20) across all 17 product planes (P-01 to P-17), preserving original audit observations and three-pass verification findings, observed-vs-planned comparisons, evidence ownership, exit criteria, reconciling active, verify, closed, and in_progress states with direct code/deployment evidence, documenting task-board-vs-git drift, merging Finding F21 into OP-G08, F24 into OP-G10, and documenting F22/F23/F25 unresolved exclusions.
2. **[SA_GAP_REMEDIATION_2026-08-30.md](./SA_GAP_REMEDIATION_2026-08-30.md)**
   Target System Architecture defining bounded context domain routing, single-namespace port consolidation (`ports/`), reverse import elimination, command executor retention, single-stimulus Source contract, authority/write/read ownership matrix, failure boundaries, 11-point normal operation definition, and strict separation of development tooling vs product runtime.
3. **[SD_GAP_REMEDIATION_2026-08-30.md](./SD_GAP_REMEDIATION_2026-08-30.md)**
   Detailed System Design for all 18 domain routers (441 HTTP decorators across 421 unique route handlers), inventory of all 2,271 `main.py` top-level AST nodes with cryptographic AST digests, 100% rationales and edge-level cutover mappings, minimal composition root allowlist with zero inline handlers/side effects/reverse imports, legacy action adapter cluster call graph and zero-root proof, port namespace consolidation (191 imported-symbol rows across 22 files: 129 production rows across 7 files, 62 test rows across 15 files; 6 deleted `domain_ports/` files), context-aware external reverse-main import inventory (269 qualified instances across 214 files, 94 excluded instances), reachability-based frontend residual cleanup (`retain_and_clean`, `delete_after_zero_reachability`, `already_absent_delivered_paths`), command caller cutover, and command plane retirement.
4. **[EXECUTION_DAG_2026-08-30.md](./EXECUTION_DAG_2026-08-30.md)**
   Acyclic multi-wave dependency graph across 30 child tasks, materialization batches (A: 1, B: 14, C: 9, D: 6), active eligible auto-worker capability assignments (`Antigravity`, `Antigravity2`, `Codex`, `Codex2`, `Claude`), predecessor reconciliation (`AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830` terminal `done`), dynamic capacity derivation, and capacity-1 `pantheon-dev` host constraint.
5. **[EXECUTION_TASK_CATALOG_2026-08-30.json](./EXECUTION_TASK_CATALOG_2026-08-30.json)**
   Machine-checkable authoritative JSON catalog containing the 30 child tasks with zero duplicate owned surfaces, route migration matrix, top-level AST node inventory with AST digests and edge-level cutover mappings, reverse-main symbol inventory (1,914 symbols with 100% symbol identity preservation), external reverse-main import inventory (269 qualified instances), domain_ports caller inventory (191 imported-symbol rows across 22 files), reachability-based frontend residual inventory, prior delivery dispositions, live-derived capacity, and embedded dynamic validation rules.

---

## Core Planning Rules & Guarantees

1. **Exact AST-Level Route & Symbol Migration**: All 2,271 top-level AST body nodes in `main.py` (68,304 lines, 441 HTTP route decorators across 421 unique route handlers) are mapped to concrete domain owners, pure composition root (`composition_keep`), real port abstractions in `ports/`, or legacy action cluster retirement. Standard library imports are never classified as `extract_shared_port`. All nodes have non-empty rationales and 100% edge-level cutover mappings for every consumer edge.
2. **Minimal Composition Root Invariant**: Governed by an explicit composition-root allowlist (FastAPI app, lifespan startup/shutdown, CORS/auth middlewares, 18 domain router inclusions, root composition logger). Target invariant: **zero inline route handlers, zero side effects outside lifespan, and zero reverse imports of main.py**.
3. **Zero Reverse Imports of `main.py`**: All 269 qualified external reverse-main import instances across BFF routers, background workers, and production scripts (including `command_executor.py`, `identity`, and `personalization`) are eliminated by extracting shared contracts to `services/control-plane/bff/ports/`.
4. **Port Namespace Consolidation**: `services/control-plane/bff/ports/` is the sole public and implementation namespace. All 191 imported-symbol rows across 22 unique files (129 production rows across 7 files, 62 test rows across 15 files) are migrated, the 6 `domain_ports/*.py` files are deleted, and rollback never restores deleted forwarding shims.
5. **Command Executor Preservation**: `command_executor.py` is retained as the production operator command executor, eliminating its reverse-main dependency while deleting dead legacy unrouted action adapters.
6. **Single-Receipt Source Contract**: Strict `reconcile_only` default in development; live stimulus bounded to a single receipt contract (`source_proof_receipt_id`) binding `connectorId` + `ingestRunId` + `sourceId` + `snapshotId` pre-switch and read-only reuse post-switch with zero second egress.
7. **Exclusive Artifact & Surface Ownership**: Zero duplicate `owned_code_surfaces` across all 30 child tasks. Implementation file owners are strictly separated from hosted evidence consumers.
8. **Non-Empty Router Deletion Inventories**: Every router task declares the exact inline `main.py` route handlers, helper functions, and globals being eliminated.
9. **Fail-Closed Forward Rollback**: All tasks specify forward repair or previous release artifact rollback, never restoring shims, duplicate handlers, or in-memory authority.
10. **Clean Materialization Batches**:
    - **Batch A (Bootstrap)**: 1 task (`OPGAP-DEVTOOL-TARGET-REPO-BRIDGE-20260830`)
    - **Batch B (Parallel Domain Preparation)**: 14 tasks (Core, Persona, Training, Agora, Research, Governance, Evolution, Capital, Strategy, Management, Postmortem, Incident, Events, Ports Consolidation)
    - **Batch C (Support & Frontend)**: 9 tasks (Tools, Control Loops, Command Adapters, Runtime Binding, Deployments, FE Cleanup, FE Management, FE Agora, FE Assembly)
    - **Batch D (Assembly, Retirement & Hosted Promotion/Acceptance)**: 6 tasks (Main Assembly, Command Cutover, Command Retirement, Hosted Promotion, Hosted Backend Acceptance, Hosted Management Acceptance)

---

## Reproducible Dynamic Validation Command

Run this command from repository root to dynamically verify all catalog invariants:

```bash
python3 -c "
import ast, json, hashlib, os, sys

source_code = open('services/control-plane/bff/main.py').read()
tree = ast.parse(source_code)
c = json.load(open('docs/04/pantheon_full_product_operation_audit_2026-08-29/EXECUTION_TASK_CATALOG_2026-08-30.json'))
tasks = c['tasks']
nodes = c['main_ast_node_inventory']['nodes']

assert len(nodes) == len(tree.body) == 2271, f'AST count mismatch: catalog {len(nodes)} != live {len(tree.body)}'
assert len(tasks) == 30, f'Task count mismatch: {len(tasks)} != 30'

# 1. Verify AST digests and content parity across all nodes
for i, (cat_node, ast_node) in enumerate(zip(nodes, tree.body)):
    dump_str = ast.dump(ast_node, annotate_fields=True, include_attributes=False)
    exp_digest = hashlib.sha256(dump_str.encode('utf-8')).hexdigest()[:16]
    assert cat_node.get('ast_digest') == exp_digest, f'Node {i} AST digest mismatch'

# 2. Verify Edge-Level Cutover Mapping Parity (100% match between named_consumers and consumer_cutover_mapping)
for n in nodes:
    nc = n.get('named_consumers')
    cm = n.get('consumer_cutover_mapping')
    if nc is not None:
        assert cm is not None, f'Node {n.get("node_index")} has named_consumers but missing consumer_cutover_mapping'
        assert set(nc) == set(cm.keys()), f'Node {n.get("node_index")} cutover mapping keys do not match named_consumers'
        assert all(isinstance(v, str) and len(v) > 0 for v in cm.values()), f'Node {n.get("node_index")} has empty cutover mapping values'
    else:
        assert cm is None, f'Node {n.get("node_index")} has consumer_cutover_mapping but no named_consumers'

# 3. Verify Legacy Action Cluster and os.makedirs disposition
legacy_cluster_nodes = [n for n in nodes if n.get('legacy_action_cluster')]
assert len(legacy_cluster_nodes) == 9, f'Expected 9 legacy action cluster nodes, found {len(legacy_cluster_nodes)}'
for n in legacy_cluster_nodes:
    assert n['owner_task'] == 'OPGAP-BFF-MAIN-ASSEMBLY-20260830', f'Legacy node {n["node_index"]} must be owned by assembly'
    assert n.get('zero_production_caller_evidence'), f'Legacy node {n["node_index"]} missing zero_production_caller_evidence'

n118 = nodes[118]
assert n118['disposition'] == 'composition_keep' and n118['owner_task'] == 'OPGAP-BFF-MAIN-ASSEMBLY-20260830', 'os.makedirs node 118 invalid'

# 4. Verify route migration inventory parity
rmi = c.get('route_migration_inventory', {})
assignments = rmi.get('assignments', [])
handlers = rmi.get('handler_migration_dispositions', [])
assert len(assignments) == 441, f'Assignments count mismatch: {len(assignments)} != 441'
assert len(handlers) == 421, f'Handlers count mismatch: {len(handlers)} != 421'

# 5. Verify batches and set equality
batches = c['materialization_contract']['batches']
batched_tasks = [tid for b in batches for tid in b['tasks']]
assert len(batched_tasks) == len(tasks) == 30, f'Batch equality failed: {len(batched_tasks)} != {len(tasks)}'
assert set(batched_tasks) == set(t['id'] for t in tasks), 'Batch task set does not match task inventory'
assert all(len(b['tasks']) <= 16 for b in batches), 'A batch exceeds fleet limit of 16 tasks'

# 6. Verify exclusive code surfaces (no duplicates, no prefix collisions)
surfaces = {}
for t in tasks:
    for s in t.get('owned_code_surfaces', []):
        surfaces.setdefault(s, []).append(t['id'])
dups = {s: ids for s, ids in surfaces.items() if len(ids) > 1}
assert not dups, f'Duplicate owned surfaces: {dups}'

assert not [n for n in nodes if n.get('disposition') == 'extract_shared_port' and n.get('node_type') in ('Import', 'ImportFrom')], 'Found stdlib extract_shared_port'
assert all(len(t.get('deletion', [])) > 0 for t in tasks), 'All tasks must have non-empty deletion inventory'

# 7. Verify safe rollback semantics
forbidden_rb = ['restores deleted', 'restore domain_ports', 'revert to in-memory', 'revert to local helper', 'preserve legacy']
for t in tasks:
    tid = t['id']
    rb = t.get('rollback', '').lower()
    for w in forbidden_rb:
        assert w not in rb, f'Forbidden rollback in {tid}: {rb}'

# 8. Verify DAG acyclicity
graph = {t['id']: set(t.get('depends_on', [])) for t in tasks}
visited, visiting = set(), set()
def dfs(n):
    if n in visiting: return False
    if n in visited: return True
    visiting.add(n)
    for nbr in graph.get(n, []):
        if nbr in graph and not dfs(nbr): return False
    visiting.remove(n); visited.add(n)
    return True
assert all(dfs(t) for t in graph), 'Cycle detected in DAG'

# 9. Verify Source Proof Contract
sp = c.get('source_proof_contract', {})
assert sp.get('receipt_binding') == 'source_proof_receipt_id', 'source_proof_contract missing receipt_binding'
assert sp.get('max_ticks') == 1 and sp.get('max_records') == 100, 'source_proof_contract bounded limits invalid'
assert sp.get('dev_mode_default') == 'reconcile_only', 'source_proof_contract dev_mode_default must be reconcile_only'

assert all(n.get('rationale') for n in nodes), 'All AST nodes must have non-empty rationale'

# 10. Verify Special node mappings
n37 = nodes[37]
assert n37['target_router'] == 'services/control-plane/bff/ports/param_utils.py' and len(n37['consumer_cutover_mapping']) >= 5, '_resolve_param mapping invalid'
assert 'OPGAP-BE-MANAGEMENT-ROUTER-20260830' in n37['named_consumers'], '_resolve_param named_consumers missing Management'

n39 = nodes[39]
assert n39['target_router'] == 'services/control-plane/bff/ports/config.py' and len(n39['consumer_cutover_mapping']) >= 3, '_REPO_ROOT mapping invalid'

n43 = nodes[43]
assert n43['target_router'] == 'services/control-plane/bff/ports/config.py' and len(n43['consumer_cutover_mapping']) >= 3, '_CRON_SERVICE_DIR mapping invalid'

n76 = nodes[76]
assert n76['disposition'] == 'composition_keep' and n76.get('consumer_cutover_mapping') is not None, 'log node cutover invalid'
assert n76['consumer_cutover_mapping']['OPGAP-BE-BFF-CORE-20260830'] == 'logging.getLogger(__name__)', 'log logger replacement invalid'

# 11. Verify Reverse-Main Symbol Inventory & External Reverse-Main Inventory
rev = c.get('reverse_main_symbol_inventory', [])
assert len(rev) == 1914, f'Expected 1914 reverse main symbols, found {len(rev)}'
for entry in rev:
    sym = entry['symbol']
    for ct, target_str in entry.get('cutover_mapping', {}).items():
        assert target_str.endswith(f".{sym}"), f'Corruption in {sym} -> {target_str}'

rev_inv = c.get('external_reverse_main_symbol_inventory', {})
assert rev_inv.get('total_import_instances', 0) == 269, f'Expected 269 reverse-main instances, found {rev_inv.get("total_import_instances")}'
assert rev_inv.get('unique_caller_files_count', 0) == 214, f'Expected 214 caller files, found {rev_inv.get("unique_caller_files_count")}'

# 12. Verify Domain Ports Caller Inventory
dp_inv = c.get('domain_ports_caller_inventory', {})
assert dp_inv.get('total_imported_symbol_rows') == 191, f'Expected 191 imported-symbol rows, found {dp_inv.get("total_imported_symbol_rows")}'
assert dp_inv.get('total_unique_caller_files') == 22, f'Expected 22 unique caller files, found {dp_inv.get("total_unique_caller_files")}'
assert dp_inv.get('production_caller_files_count') == 7, f'Expected 7 production caller files, found {dp_inv.get("production_caller_files_count")}'
assert dp_inv.get('test_caller_files_count') == 15, f'Expected 15 test caller files, found {dp_inv.get("test_caller_files_count")}'
assert dp_inv.get('production_imported_symbol_rows_count') == 129, f'Expected 129 production rows, found {dp_inv.get("production_imported_symbol_rows_count")}'
assert dp_inv.get('test_imported_symbol_rows_count') == 62, f'Expected 62 test rows, found {dp_inv.get("test_imported_symbol_rows_count")}'

# 13. Verify Planning Agent Capacity & Task Assignment
cap = c.get('planning_agent_capacity', {})
assert cap.get('dynamic_derived') is True, 'Capacity must be dynamically derived'
for t in tasks:
    assert t['owner'] != t['reviewer'], f'Owner equals reviewer in {t["id"]}'
    assert cap['agent_eligibility'][t['owner']]['eligible'], f'Owner {t["owner"]} not eligible'
    assert cap['agent_eligibility'][t['reviewer']]['eligible'], f'Reviewer {t["reviewer"]} not eligible'

# 14. Verify Baseline
pb = c.get('planning_baseline', {})
assert pb.get('hosted_pair_id') == 'c426db52184193a4063e57e6d8f06b14d8743336db7ab50f06b8325165d5902e', 'Stale hosted pair ID'
assert pb.get('hosted_backend') == 'd2bca5bc70bfae897e1ef3ca736ad3680a587679', 'Stale hosted backend'
assert pb.get('hosted_accepted_at') == '2026-08-30T09:28:54Z', 'Stale hosted accepted at'

print('SUCCESS: All comprehensive dynamic validation assertions passed!')
"
```
