from __future__ import annotations

import os
import sys
from uuid import UUID

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main


def _install_error_envelope_test_routes() -> None:
    if getattr(bff_main.app.state, "error_envelope_test_routes_installed", False):
        return

    @bff_main.app.get("/__test/error-envelope/request-validation")
    async def _request_validation_probe(limit: int):
        return {"limit": limit}

    @bff_main.app.get("/__test/error-envelope/value-error")
    async def _value_error_probe():
        raise ValueError("Synthetic invalid request")

    @bff_main.app.get("/__test/error-envelope/generic-500")
    async def _generic_500_probe():
        raise RuntimeError("Synthetic server failure")

    bff_main.app.state.error_envelope_test_routes_installed = True


_install_error_envelope_test_routes()


def _client() -> TestClient:
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _assert_error_envelope(
    response,
    *,
    status_code: int,
    code: str,
    correlation_id: str | None,
) -> dict:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert "detail" not in body
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    observed_correlation_id = body["meta"]["correlationId"]
    if correlation_id is None:
        UUID(observed_correlation_id)
    else:
        assert observed_correlation_id == correlation_id
    assert response.headers["X-Correlation-Id"] == observed_correlation_id
    return body


def test_401_error_envelope_uses_top_level_error_and_meta_correlation() -> None:
    response = _client().get(
        "/bff/me",
        headers={"X-Correlation-Id": "corr-envelope-401"},
    )

    body = _assert_error_envelope(
        response,
        status_code=401,
        code="INVALID_TOKEN",
        correlation_id="corr-envelope-401",
    )
    assert body["error"]["details"]["reason"] == "Token is absent or not a Bearer token"
    assert "correlationId" not in body["error"]["details"]


def test_404_error_envelope_uses_top_level_error_and_meta_correlation() -> None:
    response = _client().get(
        "/bff/does-not-exist",
        headers={"X-Correlation-Id": "corr-envelope-404"},
    )

    _assert_error_envelope(
        response,
        status_code=404,
        code="OBJECT_NOT_FOUND",
        correlation_id="corr-envelope-404",
    )


def test_422_request_validation_error_envelope_uses_pack_d_shape() -> None:
    response = _client().get(
        "/__test/error-envelope/request-validation?limit=not-an-int",
        headers={"X-Correlation-Id": "corr-envelope-422"},
    )

    body = _assert_error_envelope(
        response,
        status_code=422,
        code="INVALID_PARAMS",
        correlation_id="corr-envelope-422",
    )
    assert body["error"]["details"]["reason"] == "REQUEST_VALIDATION_ERROR"


def test_value_error_envelope_uses_pack_d_shape() -> None:
    response = _client().get(
        "/__test/error-envelope/value-error",
        headers={"X-Correlation-Id": "corr-envelope-value"},
    )

    body = _assert_error_envelope(
        response,
        status_code=400,
        code="INVALID_REQUEST",
        correlation_id="corr-envelope-value",
    )
    assert body["error"]["details"]["reason"] == "VALUE_ERROR"


def test_500_error_envelope_generates_uuid_correlation_when_missing() -> None:
    response = _client().get("/__test/error-envelope/generic-500")

    body = _assert_error_envelope(
        response,
        status_code=500,
        code="DOWNSTREAM_UNAVAILABLE",
        correlation_id=None,
    )
    assert body["error"]["message"] == "Internal server error"
    assert body["error"]["details"]["reason"] == "INTERNAL_SERVER_ERROR"
