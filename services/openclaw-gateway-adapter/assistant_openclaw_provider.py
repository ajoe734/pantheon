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
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from assistant_provider_usage import provider_usage_snapshot


OPENCLAW_PROVIDER = "openclaw"
OPENCLAW_PROVIDER_ID = "openclaw"
PROVIDER_RUNTIME = "openclaw_gateway_agent_cli"
DEFAULT_AGENT_ID = "main"
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_READINESS_ANSWER_TIMEOUT_SECONDS = 20
DEFAULT_OPENCLAW_BIN = "openclaw"
CODEX_DELEGATED_KERNEL_MODES = frozenset({"kernel_debug"})
# `openclaw agent` accepts the prompt ONLY as an argv string (`-m/--message
# <text>`); it has NO stdin support and `--message -` is taken LITERALLY — the
# agent then receives a bare "-" heartbeat tick and replies "HEARTBEAT_OK"
# instead of running the prompt. A single argv is bounded by the kernel's
# MAX_ARG_STRLEN (128 KiB); cap below that and fail loudly rather than silently
# truncating/dropping the prompt.
_MAX_ARGV_PROMPT_BYTES = 96 * 1024
# Canonical docker-compose service name — used when no URL is configured.
_DEFAULT_GATEWAY_WS_URL = "ws://openclaw-gateway:18789"
_AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_READINESS_PROMPT = "Reply with exactly: PANTHEON_PROVIDER_READY"


def _openclaw_cli_state_env(environment: Dict[str, str]) -> Dict[str, str]:
    """Bind agent invocations to the same state tree used by reconciliation.

    The adapter runs as root, while governed agent reconciliation deliberately
    writes the gateway-owned registry below ``/home/node/.openclaw``.  The
    OpenClaw ``agent`` CLI resolves a requested agent from its local state
    before invoking the remote gateway, so leaving its default HOME in place
    makes a freshly reconciled Persona agent appear unknown.
    """

    resolved = dict(environment)
    state_dir = str(
        resolved.get("PANTHEON_OPENCLAW_GATEWAY_STATE_DIR")
        or resolved.get("OPENCLAW_STATE_DIR")
        or ""
    ).strip()
    if state_dir:
        state_path = Path(state_dir).expanduser().resolve()
        resolved["OPENCLAW_STATE_DIR"] = str(state_path)
        resolved["HOME"] = str(state_path.parent)
    return resolved


def delegates_kernel_mode_to_codex(mode: str) -> bool:
    """Return whether the adapter must use its scoped Codex runtime.

    The upstream OpenClaw agent invocation has no task-worktree or sandbox
    contract.  Kernel debug/repair therefore cannot safely run through that
    transport and are delegated by the adapter route to the Codex runtime.
    """

    return str(mode or "").strip().lower() in CODEX_DELEGATED_KERNEL_MODES


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
    - not_checked: a cheap inventory read was requested; it never claims that
      an agent can answer a Management AI prompt
    - ready (auth_probe=True): a bounded, non-empty OpenClaw agent answer was
      received through the same CLI invocation path used by Management AI
    - degraded: the configured provider could not produce that bounded answer

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
        usage = provider_usage_snapshot(OPENCLAW_PROVIDER_ID, OPENCLAW_PROVIDER)
        answer_timeout = self._readiness_answer_timeout_seconds()
        base: Dict[str, Any] = {
            "provider": OPENCLAW_PROVIDER,
            "provider_id": OPENCLAW_PROVIDER_ID,
            "runtime": PROVIDER_RUNTIME,
            "agent_id": self._agent_id,
            "gateway_url_configured": bool(self._gateway_url),
            "usage": usage,
            "quota": usage,
            "answer_probe": {
                "status": "not_run",
                "deadline_seconds": answer_timeout,
            },
        }
        if not self._gateway_url:
            return {
                **base,
                "ready": False,
                "status": "not_configured",
                "reason": "OPENCLAW_GATEWAY_URL is not set",
            }
        if not auth_probe:
            # A configured URL or a gateway health endpoint does not prove that
            # the selected upstream model session can return an answer.
            return {
                **base,
                "ready": False,
                "status": "not_checked",
                "reason": "answer_probe_not_run",
            }
        # Full probe: exercise the actual answer path inside one bounded budget.
        binary = self._openclaw_bin()
        if not binary:
            return {
                **base,
                "ready": False,
                "status": "degraded",
                "reason": "openclaw_binary_not_found",
                "binary_path": DEFAULT_OPENCLAW_BIN,
                "answer_probe": {
                    "status": "failed",
                    "reason": "openclaw_binary_not_found",
                    "deadline_seconds": answer_timeout,
                },
            }
        if not self._token:
            return {
                **base,
                "ready": False,
                "status": "degraded",
                "reason": "OPENCLAW_GATEWAY_TOKEN_not_set",
                "binary_path": binary,
                "answer_probe": {
                    "status": "failed",
                    "reason": "OPENCLAW_GATEWAY_TOKEN_not_set",
                    "deadline_seconds": answer_timeout,
                },
            }
        started_at = time.monotonic()
        try:
            result = self.invoke(
                _READINESS_PROMPT,
                mode="user",
                operator_id="management-ai-readiness",
                timeout_seconds=answer_timeout,
            )
        except OpenClawProviderError as exc:
            duration_ms = max(0, int((time.monotonic() - started_at) * 1000))
            return {
                **base,
                "ready": False,
                "status": "degraded",
                "reason": exc.error_code,
                "binary_path": binary,
                "answer_probe": {
                    "status": "failed",
                    "reason": exc.error_code,
                    "deadline_seconds": answer_timeout,
                    "duration_ms": duration_ms,
                },
            }
        answer = self._result_text(result)
        duration_ms = max(0, int((time.monotonic() - started_at) * 1000))
        if not answer:
            return {
                **base,
                "ready": False,
                "status": "degraded",
                "reason": "openclaw_answer_probe_empty",
                "binary_path": binary,
                "answer_probe": {
                    "status": "failed",
                    "reason": "openclaw_answer_probe_empty",
                    "deadline_seconds": answer_timeout,
                    "duration_ms": duration_ms,
                },
            }
        return {
            **base,
            "ready": True,
            "status": "ready",
            "auth": "account_session",
            "auth_status": "ready",
            "binary_path": binary,
            "answer_probe": {
                "status": "completed",
                "deadline_seconds": answer_timeout,
                "duration_ms": duration_ms,
                "reply_bytes": len(answer.encode("utf-8")),
            },
        }

    def invoke(
        self,
        prompt: str,
        *,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        mode: str = "user",
        context_pack: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        operator_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> OpenClawProviderResult:
        if delegates_kernel_mode_to_codex(mode):
            raise OpenClawProviderError(
                "OpenClaw kernel modes must be delegated to the adapter Codex runtime.",
                status_code=409,
                error_code="OPENCLAW_KERNEL_DELEGATION_REQUIRED",
            )
        selected_agent_id = str(agent_id or self._agent_id).strip()
        if not _AGENT_ID_PATTERN.fullmatch(selected_agent_id):
            raise OpenClawProviderError(
                "OpenClaw agent_id contains unsupported characters.",
                status_code=422,
                error_code="OPENCLAW_AGENT_ID_INVALID",
            )

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

        # The CLI only reads the prompt from `-m/--message <text>` (argv); it has
        # no stdin mode. Guard the argv byte budget so an oversized prompt fails
        # with a clear error instead of "[Errno 7] Argument list too long".
        prompt_bytes = len(prompt.encode("utf-8"))
        if prompt_bytes > _MAX_ARGV_PROMPT_BYTES:
            raise OpenClawProviderError(
                f"prompt is {prompt_bytes} bytes, exceeding the {_MAX_ARGV_PROMPT_BYTES}-byte "
                "argv limit for `openclaw agent --message`. Trim the context pack or route "
                "this turn through the gateway HTTP /v1/responses endpoint.",
                status_code=413,
                error_code="OPENCLAW_PROMPT_TOO_LARGE",
            )

        request_id = str(uuid.uuid4())
        started_at = time.monotonic()
        invocation_timeout = float(self._timeout)
        if timeout_seconds is not None:
            invocation_timeout = min(invocation_timeout, float(timeout_seconds))
        if invocation_timeout <= 0:
            raise OpenClawProviderError(
                "openclaw agent invocation exhausted its bounded deadline.",
                status_code=504,
                error_code="OPENCLAW_GATEWAY_TIMEOUT",
            )

        # OpenClaw 2026.6.8 `agent` reads the gateway URL + token from the
        # environment (OPENCLAW_GATEWAY_URL / OPENCLAW_GATEWAY_TOKEN); it does
        # NOT accept --url/--token (those exist only on the gateway
        # call/probe/status subcommands — passing them errors with
        # "does not recognize option --url"). --json yields a structured
        # envelope on stdout (diagnostics go to stderr).
        #
        # The prompt MUST be passed as the `-m/--message` argv value. `--message -`
        # does NOT read stdin — the CLI takes "-" literally, so the agent gets a
        # bare heartbeat tick and replies "HEARTBEAT_OK" (verified live on the
        # 2026.6.8 gateway). See _MAX_ARGV_PROMPT_BYTES for the size guard above.
        cmd = [
            binary,
            "agent",
            "--agent", selected_agent_id,
            *(["--session-id", session_id] if session_id else []),
            "--message", prompt,
            "--json",
            "--timeout", str(max(1, int(invocation_timeout))),
        ]

        # Pass the normalized ws:// URL + token explicitly via the env the CLI
        # reads, so we don't depend on how compose spells OPENCLAW_GATEWAY_URL.
        # OPENCLAW_ALLOW_INSECURE_PRIVATE_WS (set in compose) is inherited from
        # os.environ and permits plaintext ws:// on the trusted docker network.
        run_env = _openclaw_cli_state_env({
            **os.environ,
            "NO_COLOR": "1",
            "OPENCLAW_GATEWAY_URL": effective_url,
            "OPENCLAW_GATEWAY_TOKEN": self._token,
        })
        try:
            proc = self._run(
                cmd,
                capture_output=True,
                text=True,
                timeout=invocation_timeout,
                env=run_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise OpenClawProviderError(
                f"openclaw agent invocation timed out after {invocation_timeout:g}s.",
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
            agent_id=selected_agent_id,
        )

        return OpenClawProviderResult(
            provider=OPENCLAW_PROVIDER,
            mode=mode,
            status="completed",
            output=output,
            redaction={"provider_invocation": {"redacted_fields": 0}},
        )

    # Only these gateway RPC methods may be proxied — persona OODA-loop cron
    # registration/inspection. Keeps the proxy from becoming an arbitrary RPC hole.
    _CRON_METHODS = frozenset(
        {"cron.add", "cron.list", "cron.run", "cron.runs", "cron.update", "cron.remove"}
    )

    def gateway_cron_call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Proxy a whitelisted `cron.*` gateway RPC via `openclaw gateway call`.

        Unlike `agent`, the `gateway call` subcommand DOES accept --url/--token.
        Used by the BFF persona OODA-loop cron registrar, which cannot reach the
        gateway directly (no docker socket / no openclaw binary in the BFF image).
        """
        if method not in self._CRON_METHODS:
            raise OpenClawProviderError(
                f"gateway method {method!r} is not permitted (allowed: {sorted(self._CRON_METHODS)}).",
                status_code=403,
                error_code="OPENCLAW_GATEWAY_METHOD_FORBIDDEN",
            )
        return self._gateway_call(method, params)

    def gateway_agents_list(self, *, timeout_seconds: Optional[float] = None) -> List[Dict[str, Any]]:
        """Read the gateway's live agent registry without widening the cron proxy."""

        payload = self._gateway_call("agents.list", timeout_seconds=timeout_seconds)
        agents = payload.get("agents") if isinstance(payload, dict) else None
        if not isinstance(agents, list) or any(not isinstance(item, dict) for item in agents):
            raise OpenClawProviderError(
                "openclaw gateway agents.list returned an invalid payload.",
                status_code=502,
                error_code="OPENCLAW_GATEWAY_SERIALIZATION_FAILURE",
            )
        return agents

    def _gateway_call(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Call one adapter-owned gateway RPC after the public method allowlist."""

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
        probe_timeout = self._timeout
        if timeout_seconds is not None:
            probe_timeout = min(float(timeout_seconds), float(self._timeout))
        if probe_timeout <= 0:
            raise OpenClawProviderError(
                f"openclaw gateway call {method} exhausted its readiness budget.",
                status_code=504,
                error_code="OPENCLAW_GATEWAY_TIMEOUT",
            )
        effective_url = self._gateway_url or _DEFAULT_GATEWAY_WS_URL
        cmd = [
            binary, "gateway", "call", method,
            "--url", effective_url,
            "--token", self._token,
            "--json",
            # Keep the CLI's own RPC deadline just beyond the process cap.  The
            # Python subprocess deadline below remains the authoritative total
            # readiness budget and therefore cannot be bypassed by a hung CLI.
            "--timeout", str(max(1, int(probe_timeout * 1000) + 1000)),
        ]
        if params is not None:
            cmd.extend(["--params", json.dumps(params, separators=(",", ":"), sort_keys=True)])
        # Suppress the CLI banner / doctor notes so they don't land on stdout and
        # corrupt the JSON envelope (NO_COLOR alone does not silence them).
        run_env = {
            **os.environ,
            "NO_COLOR": "1",
            "OPENCLAW_HIDE_BANNER": "1",
            "OPENCLAW_SUPPRESS_NOTES": "1",
        }
        try:
            proc = self._run(cmd, capture_output=True, text=True, timeout=probe_timeout, env=run_env)
        except subprocess.TimeoutExpired as exc:
            raise OpenClawProviderError(
                f"openclaw gateway call {method} timed out after {probe_timeout:g}s.",
                status_code=504, error_code="OPENCLAW_GATEWAY_TIMEOUT",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise OpenClawProviderError(
                f"openclaw gateway call {method} failed: {exc}",
                status_code=502, error_code="OPENCLAW_GATEWAY_INVOCATION_FAILED",
            ) from exc
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            raise OpenClawProviderError(
                f"openclaw gateway call {method} exited {proc.returncode}: {stderr[:400]}",
                status_code=502, error_code="OPENCLAW_GATEWAY_INVOCATION_FAILED",
            )
        raw = self._extract_gateway_json(proc.stdout or "")
        return raw

    @staticmethod
    def _extract_gateway_json(stdout: str) -> Dict[str, Any]:
        """Extract the first complete JSON value from `gateway call --json` stdout.

        The CLI may still prepend banner/doctor/migration noise (multi-line, with
        box-drawing chars) before the JSON, and the payload itself is often
        pretty-printed across many lines. A whole-string ``json.loads`` or a
        single-line scan both fail on that shape, so we scan forward from each
        ``{``/``[`` and use ``raw_decode`` to grab the first well-formed value,
        ignoring any trailing noise. Objects are returned as-is; a top-level array
        (e.g. ``agents list``) is wrapped as ``{"result": [...]}``.
        """
        text = (stdout or "").strip()
        if not text:
            return {}
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(text):
            if ch not in "{[":
                continue
            try:
                value, _ = decoder.raw_decode(text[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
            return {"result": value}
        raise OpenClawProviderError(
            f"openclaw gateway call returned non-JSON output: {text[:200]}",
            status_code=502, error_code="OPENCLAW_GATEWAY_SERIALIZATION_FAILURE",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _readiness_answer_timeout_seconds(self) -> float:
        raw = os.getenv(
            "OPENCLAW_ASSISTANT_READINESS_TIMEOUT_SECONDS",
            str(DEFAULT_READINESS_ANSWER_TIMEOUT_SECONDS),
        )
        try:
            configured = float(raw)
        except (TypeError, ValueError):
            configured = float(DEFAULT_READINESS_ANSWER_TIMEOUT_SECONDS)
        return min(float(self._timeout), max(1.0, configured))

    @staticmethod
    def _result_text(result: OpenClawProviderResult) -> str:
        output = result.output if isinstance(result.output, dict) else {}
        for event in output.get("json_events") or []:
            if not isinstance(event, dict):
                continue
            item = event.get("item")
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                if text:
                    return text
        return ""

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

    def _http_base(self) -> str:
        """HTTP base URL for the gateway's OpenAI-compatible endpoints (ws -> http)."""
        base = self._gateway_url or _DEFAULT_GATEWAY_WS_URL
        if base.startswith("ws://"):
            base = "http://" + base[len("ws://"):]
        elif base.startswith("wss://"):
            base = "https://" + base[len("wss://"):]
        return base.rstrip("/")

    def stream(
        self,
        prompt: str,
        *,
        mode: str = "user",
        operator_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        session_user: Optional[str] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Stream an agent turn via the gateway OpenAI-compatible `POST /v1/responses`
        (OpenResponses) endpoint with SSE, yielding NORMALIZED events:

            {"type": "delta", "text": "<token chunk>"}
            {"type": "done", "text": "<full reply>", "elapsed_ms": N, "transport": "responses_http"}
            {"type": "error", "error_code": "...", "message": "..."}

        The endpoint runs a normal Gateway agent run (workspace/memory/persona/tools
        preserved). Requires the gateway-side `gateway.http.endpoints.responses.enabled`.
        """
        if delegates_kernel_mode_to_codex(mode):
            raise OpenClawProviderError(
                "OpenClaw kernel modes must be delegated to the adapter Codex runtime.",
                status_code=409,
                error_code="OPENCLAW_KERNEL_DELEGATION_REQUIRED",
            )
        if not self._gateway_url:
            yield {"type": "error", "error_code": "OPENCLAW_GATEWAY_URL_NOT_SET",
                   "message": "OPENCLAW_GATEWAY_URL is not set."}
            return
        if not self._token:
            yield {"type": "error", "error_code": "OPENCLAW_TOKEN_NOT_CONFIGURED",
                   "message": "OPENCLAW_GATEWAY_TOKEN is not set."}
            return

        url = f"{self._http_base()}/v1/responses"
        payload: Dict[str, Any] = {
            "model": f"{OPENCLAW_PROVIDER}/{self._agent_id}",
            "input": prompt,
            "stream": True,
        }
        # Stable session key for warm multi-turn routing (per OpenResponses `user`).
        if session_user:
            payload["user"] = session_user
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )

        started_at = time.monotonic()
        chunks: List[str] = []
        emitted_done = False
        try:
            resp = urllib.request.urlopen(req, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            code = "OPENCLAW_RESPONSES_DISABLED" if exc.code == 404 else "OPENCLAW_RESPONSES_HTTP_ERROR"
            yield {"type": "error", "error_code": code,
                   "message": f"/v1/responses HTTP {exc.code}: {body}"}
            return
        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "error_code": "OPENCLAW_RESPONSES_UNREACHABLE",
                   "message": f"/v1/responses request failed: {exc}"}
            return

        try:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload_str = line[len("data:"):].strip()
                if payload_str == "[DONE]":
                    break
                try:
                    evt = json.loads(payload_str)
                except (ValueError, TypeError):
                    continue
                etype = evt.get("type")
                if etype == "response.output_text.delta":
                    delta = evt.get("delta", "")
                    if delta:
                        chunks.append(delta)
                        yield {"type": "delta", "text": delta}
                elif etype == "response.completed":
                    reply = "".join(chunks)
                    if not reply:
                        yield {
                            "type": "error",
                            "error_code": "OPENCLAW_RESPONSES_EMPTY",
                            "message": "Gateway completed /v1/responses without assistant text.",
                        }
                        return
                    emitted_done = True
                    yield {
                        "type": "done",
                        "text": reply,
                        "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                        "transport": "responses_http",
                    }
                elif etype in ("response.failed", "error"):
                    yield {"type": "error", "error_code": "OPENCLAW_RESPONSES_FAILED",
                           "message": json.dumps(evt)[:300]}
                    return
        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "error_code": "OPENCLAW_RESPONSES_STREAM_INTERRUPTED",
                   "message": f"stream interrupted: {exc}"}
            return
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass

        if not emitted_done:
            # Stream ended (e.g. [DONE]) without an explicit completed event.
            reply = "".join(chunks)
            if not reply:
                yield {
                    "type": "error",
                    "error_code": "OPENCLAW_RESPONSES_EMPTY",
                    "message": "Gateway ended /v1/responses without assistant text.",
                }
                return
            yield {
                "type": "done",
                "text": reply,
                "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                "transport": "responses_http",
            }

    @staticmethod
    def _extract_reply(stdout: str) -> str:
        """Pull the assistant reply out of `openclaw agent --json` stdout.

        2026.6.8 schema: {runId, status, summary, result:{payloads:[{text}],
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
        agent_id: str = DEFAULT_AGENT_ID,
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
            "agent_id": agent_id,
            "request_id": request_id,
            "duration_ms": elapsed_ms,
            "transport": "cli",
        }
        if stderr.strip():
            out["stderr_hint"] = stderr.strip()[:200]
        return out
