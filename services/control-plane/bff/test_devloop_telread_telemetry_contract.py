from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main


OPERATOR_TOKEN = "Bearer op-2:operator"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class InMemoryTelemetryReadStore:
    """Narrow test double for the one method the ``/api/v1/telemetry`` handler
    calls on ``read_store``: ``list_telemetry_events_with_source``.

    ``ReadSurfacePorts``/``ports.lifecycle_telemetry_governance`` do
    not replicate the legacy read-surface store's env-var-driven local-file fallback
    (real event-store file wins when non-empty; otherwise fall back to a
    projection of the summary store, with a distinct ``source``/``status``/
    ``staleness`` shape). This double reads the same two env vars
    (``PANTHEON_BFF_TELEMETRY_EVENT_STORE`` / ``PANTHEON_BFF_TELEMETRY_SUMMARY_STORE``)
    and reproduces that exact precedence and event-shape behavior, without
    importing that legacy store.
    """

    def list_telemetry_events_with_source(
        self,
        pool_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        event_records = self._project_events(self._load_event_store())
        if event_records:
            return "service_store", self._filter(
                event_records, pool_id=pool_id, artifact_id=artifact_id,
            )

        fallback_events = self._telemetry_summary_projection_events()
        if fallback_events:
            return "telemetry_summary_fallback", self._filter(
                fallback_events, pool_id=pool_id, artifact_id=artifact_id,
            )
        return "missing", []

    @staticmethod
    def _load_event_store() -> List[Dict[str, Any]]:
        path = os.getenv("PANTHEON_BFF_TELEMETRY_EVENT_STORE", "").strip()
        if not path or not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            payload = payload.get("events", [])
        if not isinstance(payload, list):
            return []
        return [event for event in payload if isinstance(event, dict)]

    @staticmethod
    def _load_summary_store() -> Dict[str, Dict[str, Any]]:
        path = os.getenv("PANTHEON_BFF_TELEMETRY_SUMMARY_STORE", "").strip()
        if not path or not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _project_events(raw_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        projected = []
        for event in raw_events:
            item = json.loads(json.dumps(event))
            event_id = item.get("id") or item.get("event_id") or item.get("telemetry_event_id")
            if event_id not in (None, ""):
                item.setdefault("id", str(event_id))
            runtime_id = item.get("runtime_id") or item.get("runtimeBindingId") or item.get("runtime_binding_id")
            if runtime_id not in (None, ""):
                item.setdefault("runtime_id", str(runtime_id))
            event_type = item.get("type") or item.get("event_type") or item.get("kind") or "telemetry"
            item.setdefault("type", str(event_type))
            projected.append(item)
        return projected

    def _telemetry_summary_projection_events(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for runtime_id, summary in self._load_summary_store().items():
            if not runtime_id:
                continue
            events.append(
                {
                    "id": f"tl-evt-{runtime_id}",
                    "runtime_id": runtime_id,
                    "type": "telemetry_snapshot",
                    "timestamp": summary.get("collected_at", ""),
                    "metrics": {
                        "pnl": summary.get("pnl"),
                        "drawdown": summary.get("drawdown"),
                        "sharpe_ratio": summary.get("sharpe_ratio"),
                        "total_trades": summary.get("total_trades"),
                        "fill_rate": summary.get("fill_rate"),
                        "avg_slippage_bps": summary.get("avg_slippage_bps"),
                    },
                }
            )
        return events

    @staticmethod
    def _timestamp(event: Dict[str, Any]) -> str:
        for key in ("timestamp", "occurred_at", "emitted_at", "created_at", "collected_at"):
            value = event.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    @classmethod
    def _filter(
        cls,
        events: List[Dict[str, Any]],
        *,
        pool_id: Optional[str],
        artifact_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        filtered = list(events)
        if artifact_id:
            filtered = [
                event
                for event in filtered
                if event.get("artifact_id") == artifact_id or event.get("runtime_id") == artifact_id
            ]
        if pool_id:
            filtered = [event for event in filtered if event.get("pool_id") == pool_id]
        return sorted(filtered, key=cls._timestamp, reverse=True)


def _store() -> InMemoryTelemetryReadStore:
    return InMemoryTelemetryReadStore()


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
        bff_main.read_store = _store()
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
        bff_main.read_store = _store()
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
