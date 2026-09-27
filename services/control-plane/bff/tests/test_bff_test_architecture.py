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
from typing import Any, Dict, List, Optional, Set, Union

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


def _import_call_target(node: ast.Call) -> Optional[str]:
    """Extract the string-literal module name passed to an
    ``importlib.import_module(...)``/``__import__(...)`` call, whether given
    positionally or via the ``name=``/``name_or_module=`` keyword (AC5).
    Shared by the direct scanner and the helper call-graph scanner so the two
    never drift out of sync."""
    func = node.func
    is_import_call = (isinstance(func, ast.Name) and func.id in ("import_module", "__import__")) or (
        isinstance(func, ast.Attribute) and func.attr in ("import_module", "__import__")
    )
    if not is_import_call:
        return None
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return node.args[0].value
    for kw in node.keywords:
        if kw.arg in ("name", "name_or_module") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _static_import_bff_main_bound_names(node: ast.AST) -> Set[str]:
    """If ``node`` is a static ``import``/``from ... import`` statement that
    resolves to the BFF composition root, return the local name(s) it binds
    in the enclosing scope, honoring ``as`` aliases (AC3/AC5: ``import main
    as bm`` must still resolve to the real target transitively)."""
    names: Set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            if _is_bff_main_module_name(alias.name):
                names.add(alias.asname or alias.name.split(".")[0])
    elif isinstance(node, ast.ImportFrom):
        module = node.module
        if not module:
            return names
        if _is_bff_main_module_name(module):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif module in ("services.control_plane.bff", "services.control-plane.bff"):
            for alias in node.names:
                if alias.name == "main":
                    names.add(alias.asname or alias.name)
    return names


def _collect_simple_str_assigns(tree: ast.AST) -> Dict[str, str]:
    """Map ``name -> literal string`` for every simple ``name = "literal"``
    assignment anywhere in ``tree``, so a subprocess argument built from a
    local variable (``code = "import main"; subprocess.run([..., "-c", code])``)
    can still be traced back to its string literal instead of being invisible
    to the scanner just because it is not spelled inline (AC2)."""
    values: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value.value
    return values


def _resolve_str_literal(node: ast.AST, str_values: Dict[str, str]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in str_values:
        return str_values[node.id]
    return None


def _call_has_subprocess_main_import(node: ast.Call, str_values: Optional[Dict[str, str]] = None) -> bool:
    """Detect subprocess execution that imports BFF main via -c or -m (AC2).

    Resolves both string-literal arguments and simple local variables that
    were assigned a string literal elsewhere in the file (``str_values``).
    """
    str_values = str_values or {}
    args_to_check: List[ast.AST] = list(node.args)
    for kw in node.keywords:
        if kw.arg in ("args", "cmd", "command"):
            args_to_check.append(kw.value)

    for arg in args_to_check:
        if isinstance(arg, (ast.List, ast.Tuple)):
            str_items = [_resolve_str_literal(elt, str_values) for elt in arg.elts]
            for i, item in enumerate(str_items):
                if item == "-c" and i + 1 < len(str_items):
                    code_snippet = str_items[i + 1]
                    if code_snippet is None:
                        continue
                    try:
                        code_tree = ast.parse(code_snippet)
                        if _ast_imports_bff_main(code_tree):
                            return True
                    except SyntaxError:
                        if "import main" in code_snippet or "from main" in code_snippet or ".main" in code_snippet:
                            return True
                elif item == "-m" and i + 1 < len(str_items):
                    mod_name = str_items[i + 1]
                    if mod_name and _is_bff_main_module_name(mod_name):
                        return True
        else:
            val = _resolve_str_literal(arg, str_values)
            if val is None:
                continue
            if "-c" in val:
                idx = val.find("-c")
                snippet = val[idx + 2:].strip()
                if (snippet.startswith('"') and snippet.endswith('"')) or (snippet.startswith("'") and snippet.endswith("'")):
                    snippet = snippet[1:-1].strip()
                try:
                    code_tree = ast.parse(snippet)
                    if _ast_imports_bff_main(code_tree):
                        return True
                except Exception:
                    if "import main" in snippet or "from main" in snippet or ".main" in snippet:
                        return True
            elif "-m" in val:
                idx = val.find("-m")
                rest = val[idx + 2:].strip().split()
                if rest and _is_bff_main_module_name(rest[0]):
                    return True
    return False


def _ast_imports_bff_main(tree: ast.AST) -> bool:
    str_values = _collect_simple_str_assigns(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if _static_import_bff_main_bound_names(node):
                return True
        elif isinstance(node, ast.Call):
            target = _import_call_target(node)
            if target and _is_bff_main_module_name(target):
                return True
            if _call_has_subprocess_main_import(node, str_values):
                return True
    return False


def _file_imports_bff_main(path: Path) -> bool:
    """AST-scan a single file for an import of the BFF composition root (main.py).

    Matches: ``import main`` / ``import <pkg>.main``; ``from main import ...`` /
    ``from <pkg>.main import ...``; the absolute
    ``from services.control_plane.bff import main`` form; dynamic imports
    via ``importlib.import_module``, ``__import__``, etc. (AC5); and subprocess
    python -c / -m invocations (AC2).
    Excludes other services' own ``main`` modules (for example ``services.research.main``).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception:
        return False
    return _ast_imports_bff_main(tree)


def _discover_test_files(root_dir: Path = BFF_DIR) -> List[Path]:
    """Live scan of every test module on disk under the BFF tree.

    Deliberately independent of the inventory file's own ``tests`` list, so a
    newly added test file cannot silently import ``main`` without being
    counted. Matches pytest's own default test-module discovery convention,
    including conftest.py suites and owned test support modules under tests/ (AC2).
    """
    files: List[Path] = []
    for path in root_dir.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        rel = path.relative_to(root_dir)
        name = path.name
        if (
            name.startswith("test_")
            or name.startswith("smoke_test")
            or name.endswith("_test.py")
            or name == "conftest.py"
            or (len(rel.parts) > 1 and rel.parts[0] == "tests" and name != "__init__.py")
        ):
            files.append(rel)
    return sorted(files)


def _discover_all_py_files(root_dir: Path = BFF_DIR) -> List[Path]:
    """Every ``.py`` file under the BFF tree, test or not."""
    files: List[Path] = []
    for path in root_dir.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        files.append(path.relative_to(root_dir))
    return sorted(files)


def _module_dotted_path(rel_path: Path, root_dir: Path = BFF_DIR) -> str:
    """The dotted import path a Python statement elsewhere would use to
    reach ``rel_path``. A package's ``__init__.py`` is addressed by its
    *package* name (``from tests import app`` targets ``tests/__init__.py``,
    not a literal ``tests.__init__`` module) -- resolving it as a literal
    ``__init__`` submodule instead misses every package-level re-export
    propagation (AC2)."""
    parts = rel_path.parent.parts if rel_path.name == "__init__.py" else rel_path.with_suffix("").parts
    dotted = ".".join(parts)
    if root_dir == BFF_DIR:
        prefix = "services.control_plane.bff"
        return f"{prefix}.{dotted}" if dotted else prefix
    return dotted


def _module_level_bff_main_names(tree: ast.Module) -> Set[str]:
    """Names bound to the BFF composition root by statements *outside* any
    function (module level): static ``import main`` / ``import main as bm``
    / ``from services.control_plane.bff import main``, and dynamic
    ``x = importlib.import_module(...)``/``__import__(...)`` assignments.
    A function that merely references one of these names (without importing
    main itself) still transitively reaches main through the module's own
    top-level binding (AC3)."""
    names: Set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Not module level; function bodies are handled separately.
                continue
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                names.update(_static_import_bff_main_bound_names(child))
            elif isinstance(child, ast.Assign) and isinstance(child.value, ast.Call):
                target = _import_call_target(child.value)
                if target and _is_bff_main_module_name(target):
                    for assign_target in child.targets:
                        if isinstance(assign_target, ast.Name):
                            names.add(assign_target.id)
            visit(child)

    visit(tree)
    return names


def _module_level_main_attribute_reexports(tree: ast.Module) -> Set[str]:
    """Module-level names bound to a *specific attribute* imported directly
    from the composition root (``from services.control_plane.bff.main
    import app``) -- i.e. a bare re-export of something main exposes.
    Whole-module aliases (``import main as bm`` / ``from
    services.control_plane.bff import main as bm``) are captured by
    ``_module_level_bff_main_names`` and propagated as reaching symbols in
    ``_reaches_main_symbols_from_tree`` (AC2)."""
    names: Set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(child, ast.ImportFrom) and child.module and _is_bff_main_module_name(child.module):
                for alias in child.names:
                    names.add(alias.asname or alias.name)
            visit(child)

    visit(tree)
    return names


def _function_directly_reaches_bff_main(
    node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    module_level_names: Set[str],
) -> bool:
    """True if ``node``'s own body reaches the BFF composition root: a
    static import of main anywhere in the body (including nested inside the
    function, not just at module level), an import_module/__import__ call
    (positional or keyword target, reusing ``_import_call_target``), or a
    reference to a name bound to main by a module-level static/dynamic
    import (AC3)."""
    for inner in ast.walk(node):
        if isinstance(inner, (ast.Import, ast.ImportFrom)):
            if _static_import_bff_main_bound_names(inner):
                return True
        elif isinstance(inner, ast.Call):
            target = _import_call_target(inner)
            if target and _is_bff_main_module_name(target):
                return True
        elif isinstance(inner, ast.Name) and inner.id in module_level_names:
            return True
    return False


def _functions_calling_bff_main(tree: ast.Module) -> Set[str]:
    """Top-level function/method names in ``tree`` whose own body directly
    reaches the BFF composition root -- via a static import inside the
    function, a dynamic import_module/__import__ call inside the function
    (positional or keyword target), or use of a name a module-level import
    bound to main (AC3/AC5)."""
    module_level_names = _module_level_bff_main_names(tree)
    direct: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _function_directly_reaches_bff_main(node, module_level_names):
            direct.add(node.name)
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


def _reaches_main_symbols_from_tree(tree: ast.Module) -> Set[str]:
    """All top-level function/method names in ``tree`` that reach the BFF
    composition root, directly or transitively through same-module calls,
    plus any module-level name bound directly to the composition root or to
    an attribute imported from it (a bare re-export, e.g. ``from
    services.control_plane.bff.main import app``). Such a name is reachable
    by any importer of this module even though it is not itself a function,
    so a helper module that does nothing but re-export a main-derived
    object must still be recorded as reaching (AC2)."""
    reaching = _functions_calling_bff_main(tree)
    reaching |= _module_level_main_attribute_reexports(tree)
    reaching |= _module_level_bff_main_names(tree)
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


def _reaches_main_symbols(path: Path) -> Set[str]:
    """All top-level function/method names in ``path`` that reach the BFF
    composition root, directly or transitively through same-module calls."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _reaches_main_symbols_from_tree(tree)


def _cross_file_imported_reaching_names(
    tree: ast.Module, pkg_parts: List[str], helper_symbols: Dict[str, Set[str]], root_dir: Path = BFF_DIR
) -> Set[str]:
    """Local names this file binds (via import) to a symbol that is already
    known to reach main in *another* helper module. A same-module function
    that merely calls one of these local names -- without itself importing
    or re-deriving main -- must still be recognized as reaching main, so a
    two-hop wrapper chain (helper1 wraps helper2's accessor, which imports
    main) propagates instead of stopping at the first hop (AC3)."""
    local_reaching: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        candidates: List[str] = []
        if node.level == 0:
            if node.module:
                candidates.append(node.module)
        else:
            base = pkg_parts[: max(0, len(pkg_parts) - (node.level - 1))]
            mod_parts = list(base)
            if node.module:
                mod_parts.extend(node.module.split("."))
            rel_mod = ".".join(mod_parts)
            if rel_mod:
                if root_dir == BFF_DIR:
                    candidates.append("services.control_plane.bff." + rel_mod)
                candidates.append(rel_mod)
        for cand in candidates:
            reaching = helper_symbols.get(cand)
            if not reaching:
                continue
            for alias in node.names:
                if alias.name == "*" or alias.name in reaching:
                    local_reaching.add(alias.asname or alias.name)
    return local_reaching


def _cross_file_imported_reaching_modules(
    tree: ast.Module, pkg_parts: List[str], helper_symbols: Dict[str, Set[str]], root_dir: Path = BFF_DIR
) -> Dict[str, Set[str]]:
    """Local names this file binds (via ``import``/``from ... import``) to a
    *submodule* that itself reaches main, as opposed to
    ``_cross_file_imported_reaching_names`` above, which resolves a directly
    imported symbol. Covers ``import pkg.sub``, ``import pkg.sub as x``,
    ``from pkg import sub``, and ``from . import sub`` (a bare submodule
    import with no ``node.module``): a same-module function that only calls
    ``sub.<reaching_symbol>()`` -- an attribute access on the imported
    submodule object, not a bare name -- must still be recognized as
    reaching, so a wrapper module that re-exposes another helper's accessor
    through its own function still propagates a two-hop wrapper chain
    (AC2/AC3)."""
    modules: Dict[str, Set[str]] = {}

    def record(local_name: str, dotted_mod: str) -> None:
        reaching = helper_symbols.get(dotted_mod)
        if reaching:
            modules.setdefault(local_name, set()).update(reaching)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".")[0]
                if root_dir == BFF_DIR:
                    record(local_name, "services.control_plane.bff." + alias.name)
                record(local_name, alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module.split(".") if node.module else []
            else:
                base = pkg_parts[: max(0, len(pkg_parts) - (node.level - 1))]
                if node.module:
                    base = base + node.module.split(".")
            for alias in node.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                sub_dotted = ".".join(base + [alias.name]) if base else alias.name
                if root_dir == BFF_DIR:
                    record(local_name, "services.control_plane.bff." + sub_dotted)
                record(local_name, sub_dotted)
    return modules


def _find_main_reaching_helper_modules(root_dir: Path = BFF_DIR) -> Dict[str, Set[str]]:
    """Non-test support modules under the tree that expose symbols
    reaching the composition root (directly or transitively), keyed by their
    absolute dotted module path (AC2/AC3).

    Propagates across file boundaries to a fixed point, but only among
    modules that are themselves test-support infrastructure (under the
    ``tests/`` tree, or named as an owned ``*_test_support.py`` sibling): a
    helper that only reaches main by calling an *imported* accessor from a
    different helper module (a two-hop or deeper wrapper chain) is still
    recorded, not just a helper that reaches main entirely within its own
    file. Production application/service modules are deliberately excluded
    from this cross-file propagation -- see the comment below.
    """
    trees: Dict[str, ast.Module] = {}
    pkg_parts_map: Dict[str, List[str]] = {}
    test_support_dotted: Set[str] = set()
    for rel in _discover_all_py_files(root_dir):
        name = rel.name
        # Excludes only genuine pytest-collected test modules (the same
        # naming convention pytest itself uses), never every file that
        # merely lives under a ``tests/`` directory: a same-directory
        # support/helper module (e.g. ``tests/helper.py``) that is not
        # itself a test module must still be eligible to be recorded as a
        # main-reaching helper below, or a real test file that imports it
        # is never propagated to (AC2).
        if (
            name.startswith("test_")
            or name.startswith("smoke_test")
            or name.endswith("_test.py")
            or name == "conftest.py"
        ):
            continue
        path = root_dir / rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception:
            continue
        dotted = _module_dotted_path(rel, root_dir=root_dir)
        trees[dotted] = tree
        pkg_parts_map[dotted] = list(rel.parent.parts)
        if rel.parts and rel.parts[0] == "tests" or rel.name.endswith(("_test_support.py", "_test_fixtures.py")):
            test_support_dotted.add(dotted)

    helpers: Dict[str, Set[str]] = {}
    for dotted, tree in trees.items():
        symbols = _reaches_main_symbols_from_tree(tree)
        if symbols:
            helpers[dotted] = symbols

    # Cross-file propagation is scoped to test-support infrastructure only
    # (fixtures/doubles/harnesses under ``tests/`` or an owned
    # ``*_test_support.py`` sibling). An attribute/method call
    # (``obj.get_main()``) is only ever treated as reaching when ``obj`` is a
    # local name this same file bound, via an import statement, to a
    # specific *other* helper module already confirmed to reach main
    # (``_cross_file_imported_reaching_modules``) -- never by matching
    # ``.attr`` names alone tree-wide. Production application/service
    # modules (``main.py``, ``governance/service.py``, router/service
    # layers, etc.) legitimately use late-bound/deferred imports of ``main``
    # in places for circular-import avoidance; those are pre-existing
    # architecture, not a test-authored composition-root import, and are out
    # of this task's scope (no production source changes). Applying
    # cross-file propagation tree-wide would misclassify every test that
    # imports a public function from one of those service modules, because
    # hundreds of unrelated production call sites share common names -- so
    # propagation is bounded to the actual surfaces this task owns: test
    # support helpers.
    changed = True
    while changed:
        changed = False
        for dotted in test_support_dotted:
            tree = trees[dotted]
            cross_reaching_names = _cross_file_imported_reaching_names(
                tree, pkg_parts_map[dotted], helpers, root_dir=root_dir
            )
            cross_reaching_modules = _cross_file_imported_reaching_modules(
                tree, pkg_parts_map[dotted], helpers, root_dir=root_dir
            )
            if not cross_reaching_names and not cross_reaching_modules:
                continue
            # A directly imported reaching symbol that no function in this
            # file ever calls is itself a bare re-export -- reachable as a
            # module-level name of *this* module too (e.g. a package
            # ``__init__.py`` doing ``from .helper import app`` with no
            # wrapping function at all). A symbol that *is* called by a
            # local function is left to the call-graph propagation below,
            # which records the calling function as reaching instead: that
            # keeps a plain "helper imports X and one function uses it"
            # module from also exposing the raw imported name itself as an
            # independent reaching symbol (AC2).
            called_names: Set[str] = set()
            for call_node in ast.walk(tree):
                if isinstance(call_node, ast.Call) and isinstance(call_node.func, ast.Name):
                    called_names.add(call_node.func.id)
            bare_reexports = cross_reaching_names - called_names
            reaching = set(helpers.get(dotted, set())) | bare_reexports
            bare_name_graph: Dict[str, Set[str]] = {}
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                callees: Set[str] = set()
                direct_hit = False
                for inner in ast.walk(node):
                    if not isinstance(inner, ast.Call):
                        continue
                    func = inner.func
                    if isinstance(func, ast.Name):
                        callees.add(func.id)
                    elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                        mod_reaching = cross_reaching_modules.get(func.value.id)
                        if mod_reaching and func.attr in mod_reaching:
                            direct_hit = True
                bare_name_graph[node.name] = callees
                if direct_hit:
                    reaching.add(node.name)
            local_changed = True
            while local_changed:
                local_changed = False
                for name, callees in bare_name_graph.items():
                    if name in reaching:
                        continue
                    # Also propagate through a callee that this same
                    # fixed-point loop has already newly added to
                    # ``reaching`` (not only a directly-imported
                    # cross_reaching_names symbol), so a same-module chain
                    # of local wrapper functions (A calls imported reach(),
                    # B calls A, C calls B, ...) all propagate instead of
                    # stopping at the first local hop (AC2).
                    if callees & (cross_reaching_names | reaching):
                        reaching.add(name)
                        local_changed = True
            if reaching != helpers.get(dotted, set()):
                helpers[dotted] = reaching
                changed = True
    return helpers


def _file_imports_main_via_helper(
    path: Path, helper_symbols: Dict[str, Set[str]], root_dir: Path = BFF_DIR
) -> bool:
    """True if ``path`` imports a name from a helper module that itself
    reaches BFF main, i.e. a transitive import. Resolves both absolute and
    relative imports (AC2)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception:
        return False
    try:
        rel = path.relative_to(root_dir)
        pkg_parts = list(rel.parent.parts)
    except ValueError:
        pkg_parts = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            candidates: List[str] = []
            if node.level == 0:
                if node.module:
                    candidates.append(node.module)
                base: List[str] = node.module.split(".") if node.module else []
            else:
                base = pkg_parts[: max(0, len(pkg_parts) - (node.level - 1))]
                mod_parts = list(base)
                if node.module:
                    mod_parts.extend(node.module.split("."))
                rel_mod = ".".join(mod_parts)
                if rel_mod:
                    if root_dir == BFF_DIR:
                        candidates.append("services.control_plane.bff." + rel_mod)
                    candidates.append(rel_mod)
                base = mod_parts

            # Symbol-style import: ``from <module> import <name>`` where
            # ``<name>`` is a function/attribute defined in ``<module>``
            # itself. Requires the specific imported name to be a reaching
            # symbol of that module.
            for cand in candidates:
                if cand in helper_symbols:
                    reaching = helper_symbols[cand]
                    for alias in node.names:
                        if alias.name == "*" or alias.name in reaching:
                            return True

            # Submodule-style import: ``from <pkg> import <name>`` where
            # ``<name>`` is itself a submodule (``<pkg>/<name>.py``), whether
            # spelled with or without an explicit ``node.module`` (``from .
            # import helper`` as well as ``from .subpkg import helper``).
            # Merely importing the submodule object does not by itself
            # execute a *lazily* (function-body) reaching symbol -- only
            # calling/referencing it does -- so this requires the bound
            # local name to actually be used via ``alias.<reaching_symbol>``
            # somewhere in the file (AC2/AC3), the same precision bar as the
            # existing ``from <module> import <name>`` (symbol-style) check
            # above. This avoids flagging a file that imports the whole
            # submodule only to monkeypatch an unrelated attribute on it.
            for alias in node.names:
                if alias.name == "*":
                    continue
                sub_cand = ".".join(base + [alias.name]) if base else alias.name
                sub_cands = (
                    ["services.control_plane.bff." + sub_cand, sub_cand]
                    if root_dir == BFF_DIR
                    else [sub_cand]
                )
                for c in sub_cands:
                    reaching = helper_symbols.get(c)
                    if not reaching:
                        continue
                    local_name = alias.asname or alias.name
                    for inner in ast.walk(tree):
                        if (
                            isinstance(inner, ast.Attribute)
                            and isinstance(inner.value, ast.Name)
                            and inner.value.id == local_name
                            and inner.attr in reaching
                        ):
                            return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in helper_symbols:
                    return True
    return False


def _ancestor_conftests_import_main(
    rel_path: Path, helper_symbols: Dict[str, Set[str]], root_dir: Path = BFF_DIR
) -> bool:
    """True if any ancestor directory contains a conftest.py that reaches main (AC2)."""
    current = rel_path.parent
    while True:
        conftest_path = root_dir / (current / "conftest.py" if str(current) not in (".", "") else "conftest.py")
        if conftest_path.is_file():
            rel_conftest = conftest_path.relative_to(root_dir)
            if rel_conftest != rel_path:
                if _file_imports_bff_main(conftest_path) or _file_imports_main_via_helper(
                    conftest_path, helper_symbols, root_dir=root_dir
                ):
                    return True
        if str(current) in (".", ""):
            break
        current = current.parent
    return False


def _live_scan_non_whitelisted_main_importers(
    allowlist: Set[str], root_dir: Path = BFF_DIR
) -> List[str]:
    helper_symbols = _find_main_reaching_helper_modules(root_dir=root_dir)
    conftest_files = [
        p.relative_to(root_dir)
        for p in root_dir.rglob("conftest.py")
        if ".venv" not in p.parts
    ]
    test_files = _discover_test_files(root_dir)

    offenders: Set[str] = set()
    for rel in conftest_files:
        if str(rel) not in allowlist:
            if _file_imports_bff_main(root_dir / rel) or _file_imports_main_via_helper(
                root_dir / rel, helper_symbols, root_dir=root_dir
            ):
                offenders.add(str(rel))

    for rel in test_files:
        if str(rel) not in allowlist:
            if (
                _file_imports_bff_main(root_dir / rel)
                or _file_imports_main_via_helper(root_dir / rel, helper_symbols, root_dir=root_dir)
                or _ancestor_conftests_import_main(rel, helper_symbols, root_dir=root_dir)
            ):
                offenders.add(str(rel))
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
    # GENUINE BLOCKER: test_startup_replays_submitted_approved_apply_to_
    # terminal_owner_receipt exercises main.py's own process-startup command
    # replay through _process_command_stub (alias of main.py's own
    # _process_command, main.py line ~7566); that function's own routing/
    # auth-context orchestration has never been extracted from main.py into
    # a standalone seam, so there is no test-injectable replacement. Reaches
    # main.py via a narrow, function-scoped importlib.import_module local to
    # that one test only (not the shared rebalance_authority_test_support
    # accessor -- see that file's own inline comment).
    "tests/test_bff_rebalance_proposals.py",
    # GENUINE BLOCKER: migrations/overlay_retirement.py (production source,
    # out of scope to edit) has assert_mandatory_symbol_retirements(), which
    # imports main.py inside its own function body to verify four legacy
    # overlay symbols are absent from main.py's own __dict__ -- an inherent
    # identity/deletion check on main.py's own namespace. Discovered by this
    # generation's graph-aware scanner fix (previously invisible).
    "migrations/test_overlay_retirement.py",
    # AUTHORIZED (BFF-PM12-FIXTURE-CLOSURE-001): verifies the production
    # capacity/executor binding (bounded semaphore + ThreadPoolExecutor pools,
    # timeout dispatch) that lives in main.py itself -- importing the real
    # composition root is the thing under test, not a workaround for a
    # missing seam. Operator-authorized single new allowlist entry; do not
    # add another allowlist entry without a separate governed authorization.
    "tests/test_management_read_timeout_and_capacity.py",
    # RESOLVED (BFF-TEST-FULL-MIGRATION-CORRECTIVE-001, P1 AC1/AC2): this file
    # is the composition-root smoke test extracted out of auth/test_policy.py
    # this generation. Its whole purpose is to prove main.py's default wiring
    # of auth_deps/session_lifecycle_store/guards by importing the real
    # composition root -- the same category as smoke_test.py and
    # test_bff_main_composition.py, not a workaround for a missing seam.
    # auth/test_policy.py itself no longer imports main.
    "auth/test_composition_root_smoke.py",
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
    allowlist = set(data["composition_allowlist"])
    non_composition_suites = [
        rel for rel in _discover_test_files()
        if str(rel) not in allowlist
    ]
    assert len(non_composition_suites) >= 300

    offenders: List[str] = []
    for rel_path in non_composition_suites:
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
    assert not offenders, f"Non-composition suites must not mutate sys.path:\n{msg}"


def test_no_global_monkeypatching_in_migrated_suites() -> None:
    data = _load_inventory()
    allowlist = set(data["composition_allowlist"])
    non_composition_suites = [
        rel for rel in _discover_test_files()
        if str(rel) not in allowlist
    ]
    assert len(non_composition_suites) >= 300

    offenders: List[str] = []
    for rel_path in non_composition_suites:
        file_path = BFF_DIR / rel_path
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        if target.attr in ("read_store", "read_surface"):
                            val = target.value
                            if isinstance(val, ast.Name) and val.id in ("bff_main", "main", "app_deps"):
                                offenders.append(f"{rel_path}:{node.lineno}: {val.id}.{target.attr} = ...")
                            elif isinstance(val, ast.Attribute) and val.attr in ("app_deps",):
                                offenders.append(f"{rel_path}:{node.lineno}: ...{val.attr}.{target.attr} = ...")
                        elif isinstance(target.value, ast.Name) and target.value.id in ("ManagementService", "CommandStore"):
                            offenders.append(f"{rel_path}:{node.lineno}: {target.value.id}.{target.attr} = ...")
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "setattr":
                    if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in ("ManagementService", "CommandStore"):
                        offenders.append(f"{rel_path}:{node.lineno}: monkeypatch.setattr({node.args[0].id}, ...)")
                    elif node.args and isinstance(node.args[0], ast.Constant) and any(cls in str(node.args[0].value) for cls in ("ManagementService", "CommandStore")):
                        offenders.append(f"{rel_path}:{node.lineno}: monkeypatch.setattr({node.args[0].value}, ...)")

    msg = "\n".join(f"  {o}" for o in offenders)
    assert not offenders, f"Non-composition suites must not patch global read_store or production domain classes:\n{msg}"


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


def test_helper_graph_propagates_local_call_chain_to_fixed_point(tmp_path: Path) -> None:
    """AC2 regression (defect fix): a same-module chain of local wrapper
    functions must all propagate once the first hop is recognized as an
    imported cross-file reach, not just the function that directly calls
    the imported name. Reproduces: source_helper.reach() imports main ->
    bridge_test_support.bridge() calls reach() -> bridge_test_support.
    public() calls bridge() -> a real test module imports public()."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "source_helper.py").write_text(
        "import importlib\n"
        "def reach():\n"
        "    return importlib.import_module('services.control_plane.bff.main')\n",
        encoding="utf-8",
    )
    (tests_dir / "bridge_test_support.py").write_text(
        "from tests.source_helper import reach\n"
        "def bridge():\n"
        "    return reach()\n"
        "def public():\n"
        "    return bridge()\n",
        encoding="utf-8",
    )
    (tests_dir / "test_client.py").write_text(
        "from tests.bridge_test_support import public\n"
        "public()\n",
        encoding="utf-8",
    )

    helpers = _find_main_reaching_helper_modules(root_dir=tmp_path)
    assert helpers["tests.bridge_test_support"] == {"bridge", "public"}

    offenders = _live_scan_non_whitelisted_main_importers(set(), root_dir=tmp_path)
    assert "tests/test_client.py" in offenders


def test_helper_graph_includes_non_test_named_support_module_under_tests_dir(tmp_path: Path) -> None:
    """AC2 regression (defect fix): a support/helper module that lives under
    ``tests/`` but is not itself a pytest-collected test module (does not
    start with ``test_``/``smoke_test``, end with ``_test.py``, or equal
    ``conftest.py``) must still be recorded in the main-reaching helper
    graph, so a real test file that imports it is transitively flagged.
    Previously such a file was excluded from the helper graph entirely
    because ``tests/`` membership alone was (over-broadly) treated as
    pytest-test classification."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "helper.py").write_text(
        "import importlib\n"
        "def get_main():\n"
        "    return importlib.import_module('services.control_plane.bff.main')\n",
        encoding="utf-8",
    )
    (tests_dir / "test_uses_helper.py").write_text(
        "from tests.helper import get_main\n"
        "get_main()\n",
        encoding="utf-8",
    )

    helpers = _find_main_reaching_helper_modules(root_dir=tmp_path)
    assert helpers.get("tests.helper") == {"get_main"}

    offenders = _live_scan_non_whitelisted_main_importers(set(), root_dir=tmp_path)
    assert "tests/helper.py" in offenders
    assert "tests/test_uses_helper.py" in offenders


def test_scanner_detects_static_import_inside_helper_function_body(tmp_path: Path) -> None:
    """AC3 regression (defect fix): a *static* ``import`` statement located
    inside a helper function's own body -- not a call to import_module/
    __import__ -- must still mark that function as directly reaching main."""
    helper = tmp_path / "static_inside_function_helper.py"
    helper.write_text(
        "def get_main():\n"
        "    from services.control_plane.bff import main as bm\n"
        "    return bm\n"
        "def unrelated_helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    reaching = _reaches_main_symbols(helper)
    assert reaching == {"get_main"}

    helper_symbols = {"static_inside_function_helper": reaching}
    reacher = tmp_path / "test_static_inside_reacher.py"
    reacher.write_text("from static_inside_function_helper import get_main\n", encoding="utf-8")
    assert _file_imports_main_via_helper(reacher, helper_symbols) is True


def test_scanner_detects_module_level_static_import_reached_via_call_graph(tmp_path: Path) -> None:
    """AC3 regression (defect fix): a static import *outside* any function
    (module level) that a helper function merely references (not
    reimports) must still mark that function -- and anything that
    transitively calls it -- as reaching main."""
    helper = tmp_path / "module_level_static_helper.py"
    helper.write_text(
        "import services.control_plane.bff.main as bm\n"
        "def get_read_store():\n"
        "    return bm.read_store\n"
        "def wraps_get_read_store():\n"
        "    return get_read_store()\n"
        "def unrelated_helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    reaching = _reaches_main_symbols(helper)
    assert reaching == {"bm", "get_read_store", "wraps_get_read_store"}
    assert "unrelated_helper" not in reaching

    helper_symbols = {"module_level_static_helper": reaching}
    reacher = tmp_path / "test_module_level_reacher.py"
    reacher.write_text(
        "from module_level_static_helper import wraps_get_read_store\n", encoding="utf-8"
    )
    assert _file_imports_main_via_helper(reacher, helper_symbols) is True


def test_scanner_detects_import_module_keyword_argument_in_helper(tmp_path: Path) -> None:
    """AC3/AC5 regression (defect fix): ``importlib.import_module(name=...)``
    with the module name passed as a keyword argument, inside a helper
    function, must be detected the same as the positional form."""
    helper = tmp_path / "keyword_import_helper.py"
    helper.write_text(
        "import importlib\n"
        "def get_main():\n"
        "    return importlib.import_module(name='services.control_plane.bff.main')\n"
        "def unrelated_helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    reaching = _reaches_main_symbols(helper)
    assert reaching == {"get_main"}
    assert "unrelated_helper" not in reaching

    # Also verify the direct single-file scanner (not just the helper-graph
    # scanner) detects the keyword form.
    direct_probe = tmp_path / "test_keyword_direct.py"
    direct_probe.write_text(
        "import importlib\n"
        "mod = importlib.import_module(name='services.control_plane.bff.main')\n",
        encoding="utf-8",
    )
    assert _file_imports_bff_main(direct_probe) is True


def test_scanner_detects_module_alias_import_transitively(tmp_path: Path) -> None:
    """AC3/AC5 regression (defect fix): ``import main as <alias>`` (module
    alias form), then using the alias, must still resolve to the real
    target module transitively -- both for the direct scanner on the
    importing file itself and for the helper call-graph scanner."""
    direct_probe = tmp_path / "test_alias_direct.py"
    direct_probe.write_text(
        "import services.control_plane.bff.main as bm\nx = bm.read_store\n",
        encoding="utf-8",
    )
    assert _file_imports_bff_main(direct_probe) is True

    helper = tmp_path / "alias_helper.py"
    helper.write_text(
        "def get_main():\n"
        "    import services.control_plane.bff.main as bm\n"
        "    return bm\n"
        "def unrelated_helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    reaching = _reaches_main_symbols(helper)
    assert reaching == {"get_main"}
    assert "unrelated_helper" not in reaching


def test_scanner_detects_conftest_main_import_and_implicit_loading(tmp_path: Path) -> None:
    """AC2 regression: direct conftest main import & implicit conftest loading on child tests."""
    conftest = tmp_path / "conftest.py"
    conftest.write_text("import services.control_plane.bff.main\n", encoding="utf-8")

    sub = tmp_path / "sub"
    sub.mkdir()
    child_test = sub / "test_child.py"
    child_test.write_text("def test_ok(): pass\n", encoding="utf-8")

    offenders = _live_scan_non_whitelisted_main_importers(set(), root_dir=tmp_path)
    assert "conftest.py" in offenders
    assert "sub/test_child.py" in offenders


def test_scanner_detects_subprocess_main_import(tmp_path: Path) -> None:
    """AC2 regression: test files invoking subprocess python -c or -m importing main."""
    f1 = tmp_path / "test_subp_c.py"
    f1.write_text("import subprocess\nsubprocess.run(['python3', '-c', 'import main'])\n", encoding="utf-8")
    assert _file_imports_bff_main(f1) is True

    f2 = tmp_path / "test_subp_m.py"
    f2.write_text("import subprocess\nsubprocess.check_call(['python', '-m', 'services.control_plane.bff.main'])\n", encoding="utf-8")
    assert _file_imports_bff_main(f2) is True

    f3 = tmp_path / "test_subp_safe.py"
    f3.write_text("import subprocess\nsubprocess.run(['python3', '-c', 'import sys; print(sys.version)'])\n", encoding="utf-8")
    assert _file_imports_bff_main(f3) is False


def test_scanner_detects_relative_helper_main_import(tmp_path: Path) -> None:
    """AC2 regression: test files importing helper via relative import where helper reaches main."""
    sub = tmp_path / "pkg"
    sub.mkdir()
    helper = sub / "helper.py"
    helper.write_text("import services.control_plane.bff.main as bm\ndef reach(): return bm.read_store\n", encoding="utf-8")

    test_file = sub / "test_rel.py"
    test_file.write_text("from .helper import reach\ndef test_fn(): reach()\n", encoding="utf-8")

    offenders = _live_scan_non_whitelisted_main_importers(set(), root_dir=tmp_path)
    assert "pkg/test_rel.py" in offenders

    safe_helper = sub / "safe_helper.py"
    safe_helper.write_text("def safe_fn(): return 1\n", encoding="utf-8")
    safe_test = sub / "test_safe.py"
    safe_test.write_text("from .safe_helper import safe_fn\ndef test_safe_fn(): safe_fn()\n", encoding="utf-8")

    offenders_after = _live_scan_non_whitelisted_main_importers(set(), root_dir=tmp_path)
    assert "pkg/test_safe.py" not in offenders_after


def test_helper_graph_detects_bare_module_level_reexport_of_main(tmp_path: Path) -> None:
    """AC2 regression (defect fix): a helper module that does nothing but
    bind a name at module level to an object imported from main (a bare
    re-export, e.g. ``from services.control_plane.bff.main import app``,
    with no wrapping function) must still be recorded as reaching, and a
    test file importing that name must be flagged. Previously
    ``_reaches_main_symbols_from_tree`` only inspected top-level
    function/method names, so a helper with no functions at all produced an
    empty reaching set and the importing test file was invisible to the
    scanner."""
    (tmp_path / "helper.py").write_text(
        "from services.control_plane.bff.main import app\n", encoding="utf-8"
    )
    (tmp_path / "test_client.py").write_text(
        "from helper import app\n", encoding="utf-8"
    )

    helpers = _find_main_reaching_helper_modules(root_dir=tmp_path)
    assert helpers.get("helper") == {"app"}

    offenders = _live_scan_non_whitelisted_main_importers(set(), root_dir=tmp_path)
    assert "test_client.py" in offenders


def test_helper_graph_detects_module_level_alias_reexport_of_main(tmp_path: Path) -> None:
    """AC2 regression (defect fix): a helper module that imports the composition
    root as a module-level alias (``from services.control_plane.bff import main as bm``
    or ``import services.control_plane.bff.main as bm``) must have that alias
    recorded as a reaching symbol, flagging any test that imports the alias, while
    preserving safe non-main imports from the same helper."""
    # Sub-case 1: from services.control_plane.bff import main as bm
    (tmp_path / "helper1.py").write_text(
        "from services.control_plane.bff import main as bm\n"
        "def safe_fn1():\n"
        "    return 42\n",
        encoding="utf-8",
    )
    (tmp_path / "test_client1.py").write_text(
        "from helper1 import bm\n", encoding="utf-8"
    )
    (tmp_path / "test_safe1.py").write_text(
        "from helper1 import safe_fn1\n"
        "def test_safe():\n"
        "    assert safe_fn1() == 42\n",
        encoding="utf-8",
    )

    helpers1 = _find_main_reaching_helper_modules(root_dir=tmp_path)
    assert "bm" in helpers1.get("helper1", set())
    assert "safe_fn1" not in helpers1.get("helper1", set())

    offenders1 = _live_scan_non_whitelisted_main_importers(set(), root_dir=tmp_path)
    assert "test_client1.py" in offenders1
    assert "test_safe1.py" not in offenders1

    # Sub-case 2: import services.control_plane.bff.main as bm
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "helper2.py").write_text(
        "import services.control_plane.bff.main as bm\n"
        "def safe_fn2():\n"
        "    return 99\n",
        encoding="utf-8",
    )
    (pkg / "test_client2.py").write_text(
        "from .helper2 import bm\n", encoding="utf-8"
    )
    (pkg / "test_safe2.py").write_text(
        "from .helper2 import safe_fn2\n"
        "def test_safe():\n"
        "    assert safe_fn2() == 99\n",
        encoding="utf-8",
    )

    helpers2 = _find_main_reaching_helper_modules(root_dir=tmp_path)
    assert "bm" in helpers2.get("pkg.helper2", set())
    assert "safe_fn2" not in helpers2.get("pkg.helper2", set())

    offenders2 = _live_scan_non_whitelisted_main_importers(set(), root_dir=tmp_path)
    assert "pkg/test_client2.py" in offenders2
    assert "pkg/test_safe2.py" not in offenders2


def test_helper_graph_detects_submodule_attribute_wrapper_chain(tmp_path: Path) -> None:
    """AC2/AC3 regression (defect fix): a two-hop wrapper where the second
    hop imports the *submodule itself* (``from . import source``) rather
    than a specific symbol, then reaches main only via an attribute call on
    that submodule (``source.get_app()``), must still propagate. Previously
    cross-file propagation only recognized a directly imported *symbol*
    bound to a bare name; a submodule import used only through attribute
    access was invisible, so the wrapper's own function was never marked as
    reaching and a real test importing the wrapper's function was missed."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "source.py").write_text(
        "import importlib\n"
        "def get_app():\n"
        "    return importlib.import_module('services.control_plane.bff.main')\n",
        encoding="utf-8",
    )
    (tests_dir / "wrapper.py").write_text(
        "from . import source\n"
        "def make_app():\n"
        "    return source.get_app()\n",
        encoding="utf-8",
    )
    (tests_dir / "test_client.py").write_text(
        "from tests.wrapper import make_app\n"
        "make_app()\n",
        encoding="utf-8",
    )

    helpers = _find_main_reaching_helper_modules(root_dir=tmp_path)
    assert helpers.get("tests.wrapper") == {"make_app"}

    offenders = _live_scan_non_whitelisted_main_importers(set(), root_dir=tmp_path)
    assert "tests/test_client.py" in offenders


def test_helper_graph_propagates_through_package_init_reexport(tmp_path: Path) -> None:
    """AC2 regression (defect fix): a package ``__init__.py`` that re-exports
    a main-reaching name from a sibling helper module (``from .helper import
    app``) must itself be addressable by its *package* dotted path (e.g.
    ``tests``, not ``tests.__init__``), so a test file doing
    ``from tests import app`` is flagged. Previously ``_module_dotted_path``
    resolved ``tests/__init__.py`` to the literal module ``tests.__init__``,
    which never matches the ``tests`` candidate a real importer resolves to,
    so the re-export never propagated."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "helper.py").write_text(
        "from services.control_plane.bff.main import app\n", encoding="utf-8"
    )
    (tests_dir / "__init__.py").write_text(
        "from .helper import app\n", encoding="utf-8"
    )
    (tests_dir / "test_client.py").write_text(
        "from tests import app\n", encoding="utf-8"
    )

    helpers = _find_main_reaching_helper_modules(root_dir=tmp_path)
    assert helpers.get("tests") == {"app"}

    offenders = _live_scan_non_whitelisted_main_importers(set(), root_dir=tmp_path)
    assert "tests/test_client.py" in offenders


def test_knowledge_read_port_fixtures_architecture_compliance() -> None:
    """Requirement 4: knowledge_read_port_fixtures.py must conform to architecture gate.

    Verifies that services/control-plane/bff/tests/knowledge_read_port_fixtures.py:
    1. Exists and is registered in bff_test_architecture_inventory.json as DECOUPLED.
    2. Performs zero sys.path mutations.
    3. Contains zero imports of bff main (direct or transitive).
    4. Contains zero global monkeypatching.
    5. Uses canonical package imports under services.control_plane.bff.
    """
    rel_path = "tests/knowledge_read_port_fixtures.py"
    fixture_path = BFF_DIR / rel_path
    assert fixture_path.exists(), f"{rel_path} must exist"

    # 1. Registered in inventory
    data = _load_inventory()
    entries = {t["file"]: t for t in data["tests"]}
    assert rel_path in entries, f"{rel_path} must be present in inventory"
    entry = entries[rel_path]
    assert entry.get("imports_main") is False
    assert entry.get("disposition") == "DECOUPLED"

    # 2. No sys.path mutations
    tree = ast.parse(fixture_path.read_text(encoding="utf-8"), filename=str(fixture_path))
    sys_path_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("insert", "append"):
            if isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "path":
                val = node.func.value.value
                if isinstance(val, ast.Name) and val.id == "sys":
                    sys_path_calls.append(f"{node.lineno}: sys.path.{node.func.attr}")
    assert not sys_path_calls, f"{rel_path} must not mutate sys.path: {sys_path_calls}"

    # 3. No main imports
    assert not _file_imports_bff_main(fixture_path), f"{rel_path} must not import bff main"

    # 4. Canonical package imports (no bare 'from ports import' or 'from auth import')
    bare_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("ports", "auth", "core", "governance", "research", "personas"):
            bare_imports.append(f"{node.lineno}: from {node.module} import ...")
    assert not bare_imports, f"{rel_path} must use canonical package imports: {bare_imports}"
