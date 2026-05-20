from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lovable" / "strict_publish_audit.py"
spec = importlib.util.spec_from_file_location("strict_publish_audit", SCRIPT)
assert spec and spec.loader
strict_audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strict_audit)


def _browser_probe(*, passed: bool = True) -> dict[str, object]:
    return {
        "task_id": "LSP-002-V2",
        "base_url": "https://pantheon-dev.lovable.app",
        "checked_at": "2026-05-19T16:00:00Z",
        "health": {"status": 200, "ok": passed, "mock_seed_detected": False},
        "bff_me": {"status": 200, "ok": passed, "mock_seed_detected": False},
        "mock_seed_free": passed,
        "all_passed": passed,
        "errors": [] if passed else ["/bff/me probe failed"],
    }


def _bundle_capture(*, passed: bool = True) -> dict[str, object]:
    return {
        "task_id": "LSP-003-V2",
        "deployment_url": "https://pantheon-dev.lovable.app/management",
        "checked_at": "2026-05-19T16:00:00Z",
        "index": {
            "kind": "index_html",
            "url": "https://pantheon-dev.lovable.app/management",
            "sha256": "index-sha",
            "bytes": 100,
        },
        "js_bundle_urls": ["https://pantheon-dev.lovable.app/assets/index.js"],
        "js_bundles": {
            "https://pantheon-dev.lovable.app/assets/index.js": {
                "kind": "js_bundle",
                "url": "https://pantheon-dev.lovable.app/assets/index.js",
                "sha256": "bundle-sha",
                "bytes": 200,
            }
        },
        "assets": [],
        "asset_count": 2,
        "errors": [] if passed else ["fetch js failed"],
        "passed": passed,
    }


def _forbidden_scan(*, passed: bool = True) -> dict[str, object]:
    signals = []
    if not passed:
        signals = [
            {
                "signal": "/mocks/",
                "source_url": "https://pantheon-dev.lovable.app/assets/index.js",
                "line": 12,
                "column": 4,
                "snippet": 'fetch("/mocks/current-user.json")',
            }
        ]
    return {
        "task_id": "LSP-004-V2",
        "deployment_url": "https://pantheon-dev.lovable.app/management",
        "checked_at": "2026-05-19T16:00:00Z",
        "scanned_urls": [
            "https://pantheon-dev.lovable.app/management",
            "https://pantheon-dev.lovable.app/assets/index.js",
        ],
        "bundle_urls": ["https://pantheon-dev.lovable.app/assets/index.js"],
        "forbidden_signals": signals,
        "errors": [],
        "passed": passed,
    }


def test_run_strict_publish_audit_passes_when_all_components_pass() -> None:
    result = strict_audit.run_strict_publish_audit(
        "https://pantheon-dev.lovable.app/management",
        checked_at="2026-05-19T16:00:00Z",
        browser_probe_runner=lambda base_url: _browser_probe(),
        bundle_capture_runner=lambda deployment_url: _bundle_capture(),
        forbidden_scan_runner=lambda deployment_url: _forbidden_scan(),
    )

    assert result["task_id"] == "LSP-005-V2"
    assert result["passed"] is True
    assert result["deployment_url"] == "https://pantheon-dev.lovable.app/management"
    assert result["browser_probe_base_url"] == "https://pantheon-dev.lovable.app"
    assert result["component_status"] == {
        "LSP-002-V2": True,
        "LSP-003-V2": True,
        "LSP-004-V2": True,
    }
    assert result["summary"]["js_bundle_count"] == 1
    assert result["summary"]["forbidden_signal_count"] == 0
    assert result["errors"] == []


def test_run_strict_publish_audit_fails_closed_on_component_failure() -> None:
    result = strict_audit.run_strict_publish_audit(
        "https://pantheon-dev.lovable.app/management",
        checked_at="2026-05-19T16:00:00Z",
        browser_probe_runner=lambda base_url: _browser_probe(),
        bundle_capture_runner=lambda deployment_url: _bundle_capture(),
        forbidden_scan_runner=lambda deployment_url: _forbidden_scan(passed=False),
    )

    assert result["passed"] is False
    assert result["component_status"]["LSP-004-V2"] is False
    assert result["summary"]["forbidden_signal_count"] == 1
    assert result["errors"] == ["LSP-004-V2 failed: forbidden_signals=1 errors=0"]


def test_run_strict_publish_audit_records_runner_exception() -> None:
    def broken_runner(base_url: str) -> dict[str, object]:
        del base_url
        raise RuntimeError("probe unavailable")

    result = strict_audit.run_strict_publish_audit(
        "https://pantheon-dev.lovable.app/management",
        checked_at="2026-05-19T16:00:00Z",
        browser_probe_runner=broken_runner,
        bundle_capture_runner=lambda deployment_url: _bundle_capture(),
        forbidden_scan_runner=lambda deployment_url: _forbidden_scan(),
    )

    assert result["passed"] is False
    assert result["component_status"]["LSP-002-V2"] is False
    assert "LSP-002-V2 runner failed: RuntimeError: probe unavailable" in result["errors"]
    assert result["components"]["browser_probe"]["error"] == "RuntimeError: probe unavailable"


def test_write_audit_outputs_writes_json_and_markdown(tmp_path: Path) -> None:
    result = strict_audit.run_strict_publish_audit(
        "https://pantheon-dev.lovable.app/management",
        checked_at="2026-05-19T16:00:00Z",
        browser_probe_runner=lambda base_url: _browser_probe(),
        bundle_capture_runner=lambda deployment_url: _bundle_capture(),
        forbidden_scan_runner=lambda deployment_url: _forbidden_scan(passed=False),
    )

    json_path = tmp_path / "strict-publish-audit.json"
    md_path = tmp_path / "strict-publish-audit.md"
    strict_audit.write_audit_outputs(result, json_path, md_path)

    written = json.loads(json_path.read_text(encoding="utf-8"))
    report = md_path.read_text(encoding="utf-8")

    assert written["task_id"] == "LSP-005-V2"
    assert written["passed"] is False
    assert "Status: FAIL" in report
    assert "| LSP-004-V2 | Forbidden runtime path scan | FAIL |" in report
    assert "fetch(\"/mocks/current-user.json\")" in report
