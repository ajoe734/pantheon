"""Final deletion guards for the former BFF read-surface God store."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
BFF_DIR = TESTS_DIR.parent
REPO_ROOT = TESTS_DIR.parents[3]
READ_STORE_PATH = BFF_DIR / "read_store.py"

sys.path.insert(0, str(BFF_DIR))

from models import EVIDENCE_CAPABILITY_MAP, OperatorIdentity  # noqa: E402
from read_store import (  # noqa: E402
    _market_persona_required_data_sources,
    redact_evidence_refs,
)


TASK_REVIEW_EVIDENCE = {
    "task": "ACG-RS-FINAL-DELETE-20260828",
    "owner": "Codex",
    "reviewer": "Codex2",
    "base": "dev",
    "scope": (
        "Delete the ReadSurfaceStore compatibility object, product fixtures, "
        "local snapshot fallback, and generic dataset/service adapters."
    ),
    "retained_contract": (
        "read_store.py retains only the two pure helpers imported by main.py; "
        "they perform no I/O and own no source of truth."
    ),
    "caller_collection": (
        "Production Python under services/, scripts/, and integrations/ may "
        "only import redact_evidence_refs and "
        "_market_persona_required_data_sources from read_store.py, and the "
        "only permitted caller is services/control-plane/bff/main.py."
    ),
    "verification": (
        "Run test_read_store_final_deletion.py, "
        "test_read_surface_caller_migration.py, test_read_surface_port_cutover.py, "
        "and the six domain-port suites listed in the task handoff."
    ),
    "review_requirement": (
        "Review the exact PR head; confirm the removed store cannot be imported "
        "or constructed and no fixture, snapshot, HTTP, or generic dataset path "
        "remains in product read_store.py."
    ),
}


def _read_store_tree() -> ast.Module:
    return ast.parse(
        READ_STORE_PATH.read_text(encoding="utf-8"),
        filename=str(READ_STORE_PATH),
    )


def _is_test_path(path: Path) -> bool:
    relative = path.relative_to(REPO_ROOT)
    return (
        "tests" in relative.parts
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
    )


def test_former_store_and_generic_adapters_are_deleted() -> None:
    tree = _read_store_tree()
    classes = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert not classes

    functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert functions == {
        "_market_persona_required_data_sources",
        "redact_evidence_refs",
    }


def test_product_module_has_no_fixture_snapshot_network_or_arbitration_path() -> None:
    source = READ_STORE_PATH.read_text(encoding="utf-8")
    forbidden_tokens = {
        "fixtures_pack_",
        "_default_read_data",
        "allow_local_snapshot_fallback",
        "PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK",
        "CanonicalSnapshotAdapter",
        "ServiceBackedReadAdapter",
        "_load_snapshot_dataset",
        "_load_http_dataset",
        "_http_json_get",
        "_http_json_post",
    }
    assert not {token for token in forbidden_tokens if token in source}

    imported_roots: set[str] = set()
    for node in ast.walk(_read_store_tree()):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.partition(".")[0])
    assert not imported_roots & {"json", "os", "pathlib", "urllib"}


def test_production_python_has_no_read_surface_store_symbol() -> None:
    offenders: list[str] = []
    for root_name in ("services", "scripts", "integrations"):
        root = REPO_ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path == READ_STORE_PATH or _is_test_path(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "ReadSurfaceStore":
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
                elif isinstance(node, ast.Attribute) and node.attr == "ReadSurfaceStore":
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom):
                    if any(alias.name == "ReadSurfaceStore" for alias in node.names):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert offenders == []


def test_production_python_only_imports_the_two_retained_helpers() -> None:
    imported_names: dict[str, set[str]] = {}
    module_imports: list[str] = []
    for root_name in ("services", "scripts", "integrations"):
        root = REPO_ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path == READ_STORE_PATH or _is_test_path(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            relative = str(path.relative_to(REPO_ROOT))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(alias.name.partition(".")[0] == "read_store" for alias in node.names):
                        module_imports.append(f"{relative}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.rpartition(".")[2] == "read_store":
                        imported_names.setdefault(relative, set()).update(
                            alias.name for alias in node.names
                        )

    assert module_imports == []
    assert imported_names == {
        "services/control-plane/bff/main.py": {
            "_market_persona_required_data_sources",
            "redact_evidence_refs",
        }
    }


def test_retained_persona_requirement_projection_is_narrow_and_fresh() -> None:
    first = _market_persona_required_data_sources({"market": "tw"})
    assert [item["dataset"] for item in first] == ["tw_price_daily", "tw_broker_top"]
    assert all(item["market"] == "TW" for item in first)
    assert _market_persona_required_data_sources({"market": "US"}) == []
    assert _market_persona_required_data_sources({}) == []

    first[0]["policy_gates"].append("mutated-by-test")
    second = _market_persona_required_data_sources({"market": "TW"})
    assert "mutated-by-test" not in second[0]["policy_gates"]


def test_retained_redaction_uses_model_policy_without_data_access() -> None:
    identity = OperatorIdentity(operator_id="op-read-store-delete", roles=["operator"])
    refs = [{"ref_id": "ev-1", "evidence_type": "raw_trace"}]

    unchanged, unchanged_count = redact_evidence_refs(identity, refs)
    assert unchanged == refs
    assert unchanged is not refs
    assert unchanged_count == 0

    required_capability = next(iter(EVIDENCE_CAPABILITY_MAP.values()))
    kind = next(
        key
        for key, capability in EVIDENCE_CAPABILITY_MAP.items()
        if capability == required_capability
    )
    redacted, redacted_count = redact_evidence_refs(
        identity,
        [{"ref_id": "ev-2", "evidence_type": kind}],
        capabilities=[],
    )
    assert redacted_count == 1
    assert redacted[0]["ref_id"] == "ev-2"
    assert redacted[0]["reason"] == "insufficient_capability"
