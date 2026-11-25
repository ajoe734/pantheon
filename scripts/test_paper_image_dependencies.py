"""PAPER-IMAGES-CANNOT-READ-APPROVALS-20261125.

The paper producer and paper workers run in images built from the execution-lean_runtime and paper-fleet-reconciler
locks.  The approval reader (``services.governance.approval_authority``) and the risk policy resolver
(``services.capital.risk_policy``, module level and lazy imports) need third-party packages; when a lock lacks one,
the import fails, the resolver reports "no approval", and every sized decision becomes HOLD.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCKS = {
    "execution-lean_runtime": ROOT / "dependencies/locks/services-execution-lean_runtime.txt",
    "paper_fleet_reconciler": ROOT / "dependencies/locks/services-paper_fleet_reconciler.txt",
}
REQUIREMENTS = {
    "execution-lean_runtime": ROOT / "services/execution/lean_runtime/requirements.txt",
    "paper_fleet_reconciler": ROOT / "services/paper_fleet_reconciler/requirements.txt",
}
ENTRY_MODULES = ("services/governance/approval_authority.py", "services/capital/risk_policy.py")
# import name -> distribution name where they differ
DIST = {"yaml": "pyyaml", "jwt": "pyjwt", "dateutil": "python-dateutil"}


def _first_party(top: str) -> bool:
    return (ROOT / top).is_dir() or (ROOT / f"{top}.py").is_file()


def _imports(path: Path, *, lazy: bool) -> set[str]:
    """Top-level names imported by ``path``; ``lazy`` also counts imports inside functions."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    nodes = ast.walk(tree) if lazy else _module_level(tree)
    for node in nodes:
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def _module_level(tree: ast.AST):
    stack = list(getattr(tree, "body", []))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.If, ast.Try, ast.With)):
            for field in ("body", "orelse", "finalbody", "handlers"):
                stack.extend(getattr(node, field, []) or [])
        elif isinstance(node, ast.ExceptHandler):
            stack.extend(node.body)


def _module_path(name: str) -> Path | None:
    parts = name.split(".")
    for end in range(len(parts), 0, -1):
        base = ROOT.joinpath(*parts[:end])
        if base.with_suffix(".py").is_file():
            return base.with_suffix(".py")
        if (base / "__init__.py").is_file():
            return base / "__init__.py"
    return None


def third_party_imports() -> dict[str, set[str]]:
    """Third-party top-level imports -> the entry modules that reach them (directly, any scope)."""
    found: dict[str, set[str]] = {}
    stdlib = set(sys.stdlib_module_names)
    for entry in ENTRY_MODULES:
        for name in _imports(ROOT / entry, lazy=True):
            top = name.split(".")[0]
            if top in stdlib or _first_party(top) or top == "__future__":
                continue
            found.setdefault(top, set()).add(entry)
    return found


def _locked(lock: Path) -> set[str]:
    return {m.group(1).lower().replace("_", "-") for m in re.finditer(r"^([A-Za-z0-9_.-]+)==", lock.read_text(), re.M)}


def _missing(lock: Path) -> list[str]:
    locked = _locked(lock)
    return sorted(
        top for top in third_party_imports()
        if DIST.get(top, top).lower().replace("_", "-") not in locked
    )


def test_the_import_computation_sees_the_packages_the_approval_reader_needs():
    assert "pydantic" in third_party_imports()


@pytest.mark.parametrize("image", sorted(LOCKS))
def test_paper_image_lock_carries_every_third_party_import_of_the_approval_path(image):
    assert _missing(LOCKS[image]) == [], f"{LOCKS[image].name} lacks {_missing(LOCKS[image])}"


@pytest.mark.parametrize("image", sorted(LOCKS))
def test_pydantic_is_required_and_pinned_like_the_other_service_locks(image):
    assert re.search(r"^pydantic\s*$", REQUIREMENTS[image].read_text(), re.M)
    core = (ROOT / "dependencies/constraints-core.txt").read_text()
    pinned = re.search(r"^pydantic==(\S+)", core, re.M).group(1)
    core_pin = re.search(r"^pydantic-core==(\S+)", core, re.M).group(1)
    text = LOCKS[image].read_text()
    assert re.search(rf"^pydantic=={re.escape(pinned)}$", text, re.M)
    assert re.search(rf"^pydantic-core=={re.escape(core_pin)}$", text, re.M)
