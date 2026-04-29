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
GET  /api/openclaw-adapter/capabilities        — static capability metadata (no live upstream call)
GET  /api/openclaw-adapter/sessions            — stub session list (deferred; upstream_unavailable when absent)
POST /api/openclaw-adapter/sessions            — stub session create (deferred; not activated)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

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
    "activation_state": "facade_only",
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
        "This adapter exposes the Pantheon boundary facade only. "
        "All live runtime contract methods are deferred until the EP5 human gate is passed."
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
    return _CAPABILITY_SNAPSHOT


# ---------------------------------------------------------------------------
# Session metadata stubs (deferred; degrades when upstream absent)
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
    probe = _probe_upstream()
    if not probe.get("reachable"):
        return JSONResponse(
            status_code=503,
            content={
                "status": "upstream_unavailable",
                "sessions": [],
                "note": (
                    "Upstream OpenClaw gateway is absent or unhealthy. "
                    "Session listing is unavailable in degraded mode."
                ),
                "upstream": probe,
            },
        )
    # Upstream reachable but live session query is deferred — return empty stub.
    return JSONResponse(
        status_code=200,
        content={
            "status": "deferred",
            "sessions": [],
            "note": (
                "Live session query against the upstream gateway is deferred. "
                "No session state is tracked at this adapter boundary."
            ),
        },
    )


@app.post("/api/openclaw-adapter/sessions", status_code=503)
def create_session(req: CreateSessionRequest) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "status": "deferred",
            "error_code": "CAPABILITY_DENIED",
            "message": (
                "Session creation through this adapter boundary is explicitly deferred. "
                "No live runtime contract methods are activated. "
                "This path requires the EP5 human gate to be passed first."
            ),
            "retryable": False,
            "owner_plane": "pantheon_adapter",
            "error_layer": "known",
        },
    )
