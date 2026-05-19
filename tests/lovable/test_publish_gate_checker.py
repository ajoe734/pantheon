from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lovable" / "publish_gate_checker.py"
spec = importlib.util.spec_from_file_location("publish_gate_checker", SCRIPT)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def _audit_packet() -> dict[str, object]:
    return {
        "task_id": "LSP-005-V2",
        "deployment_url": "https://pantheon-dev.lovable.app/management",
        "browser_probe_base_url": "https://pantheon-dev.lovable.app",
        "checked_at": "2026-05-19T16:00:00Z",
        "inputs": {},
        "components": {
            "browser_probe": {"task_id": "LSP-002-V2", "all_passed": True, "errors": []},
            "bundle_hash_capture": {"task_id": "LSP-003-V2", "passed": True, "errors": []},
            "forbidden_path_scan": {"task_id": "LSP-004-V2", "passed": True, "errors": []},
        },
        "component_status": {
            "LSP-002-V2": True,
            "LSP-003-V2": True,
            "LSP-004-V2": True,
        },
        "summary": {
            "js_bundle_count": 2,
            "scanned_url_count": 3,
            "forbidden_signal_count": 0,
            "browser_probe_error_count": 0,
            "bundle_capture_error_count": 0,
            "forbidden_scan_error_count": 0,
        },
        "errors": [],
        "passed": True,
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_publish_gate_allows_completion_when_strict_audit_passed(tmp_path: Path) -> None:
    audit_path = tmp_path / "strict-publish-audit.json"
    _write_json(audit_path, _audit_packet())

    result = gate.check_publish_gate(audit_path, checked_at="2026-05-19T16:05:00Z")

    assert result["task_id"] == "LSP-006-V2"
    assert result["passed"] is True
    assert result["can_mark_publish_complete"] is True
    assert result["required_component_status"] == {
        "LSP-002-V2": True,
        "LSP-003-V2": True,
        "LSP-004-V2": True,
    }
    assert result["rejection_reasons"] == []


def test_publish_gate_fails_closed_when_strict_audit_failed(tmp_path: Path) -> None:
    audit = _audit_packet()
    audit["passed"] = False
    audit["component_status"] = {
        "LSP-002-V2": True,
        "LSP-003-V2": True,
        "LSP-004-V2": False,
    }
    audit["summary"] = {
        **audit["summary"],  # type: ignore[arg-type]
        "forbidden_signal_count": 7,
    }
    audit["errors"] = ["LSP-004-V2 failed: forbidden_signals=7 errors=0"]
    audit_path = tmp_path / "strict-publish-audit.json"
    _write_json(audit_path, audit)

    result = gate.check_publish_gate(audit_path)

    assert result["passed"] is False
    assert result["can_mark_publish_complete"] is False
    assert "strict_publish_audit_passed_not_true" in result["rejection_reasons"]
    assert "component_failed: LSP-004-V2" in result["rejection_reasons"]
    assert "audit_summary_counter_nonzero: forbidden_signal_count=7" in result["rejection_reasons"]
    assert "audit_errors_present: 1" in result["rejection_reasons"]


def test_publish_gate_fails_closed_when_required_component_is_missing(tmp_path: Path) -> None:
    audit = _audit_packet()
    component_status = dict(audit["component_status"])  # type: ignore[arg-type]
    component_status.pop("LSP-003-V2")
    audit["component_status"] = component_status
    audit_path = tmp_path / "strict-publish-audit.json"
    _write_json(audit_path, audit)

    result = gate.check_publish_gate(audit_path)

    assert result["passed"] is False
    assert result["required_component_status"]["LSP-003-V2"] is False
    assert "component_status_missing: LSP-003-V2" in result["rejection_reasons"]


def test_publish_gate_fails_closed_when_audit_json_missing(tmp_path: Path) -> None:
    result = gate.check_publish_gate(tmp_path / "missing.json")

    assert result["passed"] is False
    assert result["strict_publish_audit_task_id"] is None
    assert result["rejection_reasons"][0].startswith("audit_json_missing:")


def test_publish_gate_cli_writes_result_and_returns_nonzero_on_block(tmp_path: Path) -> None:
    audit = _audit_packet()
    audit["passed"] = False
    audit_path = tmp_path / "strict-publish-audit.json"
    output_path = tmp_path / "publish-gate.json"
    _write_json(audit_path, audit)

    exit_code = gate.main(["--audit-json", str(audit_path), "--output", str(output_path)])

    assert exit_code == 1
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["task_id"] == "LSP-006-V2"
    assert written["passed"] is False
    assert written["can_mark_publish_complete"] is False
