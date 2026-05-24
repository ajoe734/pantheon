from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


HEADERS = {"Authorization": "Bearer op-bff-delta:operator,reviewer"}
LOVABLE_ORIGIN = "https://pantheon-dev.lovable.app"


def _fresh_client(td: str, *, fallback: bool = True) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=fallback,
    )
    return TestClient(bff_main.app, raise_server_exceptions=False)


def test_persona_league_heatmap() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/persona-league/heatmap",
                headers=HEADERS,
                params={"bucket": "day", "bucket_count": 3, "limit": 5},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            data = body["data"]

            assert set(body) >= {"data", "meta"}
            assert body["items"] == data["rows"]
            assert body["rows"] == data["rows"]
            assert body["buckets"] == data["buckets"]
            assert body["cells"] == data["cells"]
            assert len(data["buckets"]) == 3
            assert body["summary"]["bucket"] == "day"
            assert body["summary"]["cellCount"] == len(body["rows"]) * len(body["buckets"])
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["surfaces"]["persona_league_heatmap"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/persona-league" in body["meta"]["composition_sources"]

            alpha = next(row for row in body["rows"] if row["personaId"] == "persona-alpha")
            assert len(alpha["cells"]) == 3
            latest_cell = alpha["cells"][-1]
            assert isinstance(latest_cell["compositeScore"], (int, float))
            assert latest_cell["score"] == latest_cell["compositeScore"]
            assert latest_cell["overallScore"] == latest_cell["compositeScore"]
            assert latest_cell["formulaVersion"] == "pm12-default-v1"
            assert set(latest_cell["components"]) >= {
                "overallScore",
                "pnlScore",
                "riskScore",
                "executionScore",
                "activityScore",
            }
        finally:
            bff_main.read_store = original


def test_persona_league_heatmap_requires_auth() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get("/bff/management/persona-league/heatmap")

            assert response.status_code == 401, response.text
        finally:
            bff_main.read_store = original


def test_persona_league_heatmap_cors_preflight() -> None:
    client = TestClient(bff_main.app, raise_server_exceptions=False)
    response = client.options(
        "/bff/management/persona-league/heatmap",
        headers={
            "Origin": LOVABLE_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code in {200, 204}
    assert response.headers.get("access-control-allow-origin") == LOVABLE_ORIGIN
