"""Stateless Pack D error formatting and exception handlers for Operator BFF.

Ensures consistent error envelope, correlation ID propagation, and CORS preservation
across HTTPException, validation errors, and unhandled 500 exceptions.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..models import ErrorCode
from ..auth import policy as auth_policy
from .http_security import _with_cors_actual_response_headers, _cors_origin_allowed

log = logging.getLogger(__name__)


def _clean_correlation_id(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    return raw or None


def _error_response_correlation_id(
    request: Optional[Request],
    headers: Optional[Dict[str, Any]] = None,
) -> str:
    if headers:
        for key in ("X-Correlation-Id", "x-correlation-id", "correlationId", "correlation_id"):
            val = _clean_correlation_id(headers.get(key))
            if val:
                return val
    if request is not None:
        for key in ("X-Correlation-Id", "x-correlation-id", "X-Request-Id", "x-request-id"):
            val = _clean_correlation_id(request.headers.get(key))
            if val:
                return val
    return str(uuid.uuid4())


def _status_error_code(status_code: int) -> str:
    return auth_policy.status_error_code(status_code)


def _canonical_error_code_value(code: Any, *, status_code: Optional[int] = None) -> str:
    return auth_policy.canonical_error_code_value(code, status_code=status_code)


def _pack_d_error_metadata(code: Any, *, status_code: Optional[int] = None) -> Dict[str, Any]:
    return auth_policy.pack_d_error_metadata(code, status_code=status_code)


def _status_error_message(status_code: int, fallback: Any = None) -> str:
    clean = str(fallback or "").strip()
    if clean and clean != "{}":
        return clean
    if status_code == 404:
        return "Not Found"
    if status_code == 422:
        return "Request validation failed"
    if status_code >= 500:
        return "Internal server error"
    return "Request failed"


def _error_details_without_correlation(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    return {
        key: item
        for key, item in value.items()
        if key != "correlationId"
    }


_RESERVED_ENVELOPE_KEYS = frozenset({
    "error",
    "meta",
    "correlationId",
    "code",
    "message",
    "details",
    "detail",
})


def _pack_d_error_response(
    *,
    status_code: int,
    code: Any,
    message: Any,
    correlation_id: str,
    details: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    metadata = _pack_d_error_metadata(code, status_code=status_code)
    error_payload: Dict[str, Any] = {
        "code": metadata["code"],
        "i18nKey": metadata["i18nKey"],
        "message": str(message or _status_error_message(status_code)),
        "retryable": metadata["retryable"],
        "userActionable": metadata["userActionable"],
    }
    if details is not None:
        error_payload["details"] = details
    content: Dict[str, Any] = {
        "error": error_payload,
        "meta": {"correlationId": correlation_id},
    }
    if extra:
        for key, value in extra.items():
            if key not in _RESERVED_ENVELOPE_KEYS:
                content[key] = value
    content["meta"]["correlationId"] = correlation_id
    response_headers = dict(headers or {})
    response_headers["X-Correlation-Id"] = correlation_id
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(content),
        headers=response_headers,
    )


def _pack_d_direct_error_response(
    *,
    status_code: int,
    code: Any,
    message: Any,
    details: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    return _pack_d_error_response(
        status_code=status_code,
        code=code,
        message=message,
        correlation_id=str(uuid.uuid4()),
        details=details,
        extra=extra,
    )


def _pack_d_http_exception_response(
    request: Request,
    exc: StarletteHTTPException,
    origin_allowed_fn: Optional[Callable[[Optional[str]], bool]] = None,
) -> JSONResponse:
    headers = _with_cors_actual_response_headers(
        request,
        dict(getattr(exc, "headers", None) or {}),
        origin_allowed_fn=origin_allowed_fn,
    )
    correlation_id = _error_response_correlation_id(request, headers)
    detail = exc.detail
    source = detail
    if (
        isinstance(detail, dict)
        and isinstance(detail.get("detail"), dict)
        and "error" in detail["detail"]
    ):
        source = detail["detail"]

    error: Dict[str, Any] = {}
    if isinstance(source, dict) and isinstance(source.get("error"), dict):
        error = dict(source["error"])
    elif isinstance(source, dict) and source.get("error") is not None:
        error = {
            "code": source.get("error"),
            "message": source.get("message") or source.get("error"),
        }

    code = error.get("code") or _status_error_code(exc.status_code)
    message = error.get("message") or _status_error_message(exc.status_code, detail)
    details = _error_details_without_correlation(error.get("details"))
    if details is None and not isinstance(source, dict):
        details = {"reason": str(source or message)}

    extra: Dict[str, Any] = {}
    if isinstance(source, dict):
        for key, value in source.items():
            if key not in _RESERVED_ENVELOPE_KEYS:
                extra[key] = value

    return _pack_d_error_response(
        status_code=exc.status_code,
        code=code,
        message=message,
        correlation_id=correlation_id,
        details=details,
        headers=headers,
        extra=extra or None,
    )


async def _bff_http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
    origin_allowed_fn: Optional[Callable[[Optional[str]], bool]] = None,
) -> JSONResponse:
    return _pack_d_http_exception_response(request, exc, origin_allowed_fn=origin_allowed_fn)


async def _bff_request_validation_error_handler(
    request: Request,
    exc: RequestValidationError,
    origin_allowed_fn: Optional[Callable[[Optional[str]], bool]] = None,
) -> JSONResponse:
    headers = _with_cors_actual_response_headers(request, {}, origin_allowed_fn=origin_allowed_fn)
    correlation_id = _error_response_correlation_id(request, headers)
    return _pack_d_error_response(
        status_code=422,
        code=ErrorCode.VALIDATION_FAILED.value,
        message="Request validation failed",
        correlation_id=correlation_id,
        details={
            "reason": "REQUEST_VALIDATION_ERROR",
            "errors": exc.errors(),
        },
        headers=headers,
    )


async def _bff_value_error_handler(
    request: Request,
    exc: ValueError,
    origin_allowed_fn: Optional[Callable[[Optional[str]], bool]] = None,
) -> JSONResponse:
    headers = _with_cors_actual_response_headers(request, {}, origin_allowed_fn=origin_allowed_fn)
    correlation_id = _error_response_correlation_id(request, headers)
    return _pack_d_error_response(
        status_code=400,
        code=ErrorCode.VALIDATION_FAILED.value,
        message=str(exc) or "Validation failed",
        correlation_id=correlation_id,
        details={"reason": "VALUE_ERROR"},
        headers=headers,
    )


async def _bff_unhandled_exception_handler(
    request: Request,
    exc: Exception,
    origin_allowed_fn: Optional[Callable[[Optional[str]], bool]] = None,
) -> JSONResponse:
    log.exception("Unhandled BFF request error", exc_info=True)
    correlation_id = _error_response_correlation_id(request)
    return _pack_d_error_response(
        status_code=500,
        code=ErrorCode.INTERNAL_ERROR.value,
        message="Internal server error",
        correlation_id=correlation_id,
        details={"reason": "INTERNAL_SERVER_ERROR"},
        headers=_with_cors_actual_response_headers(request, {}, origin_allowed_fn=origin_allowed_fn),
    )


def register_error_handlers(
    app: FastAPI,
    origin_allowed_fn: Optional[Callable[[Optional[str]], bool]] = None,
) -> None:
    async def http_exc_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return await _bff_http_exception_handler(request, exc, origin_allowed_fn=origin_allowed_fn)

    async def req_val_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return await _bff_request_validation_error_handler(request, exc, origin_allowed_fn=origin_allowed_fn)

    async def val_err_handler(request: Request, exc: ValueError) -> JSONResponse:
        return await _bff_value_error_handler(request, exc, origin_allowed_fn=origin_allowed_fn)

    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        return await _bff_unhandled_exception_handler(request, exc, origin_allowed_fn=origin_allowed_fn)

    app.add_exception_handler(HTTPException, http_exc_handler)
    app.add_exception_handler(StarletteHTTPException, http_exc_handler)
    app.add_exception_handler(RequestValidationError, req_val_handler)
    app.add_exception_handler(ValueError, val_err_handler)
    app.add_exception_handler(Exception, unhandled_handler)
