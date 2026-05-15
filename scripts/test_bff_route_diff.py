from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "bff_route_diff.py"
BACKEND_MANIFEST = REPO_ROOT / "services/control-plane/bff/contract_snapshots/backend_routes_manifest.json"
FRONTEND_MANIFEST = REPO_ROOT / "services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json"
BASELINE_PATH = REPO_ROOT / "docs/bff/contract_snapshots/route-diff-baseline.json"


def _write_manifest(path: Path, entries: list[dict]) -> None:
    path.write_text(
        json.dumps({"metadata": {"snapshot_date": "test"}, "entries": entries}, indent=2) + "\n",
        encoding="utf-8",
    )


def _run_diff(
    tmp_path: Path,
    backend: list[dict],
    frontend: list[dict],
    *,
    mode: str | None = None,
) -> subprocess.CompletedProcess[str]:
    backend_path = tmp_path / "backend.json"
    frontend_path = tmp_path / "frontend.json"
    _write_manifest(backend_path, backend)
    _write_manifest(frontend_path, frontend)
    command = [
        sys.executable,
        str(SCRIPT_PATH),
        "--backend",
        str(backend_path),
        "--frontend",
        str(frontend_path),
        "--dump",
    ]
    if mode:
        command.extend(["--mode", mode])
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )


def test_route_diff_cli_fails_when_frontend_active_route_has_no_backend(tmp_path: Path) -> None:
    result = _run_diff(
        tmp_path,
        backend=[],
        frontend=[{"method": "GET", "path": "/bff/live-only", "family": "test", "status": "implemented"}],
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["summary"]["failures"]["frontend_missing_backend"] == 1
    assert payload["failures"]["frontend_missing_backend"][0]["key"] == "GET /bff/live-only"


def test_route_diff_cli_fails_when_backend_active_route_has_no_frontend(tmp_path: Path) -> None:
    result = _run_diff(
        tmp_path,
        backend=[{"method": "GET", "path": "/bff/backend-only", "family": "test", "status": "implemented"}],
        frontend=[],
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["summary"]["failures"]["backend_missing_frontend"] == 1
    assert payload["summary"]["warnings"]["backend_missing_frontend"] == 0
    assert payload["failures"]["backend_missing_frontend"][0]["key"] == "GET /bff/backend-only"


def test_route_diff_cli_keeps_fail_but_warn_compatibility_for_backend_only_routes(tmp_path: Path) -> None:
    result = _run_diff(
        tmp_path,
        backend=[{"method": "GET", "path": "/bff/backend-only", "family": "test", "status": "implemented"}],
        frontend=[],
        mode="fail-but-warn",
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["summary"]["failure_count"] == 0
    assert payload["summary"]["warnings"]["backend_missing_frontend"] == 1


def test_route_diff_cli_fails_on_shared_route_family_name_mismatch(tmp_path: Path) -> None:
    result = _run_diff(
        tmp_path,
        backend=[{"method": "GET", "path": "/bff/shared", "family": "backend-family", "status": "implemented"}],
        frontend=[{"method": "GET", "path": "/bff/shared", "family": "frontend-family", "status": "implemented"}],
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["summary"]["failures"]["naming_mismatches"] == 1
    assert payload["failures"]["naming_mismatches"][0]["key"] == "GET /bff/shared"


def test_route_diff_cli_allows_deferred_and_mock_only_routes(tmp_path: Path) -> None:
    result = _run_diff(
        tmp_path,
        backend=[
            {"method": "GET", "path": "/bff/backend-deferred", "family": "test", "status": "deferred"},
            {"method": "GET", "path": "/bff/backend-mock", "family": "test", "status": "mock_only"},
        ],
        frontend=[
            {
                "method": "GET",
                "path": "/bff/deferred",
                "family": "test",
                "status": "deferred_with_task",
                "task_id": "BFF-CONSOL-999",
            },
            {"method": "GET", "path": "/bff/mock", "family": "test", "status": "mock_only"},
        ],
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["summary"]["failure_count"] == 0
    assert payload["summary"]["warnings"]["backend_missing_frontend"] == 0


def test_route_diff_baseline_check_blocks_new_backend_only_route(tmp_path: Path) -> None:
    baseline_backend = tmp_path / "baseline-backend.json"
    current_backend = tmp_path / "current-backend.json"
    frontend_path = tmp_path / "frontend.json"
    baseline_path = tmp_path / "route-diff-baseline.json"

    _write_manifest(baseline_backend, [])
    _write_manifest(current_backend, [{"method": "GET", "path": "/bff/backend-only", "family": "test", "status": "implemented"}])
    _write_manifest(frontend_path, [])

    write_result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--backend",
            str(baseline_backend),
            "--frontend",
            str(frontend_path),
            "--baseline",
            str(baseline_path),
            "--write-baseline",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert write_result.returncode == 0, write_result.stderr

    check_result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--backend",
            str(current_backend),
            "--frontend",
            str(frontend_path),
            "--baseline",
            str(baseline_path),
            "--check-baseline",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert check_result.returncode == 1
    assert "backend_missing_frontend" in check_result.stderr


def test_route_diff_baseline_matches_current_failure_surface() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--backend",
            str(BACKEND_MANIFEST),
            "--frontend",
            str(FRONTEND_MANIFEST),
            "--baseline",
            str(BASELINE_PATH),
            "--check-baseline",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
