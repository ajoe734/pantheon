from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path
from typing import Any


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "devloop_census.py"
    spec = importlib.util.spec_from_file_location("devloop_census", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, *, status: int = 200, body: dict[str, Any]) -> None:
        self.status = status
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        return False

    def read(self) -> bytes:
        return self._body


def test_build_census_does_not_treat_synthesized_telemetry_as_started(monkeypatch) -> None:
    census = _load_module()

    payloads = {
        "/api/v1/telemetry": {
            "data": [
                {"id": "tl-1", "metrics": {"total_trades": None, "pnl": 0.0}},
                {"id": "tl-2", "metrics": {"total_trades": 0, "drawdown": None}},
            ],
            "meta": {
                "total": 2,
                "surfaces": {
                    "telemetry_events": {
                        "status": "degraded",
                        "source": "telemetry_summary_fallback",
                    }
                },
            },
        },
        "/bff/v5/loop-runs": {"data": [], "meta": {"total": 0}},
        "/bff/approvals": {"items": [], "count": 0},
        "/api/v1/evolution-decisions": {"items": [], "page_info": {"next_page_token": None}},
        "/bff/incidents": {"data": [], "page_info": {"total": 0}},
        "/api/v1/rollbacks": {"items": [], "meta": {"snapshot_at": "2026-06-14T00:00:00Z"}},
    }

    def fake_urlopen(request, timeout: float, context):
        assert timeout == 3.0
        path = request.full_url.removeprefix("https://bff.example.test")
        return _FakeResponse(body=payloads[path])

    monkeypatch.setattr(census.urllib.request, "urlopen", fake_urlopen)

    result = census.build_census(
        base_url="https://bff.example.test",
        token="op-dev:admin:mfa",
        timeout=3.0,
        insecure=True,
    )

    assert result["right_half_started"] is False
    assert result["hard_failure_count"] == 0
    assert result["totals"]["telemetry_records"] == 2
    assert result["totals"]["trades"] == 0
    assert result["surfaces"]["telemetry"]["material_reason"] == "synthesized summary fallback"


def test_build_census_marks_started_when_right_half_has_material_counts(monkeypatch) -> None:
    census = _load_module()

    payloads = {
        "/api/v1/telemetry": {
            "data": [{"id": "tl-fill", "type": "paper_fill", "metrics": {"total_trades": 3}}],
            "meta": {"surfaces": {"telemetry_events": {"status": "ok", "source": "postgres"}}},
        },
        "/bff/v5/loop-runs": {"items": [{"id": "loop-1"}], "meta": {"total": 1}},
        "/bff/approvals": {"items": []},
        "/api/v1/evolution-decisions": {"items": []},
        "/bff/incidents": {"items": []},
        "/api/v1/rollbacks": {"items": [{"id": "rb-1"}]},
    }

    def fake_urlopen(request, timeout: float, context):
        path = request.full_url.removeprefix("https://bff.example.test")
        return _FakeResponse(body=payloads[path])

    monkeypatch.setattr(census.urllib.request, "urlopen", fake_urlopen)

    result = census.build_census(
        base_url="https://bff.example.test",
        token="op-dev:admin:mfa",
        timeout=5.0,
        insecure=True,
    )

    assert result["right_half_started"] is True
    assert result["material_signal_count"] == 3
    assert result["totals"]["trades"] == 3
    assert result["totals"]["loop_runs"] == 1
    assert result["totals"]["rollbacks"] == 1


def test_build_census_records_hard_failures(monkeypatch) -> None:
    census = _load_module()

    def fake_urlopen(request, timeout: float, context):
        path = request.full_url.removeprefix("https://bff.example.test")
        if path == "/bff/approvals":
            raise urllib.error.HTTPError(
                request.full_url,
                500,
                "Internal Server Error",
                {},
                io.BytesIO(b'{"error":{"code":"SERVER_ERROR"}}'),
            )
        return _FakeResponse(body={"items": [], "meta": {"total": 0}})

    monkeypatch.setattr(census.urllib.request, "urlopen", fake_urlopen)

    result = census.build_census(
        base_url="https://bff.example.test",
        token="op-dev:admin:mfa",
        timeout=5.0,
        insecure=True,
    )

    assert result["hard_failure_count"] == 1
    assert result["hard_failures"][0]["surface"] == "approvals"
    assert result["surfaces"]["approvals"]["count"] is None


def test_count_payload_items_prefers_total_fields() -> None:
    census = _load_module()

    assert census.count_payload_items({"data": [1], "page_info": {"total": 7}}) == 7
    assert census.count_payload_items({"items": [1], "meta": {"total": 5}}) == 5
    assert census.count_payload_items({"items": [1, 2], "count": 4}) == 4
    assert census.count_payload_items({"items": [1, 2, 3]}) == 3
