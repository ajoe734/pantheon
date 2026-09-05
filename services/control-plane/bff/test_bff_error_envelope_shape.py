from __future__ import annotations

import os
import sys
from uuid import UUID

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
from models import ErrorCode


PACK_D_D21_ERROR_CODES = [
    "RESOURCE_NOT_FOUND",
    "AUTH_REQUIRED",
    "AUTH_EXPIRED",
    "FORBIDDEN",
    "RATE_LIMITED",
    "VALIDATION_FAILED",
    "BUSINESS_RULE_VIOLATION",
    "IDEMPOTENCY_CONFLICT",
    "PRECONDITION_FAILED",
    "CONFIRMATION_REQUIRED",
    "TWO_MAN_SIGNATURE_REQUIRED",
    "HUMAN_GATE_PENDING",
    "HUMAN_GATE_REJECTED",
    "HUMAN_GATE_EXPIRED",
    "RESOURCE_CONFLICT",
    "OPERATION_NOT_ALLOWED",
    "DEPENDENCY_UNAVAILABLE",
    "UPSTREAM_TIMEOUT",
    "UPSTREAM_ERROR",
    "INTERNAL_ERROR",
    "NOT_IMPLEMENTED",
    "MAINTENANCE_MODE",
    "KILL_SWITCH_ACTIVE",
    "SAFE_MODE_ACTIVE",
    "DEGRADED_READ_ONLY",
    "REQUEST_TOO_LARGE",
]


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

    @bff_main.app.get("/__test/error-envelope/direct-json-response")
    async def _direct_json_response_probe():
        return bff_main._pack_d_direct_error_response(
            status_code=503,
            code="DEPENDENCY_UNAVAILABLE",
            message="Synthetic direct response failure",
            details={"reason": "SYNTHETIC_DIRECT_RESPONSE"},
        )

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
    retryable: bool,
    user_actionable: bool,
) -> dict:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert "detail" not in body
    assert body["error"]["code"] == code
    assert body["error"]["i18nKey"] == f"errors.{code}"
    assert body["error"]["message"]
    assert body["error"]["retryable"] is retryable
    assert body["error"]["userActionable"] is user_actionable
    observed_correlation_id = body["meta"]["correlationId"]
    if correlation_id is None:
        UUID(observed_correlation_id)
    else:
        assert observed_correlation_id == correlation_id
    assert response.headers["X-Correlation-Id"] == observed_correlation_id
    return body


def test_error_code_enum_matches_pack_d_d21_allowlist() -> None:
    observed = [code.value for code in ErrorCode]
    assert observed == PACK_D_D21_ERROR_CODES
    assert len(observed) == 26


def test_error_behavior_matrix_covers_pack_d_d21_allowlist() -> None:
    behavior = bff_main._PACK_D_D21_ERROR_BEHAVIOR

    assert list(behavior.keys()) == PACK_D_D21_ERROR_CODES
    for flags in behavior.values():
        assert isinstance(flags["retryable"], bool)
        assert isinstance(flags["userActionable"], bool)

    assert behavior["RESOURCE_NOT_FOUND"] == {"retryable": False, "userActionable": True}
    assert behavior["VALIDATION_FAILED"] == {"retryable": False, "userActionable": True}
    assert behavior["FORBIDDEN"] == {"retryable": False, "userActionable": False}
    assert behavior["RATE_LIMITED"] == {"retryable": True, "userActionable": True}
    assert behavior["DEPENDENCY_UNAVAILABLE"] == {"retryable": True, "userActionable": True}
    assert behavior["UPSTREAM_TIMEOUT"] == {"retryable": True, "userActionable": True}


def test_401_error_envelope_uses_top_level_error_and_meta_correlation() -> None:
    response = _client().get(
        "/bff/me",
        headers={"X-Correlation-Id": "corr-envelope-401"},
    )

    body = _assert_error_envelope(
        response,
        status_code=401,
        code="AUTH_REQUIRED",
        correlation_id="corr-envelope-401",
        retryable=False,
        user_actionable=True,
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
        code="RESOURCE_NOT_FOUND",
        correlation_id="corr-envelope-404",
        retryable=False,
        user_actionable=True,
    )


def test_422_request_validation_error_envelope_uses_pack_d_shape() -> None:
    response = _client().get(
        "/__test/error-envelope/request-validation?limit=not-an-int",
        headers={"X-Correlation-Id": "corr-envelope-422"},
    )

    body = _assert_error_envelope(
        response,
        status_code=422,
        code="VALIDATION_FAILED",
        correlation_id="corr-envelope-422",
        retryable=False,
        user_actionable=True,
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
        code="VALIDATION_FAILED",
        correlation_id="corr-envelope-value",
        retryable=False,
        user_actionable=True,
    )
    assert body["error"]["details"]["reason"] == "VALUE_ERROR"


def test_500_error_envelope_generates_uuid_correlation_when_missing() -> None:
    response = _client().get("/__test/error-envelope/generic-500")

    body = _assert_error_envelope(
        response,
        status_code=500,
        code="INTERNAL_ERROR",
        correlation_id=None,
        retryable=False,
        user_actionable=False,
    )
    assert body["error"]["message"] == "Internal server error"
    assert body["error"]["details"]["reason"] == "INTERNAL_SERVER_ERROR"


def test_direct_json_error_response_uses_pack_d_shape() -> None:
    response = _client().get("/__test/error-envelope/direct-json-response")

    body = _assert_error_envelope(
        response,
        status_code=503,
        code="DEPENDENCY_UNAVAILABLE",
        correlation_id=None,
        retryable=True,
        user_actionable=True,
    )
    assert body["error"]["details"]["reason"] == "SYNTHETIC_DIRECT_RESPONSE"
