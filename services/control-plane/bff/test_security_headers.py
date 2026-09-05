"""Regression: the operator BFF must emit baseline security headers on every
response, without disturbing CORS, and via a streaming-safe (pure-ASGI) layer.

Verification campaign 2026-06-14, round 24, finding F14. The BFF previously
returned no X-Content-Type-Options / X-Frame-Options / Referrer-Policy headers.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient as StarletteTestClient

BFF_DIR = Path(__file__).resolve().parent

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")
os.environ.setdefault("PANTHEON_BFF_CORS_ORIGINS", "https://fe.example.com")

from services.control_plane.bff import main as bff_main
from fastapi.testclient import TestClient  # noqa: E402

CLIENT = TestClient(bff_main.app)
HEADERS = {"Authorization": "Bearer op-sec:operator,admin,reviewer:mfa"}
EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


@pytest.mark.parametrize("with_auth", [True, False])
def test_security_headers_present_on_all_responses(with_auth):
    r = CLIENT.get("/bff/me", headers=HEADERS if with_auth else {})
    for name, value in EXPECTED.items():
        assert r.headers.get(name) == value, f"{name} missing/wrong on {r.status_code}"


def test_cors_headers_coexist_with_security_headers():
    r = CLIENT.options(
        "/bff/me",
        headers={"Origin": "https://fe.example.com", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") == "https://fe.example.com"
    assert r.headers.get("x-frame-options") == "DENY"


def test_middleware_is_streaming_safe():
    # The middleware must not buffer/break a StreamingResponse: stand up a tiny
    # app wrapped with it and assert the streamed body arrives intact + headers.
    async def stream(_request):
        async def gen():
            for i in range(3):
                yield f"chunk{i};".encode()
        return StreamingResponse(gen(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/s", stream)])
    app.add_middleware(bff_main._SecurityHeadersMiddleware)
    with StarletteTestClient(app) as client:
        r = client.get("/s")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("content-type", "").startswith("text/event-stream")
    assert r.text == "chunk0;chunk1;chunk2;"
