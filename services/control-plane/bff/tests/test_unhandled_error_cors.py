"""Unhandled failures must remain readable only to allowlisted FE origins."""
from __future__ import annotations

import importlib

from fastapi.testclient import TestClient
import pytest


@pytest.mark.parametrize("origin,allowed", [
    ("https://app.dev.mvl-cap.tw", True),
    ("https://untrusted.example", False),
])
def test_unhandled_failure_retains_cors_boundary(monkeypatch, origin, allowed):
    main = importlib.import_module("services.control_plane.bff.main")
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://app.dev.mvl-cap.tw")
    monkeypatch.setattr(main, "_cors_origins", ["https://app.dev.mvl-cap.tw"])
    application = main._build_bff_app()

    @application.get("/test-owner-failure")
    async def fail():
        raise RuntimeError("private upstream diagnostic")

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/test-owner-failure", headers={"Origin": origin})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "private upstream diagnostic" not in response.text
    if allowed:
        assert response.headers["Access-Control-Allow-Origin"] == origin
        assert response.headers["Access-Control-Allow-Credentials"] == "true"
        assert "Origin" in response.headers["Vary"]
    else:
        assert "Access-Control-Allow-Origin" not in response.headers
