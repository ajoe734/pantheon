from __future__ import annotations

import os
import uuid
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.control_plane.bff.auth.policy import (
    _PACK_D_D21_ERROR_BEHAVIOR,
    bff_error,
    pack_d_error_metadata,
)
from services.control_plane.bff.models import ErrorCode

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


def _pack_d_direct_error_response(
    *,
    status_code: int,
    code: Any,
    message: Any,
    details: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    metadata = pack_d_error_metadata(code, status_code=status_code)
    correlation_id = str(uuid.uuid4())
    content: Dict[str, Any] = {
        "error": {
            "code": metadata["code"],
            "i18nKey": metadata["i18nKey"],
            "message": str(message),
            "retryable": metadata["retryable"],
            "userActionable": metadata["userActionable"],
            "details": dict(details or {}),
        },
        "meta": {"correlationId": correlation_id},
    }
    if extra:
        content.update(extra)
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers={"X-Correlation-Id": correlation_id},
    )


def _build_error_envelope_app() -> FastAPI:
    app = FastAPI()

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
        correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            error_payload = dict(detail["error"])
            if "details" in error_payload and isinstance(error_payload["details"], dict):
                error_payload["details"] = {
                    k: v for k, v in error_payload["details"].items() if k != "correlationId"
                }
            content = {
                "error": error_payload,
                "meta": {"correlationId": correlation_id},
            }
        else:
            code = "RESOURCE_NOT_FOUND" if exc.status_code == 404 else "VALIDATION_FAILED"
            metadata = pack_d_error_metadata(code, status_code=exc.status_code)
            content = {
                "error": {
                    "code": metadata["code"],
                    "i18nKey": metadata["i18nKey"],
                    "message": str(detail or "Error"),
                    "retryable": metadata["retryable"],
                    "userActionable": metadata["userActionable"],
                    "details": {"reason": str(detail or "")},
                },
                "meta": {"correlationId": correlation_id},
            }
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers={"X-Correlation-Id": correlation_id},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError):
        correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        metadata = pack_d_error_metadata("VALIDATION_FAILED", status_code=422)
        content = {
            "error": {
                "code": metadata["code"],
                "i18nKey": metadata["i18nKey"],
                "message": "Request validation failed",
                "retryable": metadata["retryable"],
                "userActionable": metadata["userActionable"],
                "details": {
                    "reason": "REQUEST_VALIDATION_ERROR",
                    "errors": exc.errors(),
                },
            },
            "meta": {"correlationId": correlation_id},
        }
        return JSONResponse(
            status_code=422,
            content=content,
            headers={"X-Correlation-Id": correlation_id},
        )

    @app.exception_handler(ValueError)
    async def _value_error_handler(request: Request, exc: ValueError):
        correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        metadata = pack_d_error_metadata("VALIDATION_FAILED", status_code=400)
        content = {
            "error": {
                "code": metadata["code"],
                "i18nKey": metadata["i18nKey"],
                "message": str(exc),
                "retryable": metadata["retryable"],
                "userActionable": metadata["userActionable"],
                "details": {"reason": "VALUE_ERROR"},
            },
            "meta": {"correlationId": correlation_id},
        }
        return JSONResponse(
            status_code=400,
            content=content,
            headers={"X-Correlation-Id": correlation_id},
        )

    @app.exception_handler(Exception)
    async def _generic_exception_handler(request: Request, exc: Exception):
        correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        metadata = pack_d_error_metadata("INTERNAL_ERROR", status_code=500)
        content = {
            "error": {
                "code": metadata["code"],
                "i18nKey": metadata["i18nKey"],
                "message": "Internal server error",
                "retryable": metadata["retryable"],
                "userActionable": metadata["userActionable"],
                "details": {"reason": "INTERNAL_SERVER_ERROR"},
            },
            "meta": {"correlationId": correlation_id},
        }
        return JSONResponse(
            status_code=500,
            content=content,
            headers={"X-Correlation-Id": correlation_id},
        )

    @app.get("/bff/me")
    async def _bff_me_endpoint(request: Request):
        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            raise bff_error(
                401,
                ErrorCode.AUTH_REQUIRED,
                "Authentication required",
                "Token is absent or not a Bearer token",
                correlation_id=request.headers.get("X-Correlation-Id"),
            )
        return {"data": {"operator_id": "op-test"}}

    @app.get("/__test/error-envelope/request-validation")
    async def _request_validation_probe(limit: int):
        return {"limit": limit}

    @app.get("/__test/error-envelope/value-error")
    async def _value_error_probe():
        raise ValueError("Synthetic invalid request")

    @app.get("/__test/error-envelope/generic-500")
    async def _generic_500_probe():
        raise RuntimeError("Synthetic server failure")

    @app.get("/__test/error-envelope/direct-json-response")
    async def _direct_json_response_probe():
        return _pack_d_direct_error_response(
            status_code=503,
            code="DEPENDENCY_UNAVAILABLE",
            message="Synthetic direct response failure",
            details={"reason": "SYNTHETIC_DIRECT_RESPONSE"},
        )

    return app


_APP = _build_error_envelope_app()


def _client() -> TestClient:
    return TestClient(_APP, raise_server_exceptions=False)


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
    behavior = _PACK_D_D21_ERROR_BEHAVIOR

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
