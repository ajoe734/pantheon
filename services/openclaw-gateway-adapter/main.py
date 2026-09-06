"""services/openclaw-gateway-adapter — Pantheon-owned boundary facade for the upstream OpenClaw gateway.

This service is the Pantheon adapter layer defined in OPENCLAW_RUNTIME_CONTRACT.md §2.2.
It exposes a controlled health/capability/session-metadata facade and degrades cleanly
when the optional upstream gateway is absent or unhealthy.

Production and live broker execution remain disabled. The paper broker adapter
surface is present but fail-closed unless OPENCLAW_PAPER_ADAPTER_ENABLED is set
and a paper RuntimeBinding can be verified through runtime-manager.

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

GET  /api/openclaw-adapter/lifecycle/sessions            — Pantheon-owned durable session list
GET  /api/openclaw-adapter/lifecycle/sessions/{id}       — Pantheon-owned session record (refreshes from upstream when active)
POST /api/openclaw-adapter/lifecycle/sessions            — idempotent create; records operator and audit trail
POST /api/openclaw-adapter/lifecycle/sessions/{id}/cancel — operator-owned cancel; preserves state on degraded upstream
GET  /api/openclaw-adapter/lifecycle/sessions/{id}/audit — append-only audit trail for the session

POST /api/openclaw-adapter/search/query              — governed evidence/citation search only
POST /api/openclaw-adapter/broker/paper/orders       — gated paper order simulation handoff
GET  /api/openclaw-adapter/broker/paper/orders       — gated paper order list via broker sidecar
GET  /api/openclaw-adapter/broker/paper/orders/{id}  — gated paper order read via broker sidecar
POST /api/openclaw-adapter/broker/live/orders        — always rejected
POST /api/openclaw-adapter/broker/canary/orders      — always rejected
GET  /api/openclaw-adapter/broker/audit              — paper intent/result audit trail
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from integrations.openclaw.search_gateway import OpenClawSearchGateway, SearchPolicyError as OpenClawSearchPolicyError
from services.knowledge.evidence import JsonlEvidenceRepository
from session_lifecycle import (
    LifecycleError,
    SessionLifecycleStore,
    SessionRecord,
)
from tool_workflow_bridge import (
    ASSISTANT_PROVIDER_REGISTER_TOOL_NAME,
    ASSISTANT_PROVIDER_REAUTH_TOOL_NAME,
    BridgeAuditLog,
    BridgeError,
    ToolPolicy,
    ToolWorkflowBridge,
)
from paper_broker_adapter import (
    PaperBrokerAdapter,
    PaperBrokerAdapterError,
    PaperBrokerAuditLog,
)
from live_gate_adapter import (
    LiveGateAdapter,
    LiveGateAuditLog,
    LiveGateError,
)
from assistant_credential_mounts import AssistantCredentialMounts
from assistant_codex_provider import (
    AssistantCodexProvider,
    CODEX_PROVIDER_ID,
    PROVIDER_RUNTIME as CODEX_PROVIDER_RUNTIME,
    CodexProviderError,
)
from assistant_claude_provider import AssistantClaudeProvider, ClaudeProviderError, ClaudeProviderResult
from assistant_openclaw_provider import (
    AssistantOpenClawProvider,
    DEFAULT_AGENT_ID as OPENCLAW_DEFAULT_AGENT_ID,
    OpenClawProviderError as GatewayOpenClawProviderError,
    delegates_kernel_mode_to_codex,
    derive_session_user,
)
from assistant_provider_registry import AssistantProviderRegistry, AssistantProviderRegistryError
from assistant_provider_runtime import (
    AssistantProviderRuntime,
    AssistantProviderRuntimeError,
    ProviderInvocationRequest,
)
from integrations.openclaw.adapter.agora_servant import ensure_agora_servant_agent

from services.foundation.health import (
    health_payload,
    readiness_status_code,
    register_fastapi_health_routes,
)


OPENCLAW_GATEWAY_URL = os.getenv("OPENCLAW_GATEWAY_URL", "")
_UPSTREAM_TIMEOUT = int(os.getenv("OPENCLAW_UPSTREAM_TIMEOUT", "3"))
_UPSTREAM_RETRIES = int(os.getenv("OPENCLAW_UPSTREAM_RETRIES", "1"))
_ASSISTANT_API_PREFIX = "/api/openclaw-adapter/assistant"
_AGENTS_API_PREFIX = "/api/openclaw-adapter/agents"
_CRON_API_PATH = "/api/openclaw-adapter/gateway/cron"
_ASSISTANT_SERVICE_TOKEN = os.getenv("PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN", "")
_ASSISTANT_SERVICE_AUTH_REQUIRED = os.getenv(
    "PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED",
    "",
).strip().lower() in {"1", "true", "yes", "on"}

# Explicit deferral guards: these env vars must be absent or falsy in all compose configs.
# Production adapter activation is intentionally deferred (no EP5 human gate completed).
_PRODUCTION_BROKER_ENABLED = os.getenv("OPENCLAW_PRODUCTION_BROKER_ENABLED", "").lower() in {"1", "true", "yes"}
_PAPER_ADAPTER_ENABLED = os.getenv("OPENCLAW_PAPER_ADAPTER_ENABLED", "").lower() in {"1", "true", "yes"}
_LIVE_ADAPTER_ENABLED = os.getenv("OPENCLAW_LIVE_ADAPTER_ENABLED", "").lower() in {"1", "true", "yes"}
_CANARY_ADAPTER_ENABLED = os.getenv("OPENCLAW_CANARY_ADAPTER_ENABLED", "").lower() in {"1", "true", "yes"}
_CAPITAL_BINDING_ENABLED = os.getenv("OPENCLAW_CAPITAL_BINDING_ENABLED", "").lower() in {"1", "true", "yes"}
_BROKER_SIDECAR_URL = os.getenv("OPENCLAW_BROKER_SIDECAR_URL", "")
_RUNTIME_MANAGER_URL = os.getenv("OPENCLAW_RUNTIME_MANAGER_URL", "") or os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "")
_SEARCH_EVIDENCE_STORE_PATH = os.getenv(
    "OPENCLAW_SEARCH_EVIDENCE_STORE_PATH",
    os.getenv(
        "SEARCH_EVIDENCE_STORE_PATH",
        os.getenv("SOURCE_INGEST_EVIDENCE_STORE_PATH", "/tmp/pantheon/search/source_evidence.jsonl"),
    ),
)

# Static capability snapshot — reflects the minimum runtime contract from OPENCLAW_RUNTIME_CONTRACT.md §4.
# Returned without a live upstream call so the adapter remains useful in degraded mode.
_CAPABILITY_SNAPSHOT: Dict[str, Any] = {
    "adapter_version": "0.2.0",
    "activation_state": "upstream_client_degraded",
    "broker_execution": "deferred",
    # sandbox_adapter: activation_ready means the fake-paper/sandbox contract is code-complete and
    # can be enabled by setting OPENCLAW_PAPER_ADAPTER_ENABLED=true with a broker sidecar.
    # No real capital or real orders — purely simulated.
    "sandbox_adapter": "activation_ready",
    "paper_adapter": "deferred",
    "live_adapter": "deferred",
    "canary_adapter": "deferred",
    "live_gate_harness": "present_disabled",
    "capital_binding": "deferred",
    "governed_search": "enabled",
    "session_lifecycle_state": "activation_ready",
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
        "session_lifecycle": "owned",
        "tool_resolution": "defined",
        "skill_resolution": "defined",
        "multi_agent_consultation": "defined",
        "workflow_cron_hooks": "defined",
    },
    "assistant_openclaw": "enabled",
    "activation_gates": {
        "broker_execution": "OPENCLAW_PRODUCTION_BROKER_ENABLED",
        "sandbox_adapter": "OPENCLAW_PAPER_ADAPTER_ENABLED + OPENCLAW_BROKER_SIDECAR_URL (fake/test-key sidecar)",
        "paper_adapter": "OPENCLAW_PAPER_ADAPTER_ENABLED",
        "live_adapter": "OPENCLAW_LIVE_ADAPTER_ENABLED",
        "canary_adapter": "OPENCLAW_CANARY_ADAPTER_ENABLED",
        "capital_binding": "OPENCLAW_CAPITAL_BINDING_ENABLED",
        "paper_runtime_binding_check": "OPENCLAW_RUNTIME_MANAGER_URL",
        "live_gate_harness": "OPENCLAW_LIVE_ADAPTER_ENABLED + OPENCLAW_LIVE_HUMAN_APPROVAL_TOKEN",
        "governed_search": "SearchGateway ACL/license/available_time filters",
        "assistant_openclaw": "OPENCLAW_GATEWAY_URL (auto-enabled when URL is set)",
    },
    "session_lifecycle": {
        "owner": "pantheon_adapter",
        "store": "durable",
        "state_machine": [
            "pending",
            "active",
            "cancel_requested",
            "canceled",
            "failed",
            "lost",
        ],
        "idempotency_header": "X-Idempotency-Key",
        "operator_header": "X-Operator-Id",
        "degraded_recovery": True,
    },
    "note": (
        "This adapter exposes the Pantheon boundary facade and typed OpenClaw upstream client. "
        "Session lifecycle is Pantheon-owned with durable state, idempotent create, operator "
        "ownership, and degraded recovery. Production/live broker execution remains deferred. "
        "Paper order submission is available only behind the explicit paper gate and requires "
        "a verified active paper RuntimeBinding. Canary broker execution remains disabled and "
        "is exposed only as an explicit fail-closed route. Search is constrained to governed "
        "evidence bundle and citation-pack responses."
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
        if payload.get("ready") is True:
            return True
        if payload.get("healthy") is True:
            return True
        if str(payload.get("status", "")).lower() == "ok":
            return True
    return False


def _probe_upstream() -> Dict[str, Any]:
    """Probe the upstream OpenClaw gateway health endpoint without raising."""
    if not OPENCLAW_GATEWAY_URL:
        return {"reachable": False, "reason": "OPENCLAW_GATEWAY_URL not configured"}
    last_error: Dict[str, Any] = {}
    for path in ("/readyz", "/healthz"):
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


def _assistant_service_auth_health_dep() -> Dict[str, Any]:
    configured = bool(_ASSISTANT_SERVICE_TOKEN)
    if _ASSISTANT_SERVICE_AUTH_REQUIRED and not configured:
        return {
            "status": "error",
            "configured": False,
            "required": True,
            "reason": "PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN is required but not configured.",
        }
    return {
        "status": "ok",
        "configured": configured,
        "required": _ASSISTANT_SERVICE_AUTH_REQUIRED,
    }


def _assistant_service_token_matches(presented_token: Optional[str]) -> bool:
    """Compare fixed-size token digests without exposing the configured secret."""

    if not _ASSISTANT_SERVICE_TOKEN:
        return False
    expected_digest = hashlib.sha256(_ASSISTANT_SERVICE_TOKEN.encode("utf-8")).digest()
    presented_digest = hashlib.sha256((presented_token or "").encode("utf-8")).digest()
    return hmac.compare_digest(presented_digest, expected_digest)


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

    def list_tools(self, *, agent_id: str) -> List[Dict[str, Any]]:
        payload = self._request("GET", f"/api/tools?agent_id={agent_id}")
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            tools = payload.get("tools", [])
            if isinstance(tools, list):
                return tools
        raise UpstreamClientError(
            status_code=502,
            error_code="UPSTREAM_SCHEMA_ERROR",
            message="OpenClaw tool list response did not match the expected schema.",
            retryable=False,
            error_layer="schema",
            details={"payload_type": type(payload).__name__},
        )

    def resolve_tools(self, *, agent_id: str, session_id: str) -> List[Dict[str, Any]]:
        payload = self._request("GET", f"/api/tools/resolve?agent_id={agent_id}&session_id={session_id}")
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            tools = payload.get("tools", [])
            if isinstance(tools, list):
                return tools
        raise UpstreamClientError(
            status_code=502,
            error_code="UPSTREAM_SCHEMA_ERROR",
            message="OpenClaw tool resolve response did not match the expected schema.",
            retryable=False,
            error_layer="schema",
            details={"payload_type": type(payload).__name__},
        )

    def invoke_tool(
        self,
        *,
        session_id: str,
        tool_name: str,
        args: Any,
        operator_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = self._request(
            "POST",
            "/api/tools/invoke",
            json_payload={
                "session_id": session_id,
                "tool_name": tool_name,
                "args": args,
                "operator_context": operator_context or {},
            },
            expected_statuses={200, 201, 202},
        )
        if not isinstance(payload, dict):
            raise UpstreamClientError(
                status_code=502,
                error_code="UPSTREAM_SCHEMA_ERROR",
                message="OpenClaw tool invoke response did not match the expected schema.",
                retryable=False,
                error_layer="schema",
                details={"payload_type": type(payload).__name__},
            )
        return payload

    def trigger_workflow(
        self,
        *,
        workflow_ref: str,
        context: Any,
        operator_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = self._request(
            "POST",
            "/api/workflows/trigger",
            json_payload={
                "workflow_ref": workflow_ref,
                "context": context,
                "operator_context": operator_context or {},
            },
            expected_statuses={200, 201, 202},
        )
        if not isinstance(payload, dict):
            raise UpstreamClientError(
                status_code=502,
                error_code="UPSTREAM_SCHEMA_ERROR",
                message="OpenClaw workflow trigger response did not match the expected schema.",
                retryable=False,
                error_layer="schema",
                details={"payload_type": type(payload).__name__},
            )
        return payload

    def get_job(self, job_id: str) -> Dict[str, Any]:
        payload = self._request("GET", f"/api/jobs/{job_id}")
        if not isinstance(payload, dict):
            raise UpstreamClientError(
                status_code=502,
                error_code="UPSTREAM_SCHEMA_ERROR",
                message="OpenClaw job status response did not match the expected schema.",
                retryable=False,
                error_layer="schema",
                details={"payload_type": type(payload).__name__},
            )
        return payload

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
        "Live broker and production adapter paths remain disabled."
    ),
)


@app.middleware("http")
async def require_assistant_service_token(request: Request, call_next):
    """Authenticate BFF-to-adapter assistant, agent, and cron control calls."""

    path = request.url.path.rstrip("/")
    is_assistant_api = path == _ASSISTANT_API_PREFIX or path.startswith(
        f"{_ASSISTANT_API_PREFIX}/"
    )
    is_cron_api = path == _CRON_API_PATH
    is_agents_api = path == _AGENTS_API_PREFIX or path.startswith(
        f"{_AGENTS_API_PREFIX}/"
    )
    if not is_assistant_api and not is_agents_api and not is_cron_api:
        return await call_next(request)

    if not _ASSISTANT_SERVICE_TOKEN:
        # Cron mutations and readback are a privileged control-plane surface,
        # so they always fail closed when the shared service token is absent.
        # The wider assistant boundary retains its existing opt-in requirement
        # for local development compatibility.
        if is_cron_api or is_agents_api or _ASSISTANT_SERVICE_AUTH_REQUIRED:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "service_auth_error",
                    "error_code": (
                        "CRON_SERVICE_AUTH_MISCONFIGURED"
                        if is_cron_api
                        else "AGENT_SERVICE_AUTH_MISCONFIGURED"
                        if is_agents_api
                        else "ASSISTANT_SERVICE_AUTH_MISCONFIGURED"
                    ),
                    "message": (
                        "Adapter service authentication is required, but the "
                        "adapter service token is not configured."
                    ),
                },
            )
        return await call_next(request)

    presented_token = request.headers.get("X-Pantheon-Service-Token")
    if not _assistant_service_token_matches(presented_token):
        return JSONResponse(
            status_code=401,
            content={
                "status": "service_auth_error",
                "error_code": (
                    "CRON_SERVICE_AUTH_DENIED"
                    if is_cron_api
                    else "AGENT_SERVICE_AUTH_DENIED"
                    if is_agents_api
                    else "ASSISTANT_SERVICE_AUTH_DENIED"
                ),
                "message": "A valid X-Pantheon-Service-Token header is required.",
            },
        )
    return await call_next(request)


register_fastapi_health_routes(
    app,
    "openclaw-gateway-adapter",
    dependencies=lambda: {
        "openclaw_gateway": _upstream_health_dep(),
        "assistant_service_auth": _assistant_service_auth_health_dep(),
    },
    details=lambda: {
        "gateway_url": OPENCLAW_GATEWAY_URL or "not_configured",
        "production_broker_enabled": _PRODUCTION_BROKER_ENABLED,
        "paper_adapter_enabled": _PAPER_ADAPTER_ENABLED,
        "live_adapter_enabled": _LIVE_ADAPTER_ENABLED,
        "canary_adapter_enabled": _CANARY_ADAPTER_ENABLED,
        "capital_binding_enabled": _CAPITAL_BINDING_ENABLED,
        "runtime_manager_url": _RUNTIME_MANAGER_URL or "not_configured",
        "assistant_credential_mounts": _ASSISTANT_MOUNTS.get_readiness_metadata(),
    },
)


@app.get("/health")
def health_compat() -> Dict[str, Any]:
    return health_payload(
        "openclaw-gateway-adapter",
        dependencies={
            "openclaw_gateway": _upstream_health_dep(),
            "assistant_service_auth": _assistant_service_auth_health_dep(),
        },
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
    payload["paper_adapter"] = "enabled" if _PAPER_ADAPTER_ENABLED else "deferred"
    payload["live_gate_harness"] = "enabled" if _LIVE_ADAPTER_ENABLED else "present_disabled"
    # Canary execution remains hard-denied even if a local env var is set; a future
    # activation task must introduce the actual adapter policy and evidence.
    payload["canary_adapter"] = "deferred"
    payload["paper_broker"] = _PAPER_BROKER.capability_snapshot()
    payload["live_gate"] = _LIVE_GATE.capability_snapshot()
    payload["assistant_credential_mounts"] = _ASSISTANT_MOUNTS.get_readiness_metadata()
    payload["assistant_openclaw"] = _OPENCLAW_AGENT_PROVIDER.readiness()["status"]
    try:
        upstream_capabilities = _client().get_capabilities()
        payload["activation_state"] = "upstream_client_ready"
        payload["upstream"] = {
            "status": "ok",
            "capabilities": upstream_capabilities,
        }
    except UpstreamClientError as exc:
        if exc.error_code == "UPSTREAM_NOT_FOUND":
            probe = _probe_upstream()
            if probe.get("reachable"):
                payload["activation_state"] = "upstream_client_ready"
                payload["upstream"] = {
                    "status": "ok",
                    "capabilities": {},
                    "capabilities_status": "not_exposed",
                    "capabilities_available": False,
                    "warning_code": "UPSTREAM_CAPABILITIES_NOT_EXPOSED",
                    "message": (
                        "OpenClaw upstream is reachable, but it does not expose "
                        "the optional /api/capabilities endpoint."
                    ),
                    "details": probe,
                }
                return payload
        payload["activation_state"] = "upstream_client_degraded"
        payload["upstream"] = {**exc.to_payload(), "status": "degraded"}
    return payload


# ---------------------------------------------------------------------------
# Assistant credentials
# ---------------------------------------------------------------------------


@app.get("/api/openclaw-adapter/assistant/credentials")
def get_assistant_credentials() -> Dict[str, Any]:
    """Return sanitized readiness metadata for assistant credential mounts."""
    return _ASSISTANT_MOUNTS.get_readiness_metadata()


# ---------------------------------------------------------------------------
# Assistant readiness
# ---------------------------------------------------------------------------

class PersonaOpinionInvocationAdmission(BaseModel):
    """Exact adapter-side proof for selecting a governed Persona agent."""

    model_config = {"extra": "forbid"}

    persona_id: str
    tenant_id: str
    persona_version: str
    agent_id: str
    workspace_ref: str
    capability_snapshot_id: str
    allowed_capabilities: List[str]
    environment_ceiling: str
    requested_environment: str
    execution_authority: str
    display_name: str
    mandate: Optional[str] = None
    archetype: Optional[str] = None
    strategy_family: Optional[str] = None
    traits: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_exact_admission(self) -> "PersonaOpinionInvocationAdmission":
        _validate_persona_opinion_admission(self.model_dump(mode="json"))
        return self


class AssistantProviderInvokeRequest(BaseModel):
    mode: str = "user"
    prompt: str
    agent_id: Optional[str] = None
    persona_admission: Optional[PersonaOpinionInvocationAdmission] = None
    context_pack: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    messages: Optional[List[Dict[str, Any]]] = None
    attachments: Optional[List[Dict[str, Any]]] = None

    @model_validator(mode="after")
    def validate_agent_selection(self) -> "AssistantProviderInvokeRequest":
        if self.agent_id is None and self.persona_admission is None:
            return self
        if self.agent_id is None or self.persona_admission is None:
            raise ValueError("agent_id and persona_admission must be supplied together")
        if self.agent_id != self.persona_admission.agent_id:
            raise ValueError("agent_id does not match the governed Persona admission")
        return self


class AssistantProviderStructuredInvokeRequest(BaseModel):
    """Restricted structured-data extraction request.

    Accepts only a caller-declared JSON-schema `parameters` body
    (`extraction_schema`) for the fixed, server-approved `emit_extraction`
    tool. A caller may never supply its own `tools`/`tool_choice` — that
    would let it smuggle in an arbitrary shell/tool definition.
    """

    mode: str = "user"
    prompt: str
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    messages: Optional[List[Dict[str, Any]]] = None
    extraction_schema: Dict[str, Any]
    tools: Optional[Any] = None
    tool_choice: Optional[Any] = None

    @model_validator(mode="after")
    def reject_caller_supplied_tools(self) -> "AssistantProviderStructuredInvokeRequest":
        if self.tools is not None or self.tool_choice is not None:
            raise ValueError(
                "caller-supplied tool definitions are not accepted; only extraction_schema is allowed"
            )
        return self


class AssistantSkillAuthorizeRequest(BaseModel):
    mode: Optional[str] = None
    operator_role: Optional[str] = None
    operatorRole: Optional[str] = None
    confirmed: Optional[Any] = None
    confirm_token: Optional[str] = None
    confirmToken: Optional[str] = None
    control_mode: Optional[Dict[str, Any]] = None
    controlMode: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    sessionId: Optional[str] = None
    request_type: Optional[str] = None
    requestType: Optional[str] = None
    audit_extra: Optional[Dict[str, Any]] = None
    auditExtra: Optional[Dict[str, Any]] = None


class AssistantProviderReauthRequest(BaseModel):
    provider: str = "codex"
    mode: Optional[str] = None
    operator_role: Optional[str] = None
    operatorRole: Optional[str] = None
    confirmed: Optional[Any] = None
    confirm_token: Optional[str] = None
    confirmToken: Optional[str] = None
    control_mode: Optional[Dict[str, Any]] = None
    controlMode: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    capture_timeout_seconds: Optional[int] = None
    captureTimeoutSeconds: Optional[int] = None
    poll_interval_seconds: Optional[int] = None
    pollIntervalSeconds: Optional[int] = None
    max_wait_seconds: Optional[int] = None
    maxWaitSeconds: Optional[int] = None

    def capture_timeout(self) -> Optional[int]:
        return self.capture_timeout_seconds if self.capture_timeout_seconds is not None else self.captureTimeoutSeconds

    def poll_interval(self) -> Optional[int]:
        return self.poll_interval_seconds if self.poll_interval_seconds is not None else self.pollIntervalSeconds

    def max_wait(self) -> Optional[int]:
        return self.max_wait_seconds if self.max_wait_seconds is not None else self.maxWaitSeconds


class AssistantProviderReauthCodeRequest(BaseModel):
    provider: str = "claude"
    code: Optional[str] = None
    authorization_code: Optional[str] = None
    authorizationCode: Optional[str] = None
    mode: Optional[str] = None
    operator_role: Optional[str] = None
    operatorRole: Optional[str] = None
    confirmed: Optional[Any] = None
    confirm_token: Optional[str] = None
    confirmToken: Optional[str] = None
    control_mode: Optional[Dict[str, Any]] = None
    controlMode: Optional[Dict[str, Any]] = None

    def auth_code(self) -> str:
        return str(self.code or self.authorization_code or self.authorizationCode or "").strip()


class AssistantProviderRegisterRequest(BaseModel):
    provider: str
    provider_name: Optional[str] = None
    providerName: Optional[str] = None
    runtime: Optional[str] = None
    model: Optional[str] = None
    auth_strategy: Optional[str] = None
    authStrategy: Optional[str] = None
    binary: Optional[str] = None
    binary_env: Optional[str] = None
    binaryEnv: Optional[str] = None
    note: Optional[str] = None
    mode: Optional[str] = None
    operator_role: Optional[str] = None
    operatorRole: Optional[str] = None
    confirmed: Optional[Any] = None
    confirm_token: Optional[str] = None
    confirmToken: Optional[str] = None
    control_mode: Optional[Dict[str, Any]] = None
    controlMode: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None

    def registry_payload(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "providerName": self.providerName or self.provider_name,
            "runtime": self.runtime,
            "model": self.model,
            "authStrategy": self.authStrategy or self.auth_strategy,
            "binary": self.binary,
            "binaryEnv": self.binaryEnv or self.binary_env,
            "note": self.note,
        }


def _assistant_provider_runtime_error_response(exc: AssistantProviderRuntimeError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "status": "provider_error",
            "error_code": exc.code,
            "message": str(exc),
            "stage": exc.stage,
            "retryable": False,
        },
    )


@app.get("/api/openclaw-adapter/assistant/readiness/{provider}")
def get_assistant_readiness(provider: str, auth_probe: bool = False) -> Dict[str, Any]:
    """Probes the provider binary and auth mount readiness."""
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider in {"codex", "codex_cli"}:
        return _with_reauth_support(_CODEX_PROVIDER.readiness(auth_probe=auth_probe), True)
    if normalized_provider in {"claude", "claude_cli"}:
        return _with_reauth_support(_CLAUDE_PROVIDER.readiness(auth_probe=auth_probe), True)
    if normalized_provider in {"openclaw", "openclaw_agent"}:
        return _with_reauth_support(_OPENCLAW_AGENT_PROVIDER.readiness(auth_probe=auth_probe), False)
    registered = _PROVIDER_REGISTRY.get_readiness(normalized_provider)
    if registered is not None:
        return registered
    return _ASSISTANT_RUNTIME.check_readiness(normalized_provider)


@app.get("/api/openclaw-adapter/assistant/providers")
def list_assistant_providers(auth_probe: bool = False) -> Dict[str, Any]:
    return {
        "status": "ok",
        "data": [
            _with_reauth_support(_OPENCLAW_AGENT_PROVIDER.readiness(auth_probe=auth_probe), False),
            _with_reauth_support(_CODEX_PROVIDER.readiness(auth_probe=auth_probe), True),
            _with_reauth_support(_CLAUDE_PROVIDER.readiness(auth_probe=auth_probe), True),
        ] + _PROVIDER_REGISTRY.list_readiness(),
    }


def _with_reauth_support(payload: Dict[str, Any], supported: bool) -> Dict[str, Any]:
    return {**payload, "reauth_supported": supported, "reauthSupported": supported}


@app.post("/api/openclaw-adapter/assistant/providers")
def register_assistant_provider(
    req: AssistantProviderRegisterRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_operator_role: Optional[str] = Header(default=None, alias="X-Operator-Role"),
    x_assistant_mode: Optional[str] = Header(default=None, alias="X-Assistant-Mode"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> JSONResponse:
    if not x_operator_id or not x_operator_id.strip():
        return JSONResponse(
            status_code=401,
            content={
                "status": "provider_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for provider registration.",
            },
        )
    try:
        _BRIDGE.authorize_assistant_skill(
            skill_id=ASSISTANT_PROVIDER_REGISTER_TOOL_NAME,
            operator_id=x_operator_id.strip(),
            mode=req.mode or x_assistant_mode or _mode_from_control_mode(req.control_mode or req.controlMode),
            operator_role=req.operator_role or req.operatorRole or x_operator_role,
            confirmed=req.confirmed is True,
            confirm_token=req.confirm_token or req.confirmToken,
            control_mode=req.control_mode or req.controlMode,
            trace_id=x_trace_id,
            request_type="assistant_provider_register",
            audit_extra={
                "provider": req.provider,
                "reason_hash": _audit_value_hash(req.reason),
            },
        )
    except BridgeError as exc:
        return _bridge_error_response(exc)
    try:
        result = _PROVIDER_REGISTRY.register(
            req.registry_payload(),
            operator_id=x_operator_id.strip(),
            trace_id=x_trace_id,
        )
    except AssistantProviderRegistryError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())
    return JSONResponse(status_code=201, content={"status": "ok", "data": result})


@app.post("/api/openclaw-adapter/assistant/skills/{skill_id}/authorize")
def authorize_assistant_skill(
    skill_id: str,
    req: AssistantSkillAuthorizeRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_operator_role: Optional[str] = Header(default=None, alias="X-Operator-Role"),
    x_assistant_mode: Optional[str] = Header(default=None, alias="X-Assistant-Mode"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> JSONResponse:
    if not x_operator_id or not x_operator_id.strip():
        return JSONResponse(
            status_code=401,
            content={
                "status": "skill_authorization_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for assistant skill authorization.",
            },
        )
    try:
        result = _BRIDGE.authorize_assistant_skill(
            skill_id=skill_id,
            operator_id=x_operator_id.strip(),
            mode=req.mode or x_assistant_mode or _mode_from_control_mode(req.control_mode or req.controlMode),
            operator_role=req.operator_role or req.operatorRole or x_operator_role,
            confirmed=req.confirmed is True,
            confirm_token=req.confirm_token or req.confirmToken,
            control_mode=req.control_mode or req.controlMode,
            trace_id=x_trace_id,
            session_id=req.session_id or req.sessionId,
            request_type=req.request_type or req.requestType or "assistant_skill_authorize",
            audit_extra=req.audit_extra or req.auditExtra,
        )
    except BridgeError as exc:
        return _bridge_error_response(exc)
    return JSONResponse(status_code=200, content={"status": "ok", "data": result})


@app.post("/api/openclaw-adapter/assistant/providers/{provider}/reauth")
def start_assistant_provider_reauth(
    provider: str,
    req: AssistantProviderReauthRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_operator_role: Optional[str] = Header(default=None, alias="X-Operator-Role"),
    x_assistant_mode: Optional[str] = Header(default=None, alias="X-Assistant-Mode"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> JSONResponse:
    if not x_operator_id or not x_operator_id.strip():
        return JSONResponse(
            status_code=401,
            content={
                "status": "provider_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for provider reauth.",
            },
        )
    normalized = str(provider or req.provider or "").strip().lower()
    if normalized not in {"codex", "codex_cli", "claude", "claude_cli"}:
        return JSONResponse(
            status_code=404,
            content={
                "status": "provider_error",
                "error_code": "PROVIDER_REAUTH_UNSUPPORTED",
                "message": "Provider reauth is currently supported only for codex and claude.",
            },
        )
    try:
        _BRIDGE.authorize_assistant_skill(
            skill_id=ASSISTANT_PROVIDER_REAUTH_TOOL_NAME,
            operator_id=x_operator_id.strip(),
            mode=req.mode or x_assistant_mode or _mode_from_control_mode(req.control_mode or req.controlMode) or "user",
            operator_role=req.operator_role or req.operatorRole or x_operator_role,
            confirmed=req.confirmed is True,
            confirm_token=req.confirm_token or req.confirmToken,
            control_mode=req.control_mode or req.controlMode,
            trace_id=x_trace_id,
            request_type="assistant_provider_reauth",
            audit_extra={
                "provider": normalized,
                "reason_hash": _audit_value_hash(req.reason),
            },
        )
    except BridgeError as exc:
        return _bridge_error_response(exc)
    try:
        if normalized in {"claude", "claude_cli"}:
            result = _CLAUDE_PROVIDER.start_device_reauth(
                operator_id=x_operator_id.strip(),
                trace_id=x_trace_id,
                reason=req.reason,
                capture_timeout_seconds=req.capture_timeout(),
                poll_interval_seconds=req.poll_interval(),
                max_wait_seconds=req.max_wait(),
            )
        else:
            result = _CODEX_PROVIDER.start_device_reauth(
                operator_id=x_operator_id.strip(),
                trace_id=x_trace_id,
                reason=req.reason,
                capture_timeout_seconds=req.capture_timeout(),
                poll_interval_seconds=req.poll_interval(),
                max_wait_seconds=req.max_wait(),
            )
    except CodexProviderError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())
    except ClaudeProviderError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())
    return JSONResponse(status_code=202, content={"status": "ok", "data": result})


@app.get("/api/openclaw-adapter/assistant/providers/{provider}/reauth/{session_id}")
def get_assistant_provider_reauth_status(
    provider: str,
    session_id: str,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
) -> JSONResponse:
    if not x_operator_id or not x_operator_id.strip():
        return JSONResponse(
            status_code=401,
            content={
                "status": "provider_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for provider reauth status.",
            },
        )
    normalized = str(provider or "").strip().lower()
    if normalized not in {"codex", "codex_cli", "claude", "claude_cli"}:
        return JSONResponse(
            status_code=404,
            content={
                "status": "provider_error",
                "error_code": "PROVIDER_REAUTH_UNSUPPORTED",
                "message": "Provider reauth is currently supported only for codex and claude.",
            },
        )
    try:
        if normalized in {"claude", "claude_cli"}:
            result = _CLAUDE_PROVIDER.reauth_status(session_id)
        else:
            result = _CODEX_PROVIDER.reauth_status(session_id)
    except CodexProviderError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())
    except ClaudeProviderError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())
    return JSONResponse(status_code=200, content={"status": "ok", "data": result})


@app.post("/api/openclaw-adapter/assistant/providers/{provider}/reauth/{session_id}/code")
def submit_assistant_provider_reauth_code(
    provider: str,
    session_id: str,
    req: AssistantProviderReauthCodeRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_operator_role: Optional[str] = Header(default=None, alias="X-Operator-Role"),
    x_assistant_mode: Optional[str] = Header(default=None, alias="X-Assistant-Mode"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> JSONResponse:
    if not x_operator_id or not x_operator_id.strip():
        return JSONResponse(
            status_code=401,
            content={
                "status": "provider_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for provider reauth code submission.",
            },
        )
    normalized = str(provider or req.provider or "").strip().lower()
    if normalized not in {"claude", "claude_cli"}:
        return JSONResponse(
            status_code=404,
            content={
                "status": "provider_error",
                "error_code": "PROVIDER_REAUTH_CODE_UNSUPPORTED",
                "message": "Provider reauth code submission is currently supported only for Claude.",
            },
        )
    code = req.auth_code()
    if not code:
        return JSONResponse(
            status_code=422,
            content={
                "status": "provider_error",
                "error_code": "CLAUDE_REAUTH_CODE_REQUIRED",
                "message": "Claude provider reauth requires an authorization code.",
            },
        )
    try:
        _BRIDGE.authorize_assistant_skill(
            skill_id=ASSISTANT_PROVIDER_REAUTH_TOOL_NAME,
            operator_id=x_operator_id.strip(),
            mode=req.mode or x_assistant_mode or _mode_from_control_mode(req.control_mode or req.controlMode) or "user",
            operator_role=req.operator_role or req.operatorRole or x_operator_role,
            confirmed=req.confirmed is True,
            confirm_token=req.confirm_token or req.confirmToken,
            control_mode=req.control_mode or req.controlMode,
            trace_id=x_trace_id,
            session_id=session_id,
            request_type="assistant_provider_reauth_code",
            audit_extra={
                "provider": normalized,
                "session_id_hash": _audit_value_hash(session_id),
                "code_hash": _audit_value_hash(code),
            },
        )
    except BridgeError as exc:
        return _bridge_error_response(exc)
    try:
        result = _CLAUDE_PROVIDER.submit_reauth_code(
            session_id,
            code=code,
            operator_id=x_operator_id.strip(),
        )
    except ClaudeProviderError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())
    return JSONResponse(status_code=200, content={"status": "ok", "data": result})


@app.post("/api/openclaw-adapter/assistant/providers/codex/invoke")
def invoke_codex_provider(
    req: AssistantProviderInvokeRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> JSONResponse:
    if not x_operator_id or not x_operator_id.strip():
        return JSONResponse(
            status_code=401,
            content={
                "status": "provider_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for Codex provider invocation.",
            },
        )
    metadata = dict(req.metadata or {})
    metadata["operator_id"] = x_operator_id.strip()
    if x_trace_id:
        metadata.setdefault("trace_id", x_trace_id)
    try:
        result = _invoke_codex_runtime(req, metadata=metadata)
    except CodexProviderError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())
    except AssistantProviderRuntimeError as exc:
        return _assistant_provider_runtime_error_response(exc)
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "data": {
                "provider": result.provider,
                "mode": result.mode,
                "status": result.status,
                "output": result.output,
                "redaction": result.redaction,
            },
        },
    )


def _invoke_codex_runtime(
    req: AssistantProviderInvokeRequest,
    *,
    metadata: Dict[str, Any],
    mode: Optional[str] = None,
) -> Any:
    return _CODEX_RUNTIME.invoke(
        ProviderInvocationRequest(
            provider=CODEX_PROVIDER_ID,
            mode=mode or req.mode,
            prompt=req.prompt,
            context_pack=req.context_pack or {},
            metadata=metadata,
            messages=req.messages,
            attachments=req.attachments,
        )
    )


def _delegated_codex_result_data(result: Any, *, route: str) -> Dict[str, Any]:
    raw_output = result.output
    output = dict(raw_output) if isinstance(raw_output, dict) else {"result": raw_output}
    runtime = str(output.get("runtime") or CODEX_PROVIDER_RUNTIME)
    delegation = {
        "from_provider": "openclaw",
        "from_route": route,
        "to_provider": str(result.provider or CODEX_PROVIDER_ID),
        "runtime": runtime,
    }
    output["delegated_from"] = "openclaw"
    output["delegated_from_route"] = route
    output["delegation"] = delegation
    return {
        "provider": str(result.provider or CODEX_PROVIDER_ID),
        "runtime": runtime,
        "mode": result.mode,
        "status": result.status,
        "output": output,
        "redaction": result.redaction,
        "delegated_from": "openclaw",
        "delegated_from_route": route,
        "delegation": delegation,
    }


def _delegated_codex_text(value: Any) -> str:
    if isinstance(value, str):
        clean = value.strip()
        if not clean:
            return ""
        for line in reversed(clean.splitlines()):
            try:
                parsed = json.loads(line)
            except (TypeError, ValueError):
                continue
            found = _delegated_codex_text(parsed)
            if found:
                return found
        return clean
    if isinstance(value, list):
        for item in reversed(value):
            found = _delegated_codex_text(item)
            if found:
                return found
        return ""
    if not isinstance(value, dict):
        return ""
    for key in ("answer", "final", "text", "content", "message"):
        found = _delegated_codex_text(value.get(key))
        if found:
            return found
    for key in ("item", "output", "json_events", "stdout"):
        found = _delegated_codex_text(value.get(key))
        if found:
            return found
    return ""


class _PersonaOpinionInvocationConflict(RuntimeError):
    pass


class _PersonaOpinionInvocationInDoubt(RuntimeError):
    pass


def _invoke_persona_opinion_idempotently(
    req: AssistantProviderInvokeRequest,
    *,
    idempotency_key: str,
    invoke_fn: Any,
) -> tuple[int, Dict[str, Any]]:
    """Fence and replay an exact governed Persona provider attempt.

    The running claim is committed before the upstream CLI is called.  If the
    adapter dies after OpenClaw has accepted the call but before the terminal
    response is committed, a restart returns ``in_doubt`` and never calls the
    provider again.  This deliberately prefers an honest missing opinion over
    duplicate provider work.
    """

    fingerprint = hashlib.sha256(
        json.dumps(req.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    _OPENCLAW_AGENT_IDEMPOTENCY_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(_OPENCLAW_AGENT_IDEMPOTENCY_DB), timeout=10.0) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS persona_opinion_invocation_replays (
                idempotency_key TEXT PRIMARY KEY,
                request_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL,
                http_status INTEGER,
                response_json TEXT,
                claimed_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        row = connection.execute(
            "SELECT request_fingerprint,state,http_status,response_json "
            "FROM persona_opinion_invocation_replays WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if row is not None:
            if row[0] != fingerprint:
                raise _PersonaOpinionInvocationConflict(
                    "Idempotency-Key was already used for different Persona invocation content"
                )
            if row[1] == "completed" and row[2] is not None and row[3]:
                return int(row[2]), json.loads(str(row[3]))
            raise _PersonaOpinionInvocationInDoubt(
                "The exact provider attempt was already claimed and has no terminal replay; duplicate invocation is fenced"
            )
        connection.execute(
            "INSERT INTO persona_opinion_invocation_replays "
            "(idempotency_key,request_fingerprint,state,claimed_at) VALUES (?,?, 'running', ?)",
            (idempotency_key, fingerprint, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()

    payload = invoke_fn()
    response_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(str(_OPENCLAW_AGENT_IDEMPOTENCY_DB), timeout=10.0) as connection:
        updated = connection.execute(
            "UPDATE persona_opinion_invocation_replays SET state='completed',http_status=200,"
            "response_json=?,completed_at=? WHERE idempotency_key=? AND request_fingerprint=? AND state='running'",
            (response_json, datetime.now(timezone.utc).isoformat(), idempotency_key, fingerprint),
        ).rowcount
        if updated != 1:
            raise _PersonaOpinionInvocationInDoubt(
                "Provider returned but its durable invocation claim could not be finalized"
            )
        connection.commit()
    return 200, payload


@app.post("/api/openclaw-adapter/assistant/providers/openclaw/invoke")
def invoke_openclaw_provider(
    req: AssistantProviderInvokeRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    """Invoke the OpenClaw gateway agent as the assistant provider.

    User mode routes through the upstream OpenClaw agent. Kernel debug/repair
    modes delegate to the adapter's Codex runtime so their read-only/task-
    worktree sandbox and repair metadata are enforced at the execution boundary.
    """
    if not x_operator_id or not x_operator_id.strip():
        return JSONResponse(
            status_code=401,
            content={
                "status": "provider_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for OpenClaw provider invocation.",
            },
        )
    metadata = dict(req.metadata or {})
    metadata["operator_id"] = x_operator_id.strip()
    if x_trace_id:
        metadata.setdefault("trace_id", x_trace_id)
    mode = str(req.mode or "user").strip().lower() or "user"
    if req.persona_admission is not None:
        if not str(idempotency_key or "").strip():
            return JSONResponse(
                status_code=422,
                content={
                    "status": "provider_error",
                    "error_code": "PERSONA_OPINION_IDEMPOTENCY_REQUIRED",
                    "message": "Idempotency-Key is required for governed Persona opinion invocation.",
                },
            )
        if mode != "user":
            return JSONResponse(
                status_code=422,
                content={
                    "status": "provider_error",
                    "error_code": "PERSONA_OPINION_MODE_DENIED",
                    "message": "Governed Persona opinion agents may only run in user mode.",
                },
            )
        try:
            _assert_persona_opinion_admitted(req.persona_admission)
            _assert_persona_opinion_runtime_policy(req.persona_admission.agent_id)
            _require_live_persona_opinion_agent(req.persona_admission.agent_id)
        except _PersonaOpinionAgentNotReady as exc:
            return _persona_opinion_agent_not_ready_response(exc)
        except GatewayOpenClawProviderError as exc:
            return _persona_opinion_gateway_error_response(exc)
        except (ValueError, RuntimeError) as exc:
            return JSONResponse(
                status_code=403,
                content={
                    "status": "provider_error",
                    "error_code": "PERSONA_OPINION_ADMISSION_DENIED",
                    "message": str(exc),
                },
            )
        requested_tools = metadata.get("allowed_tools")
        if requested_tools not in (None, []):
            return JSONResponse(
                status_code=422,
                content={
                    "status": "provider_error",
                    "error_code": "PERSONA_OPINION_TOOL_AUTHORITY_DENIED",
                    "message": "Governed Persona opinion invocation has an empty tool allowlist.",
                },
            )
        metadata.update({
            "allowed_tools": [],
            "execution_authority": "none",
            "order_submitted": False,
            "broker_called": False,
            "capital_changed": False,
            "runtime_bound": False,
            "lifecycle_promoted": False,
            "policy_mutated": False,
            "persona_memory_mutated": False,
        })
    if delegates_kernel_mode_to_codex(mode):
        try:
            result = _invoke_codex_runtime(req, metadata=metadata, mode=mode)
        except CodexProviderError as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.to_payload())
        except AssistantProviderRuntimeError as exc:
            return _assistant_provider_runtime_error_response(exc)
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "data": _delegated_codex_result_data(
                    result,
                    route="/api/openclaw-adapter/assistant/providers/openclaw/invoke",
                ),
            },
        )
    def _invoke_upstream() -> Dict[str, Any]:
        invoke_kwargs: Dict[str, Any] = {
            "mode": mode,
            "context_pack": req.context_pack or {},
            "metadata": metadata,
            "messages": req.messages,
            "attachments": req.attachments,
            "operator_id": x_operator_id.strip(),
            "trace_id": x_trace_id,
        }
        if req.agent_id is not None:
            invoke_kwargs["agent_id"] = req.agent_id
        if req.persona_admission is not None:
            # A deterministic session identity makes the upstream attempt
            # auditable.  The invocation ledger below fences a crash/in-doubt
            # attempt instead of invoking the provider a second time.
            invoke_kwargs["session_id"] = "pint-" + hashlib.sha256(
                str(idempotency_key).encode("utf-8")
            ).hexdigest()[:32]
        try:
            result = _OPENCLAW_AGENT_PROVIDER.invoke(req.prompt, **invoke_kwargs)
        except GatewayOpenClawProviderError as exc:
            return {
                "status": "ok",
                "data": {
                    "provider": "openclaw",
                    "mode": mode,
                    "status": "degraded",
                    "output": {
                        "json_events": [],
                        "reason": exc.error_code,
                        "message": exc.message,
                    },
                    "redaction": {"provider_invocation": {"redacted_fields": 0}},
                },
            }
        return {
            "status": "ok",
            "data": result.to_dict(),
        }

    if req.persona_admission is not None:
        try:
            status_code, payload = _invoke_persona_opinion_idempotently(
                req,
                idempotency_key=str(idempotency_key).strip(),
                invoke_fn=_invoke_upstream,
            )
        except _PersonaOpinionInvocationConflict as exc:
            return JSONResponse(status_code=409, content={
                "status": "provider_error",
                "error_code": "PERSONA_OPINION_IDEMPOTENCY_CONFLICT",
                "message": str(exc),
            })
        except _PersonaOpinionInvocationInDoubt as exc:
            return JSONResponse(status_code=409, content={
                "status": "provider_error",
                "error_code": "PERSONA_OPINION_INVOCATION_IN_DOUBT",
                "message": str(exc),
            })
        return JSONResponse(status_code=status_code, content=payload)
    return JSONResponse(status_code=200, content=_invoke_upstream())


@app.post("/api/openclaw-adapter/assistant/providers/openclaw/structured")
def invoke_openclaw_structured_provider(
    req: AssistantProviderStructuredInvokeRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> JSONResponse:
    """Restricted, server-approved structured-data extraction turn.

    Accepts only a caller-declared JSON-schema `parameters` body
    (`extraction_schema`) — never a full arbitrary tool/tool-list (rejected
    with 422 by the request model above). The model is pinned to the fixed
    `emit_extraction` tool via `invoke_structured`; this endpoint returns
    parsed structured data only and never executes a domain action.
    """
    if not x_operator_id or not x_operator_id.strip():
        return JSONResponse(
            status_code=401,
            content={
                "status": "provider_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for OpenClaw provider invocation.",
            },
        )
    mode = str(req.mode or "user").strip().lower() or "user"
    if delegates_kernel_mode_to_codex(mode):
        return JSONResponse(
            status_code=409,
            content={
                "status": "provider_error",
                "error_code": "OPENCLAW_KERNEL_DELEGATION_REQUIRED",
                "message": "OpenClaw kernel modes must be delegated to the adapter Codex runtime.",
            },
        )
    # This endpoint has no Persona-admission mechanism (unlike the ordinary
    # invoke endpoint's agent_id+persona_admission pairing enforced by
    # AssistantProviderInvokeRequest.validate_agent_selection). Structured
    # extraction is a restricted, data-only capability: it must never let a
    # caller route to an arbitrary/persona agent without going through that
    # admission/runtime-policy path, so only the default agent is permitted.
    if req.agent_id is not None and req.agent_id != OPENCLAW_DEFAULT_AGENT_ID:
        return JSONResponse(
            status_code=422,
            content={
                "status": "provider_error",
                "error_code": "OPENCLAW_STRUCTURED_AGENT_NOT_ALLOWED",
                "message": (
                    "Structured extraction is restricted to the default agent; "
                    "persona/tool routing requires the admitted invoke endpoint."
                ),
            },
        )
    invoke_kwargs: Dict[str, Any] = {
        "extraction_schema": req.extraction_schema,
        "mode": mode,
        "messages": req.messages,
        "operator_id": x_operator_id.strip(),
        "trace_id": x_trace_id,
    }
    if req.agent_id is not None:
        invoke_kwargs["agent_id"] = req.agent_id
    if req.session_id is not None:
        invoke_kwargs["session_id"] = req.session_id
    try:
        result = _OPENCLAW_AGENT_PROVIDER.invoke_structured(req.prompt, **invoke_kwargs)
    except GatewayOpenClawProviderError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())
    return JSONResponse(status_code=200, content={"status": "ok", "data": result.to_dict()})


@app.post("/api/openclaw-adapter/assistant/providers/openclaw/invoke/stream")
def invoke_openclaw_provider_stream(
    req: AssistantProviderInvokeRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> StreamingResponse:
    """Stream an OpenClaw agent turn as SSE via the gateway `POST /v1/responses`.

    Emits normalized events the BFF can relay verbatim to the console:
        data: {"type":"delta","text":"..."}
        data: {"type":"done","text":"...","elapsed_ms":N,"transport":"responses_http"}
        data: {"type":"error","error_code":"...","message":"..."}
        data: [DONE]
    User mode preserves the upstream agent session. Kernel debug/repair modes
    execute through the scoped Codex runtime and emit a terminal SSE event.
    """
    operator = (x_operator_id or "").strip()
    metadata = dict(req.metadata or {})
    mode = str(req.mode or "user").strip().lower() or "user"
    # Stable per-conversation session so multi-turn shares a warm agent
    # session. Derived from authenticated tenant/actor plus the caller's
    # conversation name so two callers cannot collide onto the same upstream
    # session by reusing the same caller-chosen session_user/session_id.
    session_user = derive_session_user(
        operator_id=operator, session_id=metadata.get("session_user"), metadata=metadata
    )

    def event_stream() -> Iterator[str]:
        if not operator:
            yield "data: " + json.dumps({
                "type": "error", "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for OpenClaw provider invocation.",
            }) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        metadata["operator_id"] = operator
        if x_trace_id:
            metadata.setdefault("trace_id", x_trace_id)
        if delegates_kernel_mode_to_codex(mode):
            try:
                result = _invoke_codex_runtime(req, metadata=metadata, mode=mode)
                data = _delegated_codex_result_data(
                    result,
                    route="/api/openclaw-adapter/assistant/providers/openclaw/invoke/stream",
                )
                output = data["output"]
                event = {
                    "type": "done",
                    "text": _delegated_codex_text(output),
                    "transport": "codex_runtime",
                    "provider": data["provider"],
                    "runtime": data["runtime"],
                    "mode": data["mode"],
                    "delegated_from": "openclaw",
                }
                for key in ("sandbox", "workspace_class"):
                    if output.get(key) is not None:
                        event[key] = output[key]
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            except CodexProviderError as exc:
                yield "data: " + json.dumps({
                    "type": "error",
                    "error_code": exc.code,
                    "message": str(exc),
                    "status_code": exc.status_code,
                }) + "\n\n"
            except AssistantProviderRuntimeError as exc:
                yield "data: " + json.dumps({
                    "type": "error",
                    "error_code": exc.code,
                    "message": str(exc),
                    "status_code": 400,
                }) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        try:
            for evt in _OPENCLAW_AGENT_PROVIDER.stream(
                req.prompt,
                mode=mode,
                operator_id=operator,
                trace_id=x_trace_id,
                session_user=session_user,
                agent_id=req.agent_id,
                messages=req.messages,
                attachments=req.attachments,
                context_pack=req.context_pack,
            ):
                yield "data: " + json.dumps(evt, ensure_ascii=False) + "\n\n"
        except Exception as exc:  # noqa: BLE001
            yield "data: " + json.dumps({
                "type": "error", "error_code": "ADAPTER_STREAM_ERROR",
                "message": str(exc)[:200],
            }) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


class GatewayCronCallRequest(BaseModel):
    method: str
    params: Optional[Dict[str, Any]] = None


_PERSONA_CRON_CATALOG: Dict[str, Dict[str, str]] = {
    "pantheon.ingest": {
        "schedule": "0 */6 * * *",
        "policy_id": "oc002.cron.ingest",
        "upstream_entrypoint": "research.ingest",
    },
    "pantheon.review": {
        "schedule": "15 7 * * 1-5",
        "policy_id": "oc002.cron.review",
        "upstream_entrypoint": "governance.review",
    },
    "pantheon.retrain": {
        "schedule": "0 2 * * 1-5",
        "policy_id": "oc002.cron.retrain",
        "upstream_entrypoint": "learning.retrain",
    },
    "pantheon.deploy": {
        "schedule": "*/15 * * * *",
        "policy_id": "oc002.cron.deploy",
        "upstream_entrypoint": "deployment.plan",
    },
    "pantheon.persona.first-evaluation": {
        "schedule": "*/15 * * * *",
        "policy_id": "oc002.cron.persona-first-evaluation",
        "upstream_entrypoint": "evaluation.persona.first",
    },
}


def _canonical_persona_cron_job_name(workflow_id: str, persona_id: str) -> str:
    workflow_slug = re.sub(r"[^a-z0-9]+", "-", workflow_id.lower()).strip("-")
    persona_slug = re.sub(r"[^a-z0-9]+", "-", persona_id.lower()).strip("-")
    prefix = f"pantheon-{workflow_slug}-"
    budget = 60 - len(prefix)
    if len(persona_slug) > budget:
        digest = hashlib.sha1(persona_slug.encode("utf-8")).hexdigest()[:8]
        keep = max(0, budget - len(digest) - 1)
        persona_slug = f"{persona_slug[:keep]}-{digest}"
    return f"{prefix}{persona_slug}"


def _pantheon_persona_cron_owner(job: Dict[str, Any]) -> Optional[tuple[str, str]]:
    """Return a validated Pantheon persona/workflow owner key for a cron row."""

    name = job.get("name")
    payload = job.get("payload")
    if not isinstance(name, str) or not name.startswith("pantheon-"):
        return None
    if not isinstance(payload, dict) or payload.get("kind") != "systemEvent":
        return None
    text = payload.get("text")
    if not isinstance(text, str):
        return None
    try:
        event = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(event, dict) or event.get("kind") != "pantheon.workflow.dispatch":
        return None
    persona_id = event.get("persona_id")
    workflow_id = event.get("workflow_id")
    if not isinstance(persona_id, str) or not persona_id.strip():
        return None
    if not isinstance(workflow_id, str) or not workflow_id.startswith("pantheon."):
        return None
    return persona_id, workflow_id


def _is_well_formed_persona_cron_job(job: Dict[str, Any]) -> bool:
    """Validate the complete adapter-owned Persona cron envelope."""

    owner = _pantheon_persona_cron_owner(job)
    payload = job.get("payload")
    schedule = job.get("schedule")
    delivery = job.get("delivery")
    if owner is None or not isinstance(payload, dict):
        return False
    try:
        event = json.loads(str(payload.get("text") or ""))
    except (TypeError, ValueError):
        return False
    if not isinstance(event, dict):
        return False
    persona_id, workflow_id = owner
    contract = _PERSONA_CRON_CATALOG.get(workflow_id)
    if contract is None:
        return False
    return bool(
        job.get("name")
        == _canonical_persona_cron_job_name(workflow_id, persona_id)
        and job.get("enabled") is True
        and job.get("deleteAfterRun") is False
        and isinstance(schedule, dict)
        and schedule.get("kind") == "cron"
        and schedule.get("expr") == contract["schedule"]
        and job.get("sessionTarget") == "main"
        and job.get("wakeMode") == "next-heartbeat"
        and (delivery is None or delivery == {"mode": "none"})
        and event.get("request_id")
        == f"persona-provisioning:{persona_id}:{workflow_id}"
        and event.get("policy_id") == contract["policy_id"]
        and event.get("upstream_entrypoint") == contract["upstream_entrypoint"]
    )


def _assert_persona_owned_cron_call(
    method: str,
    params: Optional[Dict[str, Any]],
) -> None:
    """Fence the proxy to the Persona namespace and its required RPC subset."""

    request_params = params if isinstance(params, dict) else {}
    if method == "cron.list":
        return
    if method == "cron.add":
        if not _is_well_formed_persona_cron_job(request_params):
            raise GatewayOpenClawProviderError(
                "cron.add requires a complete Pantheon persona-owned job envelope.",
                status_code=403,
                error_code="OPENCLAW_CRON_TARGET_FORBIDDEN",
            )
        return
    if method not in {"cron.update", "cron.remove"}:
        raise GatewayOpenClawProviderError(
            f"{method} is outside the Persona cron proxy contract.",
            status_code=403,
            error_code="OPENCLAW_CRON_TARGET_FORBIDDEN",
        )
    target_id = request_params.get("id")
    if not isinstance(target_id, str) or not target_id.strip():
        raise GatewayOpenClawProviderError(
            f"{method} requires a non-empty Pantheon persona cron job id.",
            status_code=403,
            error_code="OPENCLAW_CRON_TARGET_FORBIDDEN",
        )

    matches: list[Dict[str, Any]] = []
    offset = 0
    seen_offsets: set[int] = set()
    while True:
        if offset in seen_offsets:
            raise GatewayOpenClawProviderError(
                "cron.list returned a pagination cycle while authorizing a mutation.",
                status_code=503,
                error_code="OPENCLAW_CRON_AUTHORIZATION_UNAVAILABLE",
            )
        seen_offsets.add(offset)
        listing = _OPENCLAW_AGENT_PROVIDER.gateway_cron_call(
            "cron.list",
            {"limit": 200, "offset": offset},
        )
        jobs = listing.get("jobs") if isinstance(listing, dict) else None
        if not isinstance(jobs, list):
            raise GatewayOpenClawProviderError(
                "cron.list returned an invalid payload while authorizing a mutation.",
                status_code=503,
                error_code="OPENCLAW_CRON_AUTHORIZATION_UNAVAILABLE",
            )
        matches.extend(
            job
            for job in jobs
            if isinstance(job, dict) and job.get("id") == target_id
        )
        if not listing.get("hasMore"):
            break
        next_offset = listing.get("nextOffset", offset + len(jobs))
        if not isinstance(next_offset, int) or next_offset < 0:
            raise GatewayOpenClawProviderError(
                "cron.list returned an invalid offset while authorizing a mutation.",
                status_code=503,
                error_code="OPENCLAW_CRON_AUTHORIZATION_UNAVAILABLE",
            )
        offset = next_offset

    if len(matches) != 1:
        raise GatewayOpenClawProviderError(
            f"{method} target is not one authoritative Pantheon persona cron row.",
            status_code=403,
            error_code="OPENCLAW_CRON_TARGET_FORBIDDEN",
        )
    current_owner = _pantheon_persona_cron_owner(matches[0])
    reserved_name = str(matches[0].get("name") or "").startswith("pantheon-")
    if current_owner is None and not (method == "cron.remove" and reserved_name):
        raise GatewayOpenClawProviderError(
            f"{method} target is outside the Pantheon persona cron namespace.",
            status_code=403,
            error_code="OPENCLAW_CRON_TARGET_FORBIDDEN",
        )

    if method == "cron.update":
        patch = request_params.get("patch")
        if not isinstance(patch, dict):
            raise GatewayOpenClawProviderError(
                "cron.update requires a complete Pantheon persona-owned patch.",
                status_code=403,
                error_code="OPENCLAW_CRON_TARGET_FORBIDDEN",
            )
        patched_owner = _pantheon_persona_cron_owner(patch)
        if (
            current_owner is None
            or patched_owner != current_owner
            or not _is_well_formed_persona_cron_job(patch)
        ):
            raise GatewayOpenClawProviderError(
                "cron.update cannot change or erase the Pantheon persona cron owner key.",
                status_code=403,
                error_code="OPENCLAW_CRON_TARGET_FORBIDDEN",
            )


@app.post("/api/openclaw-adapter/gateway/cron")
def gateway_cron_call(req: GatewayCronCallRequest) -> JSONResponse:
    """Proxy a whitelisted `cron.*` gateway RPC through the adapter.

    The BFF persona OODA-loop cron registrar cannot reach the gateway directly
    (no docker socket, no openclaw binary in the BFF image). The adapter has the
    openclaw CLI + ws:// reachability, so it registers/inspects the recurring
    persona OODA cron jobs on the BFF's behalf. Only `cron.*` methods are allowed.
    """
    try:
        _assert_persona_owned_cron_call(req.method, req.params)
        result = _OPENCLAW_AGENT_PROVIDER.gateway_cron_call(req.method, req.params)
        if req.method == "cron.list" and isinstance(result, dict):
            jobs = result.get("jobs")
            if isinstance(jobs, list):
                result = {
                    **result,
                    "jobs": [
                        job
                        for job in jobs
                        if isinstance(job, dict)
                        and isinstance(job.get("name"), str)
                        and job["name"].startswith("pantheon-")
                    ],
                }
    except GatewayOpenClawProviderError as exc:
        return JSONResponse(
            status_code=exc.status_code if exc.status_code in (403, 503) else 200,
            content={"status": "error", "error_code": exc.error_code, "message": exc.message},
        )
    return JSONResponse(status_code=200, content={"status": "ok", "data": result})


class ClaudeInvokeRequest(BaseModel):
    prompt: str
    mode: str = "user"
    context_pack: Optional[Dict[str, Any]] = None


@app.post("/api/openclaw-adapter/assistant/claude/invoke")
def invoke_claude_provider(
    req: ClaudeInvokeRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
) -> JSONResponse:
    """Invoke Claude Code CLI through the mounted service-user CLAUDE_CONFIG_DIR.

    Returns a structured result.  Degraded outcomes (missing binary, missing
    auth, timeout, malformed output) produce HTTP 200 with status=degraded so
    callers can apply deterministic fallback rather than treating them as
    transport errors.

    Operators must supply X-Operator-Id; unauthenticated callers are rejected.
    """
    if not x_operator_id:
        return JSONResponse(
            status_code=401,
            content={
                "status": "provider_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for Claude provider invocation.",
            },
        )
    result: ClaudeProviderResult = _CLAUDE_PROVIDER.invoke(
        req.prompt,
        mode=req.mode,
        context_pack=req.context_pack,
    )
    return JSONResponse(status_code=200, content=result.to_dict())


# ---------------------------------------------------------------------------
# Governed Persona agent reconciliation
# ---------------------------------------------------------------------------


_OPENCLAW_AGENT_STATE_DIR = Path(
    os.getenv("PANTHEON_OPENCLAW_GATEWAY_STATE_DIR", "/home/node/.openclaw")
).resolve()
_OPENCLAW_AGENT_WORKSPACE_ROOT = (_OPENCLAW_AGENT_STATE_DIR / "workspaces").resolve()
_OPENCLAW_AGENT_IDEMPOTENCY_DB = Path(
    os.getenv(
        "PANTHEON_OPENCLAW_AGENT_IDEMPOTENCY_DB",
        "/root/.openclaw/pantheon-agent-ensure.sqlite3",
    )
).resolve()
_FORBIDDEN_SERVANT_CAPABILITIES = frozenset(
    {"runtimebinding", "brokerorder", "capitalbinding"}
)
_ALLOWED_SERVANT_CAPABILITIES = frozenset({"personaopinion"})
_PERSONA_OPINION_AGENT_PREFIX = "persona-opinion-"
_PERSONA_OPINION_ENVIRONMENTS = frozenset({"analysis", "research", "shadow", "paper"})


class OpenClawAgentCapabilitySnapshot(BaseModel):
    model_config = {"extra": "forbid"}

    allowed_capabilities: List[str]
    persona_class: str


class OpenClawAgentEnsureRequest(BaseModel):
    model_config = {"extra": "forbid"}

    persona_registry_ref: str
    workspace_ref: str
    capability_snapshot: OpenClawAgentCapabilitySnapshot


class OpenClawPersonaOpinionEnsureRequest(BaseModel):
    """Frozen Persona/version/capability admission for an advice-only agent."""

    model_config = {"extra": "forbid"}

    persona_id: str
    tenant_id: str
    persona_version: str
    agent_id: str
    workspace_ref: str
    capability_snapshot_id: str
    allowed_capabilities: List[str]
    environment_ceiling: str
    requested_environment: str
    execution_authority: str
    display_name: str
    mandate: Optional[str] = None
    archetype: Optional[str] = None
    strategy_family: Optional[str] = None
    traits: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_exact_admission(self) -> "OpenClawPersonaOpinionEnsureRequest":
        _validate_persona_opinion_admission(self.model_dump(mode="json"))
        return self


def _normalized_capability(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _persona_opinion_agent_id(
    tenant_id: str,
    persona_id: str,
    persona_version: str,
    capability_snapshot_id: str,
    requested_environment: str,
) -> str:
    digest = hashlib.sha256(
        f"{tenant_id}\0{persona_id}\0{persona_version}\0{capability_snapshot_id}\0{requested_environment}".encode("utf-8")
    ).hexdigest()[:24]
    return f"{_PERSONA_OPINION_AGENT_PREFIX}{digest}"


def _validate_persona_opinion_admission(admission: Dict[str, Any]) -> None:
    tenant_id = str(admission.get("tenant_id") or "").strip()
    persona_id = str(admission.get("persona_id") or "").strip()
    persona_version = str(admission.get("persona_version") or "").strip()
    snapshot_id = str(admission.get("capability_snapshot_id") or "").strip()
    agent_id = str(admission.get("agent_id") or "").strip()
    requested_environment = str(admission.get("requested_environment") or "").strip()
    if not tenant_id or not persona_id or not persona_version or not snapshot_id:
        raise ValueError("Tenant, Persona id, version, and capability snapshot are required")
    expected_agent_id = _persona_opinion_agent_id(
        tenant_id, persona_id, persona_version, snapshot_id, requested_environment
    )
    if agent_id != expected_agent_id:
        raise ValueError("agent_id does not match the frozen Persona admission")
    if admission.get("allowed_capabilities") != ["persona_opinion"]:
        raise ValueError("Persona opinion admission must grant exactly persona_opinion")
    if str(admission.get("execution_authority") or "") != "none":
        raise ValueError("Persona opinion admission must have execution_authority=none")
    ceiling = str(admission.get("environment_ceiling") or "")
    if ceiling not in _PERSONA_OPINION_ENVIRONMENTS:
        raise ValueError("Persona opinion admission exceeds the advisory environment ceiling")
    environment_order = ("analysis", "research", "shadow", "paper")
    if requested_environment not in environment_order or environment_order.index(requested_environment) > environment_order.index(ceiling):
        raise ValueError("Requested environment exceeds the Persona advisory ceiling")
    expected_workspace = (_OPENCLAW_AGENT_WORKSPACE_ROOT / agent_id).resolve()
    workspace = Path(str(admission.get("workspace_ref") or "").strip()).resolve()
    if workspace != expected_workspace or _OPENCLAW_AGENT_WORKSPACE_ROOT not in workspace.parents:
        raise ValueError("workspace_ref does not match the frozen Persona agent")


def _servant_agent_request(req: OpenClawAgentEnsureRequest) -> Dict[str, Any]:
    registry_ref = str(req.persona_registry_ref or "").strip()
    if not registry_ref.startswith("persona:"):
        raise ValueError("persona_registry_ref must use the persona:<id> form")
    persona_id = registry_ref.removeprefix("persona:").strip()
    if not re.fullmatch(r"agora-servant-[0-9a-f]{20}", persona_id):
        raise ValueError("persona_registry_ref is not a canonical Agora servant id")
    if req.capability_snapshot.persona_class != "agora_servant":
        raise ValueError("agent ensure is restricted to agora_servant personas")

    requested_capabilities = req.capability_snapshot.allowed_capabilities
    capabilities = {
        _normalized_capability(capability) for capability in requested_capabilities
    }
    if capabilities & _FORBIDDEN_SERVANT_CAPABILITIES:
        raise ValueError(
            "servant capability snapshot cannot grant runtime, broker, or capital binding"
        )
    if (
        requested_capabilities != ["persona_opinion"]
        or capabilities != _ALLOWED_SERVANT_CAPABILITIES
    ):
        raise ValueError("servant capability snapshot must be exactly persona_opinion")

    expected_workspace = (_OPENCLAW_AGENT_WORKSPACE_ROOT / persona_id).resolve()
    workspace = Path(str(req.workspace_ref or "").strip()).resolve()
    if workspace != expected_workspace or _OPENCLAW_AGENT_WORKSPACE_ROOT not in workspace.parents:
        raise ValueError("workspace_ref is outside the governed Persona workspace root")

    return {
        "persona_id": persona_id,
        "name": "Agora Servant",
        "archetype": "agora_servant",
        "mandate": "user_private_agora_servant",
        "strategy_family": "agora_servant",
        "lifecycle_state": "draft",
        "risk_level": "low",
        "traits": {
            "decision_style": "operator-guided",
            "hard_rules": "no runtime binding, broker order, or capital binding authority",
            "persona_voice": "concise, evidence-grounded",
        },
        "workspace_ref": str(workspace),
        "metadata": {
            "persona_class": "agora_servant",
            "execution_authority": "none",
            "allowed_capabilities": ["persona_opinion"],
        },
    }


def _gateway_state_agent_runner(args: List[str]) -> "subprocess.CompletedProcess[str]":
    env = dict(os.environ)
    env["OPENCLAW_STATE_DIR"] = str(_OPENCLAW_AGENT_STATE_DIR)
    env["HOME"] = str(_OPENCLAW_AGENT_STATE_DIR.parent)
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        user=1000,
        group=1000,
        check=False,
    )


_PERSONA_OPINION_RUNTIME_POLICY = {
    "tools": {"allow": [], "deny": ["*"]},
    "skills": [],
    "memorySearch": {
        "enabled": False,
        "sources": [],
        "experimental": {"sessionMemory": False},
        "sync": {"onSessionStart": False, "onSearch": False, "watch": False},
    },
    "contextInjection": "never",
}


def _bounded_float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


_PERSONA_OPINION_AGENT_READY_TIMEOUT_SECONDS = _bounded_float_env(
    "PANTHEON_PERSONA_OPINION_AGENT_READY_TIMEOUT_SECONDS",
    15.0,
    minimum=0.1,
    maximum=60.0,
)
_PERSONA_OPINION_AGENT_READY_POLL_SECONDS = _bounded_float_env(
    "PANTHEON_PERSONA_OPINION_AGENT_READY_POLL_SECONDS",
    0.25,
    minimum=0.01,
    maximum=1.0,
)


class _PersonaOpinionAgentNotReady(RuntimeError):
    pass


def _live_persona_opinion_agent(
    agent_id: str,
    *,
    timeout_seconds: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    return next(
        (
            item
            for item in _OPENCLAW_AGENT_PROVIDER.gateway_agents_list(
                timeout_seconds=timeout_seconds,
            )
            if str(item.get("id") or "") == agent_id
        ),
        None,
    )


def _require_live_persona_opinion_agent(
    agent_id: str,
    *,
    timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    effective_timeout = (
        _PERSONA_OPINION_AGENT_READY_TIMEOUT_SECONDS
        if timeout_seconds is None
        else timeout_seconds
    )
    try:
        agent = _live_persona_opinion_agent(
            agent_id,
            timeout_seconds=effective_timeout,
        )
    except GatewayOpenClawProviderError as exc:
        if exc.error_code != "OPENCLAW_GATEWAY_TIMEOUT":
            raise
        raise _PersonaOpinionAgentNotReady(
            f"Governed Persona agent {agent_id} live visibility probe exhausted its bounded budget."
        ) from exc
    if agent is None:
        raise _PersonaOpinionAgentNotReady(
            f"Governed Persona agent {agent_id} is not visible in the live OpenClaw gateway registry."
        )
    return agent


def _wait_for_live_persona_opinion_agent(
    agent_id: str,
    *,
    timeout_seconds: Optional[float] = None,
    poll_seconds: Optional[float] = None,
    clock: Any = time.monotonic,
    sleeper: Any = time.sleep,
) -> Dict[str, Any]:
    """Bound config reconciliation on the gateway's live agent registry."""

    timeout = (
        _PERSONA_OPINION_AGENT_READY_TIMEOUT_SECONDS
        if timeout_seconds is None
        else max(0.0, float(timeout_seconds))
    )
    poll = (
        _PERSONA_OPINION_AGENT_READY_POLL_SECONDS
        if poll_seconds is None
        else max(0.001, float(poll_seconds))
    )
    deadline = clock() + timeout
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise _PersonaOpinionAgentNotReady(
                f"Governed Persona agent {agent_id} was not visible in the live OpenClaw gateway "
                f"registry within {timeout:g} seconds."
            )
        try:
            agent = _live_persona_opinion_agent(
                agent_id,
                timeout_seconds=remaining,
            )
        except GatewayOpenClawProviderError as exc:
            if exc.error_code != "OPENCLAW_GATEWAY_TIMEOUT":
                raise
            raise _PersonaOpinionAgentNotReady(
                f"Governed Persona agent {agent_id} live visibility probe exhausted its bounded "
                f"{timeout:g}-second budget."
            ) from exc
        if agent is not None:
            return agent
        remaining = deadline - clock()
        if remaining <= 0:
            raise _PersonaOpinionAgentNotReady(
                f"Governed Persona agent {agent_id} was not visible in the live OpenClaw gateway "
                f"registry within {timeout:g} seconds."
            )
        sleeper(min(poll, remaining))


def _persona_opinion_agent_not_ready_response(exc: _PersonaOpinionAgentNotReady) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "status": "upstream_unavailable",
            "error_code": "PERSONA_OPINION_AGENT_NOT_READY",
            "message": str(exc),
            "retryable": True,
        },
    )


def _persona_opinion_gateway_error_response(exc: GatewayOpenClawProviderError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={**exc.to_payload(), "retryable": exc.status_code >= 500},
    )


def _persona_opinion_runtime_agent(agent_id: str) -> Dict[str, Any]:
    proc = _gateway_state_agent_runner(["openclaw", "config", "get", "agents.list", "--json"])
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "OpenClaw agent config read failed")[:300])
    try:
        agents = json.loads(proc.stdout or "[]")
    except (TypeError, ValueError) as exc:
        raise RuntimeError("OpenClaw agent config is not valid JSON") from exc
    match = next(
        (item for item in agents if isinstance(item, dict) and str(item.get("id") or "") == agent_id),
        None,
    )
    if match is None:
        raise RuntimeError("Governed Persona agent is absent from OpenClaw config")
    return match


def _assert_persona_opinion_runtime_policy(agent_id: str) -> Dict[str, Any]:
    agent = _persona_opinion_runtime_agent(agent_id)
    for key, expected in _PERSONA_OPINION_RUNTIME_POLICY.items():
        if agent.get(key) != expected:
            raise ValueError(f"Persona opinion OpenClaw runtime policy mismatch: {key}")
    return agent


def _apply_persona_opinion_runtime_policy(agent_id: str) -> Dict[str, Any]:
    proc = _gateway_state_agent_runner(["openclaw", "config", "get", "agents.list", "--json"])
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "OpenClaw agent config read failed")[:300])
    try:
        agents = json.loads(proc.stdout or "[]")
    except (TypeError, ValueError) as exc:
        raise RuntimeError("OpenClaw agent config is not valid JSON") from exc
    found = False
    for item in agents:
        if isinstance(item, dict) and str(item.get("id") or "") == agent_id:
            item.update(json.loads(json.dumps(_PERSONA_OPINION_RUNTIME_POLICY)))
            found = True
            break
    if not found:
        raise RuntimeError("Governed Persona agent is absent after reconciliation")
    update = _gateway_state_agent_runner([
        "openclaw", "config", "set", "agents.list",
        json.dumps(agents, sort_keys=True, separators=(",", ":")),
        "--strict-json", "--replace",
    ])
    if update.returncode != 0:
        raise RuntimeError((update.stderr or update.stdout or "OpenClaw runtime policy write failed")[:300])
    return _assert_persona_opinion_runtime_policy(agent_id)


def _gateway_state_soul_writer(workspace: str, soul: str) -> None:
    workspace_path = Path(str(workspace or "").strip()).resolve()
    if (
        workspace_path.parent != _OPENCLAW_AGENT_WORKSPACE_ROOT
        or _OPENCLAW_AGENT_WORKSPACE_ROOT not in workspace_path.parents
    ):
        raise ValueError("SOUL workspace is outside the governed Persona workspace root")
    writer = (
        "import pathlib,sys; "
        "workspace=pathlib.Path(sys.argv[1]); "
        "workspace.mkdir(parents=True,exist_ok=True); "
        "(workspace/'SOUL.md').write_text(sys.stdin.read(),encoding='utf-8')"
    )
    proc = subprocess.run(
        ["python3", "-c", writer, str(workspace_path)],
        input=soul,
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "HOME": str(_OPENCLAW_AGENT_STATE_DIR.parent)},
        user=1000,
        group=1000,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "SOUL write failed")[:300])


def _sync_servant_agent(req: OpenClawAgentEnsureRequest) -> Dict[str, Any]:
    persona = _servant_agent_request(req)
    return ensure_agora_servant_agent(
        persona,
        runner=_gateway_state_agent_runner,
        soul_writer=_gateway_state_soul_writer,
    )


def _persona_opinion_soul(req: OpenClawPersonaOpinionEnsureRequest) -> str:
    """Render the immutable advice-only identity for one frozen Persona agent."""

    traits = json.dumps(req.traits, sort_keys=True, ensure_ascii=False)
    return f"""# Governed Persona opinion agent

You are {req.display_name} (`{req.persona_id}`), frozen at Persona version
`{req.persona_version}` and capability snapshot `{req.capability_snapshot_id}`.

Mandate: {req.mandate or '(not specified)'}
Strategy family: {req.strategy_family or '(not specified)'}
Traits: {traits}

Your only capability is `persona_opinion`. Respond to the supplied immutable
context with the exact JSON schema requested by Pantheon. You have
`execution_authority=none`: do not call tools, submit orders, contact brokers,
change capital, bind runtime, promote lifecycle, mutate policy, or read/write
Persona memory. Never claim that any recommendation was executed.
"""


def _sync_persona_opinion_agent(req: OpenClawPersonaOpinionEnsureRequest) -> Dict[str, Any]:
    persona = {
        "persona_id": req.agent_id,
        "name": req.display_name,
        "mandate": req.mandate or "governed_persona_opinion",
        "strategy_family": req.strategy_family or "governed_persona_opinion",
        "lifecycle_state": "active",
        "workspace_ref": req.workspace_ref,
        "traits": req.traits,
        "metadata": {
            "execution_authority": "none",
            "interaction_capabilities": ["persona_opinion"],
            "source_persona_id": req.persona_id,
            "source_persona_version": req.persona_version,
            "capability_snapshot_id": req.capability_snapshot_id,
        },
    }

    def constrained_soul_writer(workspace: str, _generic_soul: str) -> None:
        _gateway_state_soul_writer(workspace, _persona_opinion_soul(req))

    agent = ensure_agora_servant_agent(
        persona,
        runner=_gateway_state_agent_runner,
        soul_writer=constrained_soul_writer,
    )
    _apply_persona_opinion_runtime_policy(req.agent_id)
    agent["runtime_policy"] = json.loads(json.dumps(_PERSONA_OPINION_RUNTIME_POLICY))
    return agent


class _AgentEnsureIdempotencyConflict(RuntimeError):
    pass


def _agent_ensure_fingerprint(req: BaseModel) -> str:
    canonical = json.dumps(
        req.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ensure_agent_idempotently(
    req: BaseModel,
    *,
    idempotency_key: str,
    request_id: str,
    sync_fn: Optional[Any] = None,
    preflight_fn: Optional[Any] = None,
    commit_fn: Optional[Any] = None,
    postcondition_fn: Optional[Any] = None,
) -> tuple[int, Dict[str, Any]]:
    """Serialize reconciliation and durably replay an exact request.

    The write transaction spans the CLI reconcile. SQLite's writer lock keeps
    list/add/set-identity sequences from racing across request threads or
    adapter workers. A crash rolls back the replay row; the underlying
    reconcile safely observes any agent created before that crash.
    """

    fingerprint = _agent_ensure_fingerprint(req)
    _OPENCLAW_AGENT_IDEMPOTENCY_DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(_OPENCLAW_AGENT_IDEMPOTENCY_DB),
        timeout=130.0,
        isolation_level=None,
    )
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_ensure_replays (
                idempotency_key TEXT PRIMARY KEY,
                request_fingerprint TEXT NOT NULL,
                http_status INTEGER NOT NULL,
                response_json TEXT NOT NULL
            )
            """
        )
        connection.execute("BEGIN IMMEDIATE")
        if preflight_fn is not None:
            preflight_fn(connection, req)
        replay = connection.execute(
            """
            SELECT request_fingerprint, http_status, response_json
            FROM agent_ensure_replays
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if replay is not None:
            replay_fingerprint, replay_status, replay_json = replay
            if replay_fingerprint != fingerprint:
                raise _AgentEnsureIdempotencyConflict(
                    "Idempotency-Key was already used with a different agent request"
                )
            payload = json.loads(str(replay_json))
            if postcondition_fn is not None:
                postcondition_fn(req)
            connection.commit()
            return int(replay_status), payload

        agent = (sync_fn or _sync_servant_agent)(req)
        if postcondition_fn is not None:
            postcondition_fn(req)
        status_code = 201 if agent.get("status") == "created" else 200
        payload = {
            "status": "ok",
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "agent": agent,
        }
        response_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        connection.execute(
            """
            INSERT INTO agent_ensure_replays (
                idempotency_key,
                request_fingerprint,
                http_status,
                response_json
            ) VALUES (?, ?, ?, ?)
            """,
            (idempotency_key, fingerprint, status_code, response_json),
        )
        if commit_fn is not None:
            commit_fn(connection, req)
        connection.commit()
        return status_code, payload
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def _persona_admission_fingerprint(admission: Dict[str, Any]) -> str:
    selected = {
        key: admission.get(key)
        for key in (
            "persona_id",
            "tenant_id",
            "persona_version",
            "agent_id",
            "workspace_ref",
            "capability_snapshot_id",
            "allowed_capabilities",
            "environment_ceiling",
            "requested_environment",
            "execution_authority",
            "display_name",
            "mandate",
            "archetype",
            "strategy_family",
            "traits",
        )
    }
    return hashlib.sha256(
        json.dumps(selected, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _preflight_persona_opinion_admission(connection: sqlite3.Connection, req: OpenClawPersonaOpinionEnsureRequest) -> None:
    fingerprint = _persona_admission_fingerprint(req.model_dump(mode="json"))
    connection.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_opinion_admissions (
                agent_id TEXT PRIMARY KEY,
                admission_fingerprint TEXT NOT NULL
            )
            """
        )
    existing = connection.execute(
            "SELECT admission_fingerprint FROM persona_opinion_admissions WHERE agent_id = ?",
            (req.agent_id,),
        ).fetchone()
    if existing is not None and str(existing[0]) != fingerprint:
        raise _AgentEnsureIdempotencyConflict(
            "Persona opinion agent was already admitted with different frozen claims"
        )


def _commit_persona_opinion_admission(connection: sqlite3.Connection, req: OpenClawPersonaOpinionEnsureRequest) -> None:
    fingerprint = _persona_admission_fingerprint(req.model_dump(mode="json"))
    connection.execute(
            """
            INSERT INTO persona_opinion_admissions (agent_id, admission_fingerprint)
            VALUES (?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET admission_fingerprint = excluded.admission_fingerprint
            """,
        (req.agent_id, fingerprint),
    )


def _assert_persona_opinion_admitted(admission: PersonaOpinionInvocationAdmission) -> None:
    fingerprint = _persona_admission_fingerprint(admission.model_dump(mode="json"))
    try:
        with sqlite3.connect(str(_OPENCLAW_AGENT_IDEMPOTENCY_DB), timeout=2.0) as connection:
            row = connection.execute(
                "SELECT admission_fingerprint FROM persona_opinion_admissions WHERE agent_id = ?",
                (admission.agent_id,),
            ).fetchone()
    except (sqlite3.Error, OSError) as exc:
        raise ValueError("Persona opinion admission store is unavailable") from exc
    if row is None or str(row[0]) != fingerprint:
        raise ValueError("Persona opinion agent has not passed exact governed admission")


@app.post("/api/openclaw-adapter/agents/ensure")
def ensure_servant_agent(
    req: OpenClawAgentEnsureRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
) -> JSONResponse:
    if not str(idempotency_key or "").strip() or not str(x_request_id or "").strip():
        return JSONResponse(
            status_code=400,
            content={
                "status": "invalid_request",
                "error_code": "AGENT_SYNC_HEADERS_REQUIRED",
                "message": "Idempotency-Key and X-Request-Id are required.",
            },
        )
    try:
        status_code, payload = _ensure_agent_idempotently(
            req,
            idempotency_key=str(idempotency_key).strip(),
            request_id=str(x_request_id).strip(),
        )
    except _AgentEnsureIdempotencyConflict as exc:
        return JSONResponse(
            status_code=409,
            content={
                "status": "conflict",
                "error_code": "AGENT_SYNC_IDEMPOTENCY_CONFLICT",
                "message": str(exc),
            },
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "status": "invalid_request",
                "error_code": "AGENT_SYNC_POLICY_DENIED",
                "message": str(exc),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=503,
            content={
                "status": "upstream_unavailable",
                "error_code": "OPENCLAW_AGENT_SYNC_FAILED",
                "message": str(exc)[:300],
            },
        )
    return JSONResponse(status_code=status_code, content=payload)


@app.post("/api/openclaw-adapter/agents/persona-opinion/ensure")
def ensure_persona_opinion_agent(
    req: OpenClawPersonaOpinionEnsureRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
) -> JSONResponse:
    """Admit and reconcile one immutable, advice-only Persona agent."""

    if not str(idempotency_key or "").strip() or not str(x_request_id or "").strip():
        return JSONResponse(
            status_code=400,
            content={
                "status": "invalid_request",
                "error_code": "AGENT_SYNC_HEADERS_REQUIRED",
                "message": "Idempotency-Key and X-Request-Id are required.",
            },
        )
    try:
        status_code, payload = _ensure_agent_idempotently(
            req,
            idempotency_key=str(idempotency_key).strip(),
            request_id=str(x_request_id).strip(),
            sync_fn=_sync_persona_opinion_agent,
            preflight_fn=_preflight_persona_opinion_admission,
            commit_fn=_commit_persona_opinion_admission,
            postcondition_fn=lambda request: _wait_for_live_persona_opinion_agent(request.agent_id),
        )
        fingerprint = _persona_admission_fingerprint(req.model_dump(mode="json"))
    except _PersonaOpinionAgentNotReady as exc:
        return _persona_opinion_agent_not_ready_response(exc)
    except GatewayOpenClawProviderError as exc:
        return _persona_opinion_gateway_error_response(exc)
    except _AgentEnsureIdempotencyConflict as exc:
        return JSONResponse(
            status_code=409,
            content={
                "status": "conflict",
                "error_code": "AGENT_SYNC_IDEMPOTENCY_CONFLICT",
                "message": str(exc),
            },
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "status": "invalid_request",
                "error_code": "PERSONA_OPINION_ADMISSION_DENIED",
                "message": str(exc),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=503,
            content={
                "status": "upstream_unavailable",
                "error_code": "OPENCLAW_PERSONA_AGENT_SYNC_FAILED",
                "message": str(exc)[:300],
            },
        )
    payload["admission_fingerprint"] = fingerprint
    payload["execution_authority"] = "none"
    return JSONResponse(status_code=status_code, content=payload)


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


def _degraded_session_collection_route_error(exc: UpstreamClientError) -> UpstreamClientError:
    """Normalize an absent upstream session collection route to degraded mode.

    The deployed OpenClaw gateway can be healthy while omitting Pantheon's
    optional ``/api/sessions`` compatibility route.  That is a dependency
    capability gap, not a Pantheon facade route miss, so callers must receive
    the documented 503 degraded envelope instead of a passthrough 404.

    This is intentionally limited to the collection route used for list and
    create.  A 404 for ``/api/sessions/{session_id}`` can still mean that the
    requested session itself does not exist and must retain its typed upstream
    semantics.
    """

    if exc.error_code != "UPSTREAM_NOT_FOUND" or exc.upstream_status != 404:
        return exc

    details: Dict[str, Any] = {
        "route": "/api/sessions",
        "upstream_error_code": exc.error_code,
    }
    if exc.details:
        details["upstream_details"] = exc.details
    return UpstreamClientError(
        status_code=503,
        error_code="UPSTREAM_UNAVAILABLE",
        message=(
            "The upstream OpenClaw gateway does not expose the required "
            "session collection API; session work is safely deferred."
        ),
        retryable=True,
        owner_plane=exc.owner_plane,
        error_layer=exc.error_layer,
        upstream_status=exc.upstream_status,
        details=details,
    )


@app.get("/api/openclaw-adapter/sessions")
def list_sessions() -> JSONResponse:
    try:
        return JSONResponse(status_code=200, content={"status": "ok", "sessions": _client().list_sessions()})
    except UpstreamClientError as exc:
        exc = _degraded_session_collection_route_error(exc)
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
        return _error_response(_degraded_session_collection_route_error(exc))


@app.post("/api/openclaw-adapter/sessions/{session_id}/cancel")
def cancel_session(session_id: str) -> JSONResponse:
    try:
        return JSONResponse(status_code=200, content={"status": "ok", "session": _client().cancel_session(session_id)})
    except UpstreamClientError as exc:
        return _error_response(exc)


# ---------------------------------------------------------------------------
# Pantheon-owned session lifecycle (durable state machine + audit + idempotency)
# ---------------------------------------------------------------------------


def _lifecycle_upstream_factory() -> Optional[OpenClawUpstreamClient]:
    """Hand the lifecycle a configured upstream client when one exists.

    Returning None signals to the lifecycle that no upstream channel is
    configured; the lifecycle then keeps records in a degraded local-only state
    instead of raising, which is what the SVC-OPENCLAW-SESSION-LIFECYCLE
    acceptance ("degraded upstream recovery preserves known session state")
    requires.
    """
    if not OPENCLAW_GATEWAY_URL:
        return None
    return _client()


_LIFECYCLE_STORE = SessionLifecycleStore(upstream_factory=_lifecycle_upstream_factory)


def _serialize_record(record: SessionRecord) -> Dict[str, Any]:
    return record.to_dict()


def _lifecycle_error_response(exc: LifecycleError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


class LifecycleCreateRequest(BaseModel):
    agent_id: str
    session_type: str
    context_bundle: Optional[Dict[str, Any]] = None


@app.post("/api/openclaw-adapter/lifecycle/sessions")
def lifecycle_create_session(
    req: LifecycleCreateRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> JSONResponse:
    if not x_operator_id:
        return JSONResponse(
            status_code=401,
            content={
                "status": "lifecycle_error",
                "error_code": "LIFECYCLE_OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for session lifecycle commands.",
            },
        )
    try:
        record, replayed = _LIFECYCLE_STORE.create_session(
            agent_id=req.agent_id,
            session_type=req.session_type,
            operator_id=x_operator_id,
            idempotency_key=x_idempotency_key,
            context_bundle=req.context_bundle,
        )
    except LifecycleError as exc:
        return _lifecycle_error_response(exc)
    status_code = 200 if replayed else 201
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok",
            "replayed": replayed,
            "session": _serialize_record(record),
        },
    )


@app.get("/api/openclaw-adapter/lifecycle/sessions")
def lifecycle_list_sessions(
    operator_id: Optional[str] = None,
    state: Optional[str] = None,
) -> JSONResponse:
    records = _LIFECYCLE_STORE.list_sessions(operator_id=operator_id, state=state)
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "sessions": [_serialize_record(r) for r in records],
        },
    )


@app.get("/api/openclaw-adapter/lifecycle/sessions/{session_id}")
def lifecycle_get_session(session_id: str) -> JSONResponse:
    try:
        record = _LIFECYCLE_STORE.get_session(session_id)
    except LifecycleError as exc:
        return _lifecycle_error_response(exc)
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "session": _serialize_record(record)},
    )


@app.post("/api/openclaw-adapter/lifecycle/sessions/{session_id}/cancel")
def lifecycle_cancel_session(
    session_id: str,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
) -> JSONResponse:
    if not x_operator_id:
        return JSONResponse(
            status_code=401,
            content={
                "status": "lifecycle_error",
                "error_code": "LIFECYCLE_OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for session lifecycle commands.",
            },
        )
    try:
        record = _LIFECYCLE_STORE.cancel_session(session_id, operator_id=x_operator_id)
    except LifecycleError as exc:
        return _lifecycle_error_response(exc)
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "session": _serialize_record(record)},
    )


@app.get("/api/openclaw-adapter/lifecycle/sessions/{session_id}/audit")
def lifecycle_session_audit(session_id: str) -> JSONResponse:
    try:
        record = _LIFECYCLE_STORE.get_session(session_id, refresh_from_upstream=False)
    except LifecycleError as exc:
        return _lifecycle_error_response(exc)
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "session_id": record.session_id,
            "operator_id": record.operator_id,
            "audit_log": record.audit_log,
        },
    )


# ---------------------------------------------------------------------------
# Tool / Workflow bridge
# ---------------------------------------------------------------------------

_BRIDGE_POLICY = ToolPolicy()
_BRIDGE_AUDIT = BridgeAuditLog()
_BRIDGE = ToolWorkflowBridge(policy=_BRIDGE_POLICY, audit_log=_BRIDGE_AUDIT)


def _bridge_error_response(exc: BridgeError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


def _mode_from_control_mode(control_mode: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(control_mode, dict):
        return None
    mode = str(control_mode.get("mode") or "").strip()
    return mode or None


def _audit_value_hash(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        blob = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except Exception:
        blob = repr(value).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _upstream_client_or_none() -> Optional[OpenClawUpstreamClient]:
    if not OPENCLAW_GATEWAY_URL:
        return None
    return _client()


_OPENCLAW_SEARCH_REPOSITORY = JsonlEvidenceRepository(_SEARCH_EVIDENCE_STORE_PATH)
_OPENCLAW_SEARCH_GATEWAY = OpenClawSearchGateway(_OPENCLAW_SEARCH_REPOSITORY)


class SearchQueryRequest(BaseModel):
    query: str
    persona_id: str
    workspace_id: str
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    environment: str = "paper"
    source_types: List[str] = Field(default_factory=list)
    access_scopes: List[str] = Field(default_factory=lambda: ["public"])
    license_scopes: List[str] = Field(default_factory=lambda: ["internal", "open"])
    top_k: int = 5


class ToolInvokeRequest(BaseModel):
    session_id: str
    tool_name: str
    args: Optional[Any] = None
    mode: Optional[str] = None
    operator_role: Optional[str] = None
    operatorRole: Optional[str] = None
    confirmed: Optional[Any] = None
    confirm_token: Optional[str] = None
    confirmToken: Optional[str] = None
    control_mode: Optional[Dict[str, Any]] = None
    controlMode: Optional[Dict[str, Any]] = None


class WorkflowTriggerRequest(BaseModel):
    workflow_ref: str
    context: Optional[Any] = None
    mode: Optional[str] = None
    operator_role: Optional[str] = None
    operatorRole: Optional[str] = None
    confirmed: Optional[Any] = None
    confirm_token: Optional[str] = None
    confirmToken: Optional[str] = None
    control_mode: Optional[Dict[str, Any]] = None
    controlMode: Optional[Dict[str, Any]] = None


@app.post("/api/openclaw-adapter/search/query")
def query_search(
    req: SearchQueryRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> JSONResponse:
    if not x_operator_id:
        return JSONResponse(
            status_code=401,
            content={
                "status": "search_error",
                "error_code": "SEARCH_OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required.",
            },
        )
    try:
        _OPENCLAW_SEARCH_REPOSITORY.reload()
        result = _OPENCLAW_SEARCH_GATEWAY.search(
            {
                "request_id": req.request_id or f"openclaw-search-{x_operator_id}",
                "trace_id": req.trace_id or x_trace_id or f"trace-openclaw-search-{x_operator_id}",
                "query": req.query,
                "persona_id": req.persona_id,
                "workspace_id": req.workspace_id,
                "environment": req.environment,
                "source_types": req.source_types,
                "access_scopes": req.access_scopes,
                "license_scopes": req.license_scopes,
                "top_k": req.top_k,
            }
        )
    except OpenClawSearchPolicyError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "status": "search_error",
                "error_code": "SEARCH_POLICY_ERROR",
                "message": str(exc),
            },
        )
    return JSONResponse(status_code=200, content=result)


@app.get("/api/openclaw-adapter/tools/policy")
def get_tool_policy() -> Dict[str, Any]:
    return _BRIDGE_POLICY.to_dict()


@app.get("/api/openclaw-adapter/tools")
def list_tools(
    agent_id: str,
    session_id: Optional[str] = None,
    mode: Optional[str] = None,
    operator_role: Optional[str] = None,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_operator_role: Optional[str] = Header(default=None, alias="X-Operator-Role"),
) -> JSONResponse:
    if not x_operator_id:
        return JSONResponse(
            status_code=401,
            content={
                "status": "bridge_error",
                "error_code": "BRIDGE_OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required.",
            },
        )
    try:
        result = _BRIDGE.list_effective_tools(
            agent_id=agent_id,
            session_id=session_id,
            operator_id=x_operator_id,
            mode=mode,
            operator_role=operator_role or x_operator_role,
            upstream=_upstream_client_or_none(),
        )
    except BridgeError as exc:
        return _bridge_error_response(exc)
    return JSONResponse(status_code=200, content=result)


@app.post("/api/openclaw-adapter/tools/invoke")
def invoke_tool(
    req: ToolInvokeRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_operator_role: Optional[str] = Header(default=None, alias="X-Operator-Role"),
    x_assistant_mode: Optional[str] = Header(default=None, alias="X-Assistant-Mode"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> JSONResponse:
    if not x_operator_id:
        return JSONResponse(
            status_code=401,
            content={
                "status": "bridge_error",
                "error_code": "BRIDGE_OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required.",
            },
        )
    try:
        result = _BRIDGE.invoke_tool(
            session_id=req.session_id,
            tool_name=req.tool_name,
            args=req.args,
            operator_id=x_operator_id,
            mode=req.mode or x_assistant_mode or _mode_from_control_mode(req.control_mode or req.controlMode),
            operator_role=req.operator_role or req.operatorRole or x_operator_role,
            confirmed=req.confirmed is True,
            confirm_token=req.confirm_token or req.confirmToken,
            control_mode=req.control_mode or req.controlMode,
            trace_id=x_trace_id,
            upstream=_upstream_client_or_none(),
        )
    except BridgeError as exc:
        return _bridge_error_response(exc)
    return JSONResponse(status_code=200, content=result)


@app.post("/api/openclaw-adapter/workflows/trigger")
def trigger_workflow(
    req: WorkflowTriggerRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_operator_role: Optional[str] = Header(default=None, alias="X-Operator-Role"),
    x_assistant_mode: Optional[str] = Header(default=None, alias="X-Assistant-Mode"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> JSONResponse:
    if not x_operator_id:
        return JSONResponse(
            status_code=401,
            content={
                "status": "bridge_error",
                "error_code": "BRIDGE_OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required.",
            },
        )
    try:
        result = _BRIDGE.trigger_workflow(
            workflow_ref=req.workflow_ref,
            context=req.context,
            operator_id=x_operator_id,
            mode=req.mode or x_assistant_mode or _mode_from_control_mode(req.control_mode or req.controlMode),
            operator_role=req.operator_role or req.operatorRole or x_operator_role,
            confirmed=req.confirmed is True,
            confirm_token=req.confirm_token or req.confirmToken,
            control_mode=req.control_mode or req.controlMode,
            trace_id=x_trace_id,
            upstream=_upstream_client_or_none(),
        )
    except BridgeError as exc:
        return _bridge_error_response(exc)
    return JSONResponse(status_code=200, content=result)


@app.get("/api/openclaw-adapter/workflows/jobs/{job_id}")
def get_workflow_job(job_id: str) -> JSONResponse:
    try:
        result = _client().get_job(job_id)
        return JSONResponse(status_code=200, content={"status": "ok", "job": result})
    except UpstreamClientError as exc:
        return _error_response(exc)


@app.get("/api/openclaw-adapter/audit/invocations")
def list_bridge_audit(
    session_id: Optional[str] = None,
    operator_id: Optional[str] = None,
    limit: int = 100,
) -> JSONResponse:
    entries = _BRIDGE_AUDIT.read(session_id=session_id, operator_id=operator_id, limit=limit)
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "count": len(entries), "entries": entries},
    )


# ---------------------------------------------------------------------------
# Paper broker adapter (gated by OPENCLAW_PAPER_ADAPTER_ENABLED)
# Live orders are always rejected regardless of gate state.
# ---------------------------------------------------------------------------

_PAPER_BROKER_AUDIT = PaperBrokerAuditLog()
_PAPER_BROKER = PaperBrokerAdapter(
    enabled=_PAPER_ADAPTER_ENABLED,
    broker_url=_BROKER_SIDECAR_URL,
    runtime_manager_url=_RUNTIME_MANAGER_URL,
    audit_log=_PAPER_BROKER_AUDIT,
)

_LIVE_GATE_AUDIT = LiveGateAuditLog()
_LIVE_GATE = LiveGateAdapter(
    enabled=_LIVE_ADAPTER_ENABLED,
    runtime_manager_url=_RUNTIME_MANAGER_URL,
    audit_log=_LIVE_GATE_AUDIT,
)

def _dummy_runner(payload: Dict[str, Any]) -> Any:
    return {"status": "ok"}

_ASSISTANT_MOUNTS = AssistantCredentialMounts()
_CODEX_PROVIDER = AssistantCodexProvider(mounts=_ASSISTANT_MOUNTS)
_CLAUDE_PROVIDER = AssistantClaudeProvider(mounts=_ASSISTANT_MOUNTS)
_OPENCLAW_AGENT_PROVIDER = AssistantOpenClawProvider()
_PROVIDER_REGISTRY = AssistantProviderRegistry()
_CODEX_RUNTIME = AssistantProviderRuntime(runner=_CODEX_PROVIDER.invoke)
_ASSISTANT_RUNTIME = AssistantProviderRuntime(runner=_dummy_runner)


def _paper_broker_error_response(exc: PaperBrokerAdapterError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


class PaperOrderRequest(BaseModel):
    capital_pool_id: str
    strategy_id: str
    symbol: str
    qty: float
    side: str
    order_type: str = "market"
    limit_price: Optional[float] = None


@app.post("/api/openclaw-adapter/broker/paper/orders")
def submit_paper_order(
    req: PaperOrderRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> JSONResponse:
    if not x_operator_id:
        return JSONResponse(
            status_code=401,
            content={
                "status": "paper_broker_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for paper order submission.",
            },
        )
    try:
        result = _PAPER_BROKER.submit_paper_order(
            capital_pool_id=req.capital_pool_id,
            strategy_id=req.strategy_id,
            symbol=req.symbol,
            qty=req.qty,
            side=req.side,
            order_type=req.order_type,
            limit_price=req.limit_price,
            operator_id=x_operator_id,
            trace_id=x_trace_id,
        )
    except PaperBrokerAdapterError as exc:
        return _paper_broker_error_response(exc)
    return JSONResponse(status_code=201, content=result)


@app.get("/api/openclaw-adapter/broker/paper/orders")
def list_paper_orders(
    capital_pool_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    limit: int = 100,
) -> JSONResponse:
    try:
        result = _PAPER_BROKER.list_paper_orders(
            capital_pool_id=capital_pool_id,
            strategy_id=strategy_id,
            limit=limit,
        )
    except PaperBrokerAdapterError as exc:
        return _paper_broker_error_response(exc)
    return JSONResponse(status_code=200, content=result)


@app.get("/api/openclaw-adapter/broker/paper/orders/{order_id}")
def get_paper_order(order_id: str) -> JSONResponse:
    try:
        result = _PAPER_BROKER.get_paper_order(order_id)
    except PaperBrokerAdapterError as exc:
        return _paper_broker_error_response(exc)
    return JSONResponse(status_code=200, content=result)


@app.post("/api/openclaw-adapter/broker/paper/orders/{order_id}/cancel")
def cancel_paper_order(
    order_id: str,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> JSONResponse:
    if not x_operator_id:
        return JSONResponse(
            status_code=401,
            content={
                "status": "paper_broker_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for paper order cancellation.",
            },
        )
    try:
        result = _PAPER_BROKER.cancel_paper_order(
            order_id,
            operator_id=x_operator_id,
            trace_id=x_trace_id,
        )
    except PaperBrokerAdapterError as exc:
        return _paper_broker_error_response(exc)
    return JSONResponse(status_code=200, content=result)


@app.post("/api/openclaw-adapter/broker/live/orders")
def reject_live_order(
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
) -> JSONResponse:
    try:
        _LIVE_GATE.reject_live_order(operator_id=x_operator_id)
    except LiveGateError as exc:
        return _live_gate_error_response(exc)
    # unreachable — reject_live_order always raises
    return JSONResponse(status_code=403, content={"status": "live_gate_error", "error_code": "LIVE_EXECUTION_DISABLED"})  # pragma: no cover


@app.post("/api/openclaw-adapter/broker/canary/orders")
def reject_canary_order(
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
) -> JSONResponse:
    _PAPER_BROKER_AUDIT.record(
        {
            "event": "canary_order_rejected",
            "operator_id": x_operator_id or "",
            "is_real_order": False,
            "is_real_capital": False,
            "canary_enabled": False,
            "configured_gate_enabled": _CANARY_ADAPTER_ENABLED,
            "outcome": "rejected",
        }
    )
    return JSONResponse(
        status_code=403,
        content={
            "status": "canary_gate_error",
            "error_code": "CANARY_EXECUTION_DISABLED",
            "message": (
                "Canary broker execution is disabled. OpenClaw canary orders are not accepted "
                "until a separate activation gate explicitly enables this path."
            ),
            "gate": "canary_execution",
            "details": {
                "canary_enabled": False,
                "configured_gate_enabled": _CANARY_ADAPTER_ENABLED,
                "configured_gate": "OPENCLAW_CANARY_ADAPTER_ENABLED",
                "allowed_scope": "canary_gate_not_enabled",
                "is_real_order": False,
                "is_real_capital": False,
            },
        },
    )


# ---------------------------------------------------------------------------
# Live gate harness (gated by OPENCLAW_LIVE_ADAPTER_ENABLED + all gate checks)
# Live execution is always denied — only dry handoff is supported.
# ---------------------------------------------------------------------------


def _live_gate_error_response(exc: LiveGateError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


class LiveGateValidateRequest(BaseModel):
    capital_pool_id: str


class LiveGateDryHandoffRequest(BaseModel):
    capital_pool_id: str
    strategy_id: str
    symbol: str
    qty: float
    side: str
    order_type: str = "market"
    limit_price: Optional[float] = None


@app.get("/api/openclaw-adapter/broker/live/gate/status")
def live_gate_status() -> JSONResponse:
    """Return current live gate capability and configuration status."""
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "live_gate_enabled": _LIVE_GATE.enabled,
            "live_execution_enabled": False,
            **_LIVE_GATE.capability_snapshot(),
        },
    )


@app.post("/api/openclaw-adapter/broker/live/gate/validate")
def live_gate_validate(
    req: LiveGateValidateRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_human_approval_token: Optional[str] = Header(default=None, alias="X-Human-Approval-Token"),
) -> JSONResponse:
    """Run all live gate checks for a capital pool without performing a handoff.

    Returns 200 with gate attestation when all gates pass.
    Returns 403/409/503 with structured error when any gate fails.
    """
    if not x_operator_id:
        return JSONResponse(
            status_code=401,
            content={
                "status": "live_gate_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for live gate checks.",
            },
        )
    try:
        attestation = _LIVE_GATE.check_all_gates(
            capital_pool_id=req.capital_pool_id,
            human_approval_token=x_human_approval_token or "",
            operator_id=x_operator_id,
        )
    except LiveGateError as exc:
        return _live_gate_error_response(exc)
    return JSONResponse(status_code=200, content={"status": "ok", "attestation": attestation})


@app.post("/api/openclaw-adapter/broker/live/gate/dry-handoff")
def live_gate_dry_handoff(
    req: LiveGateDryHandoffRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_human_approval_token: Optional[str] = Header(default=None, alias="X-Human-Approval-Token"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> JSONResponse:
    """Validate all live gate checks and record a dry handoff intent.

    No real broker execution occurs.  Returns a structured preview payload
    with full gate attestation.  The audit log records the intent regardless
    of gate outcome.
    """
    if not x_operator_id:
        return JSONResponse(
            status_code=401,
            content={
                "status": "live_gate_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for live gate dry handoff.",
            },
        )
    try:
        result = _LIVE_GATE.dry_handoff(
            capital_pool_id=req.capital_pool_id,
            strategy_id=req.strategy_id,
            symbol=req.symbol,
            qty=req.qty,
            side=req.side,
            order_type=req.order_type,
            limit_price=req.limit_price,
            operator_id=x_operator_id,
            human_approval_token=x_human_approval_token or "",
            trace_id=x_trace_id,
        )
    except LiveGateError as exc:
        return _live_gate_error_response(exc)
    return JSONResponse(status_code=200, content=result)


@app.get("/api/openclaw-adapter/broker/live/gate/audit")
def live_gate_audit(
    operator_id: Optional[str] = None,
    capital_pool_id: Optional[str] = None,
    limit: int = 100,
) -> JSONResponse:
    """Return the append-only live gate intent and outcome audit trail."""
    entries = _LIVE_GATE.read_audit(
        operator_id=operator_id,
        capital_pool_id=capital_pool_id,
        limit=limit,
    )
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "count": len(entries), "entries": entries},
    )


@app.get("/api/openclaw-adapter/broker/audit")
def list_paper_broker_audit(
    operator_id: Optional[str] = None,
    capital_pool_id: Optional[str] = None,
    limit: int = 100,
) -> JSONResponse:
    entries = _PAPER_BROKER.read_audit(
        operator_id=operator_id,
        capital_pool_id=capital_pool_id,
        limit=limit,
    )
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "count": len(entries), "entries": entries},
    )


@app.get("/api/openclaw-adapter/broker/capabilities")
def broker_capabilities() -> JSONResponse:
    paper_snap = _PAPER_BROKER.capability_snapshot()
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            # Explicit capability states for sandbox/paper/canary/live
            "sandbox_adapter_state": paper_snap["sandbox_adapter_state"],
            "sandbox_gate": paper_snap["sandbox_gate"],
            "paper_adapter_state": "enabled" if paper_snap["paper_adapter_enabled"] else "gated",
            "paper_adapter_gate": "OPENCLAW_PAPER_ADAPTER_ENABLED",
            "canary_adapter_state": "fail_closed",
            "canary_adapter_gate": "OPENCLAW_CANARY_ADAPTER_ENABLED",
            "live_adapter_state": "fail_closed",
            "live_adapter_gate": "OPENCLAW_LIVE_ADAPTER_ENABLED",
            # Legacy flat fields
            **paper_snap,
            "canary_adapter_enabled": False,
            "canary_execution_enabled": False,
            "canary_gate": "OPENCLAW_CANARY_ADAPTER_ENABLED",
            "canary_allowed_scope": "canary_gate_not_enabled",
            "live_gate": _LIVE_GATE.capability_snapshot(),
        },
    )
