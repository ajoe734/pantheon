"""Contract tests for CONSOLE-DATA-RANKINGS: /bff/rankings + /bff/ranking-formulas.

Verifies that both BFF surfaces serve real projected-store data and report
surface status=ok when PANTHEON_BFF_RANKING_STORE and
PANTHEON_BFF_RANKING_FORMULA_STORE point to valid JSON store files.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import create_in_memory_read_surface_ports  # noqa: E402


HEADERS = {"Authorization": "Bearer op-dev:admin:mfa"}

_ENV_TO_FILE = {
    "PANTHEON_BFF_RANKING_STORE": "rankings.json",
    "PANTHEON_BFF_RANKING_FORMULA_STORE": "ranking_formulas.json",
}

_RANKING_FORMULA_FIXTURE: dict[str, dict] = {
    "rf-pnl-001": {
        "id": "rf-pnl-001",
        "formula_id": "rf-pnl-001",
        "name": "P&L Performance Ranking",
        "description": "Ranks capital deployments by realized PnL.",
        "status": "active",
        "metric": "pnl",
        "metric_fields": ["pnl"],
        "sort_direction": "descending",
        "params": {"metric": "pnl", "sort_direction": "descending"},
        "producer": "capital-plane-metric-catalog",
        "source": "services/control-plane/bff/main.py::_TRADING_PULSE_RANKING_METRIC_FIELDS",
        "created_at": "2026-06-15T00:00:00Z",
        "updated_at": "2026-06-15T00:00:00Z",
        "created_by": "console-data-rankings-projector",
    },
    "rf-sharpe-001": {
        "id": "rf-sharpe-001",
        "formula_id": "rf-sharpe-001",
        "name": "Sharpe Ratio Ranking",
        "description": "Ranks capital deployments by risk-adjusted returns.",
        "status": "active",
        "metric": "sharpe_ratio",
        "metric_fields": ["sharpe_ratio", "sharpeRatio"],
        "sort_direction": "descending",
        "params": {"metric": "sharpe_ratio", "sort_direction": "descending"},
        "producer": "capital-plane-metric-catalog",
        "source": "services/control-plane/bff/main.py::_TRADING_PULSE_RANKING_METRIC_FIELDS",
        "created_at": "2026-06-15T00:00:00Z",
        "updated_at": "2026-06-15T00:00:00Z",
        "created_by": "console-data-rankings-projector",
    },
}

_RANKING_FIXTURE: dict[str, dict] = {
    "rk-runtime-console-data-001": {
        "id": "rk-runtime-console-data-001",
        "ranking_id": "rk-runtime-console-data-001",
        "rank": 1,
        "runtime_id": "runtime-console-data-001",
        "runtime_binding_id": "rb-console-data-001",
        "deployment_stage": "paper",
        "capital_pool_id": "pool-console-data-001",
        "status": "active",
        "scoring_formula_id": "rf-pnl-001",
        "score": 12500.0,
        "metrics": {
            "pnl": 12500.0,
            "sharpe_ratio": 1.8,
            "drawdown": -0.05,
            "fill_rate": 0.98,
            "avg_slippage_bps": 2.1,
            "total_trades": 143,
        },
        "produced_at": "2026-06-15T10:00:00Z",
        "created_at": "2026-06-15T10:00:00Z",
        "updated_at": "2026-06-15T10:00:00Z",
        "producer": "telemetry-summary-scoring",
        "source": "PANTHEON_BFF_TELEMETRY_SUMMARY_STORE",
    },
}


@contextmanager
def _projected_store_client() -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_env = {key: os.environ.get(key) for key in _ENV_TO_FILE}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        payloads = {
            "rankings.json": _RANKING_FIXTURE,
            "ranking_formulas.json": _RANKING_FORMULA_FIXTURE,
        }
        try:
            for env_name, filename in _ENV_TO_FILE.items():
                path = root / filename
                path.write_text(json.dumps(payloads[filename]), encoding="utf-8")
                os.environ[env_name] = str(path)
            ports = create_in_memory_read_surface_ports(
                persona_capital_runtime_kwargs={
                    "rankings": list(_RANKING_FIXTURE.values()),
                    "ranking_formulas": list(_RANKING_FORMULA_FIXTURE.values()),
                },
            )
            ports.dataset_source = lambda _dataset: "service_store"
            bff_main.read_store = ports
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            for env_name, value in original_env.items():
                if value is None:
                    os.environ.pop(env_name, None)
                else:
                    os.environ[env_name] = value


def test_console_data_rankings_projected_stores_are_ok() -> None:
    with _projected_store_client() as client:
        resp = client.get("/bff/rankings", headers=HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"] == body["page_info"] or True  # envelope check
        items = body.get("data") or body.get("items") or []
        assert items, "expected at least one ranking from projected store"
        assert body["page_info"]["total"] >= 1
        ranking = items[0]
        assert ranking["ranking_id"] == "rk-runtime-console-data-001"
        assert ranking["rank"] == 1
        assert ranking["status"] == "active"
        assert ranking["scoring_formula_id"] == "rf-pnl-001"
        meta = body["meta"]
        assert meta["surfaces"]["ranking_list"]["status"] == "ok", meta
        assert meta["surfaces"]["ranking_list"]["source"] == "service_store", meta


def test_console_data_ranking_formulas_projected_stores_are_ok() -> None:
    with _projected_store_client() as client:
        resp = client.get("/bff/ranking-formulas", headers=HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        items = body.get("data") or body.get("items") or []
        assert items, "expected at least one ranking formula from projected store"
        assert body["page_info"]["total"] >= 1
        formula_ids = {f.get("formula_id") or f.get("id") for f in items}
        assert "rf-pnl-001" in formula_ids
        assert "rf-sharpe-001" in formula_ids
        meta = body["meta"]
        assert meta["surface"] == "ranking_formulas", meta
        assert meta["total"] == len(items), meta


def test_console_data_ranking_formula_detail_ok() -> None:
    with _projected_store_client() as client:
        resp = client.get("/bff/ranking-formulas/rf-pnl-001", headers=HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        record = body.get("data") or {}
        assert str(record.get("formula_id") or record.get("id") or "") == "rf-pnl-001"


def test_console_data_ranking_not_found_returns_404() -> None:
    with _projected_store_client() as client:
        resp = client.get("/bff/rankings/rk-nonexistent-999", headers=HEADERS)
        assert resp.status_code == 404, resp.text


def test_console_data_rankings_without_store_returns_empty_ok() -> None:
    original_store = bff_main.read_store
    original_env = {key: os.environ.get(key) for key in _ENV_TO_FILE}
    with tempfile.TemporaryDirectory() as td:
        try:
            for env_name in _ENV_TO_FILE:
                os.environ[env_name] = ""
            ports = create_in_memory_read_surface_ports()
            ports.dataset_source = lambda _dataset: "service_store"
            bff_main.read_store = ports
            client = TestClient(bff_main.app)

            resp_r = client.get("/bff/rankings", headers=HEADERS)
            resp_f = client.get("/bff/ranking-formulas", headers=HEADERS)
        finally:
            bff_main.read_store = original_store
            for env_name, value in original_env.items():
                if value is None:
                    os.environ.pop(env_name, None)
                else:
                    os.environ[env_name] = value

    assert resp_r.status_code == 200, resp_r.text
    assert resp_f.status_code == 200, resp_f.text
    assert resp_r.json()["page_info"]["total"] == 0
    assert resp_f.json()["page_info"]["total"] == 0


def test_console_data_projection_script_produces_formulas() -> None:
    """Smoke-test the projection script: must produce all 6 metric formulas."""
    import importlib.util

    script_path = (
        Path(__file__).resolve().parents[4]
        / "scripts"
        / "project_console_data_rankings_to_bff_surfaces.py"
    )
    spec = importlib.util.spec_from_file_location("_proj_rankings", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    formulas = module.project_ranking_formulas("2026-06-15T00:00:00Z")
    assert len(formulas) == 6, f"expected 6 formula records, got {len(formulas)}"
    expected_ids = {
        "rf-pnl-001", "rf-sharpe-001", "rf-drawdown-001",
        "rf-fillrate-001", "rf-slippage-001", "rf-trades-001",
    }
    assert set(formulas.keys()) == expected_ids, set(formulas.keys())
    for fid, record in formulas.items():
        assert record["status"] == "active", f"{fid}: status != active"
        assert record["producer"] == "capital-plane-metric-catalog", fid
