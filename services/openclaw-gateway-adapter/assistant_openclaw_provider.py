"""OpenClaw gateway agent provider for the assistant pipeline.

Routes management-AI prompts through the upstream OpenClaw gateway agent
instead of the local Codex/Claude CLI mounts.  The gateway agent provides
tool resolution, memory, and persona context that the CLI paths cannot.

The provider calls the upstream agent's HTTP invocation endpoint:
  POST /api/agents/{agent_id}/invoke
using the same OPENCLAW_GATEWAY_URL that the upstream health probe already
uses (the gateway exposes both HTTP and WebSocket on that address).

Degrades cleanly when the gateway is absent — returns status="degraded" so
the BFF can apply its configured fallback rather than propagating a transport
error.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


OPENCLAW_PROVIDER = "openclaw"
OPENCLAW_PROVIDER_ID = "openclaw"
PROVIDER_RUNTIME = "openclaw_gateway_agent"
DEFAULT_AGENT_ID = "main"
DEFAULT_TIMEOUT_SECONDS = 90


@dataclass(frozen=True)
class OpenClawProviderResult:
    provider: str
    mode: str
    status: str
    output: Dict[str, Any]
    redaction: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "provider": self.provider,
            "mode": self.mode,
            "status": self.status,
            "output": self.output,
        }
        if self.redaction is not None:
            out["redaction"] = self.redaction
        return out


class OpenClawProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 500, error_code: str) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code

    def to_payload(self) -> Dict[str, Any]:
        return {
            "status": "provider_error",
            "error_code": self.error_code,
            "message": self.message,
        }


class AssistantOpenClawProvider:
    """Sends prompts to the upstream OpenClaw gateway agent and returns structured results.

    Readiness is "ready" when OPENCLAW_GATEWAY_URL is configured and the
    gateway responds to /readyz.  The provider itself does not depend on local
    credential mounts.
    """

    def __init__(
        self,
        *,
        gateway_url: Optional[str] = None,
        agent_id: Optional[str] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        raw = (gateway_url or os.getenv("OPENCLAW_GATEWAY_URL", "")).strip()
        # Normalize: strip ws:// / wss:// to http:// / https:// for REST calls.
        if raw.startswith("ws://"):
            raw = "http://" + raw[len("ws://"):]
        elif raw.startswith("wss://"):
            raw = "https://" + raw[len("wss://"):]
        self._gateway_url = raw.rstrip("/")
        self._agent_id = (agent_id or os.getenv("OPENCLAW_AGENT_ID", DEFAULT_AGENT_ID)).strip()
        self._timeout = int(os.getenv("OPENCLAW_ASSISTANT_TIMEOUT_SECONDS", str(timeout_seconds)))

    @property
    def configured(self) -> bool:
        return bool(self._gateway_url)

    def readiness(self, *, auth_probe: bool = False) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "provider": OPENCLAW_PROVIDER,
            "provider_id": OPENCLAW_PROVIDER_ID,
            "runtime": PROVIDER_RUNTIME,
            "agent_id": self._agent_id,
            "gateway_url_configured": bool(self._gateway_url),
        }
        if not self._gateway_url:
            return {
                **base,
                "ready": False,
                "status": "not_configured",
                "reason": "OPENCLAW_GATEWAY_URL is not set",
            }
        if not auth_probe:
            return {**base, "ready": True, "status": "ready"}
        # Probe the gateway health endpoint.
        probe = self._probe_gateway()
        if probe.get("reachable"):
            return {**base, "ready": True, "status": "ready", "probe": probe}
        return {
            **base,
            "ready": False,
            "status": "degraded",
            "reason": probe.get("reason", "gateway_unreachable"),
            "probe": probe,
        }

    def invoke(
        self,
        prompt: str,
        *,
        mode: str = "user",
        context_pack: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        operator_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> OpenClawProviderResult:
        if not self._gateway_url:
            raise OpenClawProviderError(
                "OpenClaw gateway URL is not configured (OPENCLAW_GATEWAY_URL).",
                status_code=503,
                error_code="OPENCLAW_GATEWAY_NOT_CONFIGURED",
            )

        request_id = str(uuid.uuid4())
        payload: Dict[str, Any] = {
            "request_id": request_id,
            "agent_id": self._agent_id,
            "prompt": prompt,
            "mode": mode,
            "context_pack": context_pack or {},
            "metadata": metadata or {},
        }
        if messages:
            payload["messages"] = messages
        if operator_id:
            payload["operator_id"] = operator_id
        if trace_id:
            payload["trace_id"] = trace_id

        started_at = time.monotonic()
        try:
            response_body = self._http_post(
                f"/api/agents/{self._agent_id}/invoke",
                payload=payload,
            )
        except OpenClawProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OpenClawProviderError(
                f"OpenClaw gateway agent invocation failed: {exc}",
                status_code=502,
                error_code="OPENCLAW_GATEWAY_INVOCATION_FAILED",
            ) from exc

        elapsed_ms = int((time.monotonic() - started_at) * 1000)

        # Normalise output into the standard provider output envelope.
        output = self._normalise_output(response_body, elapsed_ms=elapsed_ms, request_id=request_id)

        return OpenClawProviderResult(
            provider=OPENCLAW_PROVIDER,
            mode=mode,
            status=self._status_from_body(response_body),
            output=output,
            redaction={"provider_invocation": {"redacted_fields": 0}},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _http_post(self, path: str, *, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._gateway_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8") if exc.fp is not None else ""
            try:
                body = json.loads(raw) if raw.strip() else {}
            except Exception:
                body = {"raw": raw}
            error_code = str(body.get("error_code") or body.get("code") or "OPENCLAW_GATEWAY_HTTP_ERROR")
            raise OpenClawProviderError(
                str(body.get("message") or f"OpenClaw gateway returned HTTP {exc.code}."),
                status_code=exc.code if exc.code >= 500 else 502,
                error_code=error_code,
            ) from exc
        except urllib.error.URLError as exc:
            raise OpenClawProviderError(
                f"OpenClaw gateway is unreachable: {exc.reason}",
                status_code=503,
                error_code="OPENCLAW_GATEWAY_UNREACHABLE",
            ) from exc
        except TimeoutError as exc:
            raise OpenClawProviderError(
                "OpenClaw gateway agent invocation timed out.",
                status_code=504,
                error_code="OPENCLAW_GATEWAY_TIMEOUT",
            ) from exc

    def _probe_gateway(self) -> Dict[str, Any]:
        for path in ("/readyz", "/healthz"):
            try:
                req = urllib.request.Request(
                    f"{self._gateway_url}{path}",
                    headers={"Accept": "application/json"},
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    status = resp.getcode()
                    return {"reachable": status == 200, "http_status": status, "probe": path}
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    return {"reachable": False, "http_status": exc.code, "probe": path, "reason": str(exc)}
            except Exception as exc:  # noqa: BLE001
                return {"reachable": False, "probe": path, "reason": str(exc)}
        return {"reachable": False, "reason": "no_probe_path_succeeded"}

    @staticmethod
    def _status_from_body(body: Dict[str, Any]) -> str:
        s = str(body.get("status") or "").lower()
        if s in {"completed", "done", "ok", "success"}:
            return "completed"
        if s in {"error", "failed", "failure"}:
            return "error"
        if body.get("output") or body.get("message") or body.get("text"):
            return "completed"
        return "completed"

    @staticmethod
    def _normalise_output(body: Dict[str, Any], *, elapsed_ms: int, request_id: str) -> Dict[str, Any]:
        # Extract the response text from whatever shape the gateway returns.
        text: Optional[str] = None
        if isinstance(body.get("output"), dict):
            out = body["output"]
            text = (
                out.get("text")
                or out.get("message")
                or out.get("content")
                or out.get("response")
            )
        elif isinstance(body.get("output"), str):
            text = body["output"]

        if text is None:
            text = (
                body.get("text")
                or body.get("message")
                or body.get("content")
                or body.get("response")
            )
            if isinstance(text, dict):
                text = json.dumps(text)

        json_events: List[Dict[str, Any]] = [
            {
                "type": "item.completed",
                "item": {
                    "id": f"item_{request_id}",
                    "type": "agent_message",
                    "text": str(text) if text is not None else "",
                },
            }
        ]
        upstream_output = body.get("output") if isinstance(body.get("output"), dict) else {}
        return {
            "json_events": json_events,
            "agent_id": body.get("agent_id", DEFAULT_AGENT_ID),
            "request_id": request_id,
            "duration_ms": elapsed_ms,
            "upstream": upstream_output or body,
        }
