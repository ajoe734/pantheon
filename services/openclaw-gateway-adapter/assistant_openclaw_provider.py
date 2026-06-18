"""OpenClaw gateway agent provider for the assistant pipeline.

Routes management-AI prompts through the upstream OpenClaw gateway agent
using the official CLI interface, matching the pattern used by the Codex
and Claude providers.

The gateway agent protocol is WebSocket RPC (not HTTP REST).
The official programmatic interface is:

    openclaw agent --url ws://<host>:<port> --token <token> \\
                   --agent <agent_id> --message "<prompt>"

which writes the agent reply to stdout.  The adapter shell-outs to this
CLI rather than implementing a custom WS-RPC client, consistent with how
the Codex and Claude providers shell-out to their respective CLIs.

Readiness uses the gateway HTTP health probe (:18789/readyz) which is a
real HTTP endpoint distinct from the WS-RPC agent invocation port.

Degrades cleanly when the binary is absent or the gateway is unreachable.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


OPENCLAW_PROVIDER = "openclaw"
OPENCLAW_PROVIDER_ID = "openclaw"
PROVIDER_RUNTIME = "openclaw_gateway_agent_cli"
DEFAULT_AGENT_ID = "main"
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_OPENCLAW_BIN = "openclaw"
# Canonical docker-compose service name — used when no URL is configured.
_DEFAULT_GATEWAY_WS_URL = "ws://openclaw-gateway:18789"


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
    """Sends prompts to the upstream OpenClaw gateway agent via CLI and returns structured results.

    Readiness semantics:
    - not_configured: OPENCLAW_GATEWAY_URL is not set
    - ready (auth_probe=False): URL is set; binary/token not checked (light probe)
    - ready (auth_probe=True): URL set, binary found, token set, gateway health OK
    - degraded: URL set but binary/token missing or gateway unreachable

    The provider does not depend on local credential mounts — the auth token is
    supplied via OPENCLAW_GATEWAY_TOKEN.
    """

    def __init__(
        self,
        *,
        gateway_url: Optional[str] = None,
        agent_id: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        _which_func=None,
        _run_func=None,
    ) -> None:
        raw = (gateway_url or os.getenv("OPENCLAW_GATEWAY_URL", "")).strip()
        # If an http:// URL was configured by mistake, convert it back to ws://.
        if raw.startswith("http://"):
            raw = "ws://" + raw[len("http://"):]
        elif raw.startswith("https://"):
            raw = "wss://" + raw[len("https://"):]
        # Store as-is (empty string = not configured).
        self._gateway_url = raw.rstrip("/")
        self._agent_id = (agent_id or os.getenv("OPENCLAW_AGENT_ID", DEFAULT_AGENT_ID)).strip()
        self._token = token or os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
        self._timeout = int(os.getenv("OPENCLAW_ASSISTANT_TIMEOUT_SECONDS", str(timeout_seconds)))
        self._which = _which_func or shutil.which
        self._run = _run_func or subprocess.run

    @property
    def configured(self) -> bool:
        """True when OPENCLAW_GATEWAY_URL was explicitly configured."""
        return bool(self._gateway_url)

    def _openclaw_bin(self) -> Optional[str]:
        explicit = os.getenv("OPENCLAW_BIN", "").strip()
        if explicit:
            return explicit
        return self._which(DEFAULT_OPENCLAW_BIN)

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
            # Light probe: URL is set, assume the binary and token will be
            # available at invocation time (mirrors old provider behaviour).
            return {**base, "ready": True, "status": "ready"}
        # Full auth probe: verify binary, token, and gateway health.
        binary = self._openclaw_bin()
        if not binary:
            return {
                **base,
                "ready": False,
                "status": "degraded",
                "reason": "openclaw_binary_not_found",
                "binary_path": DEFAULT_OPENCLAW_BIN,
            }
        if not self._token:
            return {
                **base,
                "ready": False,
                "status": "degraded",
                "reason": "OPENCLAW_GATEWAY_TOKEN_not_set",
                "binary_path": binary,
            }
        probe = self._probe_gateway()
        if probe.get("reachable"):
            return {**base, "ready": True, "status": "ready", "probe": probe, "binary_path": binary}
        return {
            **base,
            "ready": False,
            "status": "degraded",
            "reason": probe.get("reason", "gateway_unreachable"),
            "probe": probe,
            "binary_path": binary,
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
        # When no URL was explicitly configured, fall back to the canonical
        # docker-compose service name so the adapter works out of the box.
        effective_url = self._gateway_url or _DEFAULT_GATEWAY_WS_URL

        binary = self._openclaw_bin()
        if not binary:
            raise OpenClawProviderError(
                "openclaw binary not found. Ensure the openclaw CLI is installed in the adapter image.",
                status_code=503,
                error_code="OPENCLAW_BINARY_NOT_FOUND",
            )
        if not self._token:
            raise OpenClawProviderError(
                "OPENCLAW_GATEWAY_TOKEN is not set. Configure the token in the compose env.",
                status_code=503,
                error_code="OPENCLAW_TOKEN_NOT_CONFIGURED",
            )

        request_id = str(uuid.uuid4())
        started_at = time.monotonic()

        # OpenClaw 2026.6.6 `agent` reads the gateway URL + token from the
        # environment (OPENCLAW_GATEWAY_URL / OPENCLAW_GATEWAY_TOKEN); it does
        # NOT accept --url/--token (those exist only on the gateway
        # call/probe/status subcommands — passing them errors with
        # "does not recognize option --url"). --json yields a structured
        # envelope on stdout (diagnostics go to stderr).
        #
        # The prompt is fed via STDIN (`--message -`), NOT as an argv string:
        # Management-AI prompts carry a large context pack and a single argv that
        # big overflows the kernel's MAX_ARG_STRLEN (128 KiB) with
        # "[Errno 7] Argument list too long". stdin has no such limit.
        cmd = [
            binary,
            "agent",
            "--agent", self._agent_id,
            "--message", "-",
            "--json",
            "--timeout", str(self._timeout),
        ]

        # Pass the normalized ws:// URL + token explicitly via the env the CLI
        # reads, so we don't depend on how compose spells OPENCLAW_GATEWAY_URL.
        # OPENCLAW_ALLOW_INSECURE_PRIVATE_WS (set in compose) is inherited from
        # os.environ and permits plaintext ws:// on the trusted docker network.
        run_env = {
            **os.environ,
            "NO_COLOR": "1",
            "OPENCLAW_GATEWAY_URL": effective_url,
            "OPENCLAW_GATEWAY_TOKEN": self._token,
        }
        try:
            proc = self._run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=run_env,
                input=prompt,
            )
        except subprocess.TimeoutExpired as exc:
            raise OpenClawProviderError(
                f"openclaw agent invocation timed out after {self._timeout}s.",
                status_code=504,
                error_code="OPENCLAW_GATEWAY_TIMEOUT",
            ) from exc
        except FileNotFoundError as exc:
            raise OpenClawProviderError(
                "openclaw binary disappeared after readiness check.",
                status_code=503,
                error_code="OPENCLAW_BINARY_NOT_FOUND",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise OpenClawProviderError(
                f"openclaw agent invocation failed: {exc}",
                status_code=502,
                error_code="OPENCLAW_GATEWAY_INVOCATION_FAILED",
            ) from exc

        elapsed_ms = int((time.monotonic() - started_at) * 1000)

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            raise OpenClawProviderError(
                f"openclaw agent exited with code {proc.returncode}: {stderr[:400]}",
                status_code=502,
                error_code="OPENCLAW_GATEWAY_INVOCATION_FAILED",
            )

        text = self._extract_reply(proc.stdout or "")
        output = self._build_output(
            text=text,
            request_id=request_id,
            elapsed_ms=elapsed_ms,
            stderr=proc.stderr or "",
        )

        return OpenClawProviderResult(
            provider=OPENCLAW_PROVIDER,
            mode=mode,
            status="completed",
            output=output,
            redaction={"provider_invocation": {"redacted_fields": 0}},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _probe_gateway(self) -> Dict[str, Any]:
        # Derive the HTTP base URL from the WS URL for the health probe.
        http_base = self._gateway_url
        if http_base.startswith("ws://"):
            http_base = "http://" + http_base[len("ws://"):]
        elif http_base.startswith("wss://"):
            http_base = "https://" + http_base[len("wss://"):]
        for path in ("/readyz", "/healthz"):
            try:
                req = urllib.request.Request(
                    f"{http_base}{path}",
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
    def _extract_reply(stdout: str) -> str:
        """Pull the assistant reply out of `openclaw agent --json` stdout.

        2026.6.6 schema: {runId, status, summary, result:{payloads:[{text}],
        meta:{finalAssistantVisibleText, finalAssistantRawText}}}. Falls back to
        the raw stdout when the output is not the expected JSON (defensive — a
        future CLI change should degrade to "reply = whatever was printed"
        rather than silently drop the answer).
        """
        raw = (stdout or "").strip()
        if not raw:
            return ""
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return raw
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, dict):
            meta = result.get("meta")
            if isinstance(meta, dict):
                for key in ("finalAssistantVisibleText", "finalAssistantRawText"):
                    val = meta.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
            payloads = result.get("payloads")
            if isinstance(payloads, list):
                texts = [
                    p["text"]
                    for p in payloads
                    if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"].strip()
                ]
                if texts:
                    return "\n".join(texts).strip()
        return raw

    @staticmethod
    def _build_output(
        *,
        text: str,
        request_id: str,
        elapsed_ms: int,
        stderr: str,
    ) -> Dict[str, Any]:
        json_events: List[Dict[str, Any]] = [
            {
                "type": "item.completed",
                "item": {
                    "id": f"item_{request_id}",
                    "type": "agent_message",
                    "text": text,
                },
            }
        ]
        out: Dict[str, Any] = {
            "json_events": json_events,
            "agent_id": DEFAULT_AGENT_ID,
            "request_id": request_id,
            "duration_ms": elapsed_ms,
            "transport": "cli",
        }
        if stderr.strip():
            out["stderr_hint"] = stderr.strip()[:200]
        return out
