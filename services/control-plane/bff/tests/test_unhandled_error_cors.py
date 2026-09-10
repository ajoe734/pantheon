"""Unhandled failures must remain readable only to allowlisted FE origins."""
from __future__ import annotations

import os
import uuid
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest


def _build_unhandled_error_cors_app() -> FastAPI:
    allowed_origins = [
        origin.strip()
        for origin in os.getenv(
            "PANTHEON_BFF_CORS_ORIGINS", "https://app.dev.mvl-cap.tw"
        ).split(",")
        if origin.strip()
    ]
    app = FastAPI()

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        origin = request.headers.get("origin")
        headers = {"Vary": "Origin"}
        if origin and origin in allowed_origins:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Credentials"] = "true"
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Internal server error",
                    "i18nKey": "errors.INTERNAL_ERROR",
                    "retryable": False,
                    "userActionable": False,
                    "details": {"reason": "INTERNAL_SERVER_ERROR"},
                },
                "meta": {"correlationId": str(uuid.uuid4())},
            },
            headers=headers,
        )

    return app


@pytest.mark.parametrize("origin,allowed", [
    ("https://app.dev.mvl-cap.tw", True),
    ("https://untrusted.example", False),
])
def test_unhandled_failure_retains_cors_boundary(monkeypatch, origin, allowed):
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://app.dev.mvl-cap.tw")
    application = _build_unhandled_error_cors_app()

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
