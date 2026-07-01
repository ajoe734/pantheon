from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "aggregate-release-gate.mjs"


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _dependency_overrides(*, all_done: bool = True) -> dict:
    status = "done" if all_done else "in_progress"
    return {
        task_id: {"status": status, "source": "test-fixture"}
        for task_id in [
            "MGMT-LOAD-001",
            "MGMT-LOAD-002",
            "MGMT-LOAD-003",
            "MGMT-LOAD-004",
            "MGMT-LOAD-005",
        ]
    }


def _route_timing(*, first_row_ms: int = 1000, used_networkidle: bool = False, error=None) -> dict:
    return {
        "routePath": "/management/evidence",
        "primaryApiPath": "/bff/management/evidence",
        "usedNetworkidle": used_networkidle,
        "error": error,
        "milestones": {"firstRowOrEmptyVisibleMs": first_row_ms},
    }


def _waterfall(*, duplicate_jobs: bool = False, extra_non_primary: int = 0) -> list:
    entries = [
        {"path": "/management/evidence", "startMs": 10},
        {"path": "/assets/index-ABC.js", "startMs": 20},
        {"path": "/bff/management/evidence", "startMs": 100},
        {"path": "/bff/events/stream", "startMs": 100, "note": "realtime SSE stream; excluded"},
        {"path": "/health", "startMs": 100},
    ]
    if duplicate_jobs:
        entries.append({"path": "/bff/jobs", "startMs": 100})
        entries.append({"path": "/bff/jobs", "startMs": 105})
    for i in range(extra_non_primary):
        entries.append({"path": f"/bff/extra-{i}", "startMs": 100})
    return entries


def _bundle(*, pass_budgets: bool = True) -> dict:
    initial = 400_000 if pass_budgets else 900_000
    evidence = 50_000 if pass_budgets else 200_000
    return {"results": {"initialManagementJsGzipBytes": initial, "evidenceRouteChunkGzipBytes": evidence}}


def _bff_fanout() -> dict:
    return {
        "summary": {
            "/health": {"p95Ms": 100},
            "/bff/management/evidence": {"p95Ms": 400},
            "/bff/management/shell-summary": {"p95Ms": 150},
        }
    }


def _run(tmp_path: Path, *, route_timing=None, waterfall=None, bff_fanout=None, bundle=None, dependencies=None):
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    route_timing_file = tmp_path / "route-timing.json"
    waterfall_file = tmp_path / "waterfall.json"
    bff_fanout_file = tmp_path / "bff-fanout.json"
    bundle_file = tmp_path / "bundle.json"
    dependencies_file = tmp_path / "dependencies.json"
    out_dir = tmp_path / "out"

    _write_json(route_timing_file, route_timing if route_timing is not None else _route_timing())
    _write_json(waterfall_file, waterfall if waterfall is not None else _waterfall())
    _write_json(bff_fanout_file, bff_fanout if bff_fanout is not None else _bff_fanout())
    _write_json(bundle_file, bundle if bundle is not None else _bundle())
    _write_json(dependencies_file, dependencies if dependencies is not None else _dependency_overrides())

    result = subprocess.run(
        [
            "node",
            str(SCRIPT),
            "--audit-dir", str(tmp_path),
            "--out-dir", str(out_dir),
            "--route-timing", str(route_timing_file),
            "--waterfall", str(waterfall_file),
            "--bff-fanout", str(bff_fanout_file),
            "--bundle-file", str(bundle_file),
            "--dependencies", str(dependencies_file),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, out_dir


def test_gate_passes_when_all_evidence_is_within_budget(tmp_path: Path) -> None:
    result, out_dir = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr

    manifest_files = list(out_dir.glob("release-load-gate-*.json"))
    assert len(manifest_files) == 1
    manifest = json.loads(manifest_files[0].read_text(encoding="utf-8"))
    assert manifest["result"]["pass"] is True
    assert manifest["result"]["overall"] == "pass"
    assert (out_dir / manifest_files[0].name.replace(".json", ".md")).exists()
    assert list(out_dir.glob("release-route-timing-*.json"))
    assert list(out_dir.glob("release-request-waterfall-*.json"))
    assert list(out_dir.glob("release-bff-fanout-*.json"))
    assert list(out_dir.glob("release-bundle-*.json"))


def test_gate_fails_on_duplicate_startup_jobs_request(tmp_path: Path) -> None:
    result, out_dir = _run(tmp_path, waterfall=_waterfall(duplicate_jobs=True))
    assert result.returncode == 1

    manifest = json.loads(next(out_dir.glob("release-load-gate-*.json")).read_text(encoding="utf-8"))
    assert manifest["result"]["pass"] is False
    assert any("Duplicate startup /bff/jobs" in label for label in manifest["result"]["failures"])


def test_gate_fails_on_excess_non_primary_startup_requests(tmp_path: Path) -> None:
    result, out_dir = _run(tmp_path, waterfall=_waterfall(extra_non_primary=3))
    assert result.returncode == 1

    manifest = json.loads(next(out_dir.glob("release-load-gate-*.json")).read_text(encoding="utf-8"))
    assert any("Non-primary BFF startup requests" in label for label in manifest["result"]["failures"])


def test_gate_fails_on_networkidle_readiness(tmp_path: Path) -> None:
    result, out_dir = _run(tmp_path, route_timing=_route_timing(used_networkidle=True))
    assert result.returncode == 1

    manifest = json.loads(next(out_dir.glob("release-load-gate-*.json")).read_text(encoding="utf-8"))
    assert any("did not use `networkidle`" in label for label in manifest["result"]["failures"])


def test_gate_fails_on_bundle_budget_breach(tmp_path: Path) -> None:
    result, out_dir = _run(tmp_path, bundle=_bundle(pass_budgets=False))
    assert result.returncode == 1

    manifest = json.loads(next(out_dir.glob("release-load-gate-*.json")).read_text(encoding="utf-8"))
    assert any("Initial management JS gzip" in label for label in manifest["result"]["failures"])
    assert any("Evidence route chunk gzip" in label for label in manifest["result"]["failures"])


def test_gate_fails_on_bff_fanout_latency_regression(tmp_path: Path) -> None:
    result, out_dir = _run(tmp_path, bff_fanout={"summary": {"/health": {"p95Ms": 5000}}})
    assert result.returncode == 1

    manifest = json.loads(next(out_dir.glob("release-load-gate-*.json")).read_text(encoding="utf-8"))
    assert any("/health fanout p95" in label for label in manifest["result"]["failures"])


def test_gate_fails_closed_when_a_dependency_task_is_not_terminal(tmp_path: Path) -> None:
    result, out_dir = _run(tmp_path, dependencies=_dependency_overrides(all_done=False))
    assert result.returncode == 1

    manifest = json.loads(next(out_dir.glob("release-load-gate-*.json")).read_text(encoding="utf-8"))
    assert manifest["result"]["pass"] is False
    assert any("Dependency MGMT-LOAD-001" in label for label in manifest["result"]["failures"])


def test_gate_reports_missing_evidence_as_non_pass_not_silent_green(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    out_dir = tmp_path / "out"
    empty_dir = tmp_path / "empty-audit"
    empty_dir.mkdir()
    dependencies_file = tmp_path / "dependencies.json"
    _write_json(dependencies_file, _dependency_overrides())

    result = subprocess.run(
        [
            "node",
            str(SCRIPT),
            "--audit-dir", str(empty_dir),
            "--out-dir", str(out_dir),
            "--dependencies", str(dependencies_file),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1

    manifest = json.loads(next(out_dir.glob("release-load-gate-*.json")).read_text(encoding="utf-8"))
    assert manifest["result"]["pass"] is False
    assert "route-timing evidence" in " ".join(manifest["result"]["missing"]).lower() or manifest["result"]["missing"]
