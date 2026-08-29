"""
Static AST test verifying zero imports of read_store or in-memory persistence in services/agora/.
"""
from __future__ import annotations

import ast
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGORA_DIR = REPO_ROOT / "services" / "agora"


def test_no_read_store_or_in_memory_persistence_in_agora() -> None:
    assert AGORA_DIR.exists(), f"Directory {AGORA_DIR} does not exist"
    py_files = list(AGORA_DIR.glob("*.py"))
    assert len(py_files) >= 3, f"Expected at least 3 Python files in {AGORA_DIR}"

    for py_path in py_files:
        code = py_path.read_text(encoding="utf-8")
        tree = ast.parse(code, filename=str(py_path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "read_store" not in alias.name, (
                        f"Prohibited import 'read_store' found in {py_path.name}: {alias.name}"
                    )
                    assert "bff_local_dev_store" not in alias.name, (
                        f"Prohibited import 'bff_local_dev_store' found in {py_path.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "read_store" not in mod, (
                    f"Prohibited from-import of 'read_store' found in {py_path.name}: {mod}"
                )
                assert "bff_local_dev_store" not in mod, (
                    f"Prohibited from-import of 'bff_local_dev_store' found in {py_path.name}: {mod}"
                )
