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

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from integrations.openclaw.search_gateway import OpenClawSearchGateway, SearchPolicyError as OpenClawSearchPolicyError
from services.knowledge.evidence import JsonlEvidenceRepository
from session_lifecycle import (
    LifecycleError,
    SessionLifecycleStore,
    SessionRecord,
)
from tool_workflow_bridge import (
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
from assistant_codex_provider import AssistantCodexProvider, CodexProviderError
from assistant_claude_provider import AssistantClaudeProvider, ClaudeProviderResult
from assistant_provider_runtime import (
    AssistantProviderRuntime,
    AssistantProviderRuntimeError,
    ProviderInvocationRequest,
)
from assistant_repair_workflow import AssistantRepairWorkflow, AssistantRepairWorkflowError

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

register_fastapi_health_routes(
    app,
    "openclaw-gateway-adapter",
    dependencies=lambda: {"openclaw_gateway": _upstream_health_dep()},
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
    payload["paper_adapter"] = "enabled" if _PAPER_ADAPTER_ENABLED else "deferred"
    payload["live_gate_harness"] = "enabled" if _LIVE_ADAPTER_ENABLED else "present_disabled"
    # Canary execution remains hard-denied even if a local env var is set; a future
    # activation task must introduce the actual adapter policy and evidence.
    payload["canary_adapter"] = "deferred"
    payload["paper_broker"] = _PAPER_BROKER.capability_snapshot()
    payload["live_gate"] = _LIVE_GATE.capability_snapshot()
    payload["assistant_credential_mounts"] = _ASSISTANT_MOUNTS.get_readiness_metadata()
    try:
        upstream_capabilities = _client().get_capabilities()
        payload["activation_state"] = "upstream_client_ready"
        payload["upstream"] = {
            "status": "ok",
            "capabilities": upstream_capabilities,
        }
    except UpstreamClientError as exc:
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

class AssistantProviderInvokeRequest(BaseModel):
    mode: str = "user"
    prompt: str
    context_pack: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    messages: Optional[List[Dict[str, Any]]] = None
    attachments: Optional[List[Dict[str, Any]]] = None


class AssistantRepairWorktreePrepareRequest(BaseModel):
    task_id: Optional[str] = None
    taskId: Optional[str] = None
    task_worktree: Optional[str] = None
    taskWorktree: Optional[str] = None
    declared_scope: Optional[List[str]] = None
    declaredScope: Optional[List[str]] = None
    expected_branch: Optional[str] = None
    expectedBranch: Optional[str] = None
    remote: Optional[str] = None
    repo_key: Optional[str] = None
    repoKey: Optional[str] = None
    repository: Optional[str] = None
    merge_target: Optional[str] = None
    mergeTarget: Optional[str] = None
    reason: Optional[str] = None

    def to_metadata(self) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}
        for key in (
            "task_id",
            "taskId",
            "task_worktree",
            "taskWorktree",
            "declared_scope",
            "declaredScope",
            "expected_branch",
            "expectedBranch",
            "remote",
            "repo_key",
            "repoKey",
            "repository",
            "merge_target",
            "mergeTarget",
            "reason",
        ):
            value = getattr(self, key)
            if value not in (None, "", []):
                metadata[key] = value
        return metadata


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


def _assistant_repair_workflow_error_response(exc: AssistantRepairWorkflowError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


@app.get("/api/openclaw-adapter/assistant/readiness/{provider}")
def get_assistant_readiness(provider: str, auth_probe: bool = False) -> Dict[str, Any]:
    """Probes the provider binary and auth mount readiness."""
    if provider in {"codex", "codex_cli"}:
        return _CODEX_PROVIDER.readiness(auth_probe=auth_probe)
    if provider in {"claude", "claude_cli"}:
        return _CLAUDE_PROVIDER.readiness(auth_probe=auth_probe)
    return _ASSISTANT_RUNTIME.check_readiness(provider)


@app.get("/api/openclaw-adapter/assistant/providers")
def list_assistant_providers(auth_probe: bool = False) -> Dict[str, Any]:
    return {
        "status": "ok",
        "data": [
            _CODEX_PROVIDER.readiness(auth_probe=auth_probe),
            _CLAUDE_PROVIDER.readiness(auth_probe=auth_probe),
        ],
    }


@app.post("/api/openclaw-adapter/assistant/repair-worktrees/prepare")
def prepare_assistant_repair_worktree(
    req: AssistantRepairWorktreePrepareRequest,
    x_operator_id: Optional[str] = Header(default=None, alias="X-Operator-Id"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
) -> JSONResponse:
    if not x_operator_id or not x_operator_id.strip():
        return JSONResponse(
            status_code=401,
            content={
                "status": "repair_workflow_error",
                "error_code": "OPERATOR_REQUIRED",
                "message": "X-Operator-Id header is required for repair worktree preparation.",
            },
        )
    metadata = req.to_metadata()
    metadata["operator_id"] = x_operator_id.strip()
    if x_trace_id:
        metadata["trace_id"] = x_trace_id
    try:
        prepared = _REPAIR_WORKFLOW.prepare(metadata)
    except AssistantRepairWorkflowError as exc:
        return _assistant_repair_workflow_error_response(exc)
    return JSONResponse(status_code=201, content={"status": "ok", "data": prepared.to_dict()})


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
        result = _CODEX_RUNTIME.invoke(
            ProviderInvocationRequest(
                provider="codex_cli",
                mode=req.mode,
                prompt=req.prompt,
                context_pack=req.context_pack or {},
                metadata=metadata,
                messages=req.messages,
                attachments=req.attachments,
            )
        )
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


class WorkflowTriggerRequest(BaseModel):
    workflow_ref: str
    context: Optional[Any] = None


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
_CODEX_RUNTIME = AssistantProviderRuntime(runner=_CODEX_PROVIDER.invoke)
_ASSISTANT_RUNTIME = AssistantProviderRuntime(runner=_dummy_runner)
_REPAIR_WORKFLOW = AssistantRepairWorkflow()


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
