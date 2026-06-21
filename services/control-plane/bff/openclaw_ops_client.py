"""Service client for BFF-owned OpenClaw operator surfaces.

The BFF must not call upstream OpenClaw routes directly.  This client talks to
the Pantheon-owned openclaw-gateway-adapter and preserves its fail-closed
payloads for BFF projection and command facades.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


OPENCLAW_ADAPTER_BASE_URL_ENVS = (
    "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL",
    "PANTHEON_OPENCLAW_ADAPTER_URL",
    "OPENCLAW_GATEWAY_ADAPTER_URL",
)


class OpenClawOpsClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error_code: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.payload = payload or {}

    def to_surface(self) -> Dict[str, Any]:
        return {
            "status": "unavailable" if self.status_code in {0, 503, 504} else "degraded",
            "source": "service_client",
            "reason": self.error_code,
            "message": self.message,
            "http_status": self.status_code if self.status_code else None,
        }


def _base_url_from_env() -> Optional[str]:
    for env_name in OPENCLAW_ADAPTER_BASE_URL_ENVS:
        raw = os.getenv(env_name, "").strip()
        if raw:
            return raw.rstrip("/")
    return None


def _timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "2.0").strip()
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return 2.0


def _assistant_provider_timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_ASSISTANT_PROVIDER_TIMEOUT_SECONDS", "75.0").strip()
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return 75.0


def _assistant_repair_prepare_timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_ASSISTANT_REPAIR_PREPARE_TIMEOUT_SECONDS", "45.0").strip()
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return 45.0


def _assistant_reauth_timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_ASSISTANT_REAUTH_TIMEOUT_SECONDS", "30.0").strip()
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return 30.0


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


class OpenClawOpsClient:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        raw = base_url if base_url is not None else _base_url_from_env()
        self._base_url = raw.rstrip("/") if raw else ""
        self._timeout = timeout_seconds if timeout_seconds is not None else _timeout_seconds()
        self._timeout_explicit = timeout_seconds is not None

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    @property
    def base_url(self) -> str:
        return self._base_url

    def get_capabilities(self) -> Dict[str, Any]:
        return self._request("GET", "/api/openclaw-adapter/capabilities")

    def get_upstream_status(self) -> Dict[str, Any]:
        return self._request("GET", "/api/openclaw-adapter/upstream/status")

    def list_lifecycle_sessions(
        self,
        *,
        operator_id: Optional[str] = None,
        state: Optional[str] = None,
    ) -> Dict[str, Any]:
        query: Dict[str, str] = {}
        if operator_id:
            query["operator_id"] = operator_id
        if state:
            query["state"] = state
        return self._request("GET", "/api/openclaw-adapter/lifecycle/sessions", query=query)

    def get_tool_policy(self) -> Dict[str, Any]:
        return self._request("GET", "/api/openclaw-adapter/tools/policy")

    def list_effective_tools(
        self,
        *,
        agent_id: str,
        operator_id: str,
        session_id: Optional[str] = None,
        mode: Optional[str] = None,
        operator_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = {"agent_id": agent_id}
        if session_id:
            query["session_id"] = session_id
        if mode:
            query["mode"] = mode
        if operator_role:
            query["operator_role"] = operator_role
        headers = {"X-Operator-Id": operator_id}
        if operator_role:
            headers["X-Operator-Role"] = operator_role
        return self._request(
            "GET",
            "/api/openclaw-adapter/tools",
            query=query,
            headers=headers,
        )

    def authorize_assistant_skill(
        self,
        *,
        skill_id: str,
        operator_id: str,
        mode: Optional[str] = None,
        operator_role: Optional[str] = None,
        confirmed: bool = False,
        confirm_token: Optional[str] = None,
        control_mode: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        request_type: str = "assistant_skill_authorize",
        audit_extra: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "request_type": request_type,
            "confirmed": confirmed,
        }
        if mode:
            body["mode"] = mode
        if operator_role:
            body["operator_role"] = operator_role
        if confirm_token:
            body["confirm_token"] = confirm_token
        if control_mode is not None:
            body["control_mode"] = control_mode
        if session_id:
            body["session_id"] = session_id
        if audit_extra:
            body["audit_extra"] = audit_extra
        headers: Dict[str, str] = {"X-Operator-Id": operator_id}
        if operator_role:
            headers["X-Operator-Role"] = operator_role
        if mode:
            headers["X-Assistant-Mode"] = mode
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        return self._request(
            "POST",
            f"/api/openclaw-adapter/assistant/skills/{urllib.parse.quote(skill_id, safe='')}/authorize",
            body=body,
            headers=headers,
            expected_status={200},
        )

    def list_invocation_audit(
        self,
        *,
        session_id: Optional[str] = None,
        operator_id: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        query: Dict[str, str] = {"limit": str(limit)}
        if session_id:
            query["session_id"] = session_id
        if operator_id:
            query["operator_id"] = operator_id
        return self._request("GET", "/api/openclaw-adapter/audit/invocations", query=query)

    def invoke_assistant_provider(
        self,
        *,
        provider: str,
        mode: str,
        prompt: str,
        context_pack: Dict[str, Any],
        operator_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        messages: Optional[list[Dict[str, Any]]] = None,
        attachments: Optional[list[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        normalized = str(provider or "").strip().lower()
        headers: Dict[str, str] = {"X-Operator-Id": operator_id}
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        if normalized in {"openclaw", "openclaw_agent"}:
            body: Dict[str, Any] = {
                "mode": mode,
                "prompt": prompt,
                "context_pack": context_pack,
                "metadata": metadata or {},
            }
            if messages is not None:
                body["messages"] = messages
            if attachments is not None:
                body["attachments"] = attachments
            return self._request(
                "POST",
                "/api/openclaw-adapter/assistant/providers/openclaw/invoke",
                body=body,
                headers=headers,
                expected_status={200},
                timeout_seconds=self._assistant_timeout_seconds(),
            )
        if normalized in {"codex", "codex_cli"}:
            body = {
                "mode": mode,
                "prompt": prompt,
                "context_pack": context_pack,
                "metadata": metadata or {},
            }
            if messages is not None:
                body["messages"] = messages
            if attachments is not None:
                body["attachments"] = attachments
            return self._request(
                "POST",
                "/api/openclaw-adapter/assistant/providers/codex/invoke",
                body=body,
                headers=headers,
                expected_status={200},
                timeout_seconds=self._assistant_timeout_seconds(),
            )
        if normalized in {"claude", "claude_cli"}:
            # Claude invoke route uses a different URL pattern from Codex.
            # The adapter's /assistant/claude/invoke does not accept a metadata
            # body field; operator identity is carried only via X-Operator-Id.
            return self._request(
                "POST",
                "/api/openclaw-adapter/assistant/claude/invoke",
                body={
                    "prompt": prompt,
                    "mode": mode,
                    "context_pack": context_pack,
                },
                headers=headers,
                expected_status={200},
                timeout_seconds=self._assistant_timeout_seconds(),
            )
        raise OpenClawOpsClientError(
            f"Assistant provider {provider!r} is not supported by the BFF OpenClaw client.",
            status_code=400,
            error_code="ASSISTANT_PROVIDER_NOT_SUPPORTED",
            payload={"provider": provider},
        )

    def stream_assistant_provider(
        self,
        *,
        mode: str,
        prompt: str,
        context_pack: Dict[str, Any],
        operator_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        session_user: Optional[str] = None,
    ):
        """Stream the OpenClaw assistant provider via the adapter SSE endpoint.

        Yields the adapter's normalized event dicts:
            {"type":"delta","text":...}
            {"type":"done","text":...,"elapsed_ms":N,"transport":"responses_http"}
            {"type":"error","error_code":...,"message":...}
        Raises OpenClawOpsClientError if the adapter URL is unset or the stream
        cannot be opened (the caller turns that into a degraded SSE event).
        """
        if not self._base_url:
            raise OpenClawOpsClientError(
                "OpenClaw gateway adapter URL is not configured.",
                status_code=503,
                error_code="OPENCLAW_ADAPTER_URL_NOT_CONFIGURED",
            )
        md = dict(metadata or {})
        if session_user:
            md.setdefault("session_user", session_user)
        body = {"mode": mode, "prompt": prompt, "context_pack": context_pack, "metadata": md}
        headers = {
            "X-Operator-Id": operator_id,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        request = urllib.request.Request(
            f"{self._base_url}/api/openclaw-adapter/assistant/providers/openclaw/invoke/stream",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=self._assistant_timeout_seconds())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp is not None else ""
            payload = _safe_json(raw)
            raise OpenClawOpsClientError(
                str(payload.get("message") or f"OpenClaw adapter stream returned HTTP {exc.code}."),
                status_code=exc.code,
                error_code=str(payload.get("error_code") or "OPENCLAW_ADAPTER_STREAM_HTTP_ERROR"),
                payload=payload,
            ) from exc
        except urllib.error.URLError as exc:
            raise OpenClawOpsClientError(
                f"OpenClaw adapter stream is unreachable: {exc}",
                status_code=503,
                error_code="OPENCLAW_ADAPTER_STREAM_UNREACHABLE",
            ) from exc
        try:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload_str = line[len("data:"):].strip()
                if payload_str == "[DONE]":
                    break
                try:
                    yield json.loads(payload_str)
                except (ValueError, TypeError):
                    continue
        finally:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass

    def prepare_assistant_repair_worktree(
        self,
        *,
        payload: Dict[str, Any],
        operator_id: str,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        headers: Dict[str, str] = {"X-Operator-Id": operator_id}
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        return self._request(
            "POST",
            "/api/openclaw-adapter/assistant/repair-worktrees/prepare",
            body=payload,
            headers=headers,
            expected_status={201},
            timeout_seconds=_assistant_repair_prepare_timeout_seconds(),
        )

    def start_assistant_provider_reauth(
        self,
        *,
        provider: str = "codex",
        payload: Optional[Dict[str, Any]] = None,
        operator_id: str,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized = str(provider or "codex").strip().lower()
        headers: Dict[str, str] = {"X-Operator-Id": operator_id}
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        body = dict(payload or {})
        body.setdefault("provider", normalized)
        operator_role = str(body.get("operator_role") or body.get("operatorRole") or "").strip()
        if operator_role:
            headers["X-Operator-Role"] = operator_role
        mode = str(body.get("mode") or "").strip()
        if mode:
            headers["X-Assistant-Mode"] = mode
        return self._request(
            "POST",
            f"/api/openclaw-adapter/assistant/providers/{urllib.parse.quote(normalized)}/reauth",
            body=body,
            headers=headers,
            expected_status={202},
            timeout_seconds=_assistant_reauth_timeout_seconds(),
        )

    def get_assistant_provider_reauth_status(
        self,
        *,
        provider: str = "codex",
        session_id: str,
        operator_id: str,
    ) -> Dict[str, Any]:
        normalized = str(provider or "codex").strip().lower()
        return self._request(
            "GET",
            (
                f"/api/openclaw-adapter/assistant/providers/{urllib.parse.quote(normalized)}"
                f"/reauth/{urllib.parse.quote(session_id)}"
            ),
            headers={"X-Operator-Id": operator_id},
        )

    def create_session(
        self,
        *,
        agent_id: str,
        session_type: str,
        operator_id: str,
        idempotency_key: str,
        context_bundle: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "agent_id": agent_id,
            "session_type": session_type,
        }
        if context_bundle is not None:
            body["context_bundle"] = context_bundle
        return self._request(
            "POST",
            "/api/openclaw-adapter/lifecycle/sessions",
            body=body,
            headers={
                "X-Operator-Id": operator_id,
                "X-Idempotency-Key": idempotency_key,
            },
            expected_status={200, 201},
        )

    def get_session(
        self,
        *,
        session_id: str,
        operator_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        headers: Dict[str, str] = {}
        if operator_id:
            headers["X-Operator-Id"] = operator_id
        return self._request(
            "GET",
            f"/api/openclaw-adapter/lifecycle/sessions/{urllib.parse.quote(session_id, safe='')}",
            headers=headers,
            expected_status={200},
        )

    def cancel_session(
        self,
        *,
        session_id: str,
        operator_id: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/api/openclaw-adapter/lifecycle/sessions/{urllib.parse.quote(session_id)}/cancel",
            headers={
                "X-Operator-Id": operator_id,
                "X-Idempotency-Key": idempotency_key,
            },
            expected_status={200},
        )

    # ------------------------------------------------------------------
    # Live gate operator surface (read-only: status and audit)
    # Dry handoff and gate validate remain on the adapter, not the BFF.
    # ------------------------------------------------------------------

    def get_broker_capabilities(self) -> Dict[str, Any]:
        """Return broker adapter capability states (sandbox/paper/canary/live)."""
        return self._request("GET", "/api/openclaw-adapter/broker/capabilities")

    def get_live_gate_status(self) -> Dict[str, Any]:
        """Return the current live gate capability and configuration status."""
        return self._request("GET", "/api/openclaw-adapter/broker/live/gate/status")

    def list_live_gate_audit(
        self,
        *,
        operator_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Return the live gate audit trail (intents and outcomes)."""
        query: Dict[str, str] = {"limit": str(limit)}
        if operator_id:
            query["operator_id"] = operator_id
        if capital_pool_id:
            query["capital_pool_id"] = capital_pool_id
        return self._request("GET", "/api/openclaw-adapter/broker/live/gate/audit", query=query)

    # ------------------------------------------------------------------
    # Assistant provider surfaces
    # ------------------------------------------------------------------

    def get_assistant_readiness(self, provider: str = "codex", *, auth_probe: bool = False) -> Dict[str, Any]:
        """Return readiness metadata for an assistant provider."""
        query = {"auth_probe": "true"} if auth_probe else None
        return self._request("GET", f"/api/openclaw-adapter/assistant/readiness/{provider}", query=query)

    def invoke_assistant(
        self,
        *,
        provider: str = "codex",
        mode: str = "user",
        prompt: str,
        operator_id: str,
        context_pack: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Invoke the assistant CLI provider through the OpenClaw gateway adapter."""
        normalized = str(provider or "codex").strip().lower()
        meta = dict(metadata or {})
        meta["operator_id"] = operator_id
        if normalized in {"claude", "claude_cli"}:
            # Claude uses a distinct route in the gateway adapter.
            path = "/api/openclaw-adapter/assistant/claude/invoke"
            body: Dict[str, Any] = {
                "prompt": prompt,
                "mode": mode,
                "context_pack": context_pack or {},
            }
        else:
            path = f"/api/openclaw-adapter/assistant/providers/{normalized}/invoke"
            body = {
                "mode": mode,
                "prompt": prompt,
                "context_pack": context_pack or {},
                "metadata": meta,
            }
        return self._request(
            "POST",
            path,
            body=body,
            headers={"X-Operator-Id": operator_id},
            expected_status={200},
            timeout_seconds=self._assistant_timeout_seconds(),
        )

    def _assistant_timeout_seconds(self) -> float:
        if self._timeout_explicit:
            return self._timeout
        return _assistant_provider_timeout_seconds()

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Dict[str, str]] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        expected_status: Optional[set[int]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not self._base_url:
            raise OpenClawOpsClientError(
                "OpenClaw gateway adapter URL is not configured.",
                status_code=503,
                error_code="OPENCLAW_ADAPTER_URL_NOT_CONFIGURED",
            )

        url = f"{self._base_url}{path}"
        if query:
            clean_query = {
                str(key): str(value)
                for key, value in query.items()
                if value not in (None, "")
            }
            if clean_query:
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}{urllib.parse.urlencode(clean_query)}"

        request_headers = {"Accept": "application/json"}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)

        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method=method,
        )
        expected = expected_status or {200}
        timeout = timeout_seconds if timeout_seconds is not None else self._timeout
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.getcode()
                raw = response.read().decode("utf-8").strip()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8").strip() if exc.fp is not None else ""
            payload = _safe_json(raw)
            error_code = str(
                payload.get("error_code")
                or payload.get("code")
                or "OPENCLAW_ADAPTER_HTTP_ERROR"
            )
            raise OpenClawOpsClientError(
                str(payload.get("message") or f"OpenClaw adapter returned HTTP {exc.code}."),
                status_code=exc.code,
                error_code=error_code,
                payload=payload,
            ) from exc
        except urllib.error.URLError as exc:
            raise OpenClawOpsClientError(
                f"OpenClaw gateway adapter is unreachable: {exc.reason}",
                status_code=503,
                error_code="OPENCLAW_ADAPTER_UNREACHABLE",
            ) from exc
        except OSError as exc:
            raise OpenClawOpsClientError(
                f"OpenClaw gateway adapter request failed: {exc}",
                status_code=503,
                error_code="OPENCLAW_ADAPTER_REQUEST_FAILED",
            ) from exc

        payload = _safe_json(raw)
        if status not in expected:
            error_code = str(payload.get("error_code") or "OPENCLAW_ADAPTER_UNEXPECTED_STATUS")
            raise OpenClawOpsClientError(
                str(payload.get("message") or f"OpenClaw adapter returned HTTP {status}."),
                status_code=status,
                error_code=error_code,
                payload=payload,
            )
        return payload
