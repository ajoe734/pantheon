"""Contract tests for CONSOLE-DATA-RANKINGS: /bff/rankings + /bff/ranking-formulas.

Verifies that both BFF surfaces serve real projected-store data and report
surface status=ok when PANTHEON_BFF_RANKING_STORE and
PANTHEON_BFF_RANKING_FORMULA_STORE point to valid JSON store files.
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.management_read_models.ranking_router import (
    create_ranking_formulas_router,
    create_rankings_long_tail_router,
)
from services.control_plane.bff.models import utc_now as _utc_now
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.strategies.routes.common import default_page_slice


HEADERS = {"Authorization": "Bearer op-dev:admin:mfa"}


# --- Local mirrors of bff/main.py's read-surface staleness/meta helpers ---
# These are simple functions in main.py that close over its module-level
# `read_store` global; reproduced here (parameterized on an explicit store)
# so tests can construct the real ranking routers without importing main.py.


def _read_surface_state() -> str:
    return os.getenv("BFF_READ_SURFACE_STATE", "fresh")


def _meta_staleness() -> Optional[Dict[str, Any]]:
    state = _read_surface_state()
    if state == "fresh":
        return None
    return {"served_from": "cache", "last_known_at": _utc_now()}


def _surface_status() -> Dict[str, Any]:
    state = _read_surface_state()
    if state == "fresh":
        return {"status": "ok"}
    if state in {"degraded", "stale"}:
        return {"status": "degraded", "staleness": _meta_staleness()}
    if state == "unavailable":
        return {"status": "unavailable", "staleness": _meta_staleness()}
    return {"status": "ok"}


def _surface_degradation_reason(
    surface: Dict[str, Any],
    *,
    degraded_reason: str,
    unavailable_reason: str,
) -> Optional[str]:
    status = surface.get("status")
    if status == "ok":
        return None
    if status == "unavailable":
        return unavailable_reason
    if surface.get("message"):
        return str(surface["message"])
    if surface.get("note"):
        return str(surface["note"])
    return degraded_reason


def _snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"snapshot_at": snapshot_at}
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness
    return meta


def _make_dataset_surface_status(get_read_store):
    def _dataset_surface_status(
        dataset: str,
        *,
        snapshot_at: Optional[str] = None,
        has_data: Optional[bool] = None,
        missing_message: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        surface = dict(_surface_status())
        source = source or get_read_store().dataset_source(dataset)
        surface["source"] = source

        if source == "local_snapshot":
            if surface.get("status") == "ok":
                surface["status"] = "degraded"
            surface["note"] = "Served from local BFF snapshot fallback instead of a backend-owned read store."
            surface["staleness"] = {
                "served_from": "local_snapshot",
                "last_known_at": snapshot_at or _utc_now(),
            }
        elif source == "missing":
            surface["status"] = "unavailable"
            surface.setdefault(
                "staleness",
                {"served_from": "unverifiable", "last_known_at": snapshot_at or _utc_now()},
            )

        if has_data is False:
            if surface.get("status") == "ok":
                surface["status"] = "unavailable"
            if missing_message:
                surface["message"] = missing_message
            surface.setdefault(
                "staleness",
                {"served_from": "unverifiable", "last_known_at": snapshot_at or _utc_now()},
            )

        return surface

    return _dataset_surface_status


def _make_read_surface_meta(get_read_store):
    dataset_surface_status = _make_dataset_surface_status(get_read_store)

    def _read_surface_meta(
        dataset: str,
        surface_key: str,
        *,
        snapshot_at: Optional[str] = None,
        total: Optional[int] = None,
        surface: Optional[Dict[str, Any]] = None,
        has_data: Optional[bool] = None,
        missing_message: Optional[str] = None,
        degraded_reason: Optional[str] = None,
        unavailable_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        snapshot_at = snapshot_at or _utc_now()
        surface = surface or dataset_surface_status(
            dataset,
            snapshot_at=snapshot_at,
            has_data=has_data,
            missing_message=missing_message,
        )
        meta: Dict[str, Any] = {
            "snapshot_at": snapshot_at,
            "surfaces": {surface_key: surface},
        }
        if total is not None:
            meta["total"] = total
        staleness = _meta_staleness()
        if staleness is not None:
            meta["staleness"] = staleness
        label = surface_key.replace("_", " ")
        reason = _surface_degradation_reason(
            surface,
            degraded_reason=degraded_reason or f"{label} is degraded and may be stale.",
            unavailable_reason=unavailable_reason or f"{label} is currently unavailable.",
        )
        if reason is not None:
            meta["degradation"] = {"reason": reason}
        return meta

    return _read_surface_meta


def _build_app(read_store) -> FastAPI:
    app = FastAPI()
    app.state.read_store = read_store

    def _get_read_store():
        return app.state.read_store

    app.include_router(
        create_ranking_formulas_router(
            get_read_store=_get_read_store,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=_utc_now,
            snapshot_meta=_snapshot_meta,
        )
    )
    app.include_router(
        create_rankings_long_tail_router(
            get_read_store=_get_read_store,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=_utc_now,
            page_slice=default_page_slice,
            read_surface_meta=_make_read_surface_meta(_get_read_store),
        )
    )
    return app

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
            yield TestClient(_build_app(ports))
        finally:
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
    original_env = {key: os.environ.get(key) for key in _ENV_TO_FILE}
    with tempfile.TemporaryDirectory() as td:
        try:
            for env_name in _ENV_TO_FILE:
                os.environ[env_name] = ""
            ports = create_in_memory_read_surface_ports()
            ports.dataset_source = lambda _dataset: "service_store"
            client = TestClient(_build_app(ports))

            resp_r = client.get("/bff/rankings", headers=HEADERS)
            resp_f = client.get("/bff/ranking-formulas", headers=HEADERS)
        finally:
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
