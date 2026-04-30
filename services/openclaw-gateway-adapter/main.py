"""services/openclaw-gateway-adapter — Pantheon-owned boundary facade for the upstream OpenClaw gateway.

This service is the Pantheon adapter layer defined in OPENCLAW_RUNTIME_CONTRACT.md §2.2.
It exposes a controlled health/capability/session-metadata facade and degrades cleanly
when the optional upstream gateway is absent or unhealthy.

No live broker execution, paper, or production adapter paths are enabled here.
All live operation paths are explicitly marked as deferred.

Routes
------
GET  /healthz                          — health probe (dependencies include upstream)
GET  /livez                            — liveness probe (self-only)
GET  /readyz                           — readiness probe; 503 when upstream degraded
GET  /health                           — legacy compatibility alias for /healthz
GET  /metrics                          — minimal service metrics

GET  /api/openclaw-adapter/upstream/status     — upstream gateway reachability
GET  /api/openclaw-adapter/capabilities        — adapter capability metadata plus optional upstream capabilities
GET  /api/openclaw-adapter/sessions            — typed upstream session list; degrades when absent
GET  /api/openclaw-adapter/sessions/{id}       — typed upstream session read; degrades when absent
POST /api/openclaw-adapter/sessions            — typed upstream session create; broker paths remain disabled
POST /api/openclaw-adapter/sessions/{id}/cancel — typed upstream session cancel; broker paths remain disabled
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.foundation.health import (
    health_payload,
    readiness_status_code,
    register_fastapi_health_routes,
)


OPENCLAW_GATEWAY_URL = os.getenv("OPENCLAW_GATEWAY_URL", "")
_UPSTREAM_TIMEOUT = int(os.getenv("OPENCLAW_UPSTREAM_TIMEOUT", "3"))
_UPSTREAM_RETRIES = int(os.getenv("OPENCLAW_UPSTREAM_RETRIES", "1"))

# Explicit deferral guards: these env vars must be absent or falsy in all compose configs.
# Production adapter activation is intentionally deferred (no EP5 human gate completed).
_PRODUCTION_BROKER_ENABLED = os.getenv("OPENCLAW_PRODUCTION_BROKER_ENABLED", "").lower() in {"1", "true", "yes"}
_PAPER_ADAPTER_ENABLED = os.getenv("OPENCLAW_PAPER_ADAPTER_ENABLED", "").lower() in {"1", "true", "yes"}
_LIVE_ADAPTER_ENABLED = os.getenv("OPENCLAW_LIVE_ADAPTER_ENABLED", "").lower() in {"1", "true", "yes"}
_CAPITAL_BINDING_ENABLED = os.getenv("OPENCLAW_CAPITAL_BINDING_ENABLED", "").lower() in {"1", "true", "yes"}

# Static capability snapshot — reflects the minimum runtime contract from OPENCLAW_RUNTIME_CONTRACT.md §4.
# Returned without a live upstream call so the adapter remains useful in degraded mode.
_CAPABILITY_SNAPSHOT: Dict[str, Any] = {
    "adapter_version": "0.1.0",
    "activation_state": "upstream_client_degraded",
    "broker_execution": "deferred",
    "paper_adapter": "deferred",
    "live_adapter": "deferred",
    "capital_binding": "deferred",
    "fail_closed": True,
    "supported_session_types": [
        "interactive",
        "trainer",
        "research_task",
        "consult",
        "committee",
        "red_team",
        "background_job",
    ],
    "minimum_runtime_contract": {
        "agent_provisioning": "defined",
        "session_lifecycle": "defined",
        "tool_resolution": "defined",
        "skill_resolution": "defined",
        "multi_agent_consultation": "defined",
        "workflow_cron_hooks": "defined",
    },
    "activation_gates": {
        "broker_execution": "OPENCLAW_PRODUCTION_BROKER_ENABLED",
        "paper_adapter": "OPENCLAW_PAPER_ADAPTER_ENABLED",
        "live_adapter": "OPENCLAW_LIVE_ADAPTER_ENABLED",
        "capital_binding": "OPENCLAW_CAPITAL_BINDING_ENABLED",
    },
    "note": (
        "This adapter exposes the Pantheon boundary facade and typed OpenClaw upstream client. "
        "Broker execution, paper/live adapter paths, and capital binding remain deferred until "
        "their activation gates are passed."
    ),
}


def _is_healthy_upstream_response(status: int, body: bytes) -> bool:
    if status != 200:
        return False
    if not body:
        return True
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return True
    if isinstance(payload, dict):
        if payload.get("ok") is True:
            return True
        if str(payload.get("status", "")).lower() == "ok":
            return True
    return False


def _probe_upstream() -> Dict[str, Any]:
    """Probe the upstream OpenClaw gateway health endpoint without raising."""
    if not OPENCLAW_GATEWAY_URL:
        return {"reachable": False, "reason": "OPENCLAW_GATEWAY_URL not configured"}
    last_error: Dict[str, Any] = {}
    for path in ("/healthz", "/readyz"):
        try:
            req = urllib.request.Request(
                f"{OPENCLAW_GATEWAY_URL}{path}",
                headers={"Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=_UPSTREAM_TIMEOUT) as resp:
                status = resp.getcode()
                body = resp.read()
                return {
                    "reachable": _is_healthy_upstream_response(status, body),
                    "http_status": status,
                    "probe": path,
                }
        except urllib.error.HTTPError as exc:
            last_error = {"http_status": exc.code, "reason": str(exc), "probe": path}
            if exc.code != 404:
                break
        except Exception as exc:  # noqa: BLE001
            return {"reachable": False, "reason": str(exc), "probe": path}
    return {"reachable": False, **last_error}


def _upstream_health_dep() -> Dict[str, Any]:
    probe = _probe_upstream()
    return {"status": "ok" if probe.get("reachable") else "degraded", **probe}


@dataclass
class UpstreamClientError(Exception):
    status_code: int
    error_code: str
    message: str
    retryable: bool
    owner_plane: str = "openclaw_runtime"
    error_layer: str = "upstream"
    upstream_status: Optional[int] = None
    details: Optional[Dict[str, Any]] = None

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": "upstream_error",
            "error_code": self.error_code,
            "message": self.message,
            "retryable": self.retryable,
            "owner_plane": self.owner_plane,
            "error_layer": self.error_layer,
        }
        if self.upstream_status is not None:
            payload["upstream_status"] = self.upstream_status
        if self.details:
            payload["details"] = self.details
        return payload


class OpenClawUpstreamClient:
    """Small typed client for the OpenClaw-compatible gateway HTTP contract."""

    def __init__(self, base_url: str, *, timeout: int, retries: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(0, retries)

    def get_capabilities(self) -> Dict[str, Any]:
        return self._request("GET", "/api/capabilities")

    def list_sessions(self) -> List[Dict[str, Any]]:
        payload = self._request("GET", "/api/sessions")
        if isinstance(payload, list):
            return [self._normalize_session(item) for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            sessions = payload.get("sessions", [])
            if isinstance(sessions, list):
                return [self._normalize_session(item) for item in sessions if isinstance(item, dict)]
        raise UpstreamClientError(
            status_code=502,
            error_code="UPSTREAM_SCHEMA_ERROR",
            message="OpenClaw session list response did not match the expected schema.",
            retryable=False,
            error_layer="schema",
            details={"payload_type": type(payload).__name__},
        )

    def get_session(self, session_id: str) -> Dict[str, Any]:
        payload = self._request("GET", f"/api/sessions/{session_id}")
        if not isinstance(payload, dict):
            raise UpstreamClientError(
                status_code=502,
                error_code="UPSTREAM_SCHEMA_ERROR",
                message="OpenClaw session response did not match the expected schema.",
                retryable=False,
                error_layer="schema",
                details={"payload_type": type(payload).__name__},
            )
        return self._normalize_session(payload)

    def create_session(self, req: "CreateSessionRequest") -> Dict[str, Any]:
        payload = self._request(
            "POST",
            "/api/sessions",
            json_payload={
                "agent_id": req.agent_id,
                "session_type": req.session_type,
                "context_bundle": req.context_bundle or {},
            },
            expected_statuses={200, 201, 202},
        )
        if not isinstance(payload, dict):
            raise UpstreamClientError(
                status_code=502,
                error_code="UPSTREAM_SCHEMA_ERROR",
                message="OpenClaw session create response did not match the expected schema.",
                retryable=False,
                error_layer="schema",
                details={"payload_type": type(payload).__name__},
            )
        return self._normalize_session(payload)

    def cancel_session(self, session_id: str) -> Dict[str, Any]:
        payload = self._request(
            "POST",
            f"/api/sessions/{session_id}/cancel",
            expected_statuses={200, 202, 204},
        )
        if payload is None:
            return {"session_id": session_id, "status": "cancel_requested"}
        if not isinstance(payload, dict):
            raise UpstreamClientError(
                status_code=502,
                error_code="UPSTREAM_SCHEMA_ERROR",
                message="OpenClaw session cancel response did not match the expected schema.",
                retryable=False,
                error_layer="schema",
                details={"payload_type": type(payload).__name__},
            )
        return self._normalize_session({"session_id": session_id, **payload})

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: Optional[Dict[str, Any]] = None,
        expected_statuses: Optional[set[int]] = None,
    ) -> Any:
        if not self.base_url:
            raise UpstreamClientError(
                status_code=503,
                error_code="UPSTREAM_NOT_CONFIGURED",
                message="OPENCLAW_GATEWAY_URL is not configured.",
                retryable=False,
                owner_plane="pantheon_adapter",
                error_layer="configuration",
            )
        expected = expected_statuses or {200}
        attempts = self.retries + 1
        last_error: Optional[UpstreamClientError] = None
        for attempt in range(attempts):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.request(
                        method,
                        f"{self.base_url}{path}",
                        json=json_payload,
                        headers={"Accept": "application/json"},
                    )
                if response.status_code not in expected:
                    raise self._map_http_status(response)
                if response.status_code == 204 or not response.content:
                    return None
                return response.json()
            except UpstreamClientError as exc:
                last_error = exc
                if not exc.retryable or attempt == attempts - 1:
                    raise
            except httpx.TimeoutException as exc:
                last_error = UpstreamClientError(
                    status_code=504,
                    error_code="UPSTREAM_TIMEOUT",
                    message="Timed out while calling the upstream OpenClaw gateway.",
                    retryable=True,
                    details={"reason": str(exc)},
                )
                if attempt == attempts - 1:
                    raise last_error from exc
            except (httpx.ConnectError, httpx.NetworkError) as exc:
                last_error = UpstreamClientError(
                    status_code=503,
                    error_code="UPSTREAM_UNAVAILABLE",
                    message="The upstream OpenClaw gateway is unavailable.",
                    retryable=True,
                    details={"reason": str(exc)},
                )
                if attempt == attempts - 1:
                    raise last_error from exc
            except (json.JSONDecodeError, ValueError) as exc:
                raise UpstreamClientError(
                    status_code=502,
                    error_code="UPSTREAM_INVALID_JSON",
                    message="The upstream OpenClaw gateway returned invalid JSON.",
                    retryable=False,
                    error_layer="schema",
                    details={"reason": str(exc)},
                ) from exc
        raise last_error or UpstreamClientError(503, "UPSTREAM_UNAVAILABLE", "OpenClaw upstream unavailable.", True)

    def _map_http_status(self, response: httpx.Response) -> UpstreamClientError:
        status = response.status_code
        retryable = status in {408, 409, 425, 429} or status >= 500
        code = "UPSTREAM_HTTP_ERROR"
        adapter_status = 502
        if status in {401, 403}:
            code = "UPSTREAM_AUTH_DENIED"
            retryable = False
        elif status == 404:
            code = "UPSTREAM_NOT_FOUND"
            adapter_status = 404
            retryable = False
        elif status == 409:
            code = "UPSTREAM_CONFLICT"
            adapter_status = 409
            retryable = False
        elif status == 429:
            code = "UPSTREAM_RATE_LIMITED"
            adapter_status = 503
        elif status >= 500:
            code = "UPSTREAM_BAD_RESPONSE"
            adapter_status = 502
        return UpstreamClientError(
            status_code=adapter_status,
            error_code=code,
            message=f"OpenClaw upstream returned HTTP {status}.",
            retryable=retryable,
            upstream_status=status,
        )

    def _normalize_session(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "session_id": str(payload.get("session_id") or payload.get("id") or ""),
            "agent_id": str(payload.get("agent_id") or payload.get("agent") or ""),
            "session_type": str(payload.get("session_type") or payload.get("type") or "unknown"),
            "status": str(payload.get("status") or "unknown"),
            "note": payload.get("note"),
            "upstream": payload,
        }


def _client() -> OpenClawUpstreamClient:
    return OpenClawUpstreamClient(OPENCLAW_GATEWAY_URL, timeout=_UPSTREAM_TIMEOUT, retries=_UPSTREAM_RETRIES)


def _error_response(exc: UpstreamClientError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


app = FastAPI(
    title="Pantheon OpenClaw Gateway Adapter",
    version="0.1.0",
    description=(
        "Pantheon-owned boundary facade for the upstream OpenClaw-compatible gateway. "
        "Exposes health, capability metadata, and degraded-mode semantics. "
        "No live broker or production adapter paths are activated."
    ),
)

register_fastapi_health_routes(
    app,
    "openclaw-gateway-adapter",
    dependencies=lambda: {"openclaw_gateway": _upstream_health_dep()},
    details=lambda: {
        "gateway_url": OPENCLAW_GATEWAY_URL or "not_configured",
        "production_broker_enabled": _PRODUCTION_BROKER_ENABLED,
        "paper_adapter_enabled": _PAPER_ADAPTER_ENABLED,
        "live_adapter_enabled": _LIVE_ADAPTER_ENABLED,
        "capital_binding_enabled": _CAPITAL_BINDING_ENABLED,
    },
)


@app.get("/health")
def health_compat() -> Dict[str, Any]:
    return health_payload(
        "openclaw-gateway-adapter",
        dependencies={"openclaw_gateway": _upstream_health_dep()},
    )


# ---------------------------------------------------------------------------
# Upstream status
# ---------------------------------------------------------------------------


@app.get("/api/openclaw-adapter/upstream/status")
def upstream_status() -> Dict[str, Any]:
    probe = _probe_upstream()
    return {
        "upstream_url": OPENCLAW_GATEWAY_URL or None,
        "reachable": probe.get("reachable", False),
        "details": probe,
    }


# ---------------------------------------------------------------------------
# Capability metadata (static — no live upstream call required)
# ---------------------------------------------------------------------------


@app.get("/api/openclaw-adapter/capabilities")
def get_capabilities() -> Dict[str, Any]:
    payload = dict(_CAPABILITY_SNAPSHOT)
    try:
        payload["upstream"] = {
            "status": "ok",
            "capabilities": _client().get_capabilities(),
        }
    except UpstreamClientError as exc:
        payload["upstream"] = {**exc.to_payload(), "status": "degraded"}
    return payload


# ---------------------------------------------------------------------------
# Session metadata facade
# ---------------------------------------------------------------------------


class SessionMetadata(BaseModel):
    session_id: str
    agent_id: str
    session_type: str
    status: str
    note: Optional[str] = None


class CreateSessionRequest(BaseModel):
    agent_id: str
    session_type: str
    context_bundle: Optional[Dict[str, Any]] = None


@app.get("/api/openclaw-adapter/sessions")
def list_sessions() -> JSONResponse:
    try:
        return JSONResponse(status_code=200, content={"status": "ok", "sessions": _client().list_sessions()})
    except UpstreamClientError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "status": "upstream_unavailable",
                "sessions": [],
                "note": (
                    "Upstream OpenClaw gateway is absent or unhealthy. "
                    "Session listing is unavailable in degraded mode."
                ),
                "upstream": exc.to_payload(),
            },
        )


@app.get("/api/openclaw-adapter/sessions/{session_id}")
def get_session(session_id: str) -> JSONResponse:
    try:
        return JSONResponse(status_code=200, content={"status": "ok", "session": _client().get_session(session_id)})
    except UpstreamClientError as exc:
        return _error_response(exc)


@app.post("/api/openclaw-adapter/sessions")
def create_session(req: CreateSessionRequest) -> JSONResponse:
    try:
        return JSONResponse(status_code=201, content={"status": "ok", "session": _client().create_session(req)})
    except UpstreamClientError as exc:
        return _error_response(exc)


@app.post("/api/openclaw-adapter/sessions/{session_id}/cancel")
def cancel_session(session_id: str) -> JSONResponse:
    try:
        return JSONResponse(status_code=200, content={"status": "ok", "session": _client().cancel_session(session_id)})
    except UpstreamClientError as exc:
        return _error_response(exc)
