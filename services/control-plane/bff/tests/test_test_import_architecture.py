"""Architecture gates for package-stable BFF test imports."""
from __future__ import annotations

import ast
from pathlib import Path


BFF_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BFF_ROOT.parents[2]
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


def test_baseline_test_layer_classification_completeness() -> None:
    """All 218 audited baseline cases (216 unique files) must be classified into the 5 layers."""
    import json

    evidence_path = (
        REPO_ROOT
        / "docs"
        / "deployment"
        / "evidence"
        / "BFF-TEST-ARCH-001"
        / "evidence.json"
    )
    assert evidence_path.is_file(), f"Evidence file missing: {evidence_path}"
    data = json.loads(evidence_path.read_text(encoding="utf-8"))
    classification = data["delivery"]["test_layer_classification"]

    allowed_layers = {"composition", "router", "application", "adapter", "hosted"}
    assert len(classification) == 216, f"Expected 216 classified files, got {len(classification)}"

    repo_root = REPO_ROOT
    for rel_path, layer in classification.items():
        assert (repo_root / rel_path).is_file(), f"Classified test file does not exist: {rel_path}"
        assert layer in allowed_layers, f"Invalid layer '{layer}' for {rel_path}"

    summary = data["delivery"]["test_layer_classification_summary"]
    for layer in allowed_layers:
        assert summary[layer] == sum(1 for l in classification.values() if l == layer)
    assert summary["total"] == 216


def test_direct_main_import_trend_gate_and_allowlist() -> None:
    """Direct main imports must not exceed the legacy ceiling and must belong to the allowlist."""
    import json

    evidence_path = (
        REPO_ROOT
        / "docs"
        / "deployment"
        / "evidence"
        / "BFF-TEST-ARCH-001"
        / "evidence.json"
    )
    data = json.loads(evidence_path.read_text(encoding="utf-8"))
    classification = data["delivery"]["test_layer_classification"]
    migrated = set(data["delivery"]["reconciled_migrated_tests"])

    repo_root = REPO_ROOT
    observed_importers: set[str] = set()
    offenders: list[str] = []

    for path in _test_modules():
        rel = str(path.relative_to(repo_root))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports_main = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("main", "services.control_plane.bff.main"):
                        imports_main = True
            elif isinstance(node, ast.ImportFrom):
                if node.module in ("main", "services.control_plane.bff.main"):
                    imports_main = True
                elif node.module == "services.control_plane.bff":
                    for alias in node.names:
                        if alias.name == "main":
                            imports_main = True
        if imports_main:
            observed_importers.add(rel)
            if rel not in classification or rel in migrated:
                offenders.append(rel)

    assert not offenders, "Test files importing main outside classified allowlist:\n" + "\n".join(offenders)
    assert len(observed_importers) <= 205, (
        f"Direct main import count {len(observed_importers)} exceeds monotonic ceiling 205"
    )


def test_migrated_tests_do_not_import_main_or_patch_globals() -> None:
    """Migrated router and application contract tests must not import main or patch read_store."""
    import json

    evidence_path = (
        REPO_ROOT
        / "docs"
        / "deployment"
        / "evidence"
        / "BFF-TEST-ARCH-001"
        / "evidence.json"
    )
    data = json.loads(evidence_path.read_text(encoding="utf-8"))
    migrated = data["delivery"]["reconciled_migrated_tests"]
    repo_root = REPO_ROOT

    for rel in migrated:
        target = repo_root / rel
        assert target.is_file(), f"Migrated test file missing: {rel}"
        text = target.read_text(encoding="utf-8")
        assert "bff_main.read_store" not in text, f"{rel} patches bff_main.read_store"
        assert "main as bff_main" not in text, f"{rel} imports bff_main"
