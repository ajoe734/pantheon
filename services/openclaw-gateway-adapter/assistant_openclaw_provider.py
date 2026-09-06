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

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from assistant_provider_usage import provider_usage_snapshot


OPENCLAW_PROVIDER = "openclaw"
OPENCLAW_PROVIDER_ID = "openclaw"
PROVIDER_RUNTIME = "openclaw_gateway_agent_cli"
OPENRESPONSES_MODEL = "openclaw"
DEFAULT_AGENT_ID = "main"
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_READINESS_ANSWER_TIMEOUT_SECONDS = 20
# The gateway's own internal request-lane queueing (observed up to ~12s under
# real concurrent load, logged as "lane wait exceeded ... waitedMs=...") happens
# inside this window but is invisible to this deadline accounting. A primary
# candidate given only ~1.5s never gets a fair chance to even start once the
# gateway is busy, so it fails the same way every time instead of occasionally.
# 5.0s matches the per-candidate budget already reserved for fallbacks below.
_PRIMARY_CANDIDATE_MAX_SECONDS = 5.0
DEFAULT_OPENCLAW_BIN = "openclaw"
DEFAULT_PRIMARY_MODEL = "anthropic/claude-opus-4-8"
DEFAULT_FALLBACK_MODELS = ("openai/gpt-5.6-sol", "openai/gpt-5.5")
CODEX_DELEGATED_KERNEL_MODES = frozenset({"kernel_debug"})
EMIT_EXTRACTION_TOOL_NAME = "emit_extraction"
# Canonical docker-compose service name — used when no URL is configured.
_DEFAULT_GATEWAY_WS_URL = "ws://openclaw-gateway:18789"
_AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_READINESS_SENTINEL = "PANTHEON_PROVIDER_READY"
_READINESS_PROMPT = f"Reply with exactly: {_READINESS_SENTINEL}"


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


def _normalize_input_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one `input[]` entry to the pinned OpenResponses `ItemParam` shape.

    The pinned Gateway's `MessageItemSchema` is `.strict()` and requires a
    discriminating `type: "message"` alongside `role`/`content`; a plain
    `{"role": ..., "content": ...}` dict (the shape callers/history entries
    use today) is rejected outright. Entries that already declare a
    non-message `type` (e.g. a caller replaying `function_call`/
    `function_call_output`/`reasoning` items from a prior turn) are passed
    through unchanged.
    """
    if not isinstance(item, dict) or item.get("type") is not None:
        return item
    normalized = {"type": "message", "role": item.get("role"), "content": item.get("content")}
    if "phase" in item:
        normalized["phase"] = item["phase"]
    return normalized


def derive_session_user(
    *,
    operator_id: Optional[str] = None,
    session_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Derive the upstream OpenResponses `user` key from authenticated identity.

    A caller-supplied ``session_id``/``metadata.session_id`` is only ever the
    *conversation* component. It must never be forwarded verbatim as the sole
    upstream session key: two different tenants/actors reusing the same
    caller-chosen conversation name (e.g. both naming a session "shared")
    would otherwise collide onto the same upstream `user`, cross-pollinating
    warm session routing between them. The authenticated tenant (when known)
    and actor (`operator_id`) are therefore always mixed into the derived key
    ahead of the conversation component.
    """
    metadata = metadata or {}
    tenant = str(metadata.get("tenant_id") or "").strip()
    actor = str(operator_id or "").strip()
    conversation = str(session_id or metadata.get("session_id") or "").strip()
    parts = [part for part in (tenant, actor, conversation) if part]
    if not parts:
        return None
    return "|".join(parts)


def delegates_kernel_mode_to_codex(mode: str) -> bool:
    """Return whether the adapter must use its scoped Codex runtime.

    The upstream OpenClaw agent invocation has no task-worktree or sandbox
    contract.  Kernel debug/repair therefore cannot safely run through that
    transport and are delegated by the adapter route to the Codex runtime.
    """

    return str(mode or "").strip().lower() in CODEX_DELEGATED_KERNEL_MODES



def emit_extraction_tool_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Build the fixed-shape, server-approved `emit_extraction` function tool.

    The caller supplies only the JSON-schema ``parameters`` body describing
    the extracted-fields shape; the tool name/type/description/strict flag
    are fixed so a caller cannot smuggle in an arbitrary shell/tool
    definition. This is a pure, data-emission-only tool — invoking it never
    executes a domain action.
    """
    return {
        "type": "function",
        "name": EMIT_EXTRACTION_TOOL_NAME,
        "description": "Emit only extracted structured data; no domain action is executed.",
        "parameters": schema,
        "strict": True,
    }


_JSON_TYPE_TO_PYTHON = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _schema_mismatch(path: str, detail: str) -> "OpenClawProviderError":
    return OpenClawProviderError(
        f"tool call argument {path!r} {detail}",
        status_code=422,
        error_code="OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH",
    )


def _json_value_matches_type(value: Any, declared_type: Any) -> bool:
    """Check `value` against a JSON-schema `type`, including a nullable
    `type: [<t>, "null"]` union list. Returns False for a single unsupported
    (non-str, non-list) `type` declaration so the caller can reject it
    explicitly instead of silently treating it as unconstrained."""
    if isinstance(declared_type, list):
        return any(_json_value_matches_type(value, t) for t in declared_type)
    if not isinstance(declared_type, str):
        return False
    if declared_type == "null":
        return value is None
    expected_python_type = _JSON_TYPE_TO_PYTHON.get(declared_type)
    if expected_python_type is None:
        return False
    # bool is a subclass of int in Python; a JSON "integer"/"number" field
    # should not silently accept a JSON boolean.
    if declared_type in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, expected_python_type)


def _validate_extraction_value(value: Any, schema: Dict[str, Any], path: str) -> None:
    enum_values = schema.get("enum")
    if enum_values is not None and value not in enum_values:
        raise _schema_mismatch(path, f"is not one of the declared enum values {enum_values!r}")

    declared_type = schema.get("type")
    if declared_type is None:
        # No declared type: still recurse into nested object schemas that
        # only declare `properties`/`required` without a `type: object`.
        if isinstance(value, dict) and ("properties" in schema or "required" in schema):
            _validate_extraction_object(value, schema, path)
        return

    types = declared_type if isinstance(declared_type, list) else [declared_type]
    if "object" in types and isinstance(value, dict):
        _validate_extraction_object(value, schema, path)
        return
    if not _json_value_matches_type(value, declared_type):
        raise _schema_mismatch(path, f"expected type {declared_type!r}")


def _validate_extraction_object(value: Any, schema: Dict[str, Any], path: str) -> None:
    if not isinstance(value, dict):
        raise _schema_mismatch(path or "root", "must be a JSON object")
    required = schema.get("required", []) or []
    for field in required:
        if field not in value:
            raise _schema_mismatch(path, f"is missing required field {field!r}")
    properties = schema.get("properties", {}) or {}
    for name, field_value in value.items():
        prop_schema = properties.get(name)
        if not isinstance(prop_schema, dict):
            continue
        field_path = f"{path}.{name}" if path else name
        _validate_extraction_value(field_value, prop_schema, field_path)


def _validate_extraction_arguments(parsed_arguments: Any, extraction_schema: Dict[str, Any]) -> None:
    """Dependency-free structural check of tool-call arguments against a schema.

    Deliberately not a general JSON-schema validator (``jsonschema`` is not a
    dependency of this service) — checks required-field presence (recursing
    into nested object properties), a rough type match for properties that
    declare a JSON ``type`` (including a nullable ``type: [<t>, "null"]``
    union), and ``enum`` membership. An unsupported/malformed ``type``
    declaration is rejected explicitly rather than silently ignored.
    """
    _validate_extraction_object(parsed_arguments, extraction_schema, "")


def _sanitize_failure_reason(reason: Any, message: Any = None) -> str:
    """Sanitize failure reason so diagnostics surface truthfully without leaking tokens or secrets."""
    combined = f"{str(reason or '')} {str(message or '')}".strip().lower()
    if any(k in combined for k in ("timeout", "timed out", "deadline")):
        return "OPENCLAW_GATEWAY_TIMEOUT"
    if any(k in combined for k in ("connection refused", "unreachable", "econnrefused", "503")):
        return "OPENCLAW_GATEWAY_UNREACHABLE"
    if any(k in combined for k in ("auth", "unauthorized", "expired", "401", "login", "oauth")):
        return "OPENCLAW_AUTH_UNAVAILABLE"
    raw = str(reason or "").strip()
    if raw and re.fullmatch(r"^[A-Za-z0-9_:-]{1,96}$", raw):
        return raw
    digest = hashlib.sha256((str(message or raw)).encode("utf-8")).hexdigest()[:12]
    return f"SHA256_{digest}"


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
        raw = (os.getenv("OPENCLAW_GATEWAY_URL", "") if gateway_url is None else gateway_url).strip()
        # If an http:// URL was configured by mistake, convert it back to ws://.
        if raw.startswith("http://"):
            raw = "ws://" + raw[len("http://"):]
        elif raw.startswith("https://"):
            raw = "wss://" + raw[len("https://"):]
        # Store as-is (empty string = not configured).
        self._gateway_url = raw.rstrip("/")
        self._agent_id = (os.getenv("OPENCLAW_AGENT_ID", DEFAULT_AGENT_ID) if agent_id is None else agent_id).strip()
        self._token = os.getenv("OPENCLAW_GATEWAY_TOKEN", "") if token is None else token
        self._timeout = int(os.getenv("OPENCLAW_ASSISTANT_TIMEOUT_SECONDS", str(timeout_seconds)))
        self._which = _which_func or shutil.which
        self._run = _run_func or subprocess.run
        self._active_model: Optional[str] = None

    @property
    def configured(self) -> bool:
        """True when OPENCLAW_GATEWAY_URL was explicitly configured."""
        return bool(self._gateway_url)

    def _openclaw_bin(self) -> Optional[str]:
        explicit = os.getenv("OPENCLAW_BIN", "").strip()
        if explicit:
            return explicit
        return self._which(DEFAULT_OPENCLAW_BIN)

    def _resolve_model_candidates(
        self, requested_model: Optional[str] = None
    ) -> List[str]:
        if requested_model:
            return [requested_model]
        primary = os.getenv("OPENCLAW_PRIMARY_MODEL", "").strip() or DEFAULT_PRIMARY_MODEL
        fallback_raw = os.getenv("OPENCLAW_FALLBACK_MODELS", "").strip()
        if fallback_raw:
            fallbacks = [m.strip() for m in fallback_raw.split(",") if m.strip()]
        else:
            fallbacks = list(DEFAULT_FALLBACK_MODELS)
        candidates: List[str] = []
        if getattr(self, "_active_model", None):
            candidates.append(self._active_model)
        for m in [primary, *fallbacks]:
            if m and m not in candidates:
                candidates.append(m)
        return candidates

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
        # Full probe: exercise the actual HTTP answer path inside one bounded
        # budget. Ordinary turns never spawn the `openclaw` CLI subprocess, so
        # no binary existence check is needed here anymore.
        if not self._token:
            return {
                **base,
                "ready": False,
                "status": "degraded",
                "reason": "OPENCLAW_GATEWAY_TOKEN_not_set",
                "answer_probe": {
                    "status": "failed",
                    "reason": "OPENCLAW_GATEWAY_TOKEN_not_set",
                    "deadline_seconds": answer_timeout,
                },
            }
        started_at = time.monotonic()
        deadline = started_at + answer_timeout
        candidates = self._resolve_model_candidates()
        primary = candidates[0]
        primary_unavailable: Optional[Dict[str, Any]] = None
        last_exc: Optional[OpenClawProviderError] = None

        for idx, candidate in enumerate(candidates):
            remaining = deadline - time.monotonic()
            if remaining <= 0.2:
                break
            num_remaining_after = len(candidates) - 1 - idx
            if num_remaining_after > 0 and idx == 0:
                cand_timeout = min(_PRIMARY_CANDIDATE_MAX_SECONDS, max(1.0, remaining - (num_remaining_after * 5.0)))
            elif num_remaining_after > 0:
                cand_timeout = max(1.0, min(remaining - (num_remaining_after * 3.0), remaining / (num_remaining_after + 1)))
            else:
                cand_timeout = remaining

            cand_started = time.monotonic()
            try:
                result = self._invoke_via_http(
                    _READINESS_PROMPT,
                    model=candidate,
                    mode="user",
                    operator_id="management-ai-readiness",
                    timeout_seconds=cand_timeout,
                )
                answer = self._result_text(result)
                cand_dur = max(0, int((time.monotonic() - cand_started) * 1000))
                total_dur = max(0, int((time.monotonic() - started_at) * 1000))
                if not answer or answer.strip() != _READINESS_SENTINEL:
                    probe_fail_reason = (
                        "openclaw_answer_probe_empty"
                        if not answer
                        else "openclaw_answer_probe_sentinel_mismatch"
                    )
                    if candidate == primary:
                        primary_unavailable = {
                            "model": primary,
                            "status": "unavailable",
                            "reason": probe_fail_reason,
                            "duration_ms": cand_dur,
                        }
                    last_exc = OpenClawProviderError(
                        f"openclaw answer probe failed: {probe_fail_reason}",
                        status_code=502,
                        error_code=probe_fail_reason,
                    )
                    continue

                is_fallback = candidate != primary
                self._active_model = candidate
                probe_result: Dict[str, Any] = {
                    "status": "completed",
                    "deadline_seconds": answer_timeout,
                    "duration_ms": total_dur,
                    "reply_bytes": len(answer.encode("utf-8")),
                    "active_model": candidate,
                    "primary_model": primary,
                }
                if is_fallback and primary_unavailable:
                    probe_result["fallback_used"] = True
                    probe_result["fallback_model"] = candidate
                    probe_result["primary_unavailable"] = primary_unavailable

                return {
                    **base,
                    "ready": True,
                    "status": "ready",
                    "auth": "account_session",
                    "auth_status": "ready",
                    "active_model": candidate,
                    "primary_model": primary,
                    **(
                        {
                            "primary_unavailable": primary_unavailable,
                            "fallback_used": True,
                            "fallback_model": candidate,
                        }
                        if is_fallback and primary_unavailable
                        else {}
                    ),
                    "answer_probe": probe_result,
                }
            except OpenClawProviderError as exc:
                cand_dur = max(0, int((time.monotonic() - cand_started) * 1000))
                sanitized_reason = _sanitize_failure_reason(exc.error_code or exc.message)
                if candidate == primary:
                    primary_unavailable = {
                        "model": primary,
                        "status": "unavailable",
                        "reason": sanitized_reason,
                        "duration_ms": cand_dur,
                    }
                last_exc = exc
                continue

        self._active_model = None
        total_dur = max(0, int((time.monotonic() - started_at) * 1000))
        reason = last_exc.error_code if last_exc else "OPENCLAW_ALL_MODELS_UNAVAILABLE"
        probe_err: Dict[str, Any] = {
            "status": "failed",
            "reason": reason,
            "deadline_seconds": answer_timeout,
            "duration_ms": total_dur,
            "primary_model": primary,
        }
        if primary_unavailable:
            probe_err["primary_unavailable"] = primary_unavailable

        return {
            **base,
            "ready": False,
            "status": "degraded",
            "reason": reason,
            "primary_model": primary,
            **({"primary_unavailable": primary_unavailable} if primary_unavailable else {}),
            "answer_probe": probe_err,
        }

    def _invoke_via_http(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        mode: str = "user",
        context_pack: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        operator_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> OpenClawProviderResult:
        """Run one ordinary agent turn through the Gateway `POST /v1/responses`
        (OpenResponses) HTTP transport.

        This is the single request builder for `invoke()`, `readiness()`'s
        answer-probe, and `invoke_structured()`. It never spawns a subprocess
        and its transport choice never depends on prompt length — collapsing
        the normalized terminal SSE stream (see `stream()`) back into the
        standard invoke result keeps the BFF's existing adapter contract
        intact and preserves typed Responses failures.
        """

        selected_agent_id = str(agent_id or self._agent_id).strip()
        session_user = derive_session_user(
            operator_id=operator_id, session_id=session_id, metadata=metadata
        )
        events = list(
            self.stream(
                prompt,
                mode=mode,
                operator_id=operator_id,
                trace_id=trace_id,
                session_user=session_user,
                model=model,
                agent_id=selected_agent_id,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                timeout_seconds=timeout_seconds,
            )
        )
        error = next((event for event in events if event.get("type") == "error"), None)
        if error is not None:
            status_code = error.get("status_code")
            try:
                normalized_status = int(status_code) if status_code is not None else 502
            except (TypeError, ValueError):
                normalized_status = 502
            raise OpenClawProviderError(
                str(error.get("message") or "OpenResponses invocation failed."),
                status_code=normalized_status,
                error_code=str(error.get("error_code") or "OPENCLAW_RESPONSES_FAILED"),
            )
        done = next((event for event in reversed(events) if event.get("type") == "done"), None)
        if done is None:
            raise OpenClawProviderError(
                "Gateway completed /v1/responses without a terminal event.",
                status_code=502,
                error_code="OPENCLAW_RESPONSES_EMPTY",
            )
        text = str(done.get("text") or "")
        function_calls = done.get("function_calls")
        if not text.strip() and not function_calls:
            raise OpenClawProviderError(
                "Gateway completed /v1/responses without assistant text.",
                status_code=502,
                error_code="OPENCLAW_RESPONSES_EMPTY",
            )
        output = self._build_output(
            text=text,
            request_id=str(uuid.uuid4()),
            elapsed_ms=max(0, int(done.get("elapsed_ms") or 0)),
            stderr="",
            agent_id=selected_agent_id,
        )
        output["transport"] = "responses_http"
        if model:
            output["active_model"] = model
        if function_calls:
            output["function_calls"] = function_calls
        # Missing usage is unknown, not zero — only set the key when upstream
        # actually reported it.
        if "usage" in done:
            output["usage"] = done["usage"]
        if "response_id" in done:
            output["response_id"] = done["response_id"]
        return OpenClawProviderResult(
            provider=OPENCLAW_PROVIDER,
            mode=mode,
            status="completed",
            output=output,
            redaction={"provider_invocation": {"redacted_fields": 0}},
        )

    def invoke(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
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

        # Ordinary turns go through HTTP only, so the `openclaw` CLI binary is
        # not required here. Bearer auth is still required for the HTTP call.
        if not self._token:
            raise OpenClawProviderError(
                "OPENCLAW_GATEWAY_TOKEN is not set. Configure the token in the compose env.",
                status_code=503,
                error_code="OPENCLAW_TOKEN_NOT_CONFIGURED",
            )

        invocation_timeout = float(self._timeout)
        if timeout_seconds is not None:
            invocation_timeout = min(invocation_timeout, float(timeout_seconds))
        if invocation_timeout <= 0:
            raise OpenClawProviderError(
                "openclaw agent invocation exhausted its bounded deadline.",
                status_code=504,
                error_code="OPENCLAW_GATEWAY_TIMEOUT",
            )

        # Preserve per-agent configured routing: if a non-default agent (e.g. persona agent)
        # is called and no model override was requested, invoke without a model override so
        # OpenClaw uses the model configured in the agent definition.
        if selected_agent_id != DEFAULT_AGENT_ID and model is None:
            return self._invoke_via_http(
                prompt,
                model=None,
                agent_id=selected_agent_id,
                session_id=session_id,
                mode=mode,
                context_pack=context_pack,
                metadata=metadata,
                messages=messages,
                operator_id=operator_id,
                trace_id=trace_id,
                timeout_seconds=invocation_timeout,
            )

        candidates = self._resolve_model_candidates(model)
        selected_model = candidates[0] if candidates else None
        primary_configured = os.getenv("OPENCLAW_PRIMARY_MODEL", "").strip() or DEFAULT_PRIMARY_MODEL

        result = self._invoke_via_http(
            prompt,
            model=selected_model,
            agent_id=selected_agent_id,
            session_id=session_id,
            mode=mode,
            context_pack=context_pack,
            metadata=metadata,
            messages=messages,
            operator_id=operator_id,
            trace_id=trace_id,
            timeout_seconds=invocation_timeout,
        )
        if selected_model:
            self._active_model = selected_model
            if selected_model != primary_configured:
                result.output["fallback_used"] = True
                result.output["primary_model"] = primary_configured
        return result

    def invoke_structured(
        self,
        prompt: str,
        *,
        extraction_schema: Dict[str, Any],
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        mode: str = "user",
        messages: Optional[List[Dict[str, Any]]] = None,
        operator_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> OpenClawProviderResult:
        """Run one restricted, server-approved, data-only extraction turn.

        Uses the same HTTP transport as `invoke()`, with a fixed-shape
        `emit_extraction` function tool and a pinned tool_choice so the model
        cannot decline into free text or pick a different tool. The tool call
        itself never triggers a domain mutation — this method returns parsed
        structured data only.
        """

        tool_schema = emit_extraction_tool_schema(extraction_schema)
        result = self._invoke_via_http(
            prompt,
            model=model,
            agent_id=agent_id,
            session_id=session_id,
            mode=mode,
            messages=messages,
            operator_id=operator_id,
            trace_id=trace_id,
            timeout_seconds=timeout_seconds,
            tools=[tool_schema],
            tool_choice={"type": "function", "name": EMIT_EXTRACTION_TOOL_NAME},
        )
        function_calls = result.output.get("function_calls") or []
        if not function_calls:
            raise OpenClawProviderError(
                "no matching tool call in response",
                status_code=502,
                error_code="OPENCLAW_TOOL_NO_MATCH",
            )
        call = function_calls[0]
        call_name = call.get("name")
        if call_name != EMIT_EXTRACTION_TOOL_NAME:
            raise OpenClawProviderError(
                f"tool call name {call_name!r} does not match {EMIT_EXTRACTION_TOOL_NAME!r}",
                status_code=502,
                error_code="OPENCLAW_TOOL_MISMATCH",
            )
        raw_arguments = call.get("arguments")
        try:
            parsed_arguments = json.loads(raw_arguments if isinstance(raw_arguments, str) else "")
        except (ValueError, TypeError) as exc:
            raise OpenClawProviderError(
                "tool call arguments are not valid JSON",
                status_code=422,
                error_code="OPENCLAW_TOOL_ARGS_INVALID_JSON",
            ) from exc
        _validate_extraction_arguments(parsed_arguments, extraction_schema)

        output: Dict[str, Any] = {
            "structured_data": parsed_arguments,
            "tool_call": {
                "id": call.get("call_id"),
                "name": call_name,
            },
            "agent_id": str(agent_id or self._agent_id).strip(),
            "transport": "responses_http",
        }
        if "usage" in result.output:
            output["usage"] = result.output["usage"]
        if "response_id" in result.output:
            output["response_id"] = result.output["response_id"]
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
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Stream an agent turn via the gateway OpenAI-compatible `POST /v1/responses`
        (OpenResponses) endpoint with SSE, yielding NORMALIZED events:

            {"type": "delta", "text": "<token chunk>"}
            {"type": "done", "text": "<full reply>", "elapsed_ms": N, "transport": "responses_http",
             "function_calls": [...], "usage": {...}, "response_id": "..."}
            {"type": "error", "error_code": "...", "message": "..."}

        `function_calls`/`usage`/`response_id` are only present in the "done"
        event when the upstream Gateway actually reported them — a missing
        `usage` is unknown, not zero cost.

        The endpoint runs a normal Gateway agent run (workspace/memory/persona/tools
        preserved). Requires the gateway-side `gateway.http.endpoints.responses.enabled`.
        Upstream OpenClaw v2026.7.1 contract accepts model 'openclaw' (or 'openclaw/<agentId>').
        This is the single request builder used by ordinary `invoke()`,
        `readiness()`'s answer-probe, and `invoke_structured()` — none of them
        spawn a subprocess or pick a transport based on prompt length.
        """
        if delegates_kernel_mode_to_codex(mode):
            raise OpenClawProviderError(
                "OpenClaw kernel modes must be delegated to the adapter Codex runtime.",
                status_code=409,
                error_code="OPENCLAW_KERNEL_DELEGATION_REQUIRED",
            )
        if not self._gateway_url:
            yield {
                "type": "error",
                "error_code": "OPENCLAW_GATEWAY_URL_NOT_SET",
                "message": "OPENCLAW_GATEWAY_URL is not set.",
            }
            return
        if not self._token:
            yield {
                "type": "error",
                "error_code": "OPENCLAW_TOKEN_NOT_CONFIGURED",
                "message": "OPENCLAW_GATEWAY_TOKEN is not set.",
            }
            return

        effective_agent_id = str(agent_id or self._agent_id).strip()
        effective_timeout = float(timeout_seconds) if timeout_seconds is not None else float(self._timeout)
        if effective_timeout <= 0:
            yield {
                "type": "error",
                "error_code": "OPENCLAW_GATEWAY_TIMEOUT",
                "status_code": 504,
                "message": "openclaw agent invocation exhausted its bounded deadline.",
            }
            return

        url = f"{self._http_base()}/v1/responses"
        # The pinned Gateway's OpenAI-compat model resolver
        # (`resolveOpenAiCompatModelOverride`) only accepts `openclaw` or
        # `openclaw/<agentId>` as the JSON `model` — a raw provider/model id
        # (e.g. "anthropic/claude-opus-4-8") is rejected with HTTP 400. Any
        # requested provider/model override belongs in the `x-openclaw-model`
        # header instead, which the Gateway validates separately against the
        # agent's allowed model visibility policy.
        payload: Dict[str, Any] = {
            "model": f"{OPENRESPONSES_MODEL}/{effective_agent_id}",
            "stream": True,
        }
        if messages:
            input_list: List[Dict[str, Any]] = [_normalize_input_item(m) for m in messages]
            last_entry = input_list[-1] if input_list else None
            already_last = (
                isinstance(last_entry, dict)
                and str(last_entry.get("role")) == "user"
                and str(last_entry.get("content")) == prompt
            )
            if prompt and not already_last:
                input_list = input_list + [_normalize_input_item({"role": "user", "content": prompt})]
            payload["input"] = input_list
        else:
            payload["input"] = prompt
        # Stable session key for warm multi-turn routing (per OpenResponses `user`).
        if session_user:
            payload["user"] = session_user
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-OpenClaw-Agent-Id": effective_agent_id,
        }
        if model:
            headers["X-OpenClaw-Model"] = model
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)

        started_at = time.monotonic()
        # `timeout=` on urlopen only bounds each individual socket recv, not
        # the whole read loop below: a "slow drip" that sends a little data
        # just under that per-read timeout, repeatedly, never trips it, and
        # can keep the read loop going far longer than `effective_timeout` in
        # total (observed: a 0.05s budget completing only after 0.266s real
        # elapsed against a deliberately slow real HTTP server). `read_deadline`
        # is checked once per received SSE line so the *total* streaming time
        # stays bounded even when no single read ever times out on its own.
        read_deadline = started_at + effective_timeout
        chunks: List[str] = []
        terminal_text = ""
        emitted_done = False
        try:
            resp = urllib.request.urlopen(req, timeout=effective_timeout)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            code = "OPENCLAW_RESPONSES_DISABLED" if exc.code == 404 else "OPENCLAW_RESPONSES_HTTP_ERROR"
            yield {
                "type": "error",
                "error_code": code,
                "status_code": exc.code,
                "message": f"/v1/responses HTTP {exc.code}: {body}",
            }
            return
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)):
                yield {
                    "type": "error",
                    "error_code": "OPENCLAW_RESPONSES_TIMEOUT",
                    "status_code": 504,
                    "message": "/v1/responses request timed out.",
                }
            else:
                yield {
                    "type": "error",
                    "error_code": "OPENCLAW_RESPONSES_UNREACHABLE",
                    "status_code": 503,
                    "message": "/v1/responses endpoint could not be reached.",
                }
            return
        except (TimeoutError, socket.timeout):
            yield {
                "type": "error",
                "error_code": "OPENCLAW_RESPONSES_TIMEOUT",
                "status_code": 504,
                "message": "/v1/responses request timed out.",
            }
            return
        except Exception as exc:  # noqa: BLE001
            yield {
                "type": "error",
                "error_code": "OPENCLAW_RESPONSES_UNREACHABLE",
                "status_code": 503,
                "message": "/v1/responses endpoint could not be reached.",
            }
            return

        # SSE per spec: consecutive `data:` lines with no blank line between
        # them belong to the SAME event and are joined with "\n" before
        # parsing; a blank line terminates the current event. Many emitters
        # (including this file's own test fixtures) instead put one complete
        # JSON object per `data:` line with no blank-line separators at all.
        # To support both shapes without misparsing either, each new line is
        # first tried on its own (matching the common single-line-per-event
        # shape, and letting a prior unparseable fragment be discarded rather
        # than corrupt a following, unrelated, well-formed line); only when no
        # individual line parses standalone do accumulated fragments get
        # joined — at the next blank line, or at end-of-stream.
        data_buffer: List[str] = []

        def _flush_buffer() -> Optional[str]:
            nonlocal data_buffer
            if not data_buffer:
                return None
            joined = "\n".join(data_buffer)
            data_buffer = []
            return joined

        def _handle_event(evt: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
            """Process one parsed SSE JSON event.

            Returns `(events_to_yield, control)` where `control` is `None` to
            keep reading, `"return"` to stop reading and end the generator
            (a terminal event was already yielded — the caller must not also
            run the post-loop "no completed event" fallback), or `"stop"` to
            stop reading without ending the generator (used for `[DONE]`,
            handled by the caller directly, not from here).
            """
            nonlocal terminal_text, emitted_done
            etype = evt.get("type")
            if etype == "response.output_text.delta":
                delta = evt.get("delta", "")
                if delta:
                    chunks.append(delta)
                    return [{"type": "delta", "text": delta}], None
                return [], None
            if etype == "response.output_text.done":
                # OpenClaw emits this authoritative full-text snapshot before
                # response.completed.  Some valid runs have no deltas (for
                # example when the upstream adapter only produces a final
                # message), so do not turn that real answer into an empty
                # response merely because incremental events were absent.
                text = evt.get("text")
                if isinstance(text, str) and text.strip():
                    terminal_text = text
                return [], None
            if etype == "response.completed":
                reply = terminal_text or "".join(chunks)
                # ASSUMPTION (not independently verified against a live pinned
                # OpenClaw Gateway in this dev sandbox — no live gateway was
                # reachable here): `response.completed` carries a nested
                # `response` object shaped like the OpenAI Responses API
                # family (`status`, `output[]`, `usage`, `id`). Treat this as
                # an unverified-capability caveat, not a proven contract.
                nested_response = evt.get("response")
                function_calls: List[Dict[str, Any]] = []
                usage: Optional[Dict[str, Any]] = None
                response_id: Optional[str] = None
                if isinstance(nested_response, dict):
                    nested_status = nested_response.get("status")
                    if nested_status in ("failed", "incomplete"):
                        return [{
                            "type": "error",
                            "error_code": (
                                "OPENCLAW_RESPONSES_FAILED"
                                if nested_status == "failed"
                                else "OPENCLAW_RESPONSES_INCOMPLETE"
                            ),
                            "message": json.dumps(nested_response)[:300],
                        }], "return"
                    output_items = nested_response.get("output")
                    if isinstance(output_items, list):
                        for item in output_items:
                            if isinstance(item, dict) and item.get("type") == "function_call":
                                function_calls.append(
                                    {
                                        "name": item.get("name"),
                                        "arguments": item.get("arguments"),
                                        "call_id": item.get("call_id"),
                                    }
                                )
                    if "usage" in nested_response:
                        usage = nested_response.get("usage")
                    response_id = nested_response.get("id")
                if not reply and not function_calls:
                    return [{
                        "type": "error",
                        "error_code": "OPENCLAW_RESPONSES_EMPTY",
                        "message": "Gateway completed /v1/responses without assistant text.",
                    }], "return"
                emitted_done = True
                done_event: Dict[str, Any] = {
                    "type": "done",
                    "text": reply,
                    "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                    "transport": "responses_http",
                }
                if function_calls:
                    done_event["function_calls"] = function_calls
                # Missing usage is unknown, not zero — only include the key
                # when the upstream Gateway actually reported it.
                if usage is not None:
                    done_event["usage"] = usage
                if response_id is not None:
                    done_event["response_id"] = response_id
                # A duplicate response.completed must never re-emit a second
                # "done" — stop reading immediately after the first one.
                return [done_event], "return"
            if etype in ("response.failed", "error"):
                return [{
                    "type": "error",
                    "error_code": "OPENCLAW_RESPONSES_FAILED",
                    "message": json.dumps(evt)[:300],
                }], "return"
            return [], None

        try:
            for raw in resp:
                if time.monotonic() > read_deadline:
                    yield {
                        "type": "error",
                        "error_code": "OPENCLAW_RESPONSES_TIMEOUT",
                        "status_code": 504,
                        "message": "/v1/responses response exceeded its bounded deadline while streaming.",
                    }
                    return
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line == "":
                    payload_str = _flush_buffer()
                    if payload_str is None or payload_str == "[DONE]":
                        continue
                    try:
                        evt = json.loads(payload_str)
                    except (ValueError, TypeError):
                        continue
                elif not line.startswith("data:"):
                    # Ignore other SSE fields (event:/id:/retry:/comments).
                    continue
                else:
                    fragment = line[len("data:"):]
                    if fragment.startswith(" "):
                        fragment = fragment[1:]
                    if fragment == "[DONE]" and not data_buffer:
                        break
                    try:
                        evt = json.loads(fragment)
                    except (ValueError, TypeError):
                        # Not (yet) valid JSON on its own — could be a
                        # malformed line (dropped once a later line parses
                        # standalone) or one physical line of a legal
                        # multi-line event (completed once joined at the
                        # next blank line / end of stream).
                        data_buffer.append(fragment)
                        continue
                    data_buffer = []

                events_to_yield, control = _handle_event(evt)
                for out_event in events_to_yield:
                    yield out_event
                if control == "return":
                    return
            # End of stream (real EOF, or `[DONE]` above). A legal multi-line
            # event with no trailing blank line separator is still buffered —
            # flush and process it rather than silently dropping real content.
            trailing_payload = _flush_buffer()
            if trailing_payload and trailing_payload != "[DONE]":
                try:
                    trailing_evt = json.loads(trailing_payload)
                except (ValueError, TypeError):
                    trailing_evt = None
                if trailing_evt is not None:
                    events_to_yield, control = _handle_event(trailing_evt)
                    for out_event in events_to_yield:
                        yield out_event
                    if control == "return":
                        return
        except Exception as exc:  # noqa: BLE001
            yield {
                "type": "error",
                "error_code": "OPENCLAW_RESPONSES_STREAM_INTERRUPTED",
                "message": f"stream interrupted: {exc}",
            }
            return
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass

        if not emitted_done:
            # Stream ended (e.g. [DONE]) without an explicit completed event.
            reply = terminal_text or "".join(chunks)
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
            return ""
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
