"""Internal OpenAI-compatible chat-completions bridge for Hindsight (design H5).

Hindsight's background model calls (retain extraction, consolidation, reflect,
mental-model refresh) reach the OpenClaw deny-all ``memory-index`` agent through
this bridge instead of holding a provider key. The bridge:

* is disabled unless ``MEMORY_LLM_BRIDGE_ENABLED`` and a dedicated token are set;
* authenticates with its own internal bearer token (never the gateway token);
* always runs the ``memory-index`` agent, whatever ``model`` the caller sends;
* rejects ``tools``/``tool_choice``/``functions`` and streaming, recording a reason;
* enforces a deadline, a concurrency limit and a per-day call budget; refusals
  and upstream quota errors are 429 with ``Retry-After`` (Hindsight defers);
* records operation label, latency, token usage and outcome — never content.

The module is transport-free: ``MemoryLlmBridge.handle`` takes the request
pieces and an ``invoke`` callable, so tests drive it without a gateway.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

MEMORY_INDEX_AGENT_ID = "memory-index"
LOGGER = logging.getLogger("pantheon.memory_llm_bridge")

_REJECTED_FIELDS = ("tools", "tool_choice", "functions", "function_call")
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class BridgeConfig:
    enabled: bool
    token: str
    agent_id: str = MEMORY_INDEX_AGENT_ID
    deadline_seconds: float = 120.0
    max_concurrency: int = 2
    daily_budget: int = 500
    retry_after_seconds: int = 60
    state_path: str = ""

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        return cls(
            enabled=os.getenv("MEMORY_LLM_BRIDGE_ENABLED", "").strip().lower()
            in {"1", "true", "yes", "on"},
            token=os.getenv("MEMORY_LLM_BRIDGE_TOKEN", ""),
            deadline_seconds=float(_env_int("MEMORY_LLM_BRIDGE_DEADLINE_SECONDS", 120)),
            max_concurrency=max(1, _env_int("MEMORY_LLM_BRIDGE_MAX_CONCURRENCY", 2)),
            daily_budget=max(0, _env_int("MEMORY_LLM_BRIDGE_DAILY_BUDGET", 500)),
            retry_after_seconds=max(1, _env_int("MEMORY_LLM_BRIDGE_RETRY_AFTER_SECONDS", 60)),
            state_path=os.getenv("MEMORY_LLM_BRIDGE_STATE_PATH", ""),
        )


@dataclass
class BridgeResponse:
    status_code: int
    body: Dict[str, Any]
    headers: Dict[str, str]


class _DailyBudget:
    """Per-UTC-day call counter; sqlite-backed when a path is configured so a
    restart does not reset the budget."""

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._memory: Dict[str, int] = {}
        self._path = path
        if path:
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE IF NOT EXISTS calls (day TEXT PRIMARY KEY, n INTEGER NOT NULL)")

    def take(self, limit: int, day: str) -> bool:
        with self._lock:
            if self._path:
                with sqlite3.connect(self._path) as db:
                    row = db.execute("SELECT n FROM calls WHERE day=?", (day,)).fetchone()
                    used = row[0] if row else 0
                    if used >= limit:
                        return False
                    db.execute(
                        "INSERT INTO calls(day, n) VALUES(?, 1) "
                        "ON CONFLICT(day) DO UPDATE SET n = n + 1",
                        (day,),
                    )
                    return True
            used = self._memory.get(day, 0)
            if used >= limit:
                return False
            self._memory[day] = used + 1
            return True


def _error(status: int, code: str, message: str, *, retry_after: Optional[int] = None) -> BridgeResponse:
    kind = "rate_limit_error" if status == 429 else "invalid_request_error"
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return BridgeResponse(
        status, {"error": {"message": message, "type": kind, "code": code}}, headers
    )


def _extract_json_text(text: str) -> str:
    match = _FENCE.match(text)
    return (match.group(1) if match else text).strip()


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "") for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        )
    return ""


def _resolve_root(schema: Dict[str, Any]) -> Dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        target = (schema.get("$defs") or {}).get(ref[len("#/$defs/"):])
        if isinstance(target, dict):
            return target
    return schema


def _check_root(parsed: Any, schema: Optional[Dict[str, Any]]) -> Optional[str]:
    """Root-level shape check only; Hindsight validates the full schema itself."""
    if schema is None:
        return None if isinstance(parsed, dict) else "answer is not a JSON object"
    root = _resolve_root(schema)
    expected = root.get("type")
    kinds = {"object": dict, "array": list, "string": str, "number": (int, float),
             "integer": int, "boolean": bool}
    if isinstance(expected, str) and expected in kinds and not isinstance(parsed, kinds[expected]):
        return f"answer root is not of type {expected}"
    required = root.get("required")
    if isinstance(parsed, dict) and isinstance(required, list):
        missing = [key for key in required if key not in parsed]
        if missing:
            return "answer is missing required fields: " + ", ".join(map(str, missing))
    return None


# invoke(messages, instruction, deadline_seconds) -> {"text": str, "usage": dict|None}
InvokeFn = Callable[[List[Dict[str, Any]], str, float], Dict[str, Any]]
PolicyFn = Callable[[str, float], None]


class MemoryLlmBridge:
    def __init__(self, config: BridgeConfig, *, clock: Callable[[], float] = time.monotonic,
                 today: Optional[Callable[[], str]] = None) -> None:
        self.config = config
        self._clock = clock
        self._today = today or (lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self._slots = threading.BoundedSemaphore(config.max_concurrency)
        self._budget = _DailyBudget(config.state_path)
        self.telemetry: List[Dict[str, Any]] = []

    # -- telemetry: operation label, latency, usage, outcome; never content --
    def _record(self, operation: str, started: float, outcome: str, usage: Optional[dict] = None,
                reason: str = "") -> None:
        usage = usage if isinstance(usage, dict) else {}
        event = {
            "event": "memory_llm_bridge_call",
            "operation": operation,
            "latency_ms": int((self._clock() - started) * 1000),
            "outcome": outcome,
            "reason": reason,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
        self.telemetry.append(event)
        del self.telemetry[:-1000]
        LOGGER.info(json.dumps(event, sort_keys=True))

    def _authenticated(self, authorization: Optional[str]) -> bool:
        scheme, _, presented = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not presented.strip() or not self.config.token:
            return False
        return hmac.compare_digest(presented.strip().encode(), self.config.token.encode())

    def handle(
        self,
        *,
        authorization: Optional[str],
        body: Any,
        operation_header: Optional[str],
        invoke: InvokeFn,
        verify_policy: PolicyFn,
    ) -> BridgeResponse:
        started = self._clock()
        if not self.config.enabled or not self.config.token:
            return _error(404, "memory_llm_bridge_disabled", "memory LLM bridge is not enabled")
        if not self._authenticated(authorization):
            self._record("unauthenticated", started, "denied", reason="unauthorized")
            return _error(401, "unauthorized", "invalid bridge credentials")
        if not isinstance(body, dict):
            self._record("invalid", started, "rejected", reason="body_not_object")
            return _error(422, "invalid_body", "request body must be a JSON object")
        fmt = body.get("response_format")
        schema_name = ""
        schema: Optional[Dict[str, Any]] = None
        if isinstance(fmt, dict) and fmt.get("type") == "json_schema":
            spec = fmt.get("json_schema")
            if isinstance(spec, dict) and isinstance(spec.get("schema"), dict):
                schema = spec["schema"]
                schema_name = str(spec.get("name") or "")[:64]
        operation = re.sub(r"[^A-Za-z0-9_.:-]", "", (operation_header or schema_name or "unlabelled"))[:64] or "unlabelled"
        for field in _REJECTED_FIELDS:
            if body.get(field) not in (None, [], {}):
                self._record(operation, started, "rejected", reason=f"{field}_not_allowed")
                return _error(422, f"{field}_not_allowed", f"{field} is not accepted by the memory index bridge")
        if body.get("stream"):
            self._record(operation, started, "rejected", reason="stream_not_allowed")
            return _error(422, "stream_not_allowed", "streaming is not accepted by the memory index bridge")
        if not (isinstance(fmt, dict) and fmt.get("type") in ("json_schema", "json_object")) or (
            fmt.get("type") == "json_schema" and schema is None
        ):
            self._record(operation, started, "rejected", reason="response_format_required")
            return _error(422, "response_format_required",
                          "response_format must be json_schema (with schema) or json_object")
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages or any(
            not isinstance(m, dict) or m.get("role") not in ("system", "user", "assistant")
            or not isinstance(m.get("content"), (str, list)) for m in messages
        ):
            self._record(operation, started, "rejected", reason="messages_invalid")
            return _error(422, "messages_invalid", "messages must be non-empty system/user/assistant text messages")
        clean = [{"role": m["role"], "content": _message_text(m["content"])} for m in messages]

        # Budget and concurrency refusals are rate-limit responses (Hindsight defers).
        if not self._slots.acquire(blocking=False):
            self._record(operation, started, "refused", reason="concurrency_limit")
            return _error(429, "concurrency_limit", "too many concurrent memory index calls",
                          retry_after=self.config.retry_after_seconds)
        try:
            if not self._budget.take(self.config.daily_budget, self._today()):
                self._record(operation, started, "refused", reason="daily_budget_exhausted")
                return _error(429, "daily_budget_exhausted", "daily memory index call budget is exhausted",
                              retry_after=self.config.retry_after_seconds)
            deadline = started + self.config.deadline_seconds
            try:
                verify_policy(self.config.agent_id, deadline)
            except Exception as exc:  # policy missing/mismatched/unavailable: deny before dispatch
                code = getattr(exc, "error_code", "policy_unavailable")
                self._record(operation, started, "denied", reason=str(code))
                return _error(503, str(code).lower(), "memory-index native-tool policy could not be verified")
            remaining = deadline - self._clock()
            if remaining <= 0:
                self._record(operation, started, "timeout", reason="deadline_before_dispatch")
                return _error(504, "deadline_exceeded", "memory index call exhausted its deadline")
            instruction = (
                "No tool is available in this turn. Answer with one JSON "
                + ("object" if schema is None else "value")
                + " only, no prose and no code fence"
                + ("." if schema is None else ", that satisfies this JSON schema:\n"
                   + json.dumps(schema, ensure_ascii=False))
            )
            try:
                result = invoke(clean, instruction, remaining)
            except Exception as exc:
                status = int(getattr(exc, "status_code", 502) or 502)
                code = str(getattr(exc, "error_code", "upstream_error"))
                if status == 504:
                    self._record(operation, started, "timeout", reason=code)
                    return _error(504, "deadline_exceeded", "memory index call exceeded its deadline")
                if status == 429 or re.search(r"quota|rate.?limit|usage.?limit|overloaded", code, re.I):
                    self._record(operation, started, "upstream_quota", reason=code)
                    return _error(429, "upstream_quota", "model quota is exhausted",
                                  retry_after=self.config.retry_after_seconds)
                self._record(operation, started, "upstream_error", reason=code)
                return _error(502, "upstream_error", "memory index upstream call failed")
            usage = result.get("usage") if isinstance(result, dict) else None
            text = _extract_json_text(str((result or {}).get("text") or ""))
            try:
                parsed = json.loads(text)
            except ValueError:
                self._record(operation, started, "invalid_json", usage, reason="answer_not_json")
                return _error(502, "invalid_json", "model answer is not valid JSON")
            problem = _check_root(parsed, schema)
            if problem:
                self._record(operation, started, "schema_mismatch", usage, reason="root_shape")
                return _error(502, "schema_mismatch", problem)
            self._record(operation, started, "ok", usage)
            prompt_tokens = int((usage or {}).get("prompt_tokens") or (usage or {}).get("input_tokens") or 0)
            completion_tokens = int((usage or {}).get("completion_tokens") or (usage or {}).get("output_tokens") or 0)
            return BridgeResponse(200, {
                "id": f"chatcmpl-memidx-{int(time.time() * 1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": self.config.agent_id,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                          "total_tokens": prompt_tokens + completion_tokens},
            }, {})
        finally:
            self._slots.release()
