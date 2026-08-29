"""
Static AST verification proving zero imports of read_store or BFF main.py
in services/agora/, services/signal-store/, and tests/agora_write_owner/.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _check_no_forbidden_imports(directory: Path) -> list[str]:
    violations = []
    for py_file in directory.rglob("*.py"):
        if not py_file.is_file():
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception as exc:
            violations.append(f"Failed to parse {py_file}: {exc}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if "read_store" in name or "ReadSurfaceStore" in name:
                        violations.append(f"{py_file}:{node.lineno} imports forbidden module '{name}'")
                    if name.endswith("bff.main") or name == "main" and "bff" in str(py_file):
                        violations.append(f"{py_file}:{node.lineno} imports forbidden module '{name}'")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "read_store" in module or "ReadSurfaceStore" in module:
                    violations.append(f"{py_file}:{node.lineno} imports from forbidden module '{module}'")
                for alias in node.names:
                    if alias.name in ("read_store", "ReadSurfaceStore"):
                        violations.append(f"{py_file}:{node.lineno} imports forbidden symbol '{alias.name}'")
                    if module.endswith("bff") and alias.name == "main":
                        violations.append(f"{py_file}:{node.lineno} imports forbidden symbol '{alias.name}' from '{module}'")
    return violations


def test_services_agora_has_no_read_store_import() -> None:
    agora_dir = REPO_ROOT / "services" / "agora"
    assert agora_dir.exists(), "services/agora directory must exist"
    violations = _check_no_forbidden_imports(agora_dir)
    assert not violations, f"Forbidden read_store or BFF main.py imports in services/agora:\n" + "\n".join(violations)


def test_services_signal_store_has_no_read_store_import() -> None:
    sig_dir = REPO_ROOT / "services" / "signal-store"
    assert sig_dir.exists(), "services/signal-store directory must exist"
    violations = _check_no_forbidden_imports(sig_dir)
    assert not violations, f"Forbidden read_store or BFF main.py imports in services/signal-store:\n" + "\n".join(violations)


def test_agora_write_owner_tests_have_no_read_store_import() -> None:
    tests_dir = REPO_ROOT / "tests" / "agora_write_owner"
    assert tests_dir.exists(), "tests/agora_write_owner directory must exist"
    violations = _check_no_forbidden_imports(tests_dir)
    assert not violations, f"Forbidden read_store or BFF main.py imports in tests/agora_write_owner:\n" + "\n".join(violations)
