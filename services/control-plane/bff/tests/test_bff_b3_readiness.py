"""Test production readiness evidence rules, not copied fake BFF handlers."""
import ast
from pathlib import Path

import pytest

from services.control_plane.bff.management_read_models.readiness_evidence import (
    current_evidence_checks, historical_reference, historical_surface,
)


@pytest.mark.parametrize("text", ["Approved.", '{"passed": true}', "", "Status: **approved**"])
def test_archived_pass_missing_or_modified_file_never_establishes_readiness(tmp_path, text):
    path = tmp_path / "historical-evidence"
    if text:
        path.write_text(text)
    ref = historical_reference(str(path), "Old audit")
    surface = historical_surface(str(path), "Old audit")
    checks = current_evidence_checks(
        [{"id": "old-pass", "status": "pass", "blocking": True, "evidence_refs": [str(path)]}],
        [ref], {"audit": surface},
    )
    assert ref["exists"] is None
    assert ref["historical_only"] and not ref["readiness_authority"]
    assert surface["status"] == "unavailable"
    assert surface["staleness"]["last_known_at"] is None
    assert checks[0]["status"] == "unknown"
    assert checks[-1]["blocking"] and checks[-1]["status"] == "unknown"


@pytest.mark.parametrize("status", [None, "unavailable", "degraded", "stale"])
def test_unverified_current_owner_read_cannot_make_empty_data_pass(status):
    checks = current_evidence_checks(
        [{"id": "empty-live-bindings", "status": "pass", "blocking": True}], [],
        {"owner": {"status": status}},
    )
    assert checks[-1]["id"] == "current_owner_evidence"
    assert checks[-1]["blocking"]


def test_historical_links_do_not_override_healthy_current_owner_checks():
    check = {"id": "live-owner", "status": "pass", "blocking": True}
    refs = [historical_reference("old.md", "Old report")]
    assert current_evidence_checks([check], refs, {"owner": {"status": "ok"}}) == [check]


def test_production_composition_uses_evidence_guard_and_has_no_archive_readers():
    # Composition assertion supplements behavioral tests of the imported helper.
    main = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(main.read_text())
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "_read_repo_json_artifact" not in functions
    assert "_read_repo_text_artifact" not in functions
    calls = [node.func.id for node in ast.walk(functions["_readiness_response"])
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    assert "current_evidence_checks" in calls
    assert calls.index("current_evidence_checks") < calls.index("_readiness_summary")
