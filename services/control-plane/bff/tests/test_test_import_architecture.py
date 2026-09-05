"""Architecture gates for package-stable BFF test imports."""
from __future__ import annotations

import ast
from pathlib import Path


BFF_ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_FILE_LOADER_ALLOWLIST: set[str] = set()


def _test_modules() -> list[Path]:
    return sorted(BFF_ROOT.rglob("test*.py"))


def test_bff_tests_do_not_import_unqualified_product_modules() -> None:
    """Tests must resolve BFF code through its installed package root."""
    product_roots = {
        path.stem if path.is_file() else path.name
        for path in BFF_ROOT.iterdir()
        if (path.suffix == ".py" and not path.name.startswith("test_")) or path.is_dir()
    }
    offenders: list[str] = []
    for path in _test_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                if name.split(".", 1)[0] in product_roots and not name.startswith(
                    "services.control_plane.bff"
                ):
                    offenders.append(f"{path.relative_to(BFF_ROOT)}:{node.lineno}:{name}")
    assert not offenders, "Unqualified BFF product imports:\n" + "\n".join(offenders)


def test_sys_path_mutation_is_limited_to_external_file_loaders() -> None:
    """BFF package imports cannot be enabled by test-local path surgery."""
    offenders: list[str] = []
    observed_allowlist: set[str] = set()
    for path in _test_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if (
                isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "sys"
                and owner.attr == "path"
                and node.func.attr in {"insert", "append"}
            ):
                if path.name in EXTERNAL_FILE_LOADER_ALLOWLIST:
                    observed_allowlist.add(path.name)
                else:
                    offenders.append(f"{path.relative_to(BFF_ROOT)}:{node.lineno}")
    assert not offenders, "Unexpected sys.path mutation:\n" + "\n".join(offenders)
    assert observed_allowlist == EXTERNAL_FILE_LOADER_ALLOWLIST
