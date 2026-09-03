"""Regression guard: no function call in the large BFF modules may target a
name that is bound nowhere in the module (a NameError waiting to 500).

Verification campaign 2026-06-14, round 17. Generalizes F12 (round 16), where
`/bff/audit/*` called `_parse_rfc3339_header`, defined nowhere -> NameError ->
500 on every from_ts/to_ts. A whole-repo AST sweep found F12 was the only such
case; this test keeps `main.py` and `read_store.py` (the largest, most-edited
files, neither using star imports) free of the regression.
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

BFF_DIR = Path(__file__).resolve().parent
BUILTINS = set(dir(builtins))


def _bound_names(tree: ast.AST) -> set:
    names: set = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        if isinstance(n, ast.arguments):
            for a in n.args + n.posonlyargs + n.kwonlyargs:
                names.add(a.arg)
            if n.vararg:
                names.add(n.vararg.arg)
            if n.kwarg:
                names.add(n.kwarg.arg)
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                names.add((al.asname or al.name).split(".")[0])
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            names.add(n.id)
        if isinstance(n, ast.arg):
            names.add(n.arg)
        if isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
    return names


def _undefined_calls(path: Path) -> list:
    tree = ast.parse(path.read_text(), filename=str(path))
    # Guard reliability depends on no star imports.
    assert not any(
        isinstance(n, ast.ImportFrom) and any(al.name == "*" for al in n.names)
        for n in ast.walk(tree)
    ), f"{path.name} introduced a star import; this guard would be unreliable"
    bound = _bound_names(tree) | BUILTINS
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            name = n.func.id
            if name not in bound and not name.startswith("__"):
                bad.append((n.lineno, name))
    return bad


@pytest.mark.parametrize("filename", ["main.py", "read_store.py"])
def test_no_undefined_call_symbols(filename):
    file_path = BFF_DIR / filename
    if not file_path.exists():
        pytest.skip(f"{filename} has been deleted")
    bad = _undefined_calls(file_path)
    assert not bad, f"{filename} calls undefined names (NameError risk): {bad[:10]}"
