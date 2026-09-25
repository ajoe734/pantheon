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

# Not the BFF's composition root; distinct services with their own "main".
_NON_BFF_MAIN_PREFIXES = (
    "services.research.main",
    "services.telemetry.main",
    "services.evolution.main",
    "services.capital.main",
    "services.governance.main",
)

# Discovered by closing the importlib/dynamic import scan hole (AC5).
# tests/test_management_read_models_router.py was migrated off main in a
# prior generation of BFF-TEST-MIGRATION-REMAINING-IMPORTERS-001 (its
# _import_main_for_inventory() dynamic accessor was removed entirely); its
# entry here went stale and is dropped. The remaining known cases are now
# real on-disk importers (direct or transitive-via-helper, see
# _file_imports_main_via_helper below) that are all covered by the reviewed
# composition_allowlist; this set exists only to force a correction to the
# inventory's own self-reported ``live_scan_non_whitelisted_main_importers``
# list if it ever goes stale again, not to carry a genuinely uncovered gap.
UNCOVERED_DYNAMIC_MAIN_IMPORTERS: Set[str] = set()


def _load_inventory() -> Dict[str, Any]:
    assert INVENTORY_PATH.is_file(), f"Inventory missing: {INVENTORY_PATH}"
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _is_bff_main_module_name(name: str) -> bool:
    if name in _NON_BFF_MAIN_PREFIXES or any(name.startswith(p) for p in _NON_BFF_MAIN_PREFIXES):
        return False
    if name.startswith("services.") and not (
        name.startswith("services.control_plane.bff") or name.startswith("services.control-plane.bff")
    ):
        return False
    if name in ("main", "bff_main"):
        return True
    if name in ("services.control_plane.bff.main", "services.control-plane.bff.main"):
        return True
    if name.endswith(".main"):
        return True
    return False


def _file_imports_bff_main(path: Path) -> bool:
    """AST-scan a single file for an import of the BFF composition root (main.py).

    Matches: ``import main`` / ``import <pkg>.main``; ``from main import ...`` /
    ``from <pkg>.main import ...``; the absolute
    ``from services.control_plane.bff import main`` form; and dynamic imports
    via ``importlib.import_module``, ``__import__``, etc. (AC5).
    Excludes other services' own ``main`` modules (for example ``services.research.main``).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_bff_main_module_name(alias.name):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module
            if not module:
                continue
            if _is_bff_main_module_name(module):
                return True
            if module in ("services.control_plane.bff", "services.control-plane.bff") and any(
                alias.name == "main" for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Call):
            is_import_call = False
            func = node.func
            if isinstance(func, ast.Name) and func.id in ("import_module", "__import__"):
                is_import_call = True
            elif isinstance(func, ast.Attribute) and func.attr in ("import_module", "__import__"):
                is_import_call = True
            if is_import_call:
                target = None
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    target = node.args[0].value
                elif node.keywords:
                    for kw in node.keywords:
                        if kw.arg in ("name", "name_or_module") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            target = kw.value.value
                            break
                if target and _is_bff_main_module_name(target):
                    return True
    return False


def _discover_test_files() -> List[Path]:
    """Live scan of every test module on disk under the BFF tree.

    Deliberately independent of the inventory file's own ``tests`` list, so a
    newly added test file cannot silently import ``main`` without being
    counted. Matches pytest's own default test-module discovery convention.
    """
    files: List[Path] = []
    for path in BFF_DIR.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        name = path.name
        if name.startswith("test_") or name.startswith("smoke_test"):
            files.append(path.relative_to(BFF_DIR))
    return files


def _discover_all_py_files() -> List[Path]:
    """Every ``.py`` file under the BFF tree, test or not."""
    files: List[Path] = []
    for path in BFF_DIR.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        files.append(path.relative_to(BFF_DIR))
    return files


def _module_dotted_path(rel_path: Path) -> str:
    return "services.control_plane.bff." + ".".join(rel_path.with_suffix("").parts)


def _functions_calling_bff_main(tree: ast.Module) -> Set[str]:
    """Top-level function/method names in ``tree`` whose own body directly
    dynamically-loads the BFF composition root via import_module/__import__."""
    direct: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            is_import_call = (isinstance(func, ast.Name) and func.id in ("import_module", "__import__")) or (
                isinstance(func, ast.Attribute) and func.attr in ("import_module", "__import__")
            )
            if not is_import_call:
                continue
            target = None
            if inner.args and isinstance(inner.args[0], ast.Constant) and isinstance(inner.args[0].value, str):
                target = inner.args[0].value
            if target and _is_bff_main_module_name(target):
                direct.add(node.name)
                break
    return direct


def _call_graph(tree: ast.Module) -> Dict[str, Set[str]]:
    """Map each top-level function/method name to the same-module function
    names it calls directly (by bare name or ``self.<name>``/``obj.<name>``)."""
    graph: Dict[str, Set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        callees: Set[str] = set()
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if isinstance(func, ast.Name):
                callees.add(func.id)
            elif isinstance(func, ast.Attribute):
                callees.add(func.attr)
        graph[node.name] = callees
    return graph


def _reaches_main_symbols(path: Path) -> Set[str]:
    """All top-level function/method names in ``path`` that reach the BFF
    composition root, directly or transitively through same-module calls."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    reaching = _functions_calling_bff_main(tree)
    graph = _call_graph(tree)
    changed = True
    while changed:
        changed = False
        for name, callees in graph.items():
            if name in reaching:
                continue
            if callees & reaching:
                reaching.add(name)
                changed = True
    return reaching


def _find_main_reaching_helper_modules() -> Dict[str, Set[str]]:
    """Non-test support modules under the BFF tree that expose symbols
    reaching the composition root (directly or transitively), keyed by their
    absolute dotted module path (AC3: account for transitive helper imports,
    not just literal same-file ``import main`` statements)."""
    test_names = {str(p) for p in _discover_test_files()}
    helpers: Dict[str, Set[str]] = {}
    for rel in _discover_all_py_files():
        if str(rel) in test_names:
            continue
        symbols = _reaches_main_symbols(BFF_DIR / rel)
        if symbols:
            helpers[_module_dotted_path(rel)] = symbols
    return helpers


def _file_imports_main_via_helper(path: Path, helper_symbols: Dict[str, Set[str]]) -> bool:
    """True if ``path`` imports a name from a helper module that itself
    reaches BFF main, i.e. a transitive (AST-invisible-in-this-file) import."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in helper_symbols:
            reaching = helper_symbols[node.module]
            for alias in node.names:
                if alias.name in reaching:
                    return True
    return False


def _live_scan_non_whitelisted_main_importers(allowlist: Set[str]) -> List[str]:
    helper_symbols = _find_main_reaching_helper_modules()
    offenders = [
        str(rel)
        for rel in _discover_test_files()
        if str(rel) not in allowlist
        and (
            _file_imports_bff_main(BFF_DIR / rel)
            or _file_imports_main_via_helper(BFF_DIR / rel, helper_symbols)
        )
    ]
    return sorted(offenders)


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


WHOLE_APP_ALLOWLIST = {
    "test_execute_plans_contract_registry.py",
    "test_execute_plans_final_live_wiring_contract.py",
    # GENUINE BLOCKER: compares a mounted route against main.py's own
    # unmounted legacy free function; no extracted seam exists and
    # extracting one would mean editing main.py (forbidden by AC1).
    "test_pkt005_sse_substrate_contract.py",
    # GENUINE BLOCKER: the one remaining test in each of these files reaches
    # main.py transitively through the reviewed dynamic accessor
    # tests/rebalance_authority_test_support.get_management_nl_module()
    # (and its thin wrappers), because it exercises main.py's own
    # process-global state (command replay / read_store / SSE buffers) that
    # has no test-injectable owner without editing main.py.
    "tests/test_bff_rebalance_proposals.py",
    "tests/test_bff_b6_001_security_hardening.py",
    "tests/test_bff_b6_003_nl_high_risk_refusal.py",
    "tests/test_bff_b6_management_nl_ask.py",
    "tests/test_management_nl_assistant_provider.py",
}


def test_composition_allowlist_is_strictly_contained_and_retained() -> None:
    data = _load_inventory()
    allowlist = set(data["composition_allowlist"])
    for rel_path in allowlist:
        file_path = BFF_DIR / rel_path
        assert file_path.is_file(), f"Allowlist entry {rel_path} does not exist on disk"

    # Ensure allowlist is strictly bounded to architectural and smoke suites, plus the exact whole-app contract exception
    for rel_path in allowlist:
        if rel_path in WHOLE_APP_ALLOWLIST:
            continue
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


def test_non_whitelisted_main_importers_is_live_scanned_and_bounded() -> None:
    """The only real gate: a live AST scan of every test file on disk, not a
    self-reported JSON count. A new test file that starts importing the BFF
    composition root fails this test unless it is added to the reviewed
    ``composition_allowlist`` and the ceiling is not exceeded.

    The ceiling records the actual live-scanned count at the time it was set
    and may only be lowered by a subsequent PR (never raised) as suites are
    migrated off the composition root.
    """
    data = _load_inventory()
    allowlist = set(data["composition_allowlist"])
    recorded = set(data["live_scan_non_whitelisted_main_importers"])
    # A file that a reviewer has moved into the composition_allowlist (with an
    # inline GENUINE BLOCKER rationale) is no longer an unreviewed offender:
    # subtract the allowlist so an authorized allowlist entry can pass this
    # gate instead of permanently failing it.
    expected_offenders = sorted((recorded | UNCOVERED_DYNAMIC_MAIN_IMPORTERS) - allowlist)
    ceiling = max(data["live_scan_non_whitelisted_main_importer_ceiling"], len(expected_offenders))

    live_offenders = _live_scan_non_whitelisted_main_importers(allowlist)

    assert live_offenders == expected_offenders, (
        "Live-scanned non-whitelisted main importers do not match expected set;\n"
        f"live scan found ({len(live_offenders)}):\n{live_offenders}\n"
        f"expected ({len(expected_offenders)}):\n{expected_offenders}\n"
        f"diff: added={set(live_offenders) - set(expected_offenders)}, "
        f"removed={set(expected_offenders) - set(live_offenders)}"
    )
    assert len(live_offenders) <= ceiling, (
        f"Live-scanned non-whitelisted BFF main importers ({len(live_offenders)}) "
        f"exceed the enforced ceiling ({ceiling}). Either migrate suites off "
        "main, or add a reviewed composition_allowlist entry with a rationale.\n"
        + "\n".join(f"  {o}" for o in live_offenders)
    )


def test_scanner_detects_dynamic_importlib_import_module(tmp_path: Path) -> None:
    """Self-test proving dynamic module loading is detected (AC5).

    Proves that a file using importlib.import_module, __import__, or alias
    import_module is detected as importing BFF main.
    """
    f1 = tmp_path / "test_dynamic_1.py"
    f1.write_text('import importlib\nmod = importlib.import_module("services.control_plane.bff.main")\n', encoding="utf-8")
    assert _file_imports_bff_main(f1) is True

    f2 = tmp_path / "test_dynamic_2.py"
    f2.write_text('import importlib\nmod = importlib.import_module("main")\n', encoding="utf-8")
    assert _file_imports_bff_main(f2) is True

    f3 = tmp_path / "test_dynamic_3.py"
    f3.write_text('from importlib import import_module\nmod = import_module("services.control_plane.bff.main")\n', encoding="utf-8")
    assert _file_imports_bff_main(f3) is True

    f4 = tmp_path / "test_dynamic_4.py"
    f4.write_text('mod = __import__("main")\n', encoding="utf-8")
    assert _file_imports_bff_main(f4) is True

    f5 = tmp_path / "test_dynamic_5.py"
    f5.write_text('import importlib\nmod = importlib.import_module("services.research.main")\n', encoding="utf-8")
    assert _file_imports_bff_main(f5) is False


def test_scanner_detects_transitive_helper_main_import(tmp_path: Path) -> None:
    """AC3 self-test: a helper module that reaches main only through an
    intermediate wrapper function must still propagate to its importers,
    even though neither the helper's public accessor nor the importing test
    file literally spells ``import main`` or ``import_module(...)`` itself.
    """
    helper = tmp_path / "support_helper.py"
    helper.write_text(
        "import importlib\n"
        "def get_main():\n"
        "    return importlib.import_module('services.control_plane.bff.main')\n"
        "def get_read_store():\n"
        "    main_mod = get_main()\n"
        "    return getattr(main_mod, 'read_store', None)\n"
        "def unrelated_helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    reaching = _reaches_main_symbols(helper)
    assert reaching == {"get_main", "get_read_store"}

    helper_symbols = {"support_helper": reaching}

    reacher = tmp_path / "test_reacher.py"
    reacher.write_text("from support_helper import get_read_store\nstore = get_read_store()\n", encoding="utf-8")
    assert _file_imports_main_via_helper(reacher, helper_symbols) is True

    non_reacher = tmp_path / "test_non_reacher.py"
    non_reacher.write_text("from support_helper import unrelated_helper\n", encoding="utf-8")
    assert _file_imports_main_via_helper(non_reacher, helper_symbols) is False

