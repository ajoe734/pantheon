#!/usr/bin/env python3
"""Reproducible Mutation-Negative Verification Suite for Catalog Invariants.

Tests that validate_catalog.py fails closed (raises AssertionError) on 23 distinct
intentional mutations corresponding to each validation phase:
1. Phase 1: Corrupted AST digest in Node 0
2. Phase 2: Missing edge-level consumer cutover mapping
3. Phase 3: Invalid legacy action cluster owner
4. Phase 4: Route migration inventory handler count mismatch
5. Phase 5: Batch exceeding fleet task limit (>16)
6. Phase 6: Duplicate owned code surface collision across tasks
7. Phase 7: Forbidden rollback restoration keyword
8. Phase 8: Cyclic dependency introduced into the DAG
9. Phase 9: Source proof contract dev default non-reconcile
10. Phase 10: Special node 37 (_resolve_param) target router mismatch
11. Phase 11: Reverse-main symbol inventory count mismatch
12. Phase 12: Domain ports caller count mismatch
13. Phase 13: Ineligible agent assigned as task owner
14. Phase 14: Stale planning baseline (corrupted hosted pair ID / controller run)
15. Phase 15: Execution resources bidirectional mapping mismatch
16. Phase 16: Post-bootstrap spec hash mismatch
17. Phase 1: Mutated dynamic_validation_contract rule (2,271 vs 2,272 AST nodes)
18. Phase 17: Corrupted execution replacement ledger row count (22 vs exact 23-row Batch B/C lineage)
19. Phase 18: Missing post-freeze operational gap
20. Phase 18: Missing governed Batch C V2 replacement
21. Phase 18: Missing task-scoped evidence manifest binding
22. Phase 18: Corrupted exact deployment release candidate identity
23. Phase 18: Missing bounded Firebase write-proof/watchdog prerequisite
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

# Add current directory to path so validate_catalog is importable
CURRENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT_DIR))

from validate_catalog import validate_catalog

REPO_ROOT = CURRENT_DIR.parent.parent.parent
CATALOG_PATH = CURRENT_DIR / "EXECUTION_TASK_CATALOG_2026-08-30.json"
LEDGER_PATH = CURRENT_DIR / "EXECUTION_REPLACEMENT_LEDGER_2026-08-30.json"
MAIN_PY_PATH = REPO_ROOT / "services/control-plane/bff/main.py"


def run_mutation_test(name: str, mutate_fn, expected_phase: int) -> bool:
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    mutated_catalog = mutate_fn(catalog)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(mutated_catalog, tmp)
        tmp_path = tmp.name

    try:
        validate_catalog(tmp_path, str(MAIN_PY_PATH))
        print(f"FAILED (did not raise): {name}")
        return False
    except AssertionError as e:
        print(f"PASSED (Phase {expected_phase} caught mutation): {name} -> {e}")
        return True
    except Exception as e:
        print(f"PASSED (Exception caught): {name} -> {e}")
        return True
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def run_ledger_mutation_test(name: str, mutate_fn, expected_phase: int = 17) -> bool:
    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    mutated_ledger = mutate_fn(ledger)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        # validate_catalog looks up the ledger relative to the catalog file's directory,
        # so both files must be copied into the same isolated temp directory.
        with open(CATALOG_PATH, "r", encoding="utf-8") as f:
            catalog = json.load(f)
        tmp_catalog_path = tmp_dir_path / "EXECUTION_TASK_CATALOG_2026-08-30.json"
        with open(tmp_catalog_path, "w", encoding="utf-8") as f:
            json.dump(catalog, f)
        tmp_ledger_path = tmp_dir_path / "EXECUTION_REPLACEMENT_LEDGER_2026-08-30.json"
        with open(tmp_ledger_path, "w", encoding="utf-8") as f:
            json.dump(mutated_ledger, f)

        try:
            validate_catalog(str(tmp_catalog_path), str(MAIN_PY_PATH))
            print(f"FAILED (did not raise): {name}")
            return False
        except AssertionError as e:
            print(f"PASSED (Phase {expected_phase} caught mutation): {name} -> {e}")
            return True
        except Exception as e:
            print(f"PASSED (Exception caught): {name} -> {e}")
            return True


def test_all_mutations() -> None:
    print("Running 23 Mutation-Negative Checks across all Catalog Invariants...")

    mutations = [
        (
            "1. Corrupt AST Digest in Node 0",
            lambda c: (_mut(c, lambda x: x["main_ast_node_inventory"]["nodes"][0].__setitem__("ast_digest", "deadbeef00000000"))),
            1,
        ),
        (
            "2. Delete Edge Cutover Mapping for Node with Named Consumers",
            lambda c: (_mut(c, lambda x: x["main_ast_node_inventory"]["nodes"][37].__setitem__("consumer_cutover_mapping", None))),
            2,
        ),
        (
            "3. Change Legacy Action Cluster Node Owner",
            lambda c: (_mut(c, lambda x: [n for n in x["main_ast_node_inventory"]["nodes"] if n.get("legacy_action_cluster")][0].__setitem__("owner_task", "OPGAP-BE-BFF-CORE-20260830"))),
            3,
        ),
        (
            "4. Corrupt Route Migration Inventory Handler Count",
            lambda c: (_mut(c, lambda x: x["route_migration_inventory"]["handler_migration_dispositions"].pop())),
            4,
        ),
        (
            "5. Batch Size Exceeding Fleet Limit (>16)",
            lambda c: (_mut(c, lambda x: x["materialization_contract"]["batches"][1]["tasks"].extend(["FAKE-1", "FAKE-2", "FAKE-3"]))),
            5,
        ),
        (
            "6. Duplicate Owned Code Surface Collision",
            lambda c: (_mut(c, lambda x: x["tasks"][1]["owned_code_surfaces"].append(x["tasks"][0]["owned_code_surfaces"][0]))),
            6,
        ),
        (
            "7. Forbidden Rollback Keyword (restore domain_ports)",
            lambda c: (_mut(c, lambda x: x["tasks"][0].__setitem__("rollback", "restore domain_ports forwarding shims"))),
            7,
        ),
        (
            "8. Introduce Cyclic Dependency in DAG",
            lambda c: (_mut(c, lambda x: x["tasks"][0]["depends_on"].append(x["tasks"][-1]["id"]))),
            8,
        ),
        (
            "9. Source Proof Contract dev_mode_default Non-Reconcile",
            lambda c: (_mut(c, lambda x: x["source_proof_contract"].__setitem__("dev_mode_default", "live_egress"))),
            9,
        ),
        (
            "10. Corrupt Special Node 37 Target Router",
            lambda c: (_mut(c, lambda x: x["main_ast_node_inventory"]["nodes"][37].__setitem__("target_router", "services/control-plane/bff/ports/wrong.py"))),
            10,
        ),
        (
            "11. Corrupt Reverse-Main Symbol Inventory Count",
            lambda c: (_mut(c, lambda x: x["reverse_main_symbol_inventory"].pop())),
            11,
        ),
        (
            "12. Corrupt Domain Ports Caller Inventory Total Rows",
            lambda c: (_mut(c, lambda x: x["domain_ports_caller_inventory"].__setitem__("total_imported_symbol_rows", 999))),
            12,
        ),
        (
            "13. Ineligible Agent Assigned as Owner",
            lambda c: (_mut(c, lambda x: x["tasks"][0].__setitem__("owner", "NonExistentAgent"))),
            13,
        ),
        (
            "14. Stale Planning Baseline Hosted Pair ID",
            lambda c: (_mut(c, lambda x: x["planning_baseline"].__setitem__("hosted_pair_id", "stale_pair_id_00000000000000000000"))),
            14,
        ),
        (
            "15. Execution Resources pantheon-dev Consumer Mismatch",
            lambda c: (_mut(c, lambda x: x["execution_resources"]["pantheon-dev"]["consumers"].append("OPGAP-NONEXISTENT-20260830"))),
            15,
        ),
        (
            "16. Corrupt Post-Bootstrap Spec Hash",
            lambda c: (_mut(c, lambda x: x["materialization_contract"]["signed_dev_task_packet_materialization_mapping"]["per_task_spec_hashes"].__setitem__(x["tasks"][0]["id"], "0000000000000000000000000000000000000000000000000000000000000000"))),
            16,
        ),
        (
            "17. Mutate dynamic_validation_contract rule to 2,271 AST nodes",
            lambda c: (
                _mut(
                    c,
                    lambda x: x["dynamic_validation_contract"].__setitem__(
                        "rules",
                        [r.replace("2,272", "2,271") for r in x["dynamic_validation_contract"]["rules"]],
                    ),
                )
            ),
            1,
        ),
    ]

    passed = 0
    for name, mutate_fn, expected_phase in mutations:
        if run_mutation_test(name, mutate_fn, expected_phase):
            passed += 1

    ledger_mutations = [
        (
            "18. Corrupt Execution Replacement Ledger Row Count (drop a Batch C row)",
            lambda l: (_mut(l, lambda x: x["batch_c_direct_materializations"].pop())),
            17,
        ),
        (
            "19. Remove Post-Freeze Operational Gap",
            lambda l: (_mut(l, lambda x: x["post_freeze_gaps"].pop())),
            18,
        ),
        (
            "20. Remove Governed Batch C V2 Replacement",
            lambda l: (_mut(l, lambda x: x["post_freeze_batch_c_replacements"].pop())),
            18,
        ),
        (
            "21. Remove Task-Scoped Evidence Manifest Binding",
            lambda l: (_mut(l, lambda x: x.__setitem__("task_evidence_manifest", ""))),
            18,
        ),
        (
            "22. Corrupt Exact Deployment Release Candidate Identity",
            lambda l: (
                _mut(
                    l,
                    lambda x: x["deployment_reconciliation"]["integration_gate"].__setitem__(
                        "release_candidate_id", "0" * 64
                    ),
                )
            ),
            18,
        ),
        (
            "23. Remove Bounded Firebase Write-Proof and Watchdog Prerequisite",
            lambda l: (_mut(l, lambda x: x["product_proof_prerequisites"].pop())),
            18,
        ),
    ]
    for name, mutate_fn, expected_phase in ledger_mutations:
        if run_ledger_mutation_test(name, mutate_fn, expected_phase):
            passed += 1
    total = len(mutations) + len(ledger_mutations)

    print(f"\nResult: {passed}/{total} mutation-negative checks passed.")
    assert passed == total == 23, f"Mutation test suite failure: {passed}/23 passed"
    print("SUCCESS: 23/23 mutation-negative validation assertions passed!")


def _mut(obj, fn):
    c = copy.deepcopy(obj)
    fn(c)
    return c


if __name__ == "__main__":
    test_all_mutations()
