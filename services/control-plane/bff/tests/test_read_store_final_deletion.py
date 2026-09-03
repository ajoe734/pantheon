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

from models import (  # noqa: E402
    EVIDENCE_CAPABILITY_MAP,
    OperatorIdentity,
    redact_evidence_refs,
)
from personas.service import _market_persona_required_data_sources  # noqa: E402


TASK_REVIEW_EVIDENCE = {
    "task": "OPGAP-BFF-MAIN-ASSEMBLY-V3-20260901",
    "owner": "Antigravity2",
    "reviewer": "Claude",
    "base": "dev",
    "scope": (
        "Complete retirement of read_store.py following BFF main.py composition cutover."
    ),
    "verification": (
        "Run test_read_store_final_deletion.py and test_bff_main_composition.py."
    ),
}


def _is_test_path(path: Path) -> bool:
    relative = path.relative_to(REPO_ROOT)
    return (
        "tests" in relative.parts
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
    )


def test_read_store_file_is_completely_deleted() -> None:
    assert not READ_STORE_PATH.exists(), f"{READ_STORE_PATH} must be deleted"


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


def test_production_python_has_no_read_store_import() -> None:
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
    assert imported_names == {}


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
