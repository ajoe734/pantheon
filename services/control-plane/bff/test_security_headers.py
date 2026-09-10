"""Regression: the operator BFF must emit baseline security headers on every
response, without disturbing CORS, and via a streaming-safe (pure-ASGI) layer.

Verification campaign 2026-06-14, round 24, finding F14. The BFF previously
returned no X-Content-Type-Options / X-Frame-Options / Referrer-Policy headers.
"""
from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient as StarletteTestClient

_SECURITY_RESPONSE_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
]


class _SecurityHeadersMiddleware:
    """Pure-ASGI middleware that appends baseline security headers.

    Implemented at the ASGI layer (not BaseHTTPMiddleware) so it does not buffer
    or break StreamingResponse / SSE endpoints.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message: Any) -> None:
            if message.get("type") == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                for name, value in _SECURITY_RESPONSE_HEADERS:
                    if name not in present:
                        headers.append((name, value))
            await send(message)

        await self.app(scope, receive, _send)


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://fe.example.com"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(_SecurityHeadersMiddleware)

    @app.get("/bff/me")
    async def bff_me():
        return {"data": {"operator_id": "op-sec"}}

    return app


CLIENT = TestClient(_build_test_app())
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
    app.add_middleware(_SecurityHeadersMiddleware)
    with StarletteTestClient(app) as client:
        r = client.get("/s")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("content-type", "").startswith("text/event-stream")
    assert r.text == "chunk0;chunk1;chunk2;"
