from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-2:operator"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _store(tmp_path: Path) -> ReadSurfaceStore:
    return ReadSurfaceStore(
        str(tmp_path / "read_surfaces.json"),
        allow_local_snapshot_fallback=False,
    )


def test_api_v1_telemetry_prefers_real_event_store_over_summary_projection(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        event_store = tmp_path / "telemetry_events.json"
        summary_store = tmp_path / "telemetry_summaries.json"
        _write_json(
            event_store,
            {
                "events": [
                    {
                        "id": "tel-real-001",
                        "runtime_id": "runtime-real",
                        "artifact_id": "artifact-real",
                        "pool_id": "pool-alpha",
                        "type": "paper_fill_simulated",
                        "timestamp": "2026-06-14T00:00:30Z",
                        "metrics": {"fill_qty": 10},
                    }
                ]
            },
        )
        _write_json(
            summary_store,
            {
                "runtime-summary-only": {
                    "runtime_id": "runtime-summary-only",
                    "collected_at": "2026-06-14T00:00:00Z",
                    "pnl": -0.42,
                    "drawdown": 0.08,
                }
            },
        )
        monkeypatch.delenv("PANTHEON_TELEMETRY_API_URL", raising=False)
        monkeypatch.delenv("PANTHEON_TELEMETRY_URL", raising=False)
        monkeypatch.setenv("PANTHEON_BFF_TELEMETRY_EVENT_STORE", str(event_store))
        monkeypatch.setenv("PANTHEON_BFF_TELEMETRY_SUMMARY_STORE", str(summary_store))

        original_store = bff_main.read_store
        bff_main.read_store = _store(tmp_path)
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/telemetry",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            assert [event["id"] for event in payload["data"]] == ["tel-real-001"]
            assert payload["data"][0]["type"] == "paper_fill_simulated"
            assert payload["data"][0]["metrics"] == {"fill_qty": 10}
            assert all(event["id"] != "tl-evt-runtime-summary-only" for event in payload["data"])
            surface = payload["meta"]["surfaces"]["telemetry"]
            assert surface["status"] == "ok"
            assert surface["source"] == "service_store"
            assert payload["meta"]["total"] == 1
        finally:
            bff_main.read_store = original_store


def test_api_v1_telemetry_marks_summary_projection_when_event_store_empty(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        event_store = tmp_path / "telemetry_events.json"
        summary_store = tmp_path / "telemetry_summaries.json"
        _write_json(event_store, {"events": []})
        _write_json(
            summary_store,
            {
                "runtime-fallback": {
                    "runtime_id": "runtime-fallback",
                    "collected_at": "2026-06-14T00:01:00Z",
                    "pnl": 0.17,
                    "drawdown": 0.02,
                    "sharpe_ratio": 1.2,
                    "total_trades": 5,
                    "fill_rate": 0.98,
                    "avg_slippage_bps": 1.4,
                }
            },
        )
        monkeypatch.delenv("PANTHEON_TELEMETRY_API_URL", raising=False)
        monkeypatch.delenv("PANTHEON_TELEMETRY_URL", raising=False)
        monkeypatch.setenv("PANTHEON_BFF_TELEMETRY_EVENT_STORE", str(event_store))
        monkeypatch.setenv("PANTHEON_BFF_TELEMETRY_SUMMARY_STORE", str(summary_store))

        original_store = bff_main.read_store
        bff_main.read_store = _store(tmp_path)
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/telemetry",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            assert [event["id"] for event in payload["data"]] == ["tl-evt-runtime-fallback"]
            assert payload["data"][0]["type"] == "telemetry_snapshot"
            assert payload["data"][0]["metrics"]["pnl"] == 0.17
            surface = payload["meta"]["surfaces"]["telemetry"]
            assert surface["status"] == "degraded"
            assert surface["source"] == "telemetry_summary_fallback"
            assert surface["staleness"]["served_from"] == "telemetry_summary_fallback"
            assert "event store is empty" in surface["note"]
        finally:
            bff_main.read_store = original_store
