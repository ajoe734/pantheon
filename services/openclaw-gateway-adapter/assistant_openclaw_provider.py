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
import http.client
import json
import os
import re
import shutil
import socket
import ssl
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
# Hard bound on the serialized context_pack folded into model-visible
# `input` (see stream()). An oversized pack is explicitly rejected rather
# than silently truncated, so a caller never gets a false "success" while
# the model quietly never saw the tail of its context.
_CONTEXT_PACK_MAX_CHARS = 200_000
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
    normalized = {
        "type": "message",
        "role": item.get("role"),
        "content": _normalize_message_content(item.get("content")),
    }
    if "phase" in item:
        normalized["phase"] = item["phase"]
    return normalized


def _normalize_message_content(content: Any) -> Any:
    """Normalize a Chat-Completions-style `messages[].content` array's parts
    (e.g. `{"type": "text", ...}` / `{"type": "image_url", ...}`) into the
    pinned OpenResponses content-part shapes.

    Only top-level `attachments` used to go through this conversion (via
    `_normalize_attachment_content_part`); multimodal chat *history*
    forwarded via `messages[]` must round-trip through the same
    normalization, not silently keep Chat-format part shapes the pinned
    Gateway's `.strict()` schema rejects. A bare string `content` (the
    common single-turn shape) and an already-Responses-shaped part pass
    through unchanged.
    """
    if not isinstance(content, list):
        return content
    normalized_parts: List[Any] = []
    for part in content:
        if not isinstance(part, dict):
            normalized_parts.append(part)
            continue
        part_type = part.get("type")
        if part_type == "text":
            normalized_parts.append({"type": "input_text", "text": part.get("text", "")})
            continue
        if part_type in ("input_text", "input_image", "input_file"):
            normalized_parts.append(part)
            continue
        converted = _normalize_attachment_content_part(part)
        normalized_parts.append(converted if converted is not None else part)
    return normalized_parts


def _normalize_attachment_content_part(attachment: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert one caller-supplied attachment into a pinned OpenResponses
    `input_image`/`input_file` content part.

    Callers/the BFF forward attachments in the Chat-Completions-style shape
    already used elsewhere in this adapter (`{"type": "image_url",
    "image_url": {"url": "data:<mime>;base64,<...>"}}` — see
    `assistant_codex_provider.py`'s `_collect_image_parts`), not the
    Responses-API `input_image`/`input_file` shape the pinned Gateway's
    `.strict()` schema actually requires. An already-shaped part passes
    through unchanged; anything unrecognized is dropped rather than
    corrupting the request body.
    """
    if not isinstance(attachment, dict):
        return None
    kind = attachment.get("type")
    if kind in ("input_image", "input_file"):
        return attachment
    if kind == "image_url":
        image_url = attachment.get("image_url")
        url = str(image_url.get("url") or "") if isinstance(image_url, dict) else ""
        if url.startswith("data:") and "," in url:
            header, _, b64 = url.partition(",")
            if not b64:
                return None
            mime = header[len("data:"):].split(";")[0].strip().lower() or "image/png"
            return {"type": "input_image", "source": {"type": "base64", "media_type": mime, "data": b64}}
        if url:
            return {"type": "input_image", "source": {"type": "url", "url": url}}
        return None
    if kind == "file":
        file_part = attachment.get("file")
        if not isinstance(file_part, dict):
            return None
        data_url = str(file_part.get("file_data") or "")
        if not (data_url.startswith("data:") and "," in data_url):
            return None
        header, _, b64 = data_url.partition(",")
        if not b64:
            return None
        mime = header[len("data:"):].split(";")[0].strip().lower() or "application/octet-stream"
        source: Dict[str, Any] = {"type": "base64", "media_type": mime, "data": b64}
        filename = file_part.get("filename")
        if filename:
            source["filename"] = filename
        return {"type": "input_file", "source": source}
    return None


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
    if not (tenant or actor or conversation):
        return None
    # A bare "|".join would let a boundary-shifting caller-chosen component
    # (e.g. a conversation name containing "|") collide onto the same joined
    # string as a different tenant/actor/conversation split — escape the
    # separator (and its own escape char) inside each component first so the
    # unescaped "|" only ever marks a genuine component boundary. Each of the
    # three slots (tenant, actor, conversation) must also be encoded by
    # *position*, not filtered out when absent: dropping an empty slot before
    # joining would collapse distinct identity shapes onto the same string,
    # e.g. (tenant="alice", actor="bob", conversation="") and
    # (tenant="", actor="alice", conversation="bob") would otherwise both
    # join to "alice|bob". Always emit exactly three components (using ""
    # for a genuinely-absent slot) so which slot was absent is preserved.
    return "|".join(
        part.replace("\\", "\\\\").replace("|", "\\|")
        for part in (tenant, actor, conversation)
    )


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


def _validate_extraction_numeric_bounds(value: Any, schema: Dict[str, Any], path: str) -> None:
    minimum = schema.get("minimum")
    if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and value < minimum:
        raise _schema_mismatch(path, f"is below the declared minimum {minimum!r}")
    maximum = schema.get("maximum")
    if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and value > maximum:
        raise _schema_mismatch(path, f"is above the declared maximum {maximum!r}")
    exclusive_minimum = schema.get("exclusiveMinimum")
    if isinstance(exclusive_minimum, (int, float)) and not isinstance(exclusive_minimum, bool) and value <= exclusive_minimum:
        raise _schema_mismatch(path, f"is not above the declared exclusiveMinimum {exclusive_minimum!r}")
    exclusive_maximum = schema.get("exclusiveMaximum")
    if isinstance(exclusive_maximum, (int, float)) and not isinstance(exclusive_maximum, bool) and value >= exclusive_maximum:
        raise _schema_mismatch(path, f"is not below the declared exclusiveMaximum {exclusive_maximum!r}")


def _validate_extraction_string_constraints(value: str, schema: Dict[str, Any], path: str) -> None:
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and not isinstance(min_length, bool) and len(value) < min_length:
        raise _schema_mismatch(path, f"is shorter than the declared minLength {min_length!r}")
    max_length = schema.get("maxLength")
    if isinstance(max_length, int) and not isinstance(max_length, bool) and len(value) > max_length:
        raise _schema_mismatch(path, f"is longer than the declared maxLength {max_length!r}")
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            if re.search(pattern, value) is None:
                raise _schema_mismatch(path, f"does not match the declared pattern {pattern!r}")
        except re.error:
            raise _schema_mismatch(path or "root", "declares a malformed pattern")


def _validate_extraction_array(value: Any, schema: Dict[str, Any], path: str) -> None:
    if not isinstance(value, list):
        raise _schema_mismatch(path or "root", "must be a JSON array")
    min_items = schema.get("minItems")
    if isinstance(min_items, int) and not isinstance(min_items, bool) and len(value) < min_items:
        raise _schema_mismatch(path or "root", f"has fewer items than the declared minItems {min_items!r}")
    max_items = schema.get("maxItems")
    if isinstance(max_items, int) and not isinstance(max_items, bool) and len(value) > max_items:
        raise _schema_mismatch(path or "root", f"has more items than the declared maxItems {max_items!r}")
    items_schema = schema.get("items")
    if items_schema is None:
        return
    if not isinstance(items_schema, dict):
        raise _schema_mismatch(path or "root", "declares a malformed items schema")
    for index, item in enumerate(value):
        _validate_extraction_value(item, items_schema, f"{path}[{index}]" if path else f"[{index}]")


def _json_schema_value_equal(value: Any, candidate: Any) -> bool:
    """Type-sensitive, recursive equality for `enum`/`const` comparison.

    Plain Python `==` treats `True == 1` and `False == 0` as truthy, which
    would let a boolean silently satisfy a numeric `enum`/`const` (and vice
    versa). JSON Schema treats `true`/`false` as a distinct type from
    numbers, so a boolean and a non-boolean number must never compare equal
    here even though Python's own `==` would say so. Python's `==` on
    `dict`/`list` recurses using plain `==` on the members, which reintroduces
    the exact same bool/number confusion one level down (e.g.
    `{"x": True} == {"x": 1}` is `True` in Python) — object and array members
    are therefore compared recursively through this same function instead of
    delegating to `==`.
    """
    if isinstance(value, bool) or isinstance(candidate, bool):
        return isinstance(value, bool) and isinstance(candidate, bool) and value == candidate
    if isinstance(value, (int, float)) and isinstance(candidate, (int, float)):
        return value == candidate
    if isinstance(value, dict) and isinstance(candidate, dict):
        return value.keys() == candidate.keys() and all(
            _json_schema_value_equal(value[key], candidate[key]) for key in value
        )
    if isinstance(value, list) and isinstance(candidate, list):
        return len(value) == len(candidate) and all(
            _json_schema_value_equal(item, other) for item, other in zip(value, candidate)
        )
    return type(value) is type(candidate) and value == candidate


# JSON-Schema keywords this dependency-free validator understands. A schema
# that declares any other keyword (`anyOf`, `oneOf`, `not`, `format`,
# `multipleOf`, `patternProperties`, `$ref`, ...) is rejected explicitly
# instead of silently ignoring a constraint the caller believed was
# enforced, which would let arguments that violate that constraint reach
# the downstream extraction task undetected.
_SUPPORTED_SCHEMA_KEYWORDS = {
    "type",
    "enum",
    "const",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "items",
    "properties",
    "required",
    "additionalProperties",
    "description",
    "title",
}


def _reject_unsupported_schema_keywords(schema: Dict[str, Any], path: str) -> None:
    unsupported = sorted(set(schema.keys()) - _SUPPORTED_SCHEMA_KEYWORDS)
    if unsupported:
        raise _schema_mismatch(path or "root", f"declares unsupported schema keyword(s) {unsupported!r}")


def _validate_extraction_value(value: Any, schema: Dict[str, Any], path: str) -> None:
    if not isinstance(schema, dict):
        raise _schema_mismatch(path or "root", "declares an unsupported or malformed schema")
    _reject_unsupported_schema_keywords(schema, path)

    if "const" in schema:
        const_value = schema["const"]
        if not _json_schema_value_equal(value, const_value):
            raise _schema_mismatch(path, f"does not equal the declared const {const_value!r}")

    enum_values = schema.get("enum")
    if enum_values is not None:
        if not isinstance(enum_values, list):
            raise _schema_mismatch(path or "root", "declares a malformed enum (must be a list)")
        if not any(_json_schema_value_equal(value, candidate) for candidate in enum_values):
            raise _schema_mismatch(path, f"is not one of the declared enum values {enum_values!r}")

    declared_type = schema.get("type")
    if declared_type is not None:
        types = declared_type if isinstance(declared_type, list) else [declared_type]
        if not isinstance(declared_type, (str, list)) or any(not isinstance(t, str) for t in types):
            raise _schema_mismatch(path or "root", f"declares an unsupported type {declared_type!r}")
        if not _json_value_matches_type(value, declared_type):
            raise _schema_mismatch(path, f"expected type {declared_type!r}")

    # Constraints below key off the *actual runtime type of `value`*, not the
    # declared `type` — a schema that omits `type` entirely (e.g. only
    # `properties`/`minimum`/`minLength`/`additionalProperties`) still
    # constrains whatever value actually shows up, per plain JSON Schema
    # semantics; returning early here previously let a value satisfy every
    # keyword except an omitted `type` unconditionally. `value is None` is
    # only reached for an actual JSON null (either an untyped schema or a
    # matched nullable union member) and no constraint below applies to it
    # (e.g. `type: ["number","null"], minimum: 0` with value `None` must
    # pass, not crash comparing `None < 0`).
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, dict):
        _validate_extraction_object(value, schema, path)
    elif isinstance(value, list):
        _validate_extraction_array(value, schema, path)
    elif isinstance(value, (int, float)):
        _validate_extraction_numeric_bounds(value, schema, path)
    elif isinstance(value, str):
        _validate_extraction_string_constraints(value, schema, path)


def _validate_extraction_object(value: Any, schema: Dict[str, Any], path: str) -> None:
    if not isinstance(value, dict):
        raise _schema_mismatch(path or "root", "must be a JSON object")
    required = schema.get("required", []) or []
    if not isinstance(required, list):
        raise _schema_mismatch(path or "root", "declares a malformed required list")
    for field in required:
        if not isinstance(field, str):
            raise _schema_mismatch(path or "root", "declares a malformed required list entry (must be a string)")
        if field not in value:
            raise _schema_mismatch(path, f"is missing required field {field!r}")
    properties = schema.get("properties", {}) or {}
    if not isinstance(properties, dict):
        raise _schema_mismatch(path or "root", "declares a malformed properties map")
    # `additionalProperties` defaults to permissive (True) per JSON Schema —
    # only an explicit `false` (or a schema for validating the extra
    # fields) tightens this.
    additional_properties = schema.get("additionalProperties", True)
    for name, field_value in value.items():
        prop_schema = properties.get(name)
        field_path = f"{path}.{name}" if path else name
        if not isinstance(prop_schema, dict):
            if additional_properties is False:
                raise _schema_mismatch(field_path, "is not declared and additionalProperties is false")
            if isinstance(additional_properties, dict):
                _validate_extraction_value(field_value, additional_properties, field_path)
            continue
        _validate_extraction_value(field_value, prop_schema, field_path)


def _validate_extraction_arguments(parsed_arguments: Any, extraction_schema: Dict[str, Any]) -> None:
    """Dependency-free structural check of tool-call arguments against a schema.

    Deliberately not a general JSON-schema validator (``jsonschema`` is not a
    dependency of this service) — checks required-field presence (recursing
    into nested object properties), array `items`/`minItems`/`maxItems`,
    numeric `minimum`/`maximum`/`exclusiveMinimum`/`exclusiveMaximum` bounds,
    `additionalProperties: false` rejection, a rough type match for
    properties that declare a JSON ``type`` (including a nullable
    ``type: [<t>, "null"]`` union), and ``enum`` membership. An
    unsupported/malformed schema (bad ``type``/``required``/``properties``/
    ``items``/``enum`` shape) is rejected explicitly with a typed 422 rather
    than crashing with an unhandled 500.
    """
    # Root-level validation goes through `_validate_extraction_value`, not
    # `_validate_extraction_object` directly, so a root-level `enum` (e.g.
    # `{type: "object", enum: [{"kind": "ok"}]}`) is actually enforced even
    # though the root value is also type `object` — calling the object
    # validator directly would skip the enum check entirely.
    _validate_extraction_value(parsed_arguments, extraction_schema, "")


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


class _DeadlineBoundedSocket(socket.socket):
    """A `socket.socket` subclass whose `recv`/`recv_into` re-check an
    absolute deadline and shrink the effective per-call timeout to the
    remaining budget before every blocking read.

    A single static `timeout=` on `urlopen()` only bounds each individual
    recv; a "slow drip" sender that keeps returning a few bytes just under
    that per-call timeout, repeatedly, never trips it, and can hold the
    connection open far past the caller's actual total budget -- both
    while blocked parsing response headers (before `urlopen()` ever
    returns control) and while reading an `HTTPError` body via `.read()`.
    Re-homing the connection's socket onto this subclass right after
    connect() makes every subsequent recv shrink its own timeout to
    whatever is left of the deadline, so the total time spent blocked in
    header parsing or body reads (success or error) is bounded by the same
    absolute deadline the SSE line-reading loop already uses.
    """

    _pantheon_deadline: float = float("inf")

    def _pantheon_check_deadline(self) -> None:
        remaining = self._pantheon_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("bounded deadline exceeded while reading the response")
        self.settimeout(remaining)

    def recv(self, *args: Any, **kwargs: Any) -> bytes:
        self._pantheon_check_deadline()
        return super().recv(*args, **kwargs)

    def recv_into(self, *args: Any, **kwargs: Any) -> int:
        self._pantheon_check_deadline()
        return super().recv_into(*args, **kwargs)


def _rebind_socket_to_deadline(sock: socket.socket, deadline: float) -> "_DeadlineBoundedSocket":
    """Re-home an already-connected plain socket onto `_DeadlineBoundedSocket`
    (same underlying fd) so its subsequent recv calls enforce `deadline`.
    """
    original_timeout = sock.gettimeout()
    fd = sock.detach()
    bounded = _DeadlineBoundedSocket(sock.family, sock.type, sock.proto, fileno=fd)
    bounded._pantheon_deadline = deadline
    bounded.settimeout(original_timeout)
    return bounded


class _DeadlineBoundedHTTPConnection(http.client.HTTPConnection):
    """Plain-HTTP connection whose socket is deadline-bound after connect()
    so header parsing and body reads all share one absolute deadline with
    connection establishment, instead of each only being bounded by its own
    static per-call `timeout=`.
    """

    _pantheon_deadline: float = float("inf")

    def connect(self) -> None:
        super().connect()
        self.sock = _rebind_socket_to_deadline(self.sock, self._pantheon_deadline)


class _DeadlineBoundedSSLSocket(ssl.SSLSocket):
    """`ssl.SSLSocket` subclass whose `recv`/`recv_into` enforce the same
    absolute deadline as `_DeadlineBoundedSocket`.

    `ssl.SSLSocket.recv`/`recv_into` are plain Python methods (they call
    into the C-level `_ssl` module themselves, but the methods a caller
    actually invokes are overridable Python methods on this class), so
    installing this subclass as `SSLContext.sslsocket_class` before
    `wrap_socket()` — rather than trying to patch the post-handshake
    instance in place — is enough to bound every post-handshake read the
    same way the plain-HTTP path already bounds its reads.
    """

    _pantheon_deadline: float = float("inf")

    def _pantheon_check_deadline(self) -> None:
        remaining = self._pantheon_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("bounded deadline exceeded while reading the response")
        self.settimeout(remaining)

    def recv(self, *args: Any, **kwargs: Any) -> bytes:
        self._pantheon_check_deadline()
        return super().recv(*args, **kwargs)

    def recv_into(self, *args: Any, **kwargs: Any) -> int:
        self._pantheon_check_deadline()
        return super().recv_into(*args, **kwargs)


class _DeadlineBoundedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection whose socket is deadline-bound end-to-end.

    The pre-TLS TCP socket is deadline-bound the same way the plain-HTTP
    path is. The post-handshake socket is deadline-bound too: `wrap_socket()`
    is asked to produce a `_DeadlineBoundedSSLSocket` instance (via
    `SSLContext.sslsocket_class`, the documented extension point for this
    since Python 3.7) instead of the default `ssl.SSLSocket`, so header
    parsing and body reads over TLS share the same absolute deadline as
    connection establishment -- closing the "slow drip past the static
    per-read timeout" gap this class used to leave open on the HTTPS path.
    """

    _pantheon_deadline: float = float("inf")

    def connect(self) -> None:
        raw = socket.create_connection(
            (self.host, self.port), self.timeout, self.source_address
        )
        self.sock = _rebind_socket_to_deadline(raw, self._pantheon_deadline)
        if getattr(self, "_tunnel_host", None):
            self._tunnel()
        context = self._context or ssl.create_default_context()
        # Setting this to the same class on every call is idempotent even if
        # `context` is a shared/reused `SSLContext` across concurrent calls;
        # the actual per-call deadline is set on the returned instance below,
        # never on the class or the context, so it cannot leak between calls.
        context.sslsocket_class = _DeadlineBoundedSSLSocket
        wrapped = context.wrap_socket(self.sock, server_hostname=self.host)
        wrapped._pantheon_deadline = self._pantheon_deadline
        self.sock = wrapped


def _deadline_bound_http_connection_factory(deadline: float):
    """Build a per-call factory that produces a fresh, instance-scoped
    `_DeadlineBoundedHTTPConnection` — the deadline lives on the returned
    instance, never on the shared class, so it cannot leak to any other
    call.
    """

    def factory(host: str, **kwargs: Any) -> _DeadlineBoundedHTTPConnection:
        conn = _DeadlineBoundedHTTPConnection(host, **kwargs)
        conn._pantheon_deadline = deadline
        return conn

    return factory


def _deadline_bound_https_connection_factory(deadline: float):
    def factory(host: str, **kwargs: Any) -> _DeadlineBoundedHTTPSConnection:
        conn = _DeadlineBoundedHTTPSConnection(host, **kwargs)
        conn._pantheon_deadline = deadline
        return conn

    return factory


class _DeadlineBoundedHTTPHandler(urllib.request.HTTPHandler):
    """Per-request HTTP handler that builds a fresh deadline-bound
    connection for each call instead of mutating the process-global
    `http.client.HTTPConnection` class.

    The previous implementation temporarily replaced
    `http.client.HTTPConnection`/`HTTPSConnection` for the duration of one
    `urlopen()` call. That mutated shared process-global state: two
    overlapping calls A and B interleaving as A.enter, B.enter, A.exit,
    B.exit meant A.exit restored the pre-A classes (silently discarding
    B's still-active deadline override for any connection B opens after
    that point), and B.exit then restored *A's* classes permanently (since
    that is what B's `__enter__` had captured as "original"), leaving every
    later, wholly unrelated request bound by A's already-expired deadline.
    Building a private `OpenerDirector` per call (see
    `_urlopen_with_deadline` below) means each call's deadline lives only
    on the connection instances it creates -- there is no shared class to
    race over, so overlapping calls (and any request made after either one
    finishes) cannot observe another call's deadline at all.
    """

    def __init__(self, deadline: float) -> None:
        super().__init__()
        self._factory = _deadline_bound_http_connection_factory(deadline)

    def http_open(self, req: "urllib.request.Request") -> Any:
        return self.do_open(self._factory, req)


class _DeadlineBoundedHTTPSHandler(urllib.request.HTTPSHandler):
    """SIMPLIFY-OPENCLAW-001 reviewer defect (fifth corrective pass): this
    previously passed `check_hostname=self._check_hostname` to `do_open()`,
    which forwards its `**http_conn_args` straight to the connection class
    constructor. `HTTPSHandler` never sets a `self._check_hostname`
    attribute (that only exists on the base `urllib.request.HTTPSHandler` in
    some stdlib versions as a local `__init__` variable, never as an
    instance attribute) and `http.client.HTTPSConnection.__init__` does not
    accept a `check_hostname` keyword at all — every real HTTPS request hit
    an unconditional `AttributeError` before ever reaching the network,
    which the caller's broad exception handling then misreported as
    `OPENCLAW_RESPONSES_UNREACHABLE` instead of surfacing the real defect.
    Hostname verification is controlled entirely through `self._context`
    (an `ssl.SSLContext`'s own `check_hostname`/`verify_mode`), which is
    already passed through below.
    """

    def __init__(self, deadline: float, context: Optional[ssl.SSLContext] = None) -> None:
        super().__init__(context=context)
        self._factory = _deadline_bound_https_connection_factory(deadline)

    def https_open(self, req: "urllib.request.Request") -> Any:
        return self.do_open(
            self._factory,
            req,
            context=self._context,
        )


def _urlopen_with_deadline(req: "urllib.request.Request", *, timeout: float, deadline: float) -> Any:
    """Equivalent to `urllib.request.urlopen(req, timeout=timeout)`, but
    connection establishment, header parsing, and any error-body read are
    all bound by the same absolute `deadline` via a request-scoped opener
    -- no process-global `http.client` state is mutated.
    """
    opener = urllib.request.build_opener(
        _DeadlineBoundedHTTPHandler(deadline),
        _DeadlineBoundedHTTPSHandler(deadline),
    )
    return opener.open(req, timeout=timeout)


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
        return [primary]

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
        attachments: Optional[List[Dict[str, Any]]] = None,
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
                attachments=attachments,
                context_pack=context_pack,
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
        attachments: Optional[List[Dict[str, Any]]] = None,
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
                attachments=attachments,
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
            attachments=attachments,
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
        # A pinned client-side `tool_choice` only requests a preference — it
        # is not proof the upstream Gateway actually enforced a single-tool
        # policy. Checking only `function_calls[0]` would silently ignore
        # any additional call the model emitted (e.g. a native/domain tool
        # invoked alongside the requested one); every emitted call must be
        # named `emit_extraction`, and there must be exactly one, or this
        # fails closed rather than trusting the first entry alone.
        if len(function_calls) > 1:
            raise OpenClawProviderError(
                f"expected exactly one {EMIT_EXTRACTION_TOOL_NAME!r} tool call, got {len(function_calls)}",
                status_code=502,
                error_code="OPENCLAW_TOOL_MISMATCH",
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
        attachments: Optional[List[Dict[str, Any]]] = None,
        context_pack: Optional[Dict[str, Any]] = None,
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
        if not _AGENT_ID_PATTERN.fullmatch(effective_agent_id):
            yield {
                "type": "error",
                "error_code": "OPENCLAW_AGENT_ID_INVALID",
                "status_code": 422,
                "message": "OpenClaw agent_id contains unsupported characters.",
            }
            return
        effective_timeout = float(timeout_seconds) if timeout_seconds is not None else float(self._timeout)
        if effective_timeout <= 0:
            yield {
                "type": "error",
                "error_code": "OPENCLAW_GATEWAY_TIMEOUT",
                "status_code": 504,
                "message": "openclaw agent invocation exhausted its bounded deadline.",
            }
            return

        # The real Gateway builds model context only from `input`/`instructions`
        # -- it never reads arbitrary `metadata` -- so a `context_pack` folded
        # only into `metadata` (as this used to do, truncated at 4000 chars)
        # is never actually seen by the model, truncated or not. It must
        # instead be folded into the genuinely model-visible `input`. Rather
        # than silently truncating an oversized pack (which would report
        # success while quietly dropping model-visible context), an
        # over-budget pack is explicitly rejected.
        context_pack_input_item: Optional[Dict[str, Any]] = None
        if context_pack:
            try:
                serialized_context = json.dumps(context_pack, sort_keys=True, default=str)
            except (TypeError, ValueError):
                serialized_context = None
            if serialized_context:
                if len(serialized_context) > _CONTEXT_PACK_MAX_CHARS:
                    yield {
                        "type": "error",
                        "error_code": "OPENCLAW_CONTEXT_PACK_TOO_LARGE",
                        "status_code": 413,
                        "message": (
                            f"context_pack serializes to {len(serialized_context)} chars, "
                            f"exceeding the {_CONTEXT_PACK_MAX_CHARS}-char bound; rejecting "
                            "rather than silently truncating model-visible context."
                        ),
                    }
                    return
                context_pack_input_item = {
                    "type": "message",
                    "role": "system",
                    "content": [{"type": "input_text", "text": f"context_pack: {serialized_context}"}],
                }

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
        attachment_parts = [
            part
            for part in (_normalize_attachment_content_part(a) for a in (attachments or []))
            if part is not None
        ]
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
            if attachment_parts:
                # Attach to the trailing user message's content — the
                # Gateway's `MessageItemSchema.content` is a union of a bare
                # string or a content-part array, so a string content must be
                # converted to a `[input_text, ...]` array before appending
                # the attachment parts, never silently dropped.
                trailing = input_list[-1] if input_list else None
                if isinstance(trailing, dict) and trailing.get("type") == "message" and str(trailing.get("role")) == "user":
                    content = trailing.get("content")
                    if isinstance(content, list):
                        trailing["content"] = content + attachment_parts
                    else:
                        text_parts = [{"type": "input_text", "text": str(content)}] if content else []
                        trailing["content"] = text_parts + attachment_parts
                else:
                    input_list = input_list + [{"type": "message", "role": "user", "content": attachment_parts}]
            payload["input"] = input_list
        elif attachment_parts:
            text_parts = [{"type": "input_text", "text": prompt}] if prompt else []
            payload["input"] = [{"type": "message", "role": "user", "content": text_parts + attachment_parts}]
        else:
            payload["input"] = prompt
        if context_pack_input_item is not None:
            if isinstance(payload["input"], list):
                payload["input"] = [context_pack_input_item] + payload["input"]
            else:
                # Bare-string input (no messages/attachments) — convert to a
                # content-part array so the context item and the prompt both
                # ride in the same model-visible `input`.
                payload["input"] = [
                    context_pack_input_item,
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": payload["input"]}],
                    },
                ]
        # Stable session key for warm multi-turn routing (per OpenResponses `user`).
        if session_user:
            payload["user"] = session_user
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        # The pinned Gateway's `metadata` field is the only strict-schema slot
        # for opaque caller context — trace_id is otherwise silently dropped
        # at the HTTP boundary rather than actually reaching the upstream
        # turn. Values must be strings (per the Gateway's
        # `z.record(z.string(), z.string())`). context_pack itself is folded
        # into `input` above (not here) since the model never reads `metadata`.
        request_metadata: Dict[str, str] = {}
        if trace_id:
            request_metadata["trace_id"] = str(trace_id)
        if request_metadata:
            payload["metadata"] = request_metadata
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
        # The same `read_deadline` also has to bound connection establishment,
        # header parsing, and an HTTPError body read (`exc.read()` below) --
        # none of those are covered by the per-line check above, since they
        # all happen before or outside that loop. `_urlopen_with_deadline`
        # opens the request through connection instances built just for this
        # call (see `_DeadlineBoundedHTTPHandler`), so every subsequent recv
        # on the resulting socket (header parse, success body, or error body
        # -- they share the same underlying socket) shrinks its own timeout
        # to whatever is left of `read_deadline`, without mutating any
        # process-global state another overlapping call could observe.
        try:
            resp = _urlopen_with_deadline(req, timeout=effective_timeout, deadline=read_deadline)
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
        # shape); only when it does not parse as a JSON *object* on its own
        # does it get appended to the buffer of a still-incomplete multi-line
        # event, whose join is tried first on the next line before that new
        # line is ever considered standalone — a later fragment that happens
        # to parse alone (e.g. a bare JSON string half of a legitimately
        # split value) must not silently discard genuine buffered content.
        data_buffer: List[str] = []

        def _parse_event(payload: str) -> Optional[Dict[str, Any]]:
            try:
                parsed = json.loads(payload)
            except (ValueError, TypeError):
                return None
            return parsed if isinstance(parsed, dict) else None

        def _flush_buffer() -> Optional[Dict[str, Any]]:
            nonlocal data_buffer
            if not data_buffer:
                return None
            joined = "\n".join(data_buffer)
            data_buffer = []
            if joined == "[DONE]":
                return None
            return _parse_event(joined)

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
                    if nested_status == "failed":
                        return [{
                            "type": "error",
                            "error_code": "OPENCLAW_RESPONSES_FAILED",
                            "message": json.dumps(nested_response)[:300],
                        }], "return"
                    # The real pinned OpenClaw Gateway (v2026.7.1) emits
                    # response.completed with status="incomplete" as the
                    # *normal* tool-call yield whenever the model stops to
                    # hand back a function_call item for the caller's
                    # extraction/action tool -- this is not truncation or
                    # refusal. Only report OPENCLAW_RESPONSES_INCOMPLETE when
                    # there is genuinely no tool call to fall back on.
                    if nested_status == "incomplete" and not function_calls:
                        return [{
                            "type": "error",
                            "error_code": "OPENCLAW_RESPONSES_INCOMPLETE",
                            "message": json.dumps(nested_response)[:300],
                        }], "return"
                    if nested_status not in (None, "completed", "incomplete"):
                        # Any other non-terminal-success status ("cancelled",
                        # "in_progress", etc.) must never be reported as a
                        # successful "done", even when partial text/output
                        # happens to be present.
                        return [{
                            "type": "error",
                            "error_code": "OPENCLAW_RESPONSES_NOT_TERMINAL",
                            "message": json.dumps(nested_response)[:300],
                        }], "return"
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

        def _iter_deadline_bounded_lines(response: Any, deadline: float) -> Iterator[bytes]:
            """Yield raw response lines while keeping the *total* remaining
            time bounded to `deadline`, even when data trickles in with no
            newline for a long time.

            Plain line iteration (`for raw in response`) calls one atomic
            `readline()`-style operation per line: a real socket sending a
            handful of newline-free bytes well inside any single `recv()`'s
            own timeout keeps that one call blocking long after `deadline`
            has passed, because nothing re-checks the clock until the whole
            line finally arrives. Reading through the response's real
            `.read(1)` (when available — it already understands chunked vs.
            close-delimited framing) lets the deadline be rechecked before
            every single byte instead. Test doubles that only implement
            `__iter__` (never block in real time) fall back to plain
            iteration with a per-line check.
            """
            read_one = getattr(response, "read", None)
            if not callable(read_one):
                for raw in response:
                    if time.monotonic() > deadline:
                        raise TimeoutError("bounded deadline exceeded while reading the response body")
                    yield raw
                return
            buf = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError("bounded deadline exceeded while reading the response body")
                chunk = read_one(1)
                if not chunk:
                    if buf:
                        yield bytes(buf)
                    return
                buf += chunk
                if chunk == b"\n":
                    yield bytes(buf)
                    buf = bytearray()

        # Set only once an explicit end-of-stream signal (`[DONE]` or a
        # `response.completed` event) is actually observed. A real EOF with
        # neither — a dropped connection, a client cancellation, a truncated
        # frame — is never a legitimate completion and must not fabricate a
        # "done" from whatever partial text happened to accumulate first.
        stream_terminated_cleanly = False

        try:
            for raw in _iter_deadline_bounded_lines(resp, read_deadline):
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line == "":
                    evt = _flush_buffer()
                    if evt is None:
                        continue
                elif not line.startswith("data:"):
                    # Ignore other SSE fields (event:/id:/retry:/comments).
                    continue
                else:
                    fragment = line[len("data:"):]
                    if fragment.startswith(" "):
                        fragment = fragment[1:]
                    if fragment == "[DONE]" and not data_buffer:
                        stream_terminated_cleanly = True
                        break
                    if data_buffer:
                        # Try completing the buffered multi-line event first.
                        # A later fragment that happens to parse as valid
                        # JSON *on its own* (e.g. a bare JSON string that is
                        # only one physical half of a legally split value)
                        # must not silently discard genuine buffered content
                        # before the join is even attempted.
                        joined_evt = _parse_event("\n".join(data_buffer + [fragment]))
                        if joined_evt is not None:
                            data_buffer = []
                            evt = joined_evt
                        else:
                            solo_evt = _parse_event(fragment)
                            if solo_evt is not None:
                                # The buffered fragment(s) never became valid
                                # JSON on their own — treat them as orphaned/
                                # malformed and start fresh with this new,
                                # independently-valid event.
                                data_buffer = []
                                evt = solo_evt
                            else:
                                data_buffer.append(fragment)
                                continue
                    else:
                        solo_evt = _parse_event(fragment)
                        if solo_evt is None:
                            data_buffer.append(fragment)
                            continue
                        evt = solo_evt

                events_to_yield, control = _handle_event(evt)
                for out_event in events_to_yield:
                    yield out_event
                if control == "return":
                    return
            # End of stream (real EOF, or `[DONE]` above). A legal multi-line
            # event with no trailing blank line separator is still buffered —
            # flush and process it rather than silently dropping real content.
            trailing_evt = _flush_buffer()
            if trailing_evt is not None:
                events_to_yield, control = _handle_event(trailing_evt)
                for out_event in events_to_yield:
                    yield out_event
                if control == "return":
                    return
        except (TimeoutError, socket.timeout):
            yield {
                "type": "error",
                "error_code": "OPENCLAW_RESPONSES_TIMEOUT",
                "status_code": 504,
                "message": "/v1/responses response exceeded its bounded deadline while streaming.",
            }
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
            reply = terminal_text or "".join(chunks)
            if not stream_terminated_cleanly:
                # The connection ended (real EOF) without ever seeing an
                # explicit completion signal — a cancelled/disconnected/
                # truncated run, not a genuine success. Report it truthfully
                # even when partial text was salvaged; never fabricate a
                # "done" for output the Gateway never actually finished.
                if reply:
                    yield {
                        "type": "error",
                        "error_code": "OPENCLAW_RESPONSES_STREAM_INTERRUPTED",
                        "message": "stream ended without a completion signal after partial output.",
                    }
                else:
                    yield {
                        "type": "error",
                        "error_code": "OPENCLAW_RESPONSES_EMPTY",
                        "message": "stream ended without a completion signal and no assistant text.",
                    }
                return
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
