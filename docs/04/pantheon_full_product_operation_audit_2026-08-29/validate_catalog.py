#!/usr/bin/env python3
"""Authoritative validator for Full Product Operation GAP SA/SD and Execution Catalog.

Validates all 16 architectural and catalog invariants across:
1. services/control-plane/bff/main.py AST nodes and cutover mapping parity
2. Edge-level cutover mapping parity across all named consumers
3. Legacy action cluster (9 nodes) and os.makedirs (node 118) disposition
4. Route migration inventory parity (441 decorators across 421 handlers)
5. Materialization batches (A: 1, B: 14, C: 9, D: 6) and task set equality (<=16/packet)
6. Exclusive code surfaces (zero duplicates and zero prefix collisions across 30 tasks)
7. Safe forward rollback semantics across all tasks
8. DAG acyclicity across all 30 child tasks
9. Single-stimulus Source proof receipt contract
10. Special AST node mappings (_resolve_param, _REPO_ROOT, _CRON_SERVICE_DIR, log)
11. Reverse-main symbol inventory (29 symbols) and external caller AST instances (215 files, 270 instances recomputed from source)
12. Domain ports caller inventory (191 rows across 22 files recomputed from source AST: 129 prod, 62 tests)
13. Dynamic planning agent capacity and authoritative capability selectors
14. Planning baseline provenance across Pantheon, execute-plans, and hosted runtime
15. Bidirectional pantheon-dev execution resources invariant
16. Signed DevTaskPacket materialization mapping and post-bootstrap spec hash contract (binding target_repo + task_class + delivery_repository)
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from pathlib import Path

def validate_catalog(catalog_path: str, main_py_path: str) -> None:
    print(f"Loading catalog from {catalog_path}...")
    with open(catalog_path, "r", encoding="utf-8") as f:
        c = json.load(f)

    tasks = c["tasks"]
    nodes = c["main_ast_node_inventory"]["nodes"]
    repo_root = Path(main_py_path).resolve().parent.parent.parent.parent

    print(f"Parsing main.py from {main_py_path}...")
    source_code = Path(main_py_path).read_text(encoding="utf-8")
    tree = ast.parse(source_code)

    print(f"Checking AST node count ({len(nodes)} catalog vs {len(tree.body)} live)...")
    assert len(nodes) == len(tree.body) == 2272, f"AST count mismatch: catalog {len(nodes)} != live {len(tree.body)}"
    assert len(tasks) == 30, f"Task count mismatch: {len(tasks)} != 30"

    # 1. Verify AST digests and content parity across all nodes
    print("1. Verifying AST digests and content parity across all 2,272 nodes...")
    for i, (cat_node, ast_node) in enumerate(zip(nodes, tree.body)):
        dump_str = ast.dump(ast_node, annotate_fields=True, include_attributes=False)
        exp_digest = hashlib.sha256(dump_str.encode("utf-8")).hexdigest()[:16]
        assert cat_node.get("ast_digest") == exp_digest, f"Node {i} AST digest mismatch: expected {exp_digest}, got {cat_node.get('ast_digest')}"

    # 2. Verify Edge-Level Cutover Mapping Parity
    print("2. Verifying edge-level cutover mappings for all nodes...")
    for n in nodes:
        nc = n.get("named_consumers")
        cm = n.get("consumer_cutover_mapping")
        if nc is not None:
            assert cm is not None, f"Node {n.get('node_index')} has named_consumers but missing consumer_cutover_mapping"
            assert set(nc) == set(cm.keys()), f"Node {n.get('node_index')} cutover mapping keys do not match named_consumers"
            assert all(isinstance(v, str) and len(v) > 0 for v in cm.values()), f"Node {n.get('node_index')} has empty cutover mapping values"
        else:
            assert cm is None, f"Node {n.get('node_index')} has consumer_cutover_mapping but no named_consumers"

    # 3. Verify Legacy Action Cluster and os.makedirs disposition
    print("3. Verifying legacy action cluster (9 nodes) and node 118 disposition...")
    legacy_cluster_nodes = [n for n in nodes if n.get("legacy_action_cluster")]
    assert len(legacy_cluster_nodes) == 9, f"Expected 9 legacy action cluster nodes, found {len(legacy_cluster_nodes)}"
    for n in legacy_cluster_nodes:
        assert n["owner_task"] == "OPGAP-BFF-MAIN-ASSEMBLY-20260830", f"Legacy node {n['node_index']} must be owned by assembly"
        assert n.get("zero_production_caller_evidence"), f"Legacy node {n['node_index']} missing zero_production_caller_evidence"

    n118 = nodes[118]
    assert n118["disposition"] == "composition_keep" and n118["owner_task"] == "OPGAP-BFF-MAIN-ASSEMBLY-20260830", "os.makedirs node 118 invalid"

    # 4. Verify route migration inventory parity
    print("4. Verifying route migration inventory (441 decorators across 421 handlers)...")
    rmi = c.get("route_migration_inventory", {})
    assignments = rmi.get("assignments", [])
    handlers = rmi.get("handler_migration_dispositions", [])
    assert len(assignments) == 441, f"Assignments count mismatch: {len(assignments)} != 441"
    assert len(handlers) == 421, f"Handlers count mismatch: {len(handlers)} != 421"

    # 5. Verify batches and set equality
    print("5. Verifying materialization batches (A: 1, B: 14, C: 9, D: 6) and task set equality...")
    batches = c["materialization_contract"]["batches"]
    batched_tasks = [tid for b in batches for tid in b["tasks"]]
    assert len(batched_tasks) == len(tasks) == 30, f"Batch equality failed: {len(batched_tasks)} != {len(tasks)}"
    assert set(batched_tasks) == set(t["id"] for t in tasks), "Batch task set does not match task inventory"
    assert all(len(b["tasks"]) <= 16 for b in batches), "A batch exceeds fleet limit of 16 tasks"

    # 6. Verify exclusive code surfaces (no duplicates, no prefix collisions)
    print("6. Verifying exclusive code surfaces (zero duplicates and zero prefix collisions across 30 tasks)...")
    surfaces: dict[str, list[str]] = {}
    surface_list: list[tuple[str, str]] = []
    for t in tasks:
        for s in t.get("owned_code_surfaces", []):
            surfaces.setdefault(s, []).append(t["id"])
            surface_list.append((s, t["id"]))
    dups = {s: ids for s, ids in surfaces.items() if len(ids) > 1}
    assert not dups, f"Duplicate owned surfaces: {dups}"

    prefix_collisions = []
    for s1, t1 in surface_list:
        for s2, t2 in surface_list:
            if t1 != t2 and s1 != s2:
                p1 = s1.rstrip("/") + "/"
                p2 = s2.rstrip("/") + "/"
                if p2.startswith(p1):
                    prefix_collisions.append((s1, t1, s2, t2))
    assert not prefix_collisions, f"Prefix collisions detected across tasks: {prefix_collisions}"

    assert not [n for n in nodes if n.get("disposition") == "extract_shared_port" and n.get("node_type") in ("Import", "ImportFrom")], "Found stdlib extract_shared_port"
    assert all(len(t.get("deletion", [])) > 0 for t in tasks), "All tasks must have non-empty deletion inventory"

    # 7. Verify safe rollback semantics
    print("7. Verifying safe forward rollback semantics across all tasks...")
    forbidden_rb = ["restores deleted", "restore domain_ports", "revert to in-memory", "revert to local helper", "preserve legacy"]
    for t in tasks:
        tid = t["id"]
        rb = t.get("rollback", "").lower()
        for w in forbidden_rb:
            assert w not in rb, f"Forbidden rollback in {tid}: {rb}"

    # 8. Verify DAG acyclicity
    print("8. Verifying DAG acyclicity and topological ordering...")
    graph = {t["id"]: set(t.get("depends_on", [])) for t in tasks}
    visited, visiting = set(), set()
    def dfs(n: str) -> bool:
        if n in visiting: return False
        if n in visited: return True
        visiting.add(n)
        for nbr in graph.get(n, []):
            if nbr in graph and not dfs(nbr): return False
        visiting.remove(n)
        visited.add(n)
        return True
    assert all(dfs(t) for t in graph), "Cycle detected in DAG"

    # 9. Verify Source Proof Contract
    print("9. Verifying single-stimulus Source proof contract...")
    sp = c.get("source_proof_contract", {})
    assert sp.get("receipt_binding") == "source_proof_receipt_id", "source_proof_contract missing receipt_binding"
    assert sp.get("max_ticks") == 1 and sp.get("max_records") == 100, "source_proof_contract bounded limits invalid"
    assert sp.get("dev_mode_default") == "reconcile_only", "source_proof_contract dev_mode_default must be reconcile_only"

    assert all(n.get("rationale") for n in nodes), "All AST nodes must have non-empty rationale"

    # 10. Verify Special node mappings
    print("10. Verifying special AST node mappings (37, 39, 43, 76)...")
    n37 = nodes[37]
    assert n37["target_router"] == "services/control-plane/bff/ports/param_utils.py" and len(n37["consumer_cutover_mapping"]) >= 5, "_resolve_param mapping invalid"
    assert "OPGAP-BE-MANAGEMENT-ROUTER-20260830" in n37["named_consumers"], "_resolve_param named_consumers missing Management"

    n39 = nodes[39]
    assert n39["target_router"] == "services/control-plane/bff/ports/config.py" and len(n39["consumer_cutover_mapping"]) >= 3, "_REPO_ROOT mapping invalid"

    n43 = nodes[43]
    assert n43["target_router"] == "services/control-plane/bff/ports/config.py" and len(n43["consumer_cutover_mapping"]) >= 3, "_CRON_SERVICE_DIR mapping invalid"

    n76 = nodes[76]
    assert n76["disposition"] == "composition_keep" and n76.get("consumer_cutover_mapping") is not None, "log node cutover invalid"
    assert n76["consumer_cutover_mapping"]["OPGAP-BE-BFF-CORE-20260830"] == "logging.getLogger(__name__)", "log logger replacement invalid"

    # 11. Verify Reverse-Main Symbol Inventory & External Reverse-Main Inventory (recomputed from source AST)
    print("11. Verifying reverse-main symbol inventory (29 symbols) and external caller AST instances (215 files, 270 instances)...")
    rev = c.get("reverse_main_symbol_inventory", [])
    assert len(rev) == 29, f"Expected 29 callsite-proven reverse-main symbols, found {len(rev)}"
    for entry in rev:
        assert not entry["target_module"].endswith("/" + entry["symbol"] + ".py"), f"Fake port target detected: {entry}"

    # Recompute reverse-main import instances from repository source AST
    scanned_rev_instances: list[dict[str, any]] = []
    target_files = ["scripts/bff_route_manifest_backend.py"]
    for root, dirs, files in os.walk(repo_root / "services/control-plane/bff"):
        for f in files:
            if f.endswith(".py"):
                full = Path(root) / f
                rel = full.relative_to(repo_root).as_posix()
                if rel != "services/control-plane/bff/main.py":
                    target_files.append(rel)

    for rel in sorted(target_files):
        p = repo_root / rel
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod in ("main", "bff.main") or mod.endswith(".bff.main") or mod.startswith("services.control_plane.bff.main") or mod.startswith("services.control-plane.bff.main"):
                    for alias in node.names:
                        scanned_rev_instances.append({
                            "caller_file": rel,
                            "line_number": node.lineno,
                            "import_module": mod,
                            "imported_symbol": alias.name,
                            "asname": alias.asname,
                        })
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("main", "bff.main") or alias.name.endswith(".bff.main") or alias.name.startswith("services.control_plane.bff.main") or alias.name.startswith("services.control-plane.bff.main"):
                        scanned_rev_instances.append({
                            "caller_file": rel,
                            "line_number": node.lineno,
                            "import_module": alias.name,
                            "imported_symbol": "*",
                            "asname": alias.asname,
                        })

    rev_inv = c.get("external_reverse_main_symbol_inventory", {})
    import_instances = rev_inv.get("import_instances", [])
    assert len(import_instances) == len(scanned_rev_instances), f"Reverse main instance count mismatch: catalog {len(import_instances)} != source {len(scanned_rev_instances)}"

    scanned_rev_files = set(x["caller_file"] for x in scanned_rev_instances)
    scanned_rev_symbols = set(x["imported_symbol"] for x in scanned_rev_instances)
    assert rev_inv.get("total_import_instances", 0) == len(scanned_rev_instances) == 270, f"Expected 270 reverse-main instances, found {rev_inv.get('total_import_instances')}"
    assert rev_inv.get("unique_caller_files_count", 0) == len(scanned_rev_files) == 215, f"Expected 215 caller files, found {rev_inv.get('unique_caller_files_count')}"
    assert rev_inv.get("unique_imported_symbols_count", 0) == len(scanned_rev_symbols) == 29, f"Expected 29 unique symbols, found {rev_inv.get('unique_imported_symbols_count')}"

    # Verify 1-to-1 exact row parity between source AST and catalog
    scanned_rev_tuples = set((x["caller_file"], x["line_number"], x["import_module"], x["imported_symbol"], x["asname"]) for x in scanned_rev_instances)
    catalog_rev_tuples = set((x["caller_file"], x["line_number"], x["import_module"], x["imported_symbol"], x.get("asname")) for x in import_instances)
    assert catalog_rev_tuples == scanned_rev_tuples, f"Mismatch in reverse-main row identities: diff {catalog_rev_tuples ^ scanned_rev_tuples}"

    # Verify caller files exist on disk
    for inst in import_instances:
        caller_f = repo_root / inst.get("caller_file")
        assert caller_f.exists(), f"Caller file does not exist: {caller_f}"

    # 12. Verify Domain Ports Caller Inventory (recomputed from source AST)
    print("12. Verifying domain_ports caller inventory from source AST (191 rows across 22 files: 129 prod, 62 tests)...")
    scanned_dp_rows: list[dict[str, any]] = []
    skip_dirs = {".venv", "lean", ".git", "node_modules", "__pycache__", "dist", "build"}
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for f in files:
            if f.endswith(".py"):
                full = Path(root) / f
                try:
                    content = full.read_text(encoding="utf-8")
                except Exception:
                    continue
                if "domain_ports" not in content:
                    continue
                rel = full.relative_to(repo_root).as_posix()
                try:
                    tree = ast.parse(content, filename=rel)
                except Exception:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        mod = node.module or ""
                        if "domain_ports" in mod or mod.startswith("services.control_plane.bff.domain_ports") or mod.startswith("services.control-plane.bff.domain_ports"):
                            is_test = rel.startswith("tests/") or "/tests/" in rel or Path(rel).name.startswith("test_")
                            for alias in node.names:
                                scanned_dp_rows.append({
                                    "caller_file": rel,
                                    "line_number": node.lineno,
                                    "import_module": mod,
                                    "imported_symbol": alias.name,
                                    "asname": alias.asname,
                                    "category": "test" if is_test else "production",
                                })
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            if "domain_ports" in alias.name:
                                is_test = rel.startswith("tests/") or "/tests/" in rel or Path(rel).name.startswith("test_")
                                scanned_dp_rows.append({
                                    "caller_file": rel,
                                    "line_number": node.lineno,
                                    "import_module": alias.name,
                                    "imported_symbol": alias.name,
                                    "asname": alias.asname,
                                    "category": "test" if is_test else "production",
                                })

    dp_inv = c.get("domain_ports_caller_inventory", {})
    callers = dp_inv.get("callers", [])
    assert len(callers) == len(scanned_dp_rows), f"Domain ports callers count mismatch: catalog {len(callers)} != source {len(scanned_dp_rows)}"

    scanned_dp_files = set(x["caller_file"] for x in scanned_dp_rows)
    prod_files = set(x["caller_file"] for x in scanned_dp_rows if x["category"] == "production")
    test_files = set(x["caller_file"] for x in scanned_dp_rows if x["category"] == "test")
    prod_rows = [x for x in scanned_dp_rows if x["category"] == "production"]
    test_rows = [x for x in scanned_dp_rows if x["category"] == "test"]

    assert dp_inv.get("total_imported_symbol_rows") == len(scanned_dp_rows) == 191, f"Expected 191 imported-symbol rows, found {dp_inv.get('total_imported_symbol_rows')}"
    assert dp_inv.get("total_unique_caller_files") == len(scanned_dp_files) == 22, f"Expected 22 unique caller files, found {dp_inv.get('total_unique_caller_files')}"
    assert dp_inv.get("production_caller_files_count") == len(prod_files) == 7, f"Expected 7 production caller files, found {dp_inv.get('production_caller_files_count')}"
    assert dp_inv.get("test_caller_files_count") == len(test_files) == 15, f"Expected 15 test caller files, found {dp_inv.get('test_caller_files_count')}"
    assert dp_inv.get("production_imported_symbol_rows_count") == len(prod_rows) == 129, f"Expected 129 production rows, found {dp_inv.get('production_imported_symbol_rows_count')}"
    assert dp_inv.get("test_imported_symbol_rows_count") == len(test_rows) == 62, f"Expected 62 test rows, found {dp_inv.get('test_imported_symbol_rows_count')}"

    # Verify 1-to-1 exact row parity between source AST and catalog
    scanned_dp_tuples = set((x["caller_file"], x["line_number"], x["import_module"], x["imported_symbol"], x["asname"], x["category"]) for x in scanned_dp_rows)
    catalog_dp_tuples = set((x["caller_file"], x["line_number"], x["import_module"], x["imported_symbol"], x.get("asname"), x["category"]) for x in callers)
    assert catalog_dp_tuples == scanned_dp_tuples, f"Mismatch in domain_ports row identities: diff {catalog_dp_tuples ^ scanned_dp_tuples}"

    for row in callers:
        caller_f = repo_root / row.get("caller_file")
        assert caller_f.exists(), f"Domain ports caller file does not exist: {caller_f}"

    # 13. Verify Planning Agent Capacity, Selectors & Task Assignment
    print("13. Verifying agent capacity, authoritative capability selectors, and task assignments...")
    cap = c.get("planning_agent_capacity", {})
    assert cap.get("dynamic_derived") is True, "Capacity must be dynamically derived"
    assert cap.get("command_runtime_sha") == "072ee68bbba8bbffb84a188ccf4d50d67429a7a8", "Stale command runtime SHA"
    for t in tasks:
        assert t["owner"] != t["reviewer"], f"Owner equals reviewer in {t['id']}"
        assert cap["agent_eligibility"][t["owner"]]["eligible"], f"Owner {t['owner']} not eligible"
        assert cap["agent_eligibility"][t["reviewer"]]["eligible"], f"Reviewer {t['reviewer']} not eligible"
        assert "owner_selector" in t and "reviewer_selector" in t, f"Task {t['id']} missing selectors"
        assert t["delivery_repository"] == t["target_repo"], f"Repository mismatch in {t['id']}"

    # 14. Verify Baseline
    print("14. Verifying planning baseline provenance...")
    pb = c.get("planning_baseline", {})
    assert pb.get("pantheon") == "072ee68bbba8bbffb84a188ccf4d50d67429a7a8", "Stale pantheon baseline"
    assert pb.get("execute_plans") == "7d30e78476be61222af63a089e7ab141aa43b809", "Stale execute-plans baseline"
    assert pb.get("hosted_pair_id") == "8961f959e54db4801438cef5fb7bb4047bc2506879afe6fc739572d0e2ba07f8", "Stale hosted pair ID"
    assert pb.get("hosted_backend") == "d5c312ef0a4139329d66bda13c7e487248602ed7", "Stale hosted backend"
    assert pb.get("hosted_frontend") == "7d30e78476be61222af63a089e7ab141aa43b809", "Stale hosted frontend"
    assert pb.get("hosted_accepted_at") == "2026-08-30T13:27:59Z", "Stale hosted accepted at"

    # 15. Verify Execution Resources Bidirectional Invariant
    print("15. Verifying execution resources bidirectional mapping (pantheon-dev)...")
    exec_res = c.get("execution_resources", {})
    pdev_consumers = set(exec_res.get("pantheon-dev", {}).get("consumers", []))
    task_pdev_consumers = set(t["id"] for t in tasks if "pantheon-dev" in t.get("execution_resources", []))
    assert pdev_consumers == task_pdev_consumers, f"pantheon-dev consumers mismatch: {pdev_consumers} != {task_pdev_consumers}"

    # 16. Verify Signed DevTaskPacket Materialization Mapping & Post-Bootstrap Spec Hash Contract
    print("16. Verifying signed DevTaskPacket materialization mapping and post-bootstrap spec hashes...")
    mat_map = c.get("materialization_contract", {}).get("signed_dev_task_packet_materialization_mapping", {})
    assert mat_map.get("max_tasks_per_packet") == 16, "DevTaskPacket limit must be <= 16"
    assert mat_map.get("total_batches") == 4 and mat_map.get("total_tasks") == 30, "DevTaskPacket batch/task count invalid"

    batches_summary = mat_map.get("batches_summary", [])
    assert len(batches_summary) == 4, "batches_summary must have 4 batches"
    assert batches_summary[0]["batch_id"] == "BATCH-A-BOOTSTRAP" and batches_summary[0]["materializable_now"] is True
    assert batches_summary[1]["batch_id"] == "BATCH-B-PARALLEL-DOMAIN-PREP" and batches_summary[1]["materializable_now"] is False
    assert batches_summary[2]["batch_id"] == "BATCH-C-SUPPORT-AND-FRONTEND" and batches_summary[2]["materializable_now"] is False
    assert batches_summary[3]["batch_id"] == "BATCH-D-ASSEMBLY-RETIREMENT-PROMOTION" and batches_summary[3]["materializable_now"] is False
    assert all(b["task_count"] <= 16 and b["dependency_closed"] for b in batches_summary), "Batches must be <= 16 and dependency closed"

    assert len(mat_map.get("per_task_spec_hashes", {})) == 30, "per_task_spec_hashes count invalid"
    assert mat_map.get("tasks_spec_catalog_sha256"), "tasks_spec_catalog_sha256 missing"

    calculated_hashes: dict[str, str] = {}
    for t in tasks:
        spec = {
            "acceptance": list(t.get("acceptance", [])),
            "artifacts": list(t.get("artifacts", [])),
            "delivery_repository": t.get("delivery_repository") or t.get("target_repo", "pantheon"),
            "dependency_tracks": dict(t.get("dependency_tracks", {})),
            "depends_on": list(t.get("depends_on", [])),
            "execution_resources": list(t.get("execution_resources", [])),
            "id": t["id"],
            "owner": t["owner"],
            "phase": t["phase"],
            "reviewer": t["reviewer"],
            "summary": t.get("summary") or t.get("summary_zh", ""),
            "target_repo": t.get("target_repo", "pantheon"),
            "task_class": t.get("task_class", "functional"),
            "title": t["title"],
        }
        exp_h = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
        assert mat_map["per_task_spec_hashes"][t["id"]] == exp_h, f"Post-bootstrap spec hash mismatch for {t['id']}: expected {exp_h}, got {mat_map['per_task_spec_hashes'][t['id']]}"
        calculated_hashes[t["id"]] = exp_h

    exp_catalog_sha256 = hashlib.sha256(json.dumps(calculated_hashes, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
    assert mat_map["tasks_spec_catalog_sha256"] == exp_catalog_sha256, f"Catalog SHA256 mismatch: expected {exp_catalog_sha256}, got {mat_map['tasks_spec_catalog_sha256']}"

    print("SUCCESS: All 16 comprehensive dynamic validation assertions passed!")

if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    default_catalog = repo_root / "docs/04/pantheon_full_product_operation_audit_2026-08-29/EXECUTION_TASK_CATALOG_2026-08-30.json"
    default_main_py = repo_root / "services/control-plane/bff/main.py"
    catalog_arg = sys.argv[1] if len(sys.argv) > 1 else str(default_catalog)
    main_py_arg = sys.argv[2] if len(sys.argv) > 2 else str(default_main_py)
    validate_catalog(catalog_arg, main_py_arg)
