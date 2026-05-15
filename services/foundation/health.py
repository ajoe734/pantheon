"""Shared service health and minimal observability payloads."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .serialization import isoformat_z, utc_now

HealthDetails = Mapping[str, Any] | Callable[[], Mapping[str, Any]]


def _resolve(value: HealthDetails | None) -> dict[str, Any]:
    if value is None:
        return {}
    resolved = value() if callable(value) else value
    return dict(resolved)


def _normalize_dependency(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        payload = dict(value)
        status = str(payload.get("status") or "ok").lower()
        payload["status"] = status
        return payload
    return {"status": "ok" if bool(value) else "error"}


def _overall_status(live: bool, ready: bool, dependencies: Mapping[str, Any]) -> str:
    if not live:
        return "error"
    dependency_statuses = [
        str(_normalize_dependency(value).get("status") or "ok").lower()
        for value in dependencies.values()
    ]
    if not ready or any(status in {"degraded", "error", "unavailable", "failed"} for status in dependency_statuses):
        return "degraded"
    return "ok"


def health_payload(
    service: str,
    *,
    live: bool = True,
    ready: bool = True,
    dependencies: HealthDetails | None = None,
    metrics: HealthDetails | None = None,
    details: HealthDetails | None = None,
) -> dict[str, Any]:
    """Return the canonical service health payload.

    The shape is intentionally small and JSON-native so legacy routes can
    return it directly while readiness probes can map non-ok status to 503.
    """

    dependency_payload = {
        name: _normalize_dependency(value)
        for name, value in _resolve(dependencies).items()
    }
    status = _overall_status(live=live, ready=ready, dependencies=dependency_payload)
    return {
        "status": status,
        "service": service,
        "timestamp": isoformat_z(utc_now()),
        "live": bool(live),
        "ready": status == "ok",
        "dependencies": dependency_payload,
        "metrics": {"service_up": 1, **_resolve(metrics)},
        "details": _resolve(details),
    }


def readiness_status_code(payload: Mapping[str, Any]) -> int:
    return 200 if str(payload.get("status") or "").lower() == "ok" else 503


def metrics_payload(service: str, metrics: HealthDetails | None = None) -> dict[str, Any]:
    return {
        "service": service,
        "timestamp": isoformat_z(utc_now()),
        "metrics": {"service_up": 1, **_resolve(metrics)},
    }


def register_fastapi_health_routes(
    app: Any,
    service: str,
    *,
    dependencies: HealthDetails | None = None,
    metrics: HealthDetails | None = None,
    details: HealthDetails | None = None,
) -> None:
    """Register /healthz, /livez, /readyz, and /metrics on a FastAPI app."""

    from fastapi.responses import JSONResponse

    def _payload() -> dict[str, Any]:
        return health_payload(service, dependencies=dependencies, metrics=metrics, details=details)

    async def healthz() -> dict[str, Any]:
        return _payload()

    async def livez() -> dict[str, Any]:
        return health_payload(service, ready=True, metrics=metrics, details=details)

    async def readyz() -> Any:
        payload = _payload()
        return JSONResponse(payload, status_code=readiness_status_code(payload))

    async def metrics_route() -> dict[str, Any]:
        return metrics_payload(service, metrics)

    app.add_api_route("/healthz", healthz, methods=["GET"], summary="Health probe")
    app.add_api_route("/livez", livez, methods=["GET"], summary="Liveness probe")
    app.add_api_route("/readyz", readyz, methods=["GET"], summary="Readiness probe")
    app.add_api_route("/metrics", metrics_route, methods=["GET"], summary="Minimal service metrics")


def register_flask_health_routes(
    app: Any,
    service: str,
    *,
    dependencies: HealthDetails | None = None,
    metrics: HealthDetails | None = None,
    details: HealthDetails | None = None,
) -> None:
    """Register /healthz, /livez, /readyz, and /metrics on a Flask app."""

    from flask import jsonify

    def _payload() -> dict[str, Any]:
        return health_payload(service, dependencies=dependencies, metrics=metrics, details=details)

    def healthz():
        return jsonify(_payload()), 200

    def livez():
        return jsonify(health_payload(service, ready=True, metrics=metrics, details=details)), 200

    def readyz():
        payload = _payload()
        return jsonify(payload), readiness_status_code(payload)

    def metrics_route():
        return jsonify(metrics_payload(service, metrics)), 200

    app.add_url_rule("/healthz", endpoint=f"{service}_healthz", view_func=healthz, methods=["GET"])
    app.add_url_rule("/livez", endpoint=f"{service}_livez", view_func=livez, methods=["GET"])
    app.add_url_rule("/readyz", endpoint=f"{service}_readyz", view_func=readyz, methods=["GET"])
    app.add_url_rule("/metrics", endpoint=f"{service}_metrics", view_func=metrics_route, methods=["GET"])
