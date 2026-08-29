"""
Static AST test verifying that Source Ingestion is strictly reconcile-only and has zero write mutations on Agora stores.
"""
from __future__ import annotations

import ast
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_INGESTION_DIR = REPO_ROOT / "services" / "source_ingestion"

PROHIBITED_WRITE_METHODS = {
    "create_session",
    "open_committee_session",
    "close_committee_session",
    "submit_committee_memo",
    "publish_committee_memo",
    "create_evidence_pack",
    "append_evidence_files",
    "create_note",
    "create_insight",
    "create_training_example",
    "create_signal",
    "record_signal_feedback",
    "create_feedback",
    "create_handoff",
    "record_audit_event",
    "create_journal_entry",
    "patch_journal_entry",
    "create_workshop",
    "create_proposal",
    "create_interaction",
}


def test_source_ingestion_is_reconcile_only() -> None:
    if not SOURCE_INGESTION_DIR.exists():
        pytest.skip(f"{SOURCE_INGESTION_DIR} does not exist in this checkout")

    py_files = list(SOURCE_INGESTION_DIR.glob("**/*.py"))
    for py_path in py_files:
        code = py_path.read_text(encoding="utf-8")
        tree = ast.parse(code, filename=str(py_path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    assert func.attr not in PROHIBITED_WRITE_METHODS, (
                        f"Prohibited write mutation '{func.attr}' called in source ingestion file {py_path.name}"
                    )
