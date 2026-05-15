"""Typed HTTP client for the Pantheon OpenClaw session lifecycle surface.

Downstream services (BFF operator surface in SVC-OPENCLAW-BFF-OPS-SURFACE,
broker adapter in SVC-OPENCLAW-PAPER-BROKER-ADAPTER) call the lifecycle
endpoints through this client instead of reaching into the upstream OpenClaw
gateway directly. The lifecycle service is the durable Pantheon-owned state
authority for sessions; this client never bypasses it.

The client only handles the lifecycle surface (create/get/list/cancel/audit).
It does not touch broker or live execution paths — those remain disabled.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


class LifecycleClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.payload = payload or {}


class OpenClawLifecycleClient:
    """Typed HTTP client for `/api/openclaw-adapter/lifecycle/sessions/*`.

    Usage::

        client = OpenClawLifecycleClient()
        record, replayed = client.create_session(
            agent_id="agent-1",
            session_type="interactive",
            operator_id="alice@pantheon",
            idempotency_key="op-123",
        )

    The client returns plain dicts so callers stay decoupled from the
    server-side dataclass types.
    """

    DEFAULT_BASE_URL_ENV = "OPENCLAW_GATEWAY_ADAPTER_URL"
    DEFAULT_TIMEOUT_ENV = "OPENCLAW_LIFECYCLE_CLIENT_TIMEOUT"

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        raw = base_url if base_url is not None else os.getenv(self.DEFAULT_BASE_URL_ENV, "")
        self._base_url = raw.rstrip("/")
        self._timeout = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv(self.DEFAULT_TIMEOUT_ENV, "5")
        )

    # ----- public API ---------------------------------------------------------

    def create_session(
        self,
        *,
        agent_id: str,
        session_type: str,
        operator_id: str,
        idempotency_key: Optional[str] = None,
        context_bundle: Optional[Dict[str, Any]] = None,
    ) -> tuple[Dict[str, Any], bool]:
        body = {"agent_id": agent_id, "session_type": session_type}
        if context_bundle is not None:
            body["context_bundle"] = context_bundle
        headers = {"X-Operator-Id": operator_id}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        status, payload = self._request(
            "POST",
            "/api/openclaw-adapter/lifecycle/sessions",
            body=body,
            headers=headers,
            expected_status={200, 201},
        )
        return payload.get("session", {}), bool(payload.get("replayed"))

    def get_session(self, session_id: str) -> Dict[str, Any]:
        _, payload = self._request(
            "GET",
            f"/api/openclaw-adapter/lifecycle/sessions/{session_id}",
            expected_status={200},
        )
        return payload.get("session", {})

    def list_sessions(
        self,
        *,
        operator_id: Optional[str] = None,
        state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query: Dict[str, str] = {}
        if operator_id is not None:
            query["operator_id"] = operator_id
        if state is not None:
            query["state"] = state
        path = "/api/openclaw-adapter/lifecycle/sessions"
        if query:
            from urllib.parse import urlencode

            path = f"{path}?{urlencode(query)}"
        _, payload = self._request("GET", path, expected_status={200})
        return list(payload.get("sessions", []))

    def cancel_session(self, session_id: str, *, operator_id: str) -> Dict[str, Any]:
        _, payload = self._request(
            "POST",
            f"/api/openclaw-adapter/lifecycle/sessions/{session_id}/cancel",
            headers={"X-Operator-Id": operator_id},
            expected_status={200},
        )
        return payload.get("session", {})

    def get_audit(self, session_id: str) -> List[Dict[str, Any]]:
        _, payload = self._request(
            "GET",
            f"/api/openclaw-adapter/lifecycle/sessions/{session_id}/audit",
            expected_status={200},
        )
        return list(payload.get("audit_log", []))

    # ----- internals ----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        expected_status: Optional[set[int]] = None,
    ) -> tuple[int, Dict[str, Any]]:
        if not self._base_url:
            raise LifecycleClientError(
                f"{self.DEFAULT_BASE_URL_ENV} is not configured.",
                status_code=503,
                error_code="LIFECYCLE_CLIENT_NOT_CONFIGURED",
            )
        request_headers = {"Accept": "application/json"}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"{self._base_url}{path}",
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                status = resp.getcode()
                raw = resp.read().decode("utf-8") if resp.length != 0 else ""
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8") if exc.fp is not None else ""
            payload = _safe_json(raw)
            raise LifecycleClientError(
                payload.get("message") or f"Lifecycle service returned HTTP {exc.code}.",
                status_code=exc.code,
                error_code=payload.get("error_code"),
                payload=payload,
            ) from exc
        except urllib.error.URLError as exc:
            raise LifecycleClientError(
                f"Lifecycle service is unreachable: {exc.reason}",
                status_code=503,
                error_code="LIFECYCLE_CLIENT_UNREACHABLE",
            ) from exc
        payload = _safe_json(raw)
        if expected_status and status not in expected_status:
            raise LifecycleClientError(
                payload.get("message") or f"Lifecycle service returned HTTP {status}.",
                status_code=status,
                error_code=payload.get("error_code"),
                payload=payload,
            )
        return status, payload


def _safe_json(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    if isinstance(loaded, dict):
        return loaded
    return {"data": loaded}
