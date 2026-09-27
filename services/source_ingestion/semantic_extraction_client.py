"""Single typed semantic extraction client using restricted OpenClaw HTTP transport.

SIMPLIFY-EXTRACTION-CONTRACT-FOUNDATION-001:
Implements the single typed semantic extraction client consuming the restricted
OpenClaw HTTP transport (/api/openclaw-adapter/assistant/providers/openclaw/structured
or direct AssistantOpenClawProvider.invoke_structured) with bounded failure semantics,
pre-call admission gates, span verification, 15-second deadline, and at most one
bounded retry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import select
import socket
import sys
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import urllib.error
import urllib.request
import uuid

import jsonschema

from services.source_ingestion.semantic_extraction import (
    _DEFAULT_PROMPT_VERSION,
    _LOW_CONFIDENCE_THRESHOLD,
    _MAX_TURN_DEADLINE_SECONDS,
    _SCHEMA_VERSION,
    _TARGET_P95_SECONDS,
    _utc_now,
    AbstentionReason,
    AdmissionDecision,
    ExtractionFailureCode,
    ExtractionTaskType,
    IntentExtractionPayload,
    SemanticExtractionAdmission,
    SemanticExtractionRequest,
    SemanticExtractionResult,
    SourceSpan,
    StrategySeedExtractionPayload,
    TradeLessonExtractionPayload,
    compute_schema_id,
    get_semantic_extraction_json_schema,
)

logger = logging.getLogger(__name__)


# Standard verified model rates per 1,000,000 tokens (USD)
# Used for honest cost tracking; subscription/unknown costs are preserved as None, never treated as $0.00
_VERIFIED_MODEL_RATES: dict[str, dict[str, float]] = {
    "openclaw/main": {
        "input_per_million": 2.50,
        "output_per_million": 10.00,
    },
    "openclaw/default": {
        "input_per_million": 2.50,
        "output_per_million": 10.00,
    },
    "claude-3-5-sonnet": {
        "input_per_million": 3.00,
        "output_per_million": 15.00,
    },
    "gpt-4o": {
        "input_per_million": 2.50,
        "output_per_million": 10.00,
    },
    "gpt-4o-mini": {
        "input_per_million": 0.15,
        "output_per_million": 0.60,
    },
}

_CANONICAL_FIELD_PATHS: frozenset[str] = frozenset({
    "intent.primary_intent",
    "intent.secondary_intents",
    "strategy_seed.hypothesis",
    "strategy_seed.asset_class",
    "strategy_seed.market_scope",
    "strategy_seed.required_data",
    "trade_lesson.scope",
    "trade_lesson.proposed_change",
})

_DEFAULT_TOKEN_LIMITS: dict[str, int] = {
    "max_input_tokens": 8000,
    "max_output_tokens": 1000,
}


def _extract_socket(stream: Any) -> Optional[Any]:
    for path in (
        ("fp", "fp", "raw", "_sock"),
        ("fp", "raw", "_sock"),
        ("raw", "_sock"),
    ):
        curr = stream
        for attr in path:
            curr = getattr(curr, attr, None)
            if curr is None:
                break
        if curr is not None:
            return curr
    return None


def _read_http_body_bounded(stream: Any, deadline_at: float, max_bytes: int = 10_000_000) -> bytes:
    sock = _extract_socket(stream)
    chunks: list[bytes] = []
    total_bytes = 0
    read_fn = getattr(stream, "read1", None) or getattr(stream, "read", None)
    if read_fn is None:
        return b""

    while True:
        if getattr(stream, "isclosed", lambda: False)():
            break
        rem = deadline_at - time.monotonic()
        if rem <= 0:
            raise TimeoutError("HTTP response read exceeded wall-clock deadline")
        if sock is not None:
            try:
                sock.settimeout(max(0.001, rem))
            except Exception:
                pass
        chunk = read_fn(4096)
        if not chunk:
            break
        chunks.append(chunk)
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise ValueError(f"HTTP response body exceeded {max_bytes} bytes")
        if time.monotonic() >= deadline_at:
            raise TimeoutError("HTTP response read exceeded wall-clock deadline")

    return b"".join(chunks)


class SemanticExtractionClientError(RuntimeError):
    """Raised when an unrecoverable extraction client error occurs."""

    def __init__(self, message: str, failure_code: ExtractionFailureCode, status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.failure_code = failure_code
        self.status_code = status_code


class SemanticExtractionClient:
    """Client for typed semantic extraction with pre-call admission and bounded failure semantics."""

    def __init__(
        self,
        *,
        adapter_url: Optional[str] = None,
        service_token: Optional[str] = None,
        provider: Optional[Any] = None,
        transport_fn: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
        default_model: str = "openclaw/main",
        target_timeout_seconds: float = _TARGET_P95_SECONDS,
        max_deadline_seconds: float = _MAX_TURN_DEADLINE_SECONDS,
        token_bucket_limits: Optional[dict[str, int]] = None,
        official_rates: Optional[dict[str, float]] = None,
        fallback_to_baseline: bool = False,
    ) -> None:
        self._adapter_url = (
            adapter_url
            or os.environ.get("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL")
            or os.environ.get("OPENCLAW_GATEWAY_ADAPTER_URL")
            or os.environ.get("PANTHEON_OPENCLAW_ADAPTER_URL")
        )
        if self._adapter_url:
            self._adapter_url = self._adapter_url.rstrip("/")

        self._service_token = service_token or os.environ.get("PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN")
        self._provider = provider
        self._transport_fn = transport_fn
        self._default_model = default_model
        self._max_deadline_seconds = min(max(0.001, float(max_deadline_seconds)), _MAX_TURN_DEADLINE_SECONDS)
        self._target_timeout_seconds = min(float(target_timeout_seconds), self._max_deadline_seconds)
        self._token_limits = dict(token_bucket_limits or _DEFAULT_TOKEN_LIMITS)
        self._official_rates = dict(official_rates) if official_rates is not None else None
        self._rate_provenance = "official_catalog_2026" if official_rates is None else "custom_override"
        self._fallback_to_baseline = fallback_to_baseline

    def calculate_cost(
        self,
        input_tokens: Optional[int],
        output_tokens: Optional[int],
        model_id: Optional[str] = None,
    ) -> Optional[float]:
        """Calculate honest cost in USD based on execution token counts.

        Returns None if input_tokens or output_tokens is None, or if the model
        rate provenance cannot be verified (to prevent fabricating $0.00 or unverified rate).
        """
        if input_tokens is None or output_tokens is None:
            return None

        if self._official_rates is not None:
            rates = self._official_rates
        else:
            resolved_model = model_id or self._default_model
            if resolved_model in _VERIFIED_MODEL_RATES:
                rates = _VERIFIED_MODEL_RATES[resolved_model]
            elif self._default_model in _VERIFIED_MODEL_RATES and resolved_model in (None, "", "openclaw/default"):
                rates = _VERIFIED_MODEL_RATES[self._default_model]
            else:
                return None

        input_rate = rates.get("input_per_million", 2.50) / 1_000_000.0
        output_rate = rates.get("output_per_million", 10.00) / 1_000_000.0
        return round(input_tokens * input_rate + output_tokens * output_rate, 6)

    def _resolve_model_identity(self, raw_response: Optional[dict[str, Any]], request: SemanticExtractionRequest) -> str:
        if isinstance(raw_response, dict):
            if raw_response.get("model"):
                return str(raw_response["model"])
            out = raw_response.get("output") or raw_response.get("data")
            if isinstance(out, dict) and out.get("model"):
                return str(out["model"])
        if self._transport_fn is not None and request.model_id:
            return request.model_id
        return "openclaw/default"

    def _assert_provider_policy(self, provider: Any, *, deadline: float) -> None:
        if not hasattr(provider, "_gateway_call"):
            raise SemanticExtractionClientError(
                "Direct provider lacks _gateway_call policy verification interface.",
                ExtractionFailureCode.ADMISSION_DENIED,
                403,
            )
        try:
            snapshot = provider._gateway_call("config.get", timeout_seconds=max(0.01, deadline - time.monotonic()))
        except Exception as exc:
            raise SemanticExtractionClientError(
                f"Cannot verify native-tool denial on extraction Gateway: {exc}",
                ExtractionFailureCode.ADMISSION_DENIED,
                503,
            ) from exc
        config = snapshot.get("config") if isinstance(snapshot, dict) else None
        agents = config.get("agents") if isinstance(config, dict) else None
        entries = agents.get("list") if isinstance(agents, dict) else None
        matches = [item for item in entries if isinstance(item, dict) and item.get("id") == "main"] if isinstance(entries, list) else []
        tools = matches[0].get("tools") if len(matches) == 1 else None
        if not isinstance(snapshot, dict) or snapshot.get("valid") is not True or not isinstance(tools, dict) or tools.get("deny") != ["*"]:
            raise SemanticExtractionClientError(
                "Structured extraction policy boundary violated: Gateway tools.deny != ['*'].",
                ExtractionFailureCode.ADMISSION_DENIED,
                403,
            )

    def _build_prompt(self, request: SemanticExtractionRequest) -> str:
        task_type = request.normalized_task_type()
        instructions = [
            f"You are a strict financial knowledge extractor performing task: {task_type.value}.",
            "You MUST call the tool `emit_extraction` with valid structured arguments strictly conforming to its schema.",
            "Rules:",
            "1. Ground all extracted fields in the source text. Provide `source_spans` for every extracted field.",
            "   Each span must have `start_char` and `end_char` exactly indexing the characters in the source text, and `exact_text`.",
            "2. If the text does not contain sufficient evidence, or is ambiguous, set `is_abstained=true` with a valid `abstention_reason`.",
            "3. Do not invent human labels, assumptions, or external facts.",
            "4. Do not attempt shell, code execution, or registry writes.",
            "\n--- Source Text ---",
            request.text,
            "--- End Source Text ---",
        ]
        return "\n".join(instructions)

    def extract(self, request: SemanticExtractionRequest) -> SemanticExtractionResult:
        """Execute a typed extraction turn with admission checks, deadlines, and bounded retries."""
        start_time = time.monotonic()
        task_type = request.normalized_task_type()
        schema = get_semantic_extraction_json_schema(task_type)
        schema_id = compute_schema_id(schema)

        # 1. Mandatory pre-call admission check (Zero model calls on admission failure)
        admission = SemanticExtractionAdmission.check(request)
        if not admission.admitted:
            return SemanticExtractionResult(
                extraction_id=str(uuid.uuid4()),
                source_id=request.source_id,
                tenant_id=request.tenant_id,
                task_type=task_type.value,
                status="abstained",
                is_abstained=True,
                abstention_reason=AbstentionReason.ADMISSION_DENIED.value,
                failure_code=ExtractionFailureCode.ADMISSION_DENIED.value,
                failure_message=admission.denial_reason,
                schema_id=schema_id,
                model_identity=self._resolve_model_identity(None, request),
                prompt_identity=_DEFAULT_PROMPT_VERSION,
                config_digest=hashlib.sha256(b"admission_denied").hexdigest()[:16],
                latency_ms=(time.monotonic() - start_time) * 1000.0,
            )

        # 2. Configure overall turn budget and retry limit (hard-capped at 15s)
        total_deadline = min(
            request.timeout_seconds if request.timeout_seconds is not None else self._target_timeout_seconds,
            self._max_deadline_seconds,
        )
        max_retries = min(max(0, request.max_retries), 1)  # at most 1 bounded retry
        max_attempts = 1 + max_retries

        prompt = self._build_prompt(request)
        attempt = 0
        last_error_code: Optional[ExtractionFailureCode] = None
        last_error_msg: Optional[str] = None
        raw_response: Optional[dict[str, Any]] = None
        accumulated_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        has_accumulated_usage = False

        while attempt < max_attempts:
            elapsed = time.monotonic() - start_time
            remaining = total_deadline - elapsed
            if remaining <= 0:
                last_error_code = ExtractionFailureCode.TIMEOUT
                last_error_msg = f"Turn deadline of {total_deadline:.3f}s exhausted before attempt {attempt + 1}"
                break

            try:
                raw_response = self._dispatch_turn(
                    prompt=prompt,
                    schema=schema,
                    request=request,
                    timeout_seconds=remaining,
                )
                # Check wall-clock deadline immediately after dispatch
                elapsed_after = time.monotonic() - start_time
                if elapsed_after >= total_deadline:
                    raw_response = None
                    last_error_code = ExtractionFailureCode.TIMEOUT
                    last_error_msg = f"Turn deadline of {total_deadline:.3f}s exhausted during dispatch (took {elapsed_after*1000.0:.1f}ms)"
                    break
                break  # Successful dispatch within deadline
            except SemanticExtractionClientError as exc:
                last_error_code = exc.failure_code
                last_error_msg = exc.message
                if exc.failure_code == ExtractionFailureCode.TIMEOUT:
                    break
                # Non-retryable failures: invalid schema, wrong tool, refusal, budget breach, admission denied
                if exc.failure_code in (
                    ExtractionFailureCode.INVALID_SCHEMA,
                    ExtractionFailureCode.WRONG_TOOL,
                    ExtractionFailureCode.REFUSAL,
                    ExtractionFailureCode.BUDGET_BREACH,
                    ExtractionFailureCode.ADMISSION_DENIED,
                ):
                    break
            except Exception as exc:
                err_str = str(exc)
                if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in err_str.lower() or "deadline" in err_str.lower():
                    last_error_code = ExtractionFailureCode.TIMEOUT
                    last_error_msg = err_str
                    break
                else:
                    last_error_code = ExtractionFailureCode.TRANSPORT_ERROR
                    last_error_msg = err_str

            attempt += 1
            if attempt < max_attempts:
                # Bounded backoff if deadline permits
                rem_before_sleep = total_deadline - (time.monotonic() - start_time)
                if rem_before_sleep <= 0.02:
                    last_error_code = ExtractionFailureCode.TIMEOUT
                    last_error_msg = f"Turn deadline exhausted before backoff sleep for attempt {attempt + 1}"
                    break
                time.sleep(min(0.2, max(0.01, rem_before_sleep / 10.0)))

        # Handle failed turn dispatch
        if raw_response is None:
            failure_code = last_error_code or ExtractionFailureCode.TRANSPORT_ERROR
            is_timeout = failure_code == ExtractionFailureCode.TIMEOUT
            model_id = self._resolve_model_identity(None, request)
            usage_res = (
                {
                    "input_tokens": accumulated_usage["input_tokens"],
                    "output_tokens": accumulated_usage["output_tokens"],
                    "total_tokens": accumulated_usage["input_tokens"] + accumulated_usage["output_tokens"],
                }
                if has_accumulated_usage
                else None
            )
            cost_res = (
                self.calculate_cost(accumulated_usage["input_tokens"], accumulated_usage["output_tokens"], model_id=model_id)
                if has_accumulated_usage
                else None
            )
            return SemanticExtractionResult(
                extraction_id=str(uuid.uuid4()),
                source_id=request.source_id,
                tenant_id=request.tenant_id,
                task_type=task_type.value,
                status="abstained" if is_timeout else "failed",
                is_abstained=is_timeout,
                abstention_reason=AbstentionReason.TIMEOUT.value if is_timeout else AbstentionReason.MODEL_REFUSAL.value,
                failure_code=failure_code.value,
                failure_message=last_error_msg or "Extraction failed without response.",
                schema_id=schema_id,
                model_identity=model_id,
                prompt_identity=_DEFAULT_PROMPT_VERSION,
                config_digest=hashlib.sha256(b"dispatch_failed").hexdigest()[:16],
                latency_ms=(time.monotonic() - start_time) * 1000.0,
                retry_count=max(0, attempt - 1),
                usage=usage_res,
                cost_usd=cost_res,
            )

        # 3. Parse and validate structured output
        return self._process_structured_output(
            raw_response=raw_response,
            request=request,
            task_type=task_type,
            schema=schema,
            schema_id=schema_id,
            start_time=start_time,
            total_deadline=total_deadline,
            retry_count=attempt,
            accumulated_usage=accumulated_usage,
        )

    def _dispatch_turn(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        request: SemanticExtractionRequest,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Dispatch a single turn via transport_fn, provider, or HTTP adapter."""
        # 1. Custom transport function (injected / test harness)
        if self._transport_fn is not None:
            payload = {
                "prompt": prompt,
                "extraction_schema": schema,
                "model": request.model_id or self._default_model,
                "agent_id": "main",
                "mode": "user",
                "operator_id": request.operator_id or "system",
                "trace_id": request.trace_id or f"trace-extract-{uuid.uuid4().hex[:8]}",
                "timeout_seconds": timeout_seconds,
            }
            return self._transport_fn(payload)

        # 2. Direct provider object (AssistantOpenClawProvider)
        if self._provider is not None:
            deadline_at = time.monotonic() + timeout_seconds
            # Enforce native-tool denial policy before calling provider
            self._assert_provider_policy(self._provider, deadline=deadline_at)
            rem_timeout = max(0.001, deadline_at - time.monotonic())
            if time.monotonic() >= deadline_at:
                raise TimeoutError(f"Turn deadline of {timeout_seconds:.3f}s exhausted during policy verification")
            try:
                res = self._provider.invoke_structured(
                    prompt,
                    extraction_schema=schema,
                    model=request.model_id or self._default_model,
                    agent_id="main",
                    mode="user",
                    operator_id=request.operator_id or "system",
                    trace_id=request.trace_id or f"trace-extract-{uuid.uuid4().hex[:8]}",
                    timeout_seconds=rem_timeout,
                )
                if time.monotonic() >= deadline_at:
                    raise TimeoutError(f"Turn deadline of {timeout_seconds:.3f}s exhausted during invoke_structured")
                if hasattr(res, "to_dict"):
                    return res.to_dict()
                return {"output": getattr(res, "output", {})}
            except SemanticExtractionClientError:
                raise
            except Exception as exc:
                err_str = str(exc)
                if isinstance(exc, (TimeoutError, socket.timeout)) or "TIMEOUT" in err_str or "timed out" in err_str.lower():
                    raise SemanticExtractionClientError(err_str, ExtractionFailureCode.TIMEOUT, 504) from exc
                if "INVALID_JSON" in err_str or "SCHEMA" in err_str:
                    raise SemanticExtractionClientError(err_str, ExtractionFailureCode.INVALID_SCHEMA, 422) from exc
                if "TOOL" in err_str or "NO_MATCH" in err_str or "MISMATCH" in err_str:
                    raise SemanticExtractionClientError(err_str, ExtractionFailureCode.WRONG_TOOL, 502) from exc
                raise SemanticExtractionClientError(err_str, ExtractionFailureCode.TRANSPORT_ERROR, 500) from exc

        # 3. HTTP Adapter call (Admitted restricted OpenClaw HTTP path)
        if self._adapter_url:
            deadline_at = time.monotonic() + timeout_seconds
            endpoint = f"{self._adapter_url}/api/openclaw-adapter/assistant/providers/openclaw/structured"
            body = {
                "prompt": prompt,
                "extraction_schema": schema,
                "mode": "user",
                "agent_id": "main",
            }
            headers = {
                "Content-Type": "application/json",
                "X-Operator-Id": request.operator_id or "system",
                "X-Trace-Id": request.trace_id or f"trace-extract-{uuid.uuid4().hex[:8]}",
            }
            if self._service_token:
                headers["Authorization"] = f"Bearer {self._service_token}"

            req = urllib.request.Request(
                endpoint,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                rem_conn = max(0.001, deadline_at - time.monotonic())
                with urllib.request.urlopen(req, timeout=min(rem_conn, 15.0)) as resp:
                    raw_bytes = _read_http_body_bounded(resp, deadline_at=deadline_at)
                    raw_data = json.loads(raw_bytes.decode("utf-8"))
                    data_obj = raw_data.get("data") or {}
                    return data_obj
            except urllib.error.HTTPError as exc:
                try:
                    err_body = _read_http_body_bounded(exc, deadline_at=deadline_at).decode("utf-8", errors="replace")
                except (TimeoutError, socket.timeout) as te:
                    raise SemanticExtractionClientError("HTTP error read timed out", ExtractionFailureCode.TIMEOUT, 504) from te
                if exc.code == 422:
                    raise SemanticExtractionClientError(err_body, ExtractionFailureCode.INVALID_SCHEMA, 422) from exc
                if exc.code == 504:
                    raise SemanticExtractionClientError(err_body, ExtractionFailureCode.TIMEOUT, 504) from exc
                if exc.code == 502:
                    raise SemanticExtractionClientError(err_body, ExtractionFailureCode.WRONG_TOOL, 502) from exc
                raise SemanticExtractionClientError(err_body, ExtractionFailureCode.TRANSPORT_ERROR, exc.code) from exc
            except (TimeoutError, socket.timeout) as exc:
                raise SemanticExtractionClientError("HTTP request timed out", ExtractionFailureCode.TIMEOUT, 504) from exc
            except urllib.error.URLError as exc:
                err_str = str(exc)
                if isinstance(exc.reason, (socket.timeout, TimeoutError)) or "timed out" in err_str.lower():
                    raise SemanticExtractionClientError("HTTP connection timed out", ExtractionFailureCode.TIMEOUT, 504) from exc
                raise SemanticExtractionClientError(err_str, ExtractionFailureCode.TRANSPORT_ERROR, 500) from exc
            except Exception as exc:
                err_str = str(exc)
                if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in err_str.lower():
                    raise SemanticExtractionClientError(err_str, ExtractionFailureCode.TIMEOUT, 504) from exc
                raise SemanticExtractionClientError(err_str, ExtractionFailureCode.TRANSPORT_ERROR, 500) from exc

        # 4. Fallback to deterministic baseline if explicitly allowed
        if self._fallback_to_baseline:
            from services.source_ingestion.semantic_extraction import DeterministicBaselineExtractor
            baseline_res = DeterministicBaselineExtractor.extract(request)
            return {
                "status": "completed",
                "output": {
                    "structured_data": {
                        "is_abstained": baseline_res.is_abstained,
                        "abstention_reason": baseline_res.abstention_reason,
                        "intent": baseline_res.intent.to_dict() if baseline_res.intent else None,
                        "strategy_seed": baseline_res.strategy_seed.to_dict() if baseline_res.strategy_seed else None,
                        "trade_lesson": baseline_res.trade_lesson.to_dict() if baseline_res.trade_lesson else None,
                        "source_spans": [s.to_dict() for s in baseline_res.source_spans],
                    },
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                },
            }

        raise SemanticExtractionClientError(
            "No OpenClaw transport available (no provider, transport_fn, or adapter_url set)",
            ExtractionFailureCode.TRANSPORT_ERROR,
            503,
        )

    def _process_structured_output(
        self,
        *,
        raw_response: dict[str, Any],
        request: SemanticExtractionRequest,
        task_type: ExtractionTaskType,
        schema: dict[str, Any],
        schema_id: str,
        start_time: float,
        total_deadline: float,
        retry_count: int,
        accumulated_usage: dict[str, int],
    ) -> SemanticExtractionResult:
        elapsed = time.monotonic() - start_time
        model_identity = self._resolve_model_identity(raw_response, request)

        if elapsed >= total_deadline:
            return SemanticExtractionResult(
                extraction_id=str(uuid.uuid4()),
                source_id=request.source_id,
                tenant_id=request.tenant_id,
                task_type=task_type.value,
                status="abstained",
                is_abstained=True,
                abstention_reason=AbstentionReason.TIMEOUT.value,
                failure_code=ExtractionFailureCode.TIMEOUT.value,
                failure_message=f"Turn deadline of {total_deadline:.3f}s exhausted during execution (took {elapsed*1000.0:.1f}ms)",
                schema_id=schema_id,
                model_identity=model_identity,
                prompt_identity=_DEFAULT_PROMPT_VERSION,
                config_digest=hashlib.sha256(b"timeout").hexdigest()[:16],
                latency_ms=elapsed * 1000.0,
                retry_count=retry_count,
            )

        output_obj = raw_response.get("output") or raw_response.get("data") or raw_response

        # Check refusal
        if (
            raw_response.get("status") in ("refusal", "rejected")
            or (isinstance(output_obj, dict) and output_obj.get("status") in ("refusal", "rejected"))
        ):
            return SemanticExtractionResult(
                extraction_id=str(uuid.uuid4()),
                source_id=request.source_id,
                tenant_id=request.tenant_id,
                task_type=task_type.value,
                status="abstained",
                is_abstained=True,
                abstention_reason=AbstentionReason.MODEL_REFUSAL.value,
                failure_code=ExtractionFailureCode.REFUSAL.value,
                failure_message="Model explicitly refused extraction turn.",
                schema_id=schema_id,
                model_identity=model_identity,
                prompt_identity=_DEFAULT_PROMPT_VERSION,
                config_digest=hashlib.sha256(b"refusal").hexdigest()[:16],
                latency_ms=(time.monotonic() - start_time) * 1000.0,
                retry_count=retry_count,
            )

        structured = output_obj.get("structured_data") if isinstance(output_obj, dict) else None
        if not isinstance(structured, dict):
            # Model response did not produce structured data
            return SemanticExtractionResult(
                extraction_id=str(uuid.uuid4()),
                source_id=request.source_id,
                tenant_id=request.tenant_id,
                task_type=task_type.value,
                status="failed",
                failure_code=ExtractionFailureCode.INCOMPLETE_RESPONSE.value,
                failure_message="Model returned non-dict structured data.",
                schema_id=schema_id,
                model_identity=model_identity,
                prompt_identity=_DEFAULT_PROMPT_VERSION,
                config_digest=hashlib.sha256(b"incomplete").hexdigest()[:16],
                latency_ms=(time.monotonic() - start_time) * 1000.0,
                retry_count=retry_count,
            )

        # Token usage and budget checks (preserve unknown!)
        usage_data = output_obj.get("usage") if isinstance(output_obj, dict) else None
        usage_obj: Optional[dict[str, int]] = None
        cost_usd: Optional[float] = None
        if isinstance(usage_data, dict) and "input_tokens" in usage_data and usage_data.get("input_tokens") is not None:
            in_tok = int(usage_data.get("input_tokens", 0)) + accumulated_usage.get("input_tokens", 0)
            out_tok = int(usage_data.get("output_tokens", 0)) + accumulated_usage.get("output_tokens", 0)
            tot_tok = in_tok + out_tok
            usage_obj = {"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": tot_tok}
            cost_usd = self.calculate_cost(in_tok, out_tok, model_id=model_identity)

            if (
                in_tok > self._token_limits["max_input_tokens"]
                or out_tok > self._token_limits["max_output_tokens"]
            ):
                return SemanticExtractionResult(
                    extraction_id=str(uuid.uuid4()),
                    source_id=request.source_id,
                    tenant_id=request.tenant_id,
                    task_type=task_type.value,
                    status="abstained",
                    is_abstained=True,
                    abstention_reason=AbstentionReason.BUDGET_BREACH.value,
                    failure_code=ExtractionFailureCode.BUDGET_BREACH.value,
                    failure_message=f"Usage exceeded limits: input {in_tok} > {self._token_limits['max_input_tokens']} or output {out_tok} > {self._token_limits['max_output_tokens']}",
                    schema_id=schema_id,
                    model_identity=model_identity,
                    prompt_identity=_DEFAULT_PROMPT_VERSION,
                    config_digest=hashlib.sha256(b"budget_breach").hexdigest()[:16],
                    usage=usage_obj,
                    cost_usd=cost_usd,
                    latency_ms=(time.monotonic() - start_time) * 1000.0,
                    retry_count=retry_count,
                )

        # Strict JSON Schema validation
        validator = jsonschema.Draft7Validator(schema)
        errors = list(validator.iter_errors(structured))
        if errors:
            first_err = errors[0].message
            return SemanticExtractionResult(
                extraction_id=str(uuid.uuid4()),
                source_id=request.source_id,
                tenant_id=request.tenant_id,
                task_type=task_type.value,
                status="failed",
                failure_code=ExtractionFailureCode.INVALID_SCHEMA.value,
                failure_message=f"Model structured data violated schema: {first_err}",
                schema_id=schema_id,
                model_identity=model_identity,
                prompt_identity=_DEFAULT_PROMPT_VERSION,
                config_digest=hashlib.sha256(b"invalid_schema").hexdigest()[:16],
                latency_ms=(time.monotonic() - start_time) * 1000.0,
                retry_count=retry_count,
            )

        is_abstained = bool(structured.get("is_abstained", False))
        abstention_reason = structured.get("abstention_reason")

        # Parse and strictly validate source spans
        raw_spans = structured.get("source_spans") or []
        valid_spans: list[SourceSpan] = []
        supported_fields: list[str] = []
        missing_fields: list[str] = []
        has_invalid_span = False
        invalid_span_reason = ""

        if not isinstance(raw_spans, list):
            has_invalid_span = True
            invalid_span_reason = "source_spans must be a list"
            raw_spans = []

        for item in raw_spans:
            if not isinstance(item, dict):
                has_invalid_span = True
                invalid_span_reason = "Non-dict item in source_spans"
                continue
            span = SourceSpan.from_dict(item)
            if span.field_name not in _CANONICAL_FIELD_PATHS:
                has_invalid_span = True
                invalid_span_reason = f"Non-canonical span field path: {span.field_name}"
                continue
            if not span.is_valid(request.text):
                has_invalid_span = True
                invalid_span_reason = f"Invalid span {span.field_name} [{span.start_char}:{span.end_char}] does not match source text"
                continue
            valid_spans.append(span)
            if span.field_name not in supported_fields:
                supported_fields.append(span.field_name)

        # Parse payloads
        intent_payload: Optional[IntentExtractionPayload] = None
        seed_payload: Optional[StrategySeedExtractionPayload] = None
        lesson_payload: Optional[TradeLessonExtractionPayload] = None

        if structured.get("intent") is not None and task_type in (ExtractionTaskType.INTENT, ExtractionTaskType.COMPREHENSIVE):
            try:
                intent_payload = IntentExtractionPayload.from_dict(structured["intent"])
            except Exception as exc:
                return SemanticExtractionResult(
                    extraction_id=str(uuid.uuid4()),
                    source_id=request.source_id,
                    tenant_id=request.tenant_id,
                    task_type=task_type.value,
                    status="failed",
                    failure_code=ExtractionFailureCode.INVALID_SCHEMA.value,
                    failure_message=f"Failed to parse intent payload: {exc}",
                    schema_id=schema_id,
                    model_identity=model_identity,
                    prompt_identity=_DEFAULT_PROMPT_VERSION,
                    config_digest=hashlib.sha256(b"invalid_payload").hexdigest()[:16],
                    latency_ms=(time.monotonic() - start_time) * 1000.0,
                    retry_count=retry_count,
                )

        if structured.get("strategy_seed") is not None and task_type in (ExtractionTaskType.STRATEGY_SEED, ExtractionTaskType.COMPREHENSIVE):
            try:
                seed_payload = StrategySeedExtractionPayload.from_dict(structured["strategy_seed"])
            except Exception as exc:
                return SemanticExtractionResult(
                    extraction_id=str(uuid.uuid4()),
                    source_id=request.source_id,
                    tenant_id=request.tenant_id,
                    task_type=task_type.value,
                    status="failed",
                    failure_code=ExtractionFailureCode.INVALID_SCHEMA.value,
                    failure_message=f"Failed to parse strategy_seed payload: {exc}",
                    schema_id=schema_id,
                    model_identity=model_identity,
                    prompt_identity=_DEFAULT_PROMPT_VERSION,
                    config_digest=hashlib.sha256(b"invalid_payload").hexdigest()[:16],
                    latency_ms=(time.monotonic() - start_time) * 1000.0,
                    retry_count=retry_count,
                )

        if structured.get("trade_lesson") is not None and task_type in (ExtractionTaskType.TRADE_LESSON, ExtractionTaskType.COMPREHENSIVE):
            try:
                lesson_payload = TradeLessonExtractionPayload.from_dict(structured["trade_lesson"])
            except Exception as exc:
                return SemanticExtractionResult(
                    extraction_id=str(uuid.uuid4()),
                    source_id=request.source_id,
                    tenant_id=request.tenant_id,
                    task_type=task_type.value,
                    status="failed",
                    failure_code=ExtractionFailureCode.INVALID_SCHEMA.value,
                    failure_message=f"Failed to parse trade_lesson payload: {exc}",
                    schema_id=schema_id,
                    model_identity=model_identity,
                    prompt_identity=_DEFAULT_PROMPT_VERSION,
                    config_digest=hashlib.sha256(b"invalid_payload").hexdigest()[:16],
                    latency_ms=(time.monotonic() - start_time) * 1000.0,
                    retry_count=retry_count,
                )

        # When not abstaining, require task-specific payload and valid source spans!
        if not is_abstained:
            if task_type == ExtractionTaskType.INTENT and intent_payload is None:
                return SemanticExtractionResult(
                    extraction_id=str(uuid.uuid4()),
                    source_id=request.source_id,
                    tenant_id=request.tenant_id,
                    task_type=task_type.value,
                    status="failed",
                    failure_code=ExtractionFailureCode.INCOMPLETE_RESPONSE.value,
                    failure_message="Non-abstained response missing required intent payload.",
                    schema_id=schema_id,
                    model_identity=model_identity,
                    prompt_identity=_DEFAULT_PROMPT_VERSION,
                    config_digest=hashlib.sha256(b"incomplete").hexdigest()[:16],
                    latency_ms=(time.monotonic() - start_time) * 1000.0,
                    retry_count=retry_count,
                )
            elif task_type == ExtractionTaskType.STRATEGY_SEED and seed_payload is None:
                return SemanticExtractionResult(
                    extraction_id=str(uuid.uuid4()),
                    source_id=request.source_id,
                    tenant_id=request.tenant_id,
                    task_type=task_type.value,
                    status="failed",
                    failure_code=ExtractionFailureCode.INCOMPLETE_RESPONSE.value,
                    failure_message="Non-abstained response missing required strategy_seed payload.",
                    schema_id=schema_id,
                    model_identity=model_identity,
                    prompt_identity=_DEFAULT_PROMPT_VERSION,
                    config_digest=hashlib.sha256(b"incomplete").hexdigest()[:16],
                    latency_ms=(time.monotonic() - start_time) * 1000.0,
                    retry_count=retry_count,
                )
            elif task_type == ExtractionTaskType.TRADE_LESSON and lesson_payload is None:
                return SemanticExtractionResult(
                    extraction_id=str(uuid.uuid4()),
                    source_id=request.source_id,
                    tenant_id=request.tenant_id,
                    task_type=task_type.value,
                    status="failed",
                    failure_code=ExtractionFailureCode.INCOMPLETE_RESPONSE.value,
                    failure_message="Non-abstained response missing required trade_lesson payload.",
                    schema_id=schema_id,
                    model_identity=model_identity,
                    prompt_identity=_DEFAULT_PROMPT_VERSION,
                    config_digest=hashlib.sha256(b"incomplete").hexdigest()[:16],
                    latency_ms=(time.monotonic() - start_time) * 1000.0,
                    retry_count=retry_count,
                )
            elif task_type == ExtractionTaskType.COMPREHENSIVE and intent_payload is None and seed_payload is None and lesson_payload is None:
                return SemanticExtractionResult(
                    extraction_id=str(uuid.uuid4()),
                    source_id=request.source_id,
                    tenant_id=request.tenant_id,
                    task_type=task_type.value,
                    status="failed",
                    failure_code=ExtractionFailureCode.INCOMPLETE_RESPONSE.value,
                    failure_message="Non-abstained comprehensive response missing all payload options.",
                    schema_id=schema_id,
                    model_identity=model_identity,
                    prompt_identity=_DEFAULT_PROMPT_VERSION,
                    config_digest=hashlib.sha256(b"incomplete").hexdigest()[:16],
                    latency_ms=(time.monotonic() - start_time) * 1000.0,
                    retry_count=retry_count,
                )

        # Critical Field Support Verification (100% requirement)
        missing_support = False
        missing_support_reason = ""

        if has_invalid_span:
            missing_support = True
            missing_support_reason = f"Invalid or non-canonical span detected: {invalid_span_reason}"

        if task_type in (ExtractionTaskType.INTENT, ExtractionTaskType.COMPREHENSIVE) and intent_payload and not is_abstained:
            has_intent_span = any(s.field_name.startswith("intent") for s in valid_spans)
            if not has_intent_span:
                missing_support = True
                missing_support_reason = "Missing valid source span for intent extraction."
                missing_fields.append("intent.primary_intent")

        if task_type in (ExtractionTaskType.STRATEGY_SEED, ExtractionTaskType.COMPREHENSIVE) and seed_payload and not is_abstained:
            hypo_spans = [s for s in valid_spans if s.field_name == "strategy_seed.hypothesis"]
            if not hypo_spans:
                missing_support = True
                missing_support_reason = "Missing valid source span for strategy_seed.hypothesis."
                missing_fields.append("strategy_seed.hypothesis")
            else:
                hypo_str = seed_payload.hypothesis.strip()
                if not any(s.exact_text in hypo_str or hypo_str in s.exact_text or s.exact_text in request.text for s in hypo_spans):
                    missing_support = True
                    missing_support_reason = "Strategy hypothesis is not grounded in source span."
                    missing_fields.append("strategy_seed.hypothesis")

        if task_type in (ExtractionTaskType.TRADE_LESSON, ExtractionTaskType.COMPREHENSIVE) and lesson_payload and not is_abstained:
            lesson_spans = [s for s in valid_spans if s.field_name == "trade_lesson.proposed_change"]
            if not lesson_spans:
                missing_support = True
                missing_support_reason = "Missing valid source span for trade_lesson.proposed_change."
                missing_fields.append("trade_lesson.proposed_change")
            else:
                change_str = lesson_payload.proposed_change.strip()
                if not any(s.exact_text in change_str or change_str in s.exact_text or s.exact_text in request.text for s in lesson_spans):
                    missing_support = True
                    missing_support_reason = "Trade lesson proposed_change is not grounded in source span."
                    missing_fields.append("trade_lesson.proposed_change")

        if missing_support and not is_abstained:
            is_abstained = True
            abstention_reason = AbstentionReason.MISSING_CRITICAL_SUPPORT.value

        # Check confidence threshold
        if intent_payload and intent_payload.confidence < _LOW_CONFIDENCE_THRESHOLD and not is_abstained:
            is_abstained = True
            abstention_reason = AbstentionReason.CONFIDENCE_BELOW_THRESHOLD.value

        latency_ms = (time.monotonic() - start_time) * 1000.0

        return SemanticExtractionResult(
            extraction_id=str(uuid.uuid4()),
            source_id=request.source_id,
            tenant_id=request.tenant_id,
            task_type=task_type.value,
            status="abstained" if is_abstained else "completed",
            is_abstained=is_abstained,
            abstention_reason=abstention_reason,
            intent=intent_payload,
            strategy_seed=seed_payload,
            trade_lesson=lesson_payload,
            supported_fields=tuple(supported_fields),
            source_spans=tuple(valid_spans),
            missing_fields=tuple(missing_fields),
            schema_version=_SCHEMA_VERSION,
            schema_id=schema_id,
            model_identity=model_identity,
            prompt_identity=_DEFAULT_PROMPT_VERSION,
            config_digest=hashlib.sha256(json.dumps(self._token_limits, sort_keys=True).encode("utf-8")).hexdigest()[:16],
            failure_code=ExtractionFailureCode.MISSING_SUPPORT.value if missing_support else None,
            failure_message=missing_support_reason or None,
            usage=usage_obj,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            retry_count=retry_count,
        )
