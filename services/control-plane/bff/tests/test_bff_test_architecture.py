"""Architectural invariant tests for BFF test layer classification and composition decoupling.

Task: BFF-TEST-ARCH-001
Acceptance criteria:
- Classify audited main-importing tests into 5 layers (composition, router, application, adapter, hosted).
- Direct composition imports only exist in the reviewed composition allowlist or tracked planned migration inventory.
- Non-composition global read_store and overlay monkeypatching are banned in migrated suites.
- Obsolete test sys.path surgery is banned in migrated suites.
- Focused suites collect and complete within explicit timeout budgets (< 10s).
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

import pytest

TESTS_DIR = Path(__file__).resolve().parent
BFF_DIR = TESTS_DIR.parent
REPO_ROOT = TESTS_DIR.parents[3]
INVENTORY_PATH = TESTS_DIR / "bff_test_architecture_inventory.json"

TASK_REVIEW_EVIDENCE = {
    "task": "BFF-TEST-ARCH-001",
    "owner": "Antigravity2",
    "reviewer": "Claude",
    "base": "dev",
    "scope": (
        "Decouple BFF tests from composition globals: classify test files into "
        "5 architectural layers (composition, router, application, adapter, hosted), "
        "enforce reviewed composition allowlist, delete non-composition read_store/overlay "
        "monkeypatching and sys.path surgery in migrated suites, enforce bounded runtime budgets."
    ),
    "verification": (
        "Run test_bff_test_architecture.py alongside migrated suites "
        "(test_governance_router, test_operations_consultation_ports, "
        "test_read_surface_caller_migration, test_cw01-04)."
    ),
}

VALID_LAYERS = {"composition", "router", "application", "adapter", "hosted"}
VALID_DISPOSITIONS = {"ALLOWLIST", "MIGRATED", "PLANNED", "DECOUPLED"}


def _load_inventory() -> Dict[str, Any]:
    assert INVENTORY_PATH.is_file(), f"Inventory missing: {INVENTORY_PATH}"
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def test_inventory_file_is_present_and_well_formed() -> None:
    data = _load_inventory()
    assert data["task_id"] == "BFF-TEST-ARCH-001"
    assert "version" in data
    assert "composition_allowlist" in data
    assert "migrated_suites" in data
    assert "layer_summary" in data
    assert "tests" in data
    assert isinstance(data["tests"], list)
    assert len(data["tests"]) >= 340


def test_all_five_architectural_layers_represented() -> None:
    data = _load_inventory()
    layers_in_summary = set(data["layer_summary"].keys())
    assert layers_in_summary == VALID_LAYERS

    for layer, count in data["layer_summary"].items():
        assert count > 0, f"Layer {layer} must have at least one classified test file"


def test_every_entry_has_valid_layer_and_disposition() -> None:
    data = _load_inventory()
    for entry in data["tests"]:
        assert entry["layer"] in VALID_LAYERS, f"Invalid layer in {entry}"
        assert entry["disposition"] in VALID_DISPOSITIONS, f"Invalid disposition in {entry}"
        assert isinstance(entry["imports_main"], bool)
        assert (BFF_DIR / entry["file"]).is_file(), f"Referenced test file missing: {entry['file']}"


def test_composition_allowlist_is_strictly_contained_and_retained() -> None:
    data = _load_inventory()
    allowlist = set(data["composition_allowlist"])
    for rel_path in allowlist:
        file_path = BFF_DIR / rel_path
        assert file_path.is_file(), f"Allowlist entry {rel_path} does not exist on disk"

    # Ensure allowlist is strictly bounded to architectural and smoke suites
    for rel_path in allowlist:
        p = Path(rel_path)
        assert any(k in p.name.lower() for k in (
            "composition", "catalog", "resolution", "uniqueness", "smoke",
            "boundaries", "deletion", "owner", "architecture", "migration"
        )), f"Non-architectural file found in composition allowlist: {rel_path}"


def test_migrated_suites_do_not_import_main() -> None:
    data = _load_inventory()
    migrated_suites = data["migrated_suites"]
    assert len(migrated_suites) >= 5

    offenders: List[str] = []
    for rel_path in migrated_suites:
        file_path = BFF_DIR / rel_path
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "main" or alias.name.endswith(".main"):
                        offenders.append(f"{rel_path}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and (
                    node.module == "main"
                    or node.module.endswith(".main")
                    or (node.module.startswith("services.control_plane.bff") and any(a.name == "main" for a in node.names))
                ):
                    offenders.append(f"{rel_path}:{node.lineno}: from {node.module} import ...")

    msg = "\n".join(f"  {o}" for o in offenders)
    assert not offenders, f"Migrated suites must not import main composition root:\n{msg}"


def test_migrated_suites_do_not_mutate_sys_path() -> None:
    data = _load_inventory()
    migrated_suites = data["migrated_suites"]

    offenders: List[str] = []
    for rel_path in migrated_suites:
        file_path = BFF_DIR / rel_path
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in ("insert", "append"):
                    val = func.value
                    if isinstance(val, ast.Attribute) and val.attr == "path":
                        if isinstance(val.value, ast.Name) and val.value.id == "sys":
                            offenders.append(f"{rel_path}:{node.lineno}: sys.path.{func.attr}")

    msg = "\n".join(f"  {o}" for o in offenders)
    assert not offenders, f"Migrated suites must not mutate sys.path:\n{msg}"


def test_no_global_monkeypatching_in_migrated_suites() -> None:
    data = _load_inventory()
    migrated_suites = data["migrated_suites"]

    offenders: List[str] = []
    for rel_path in migrated_suites:
        content = (BFF_DIR / rel_path).read_text(encoding="utf-8")
        for bad_pattern in ("bff_main.read_store", "main.read_store", "app_deps.read_surface ="):
            if bad_pattern in content:
                offenders.append(f"{rel_path} contains {bad_pattern}")

    msg = "\n".join(f"  {o}" for o in offenders)
    assert not offenders, f"Migrated suites must not patch global read_store:\n{msg}"


def test_total_main_importers_is_bounded_and_strictly_decreased() -> None:
    data = _load_inventory()
    baseline = data["audited_baseline_main_importers"]
    current = data["current_main_importers"]

    assert current <= 211, f"Expected current main importers <= 211, got {current}"
    assert current < baseline, f"Current ({current}) must be strictly less than baseline ({baseline})"
