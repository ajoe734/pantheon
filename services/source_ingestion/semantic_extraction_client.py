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
import sys
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import urllib.error
import urllib.request
import uuid

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


# Standard official rates per 1,000,000 tokens (USD)
# Used for honest cost tracking; subscription/unknown costs are never treated as $0.00
_DEFAULT_RATES: dict[str, float] = {
    "input_per_million": 2.50,
    "output_per_million": 10.00,
}

_DEFAULT_TOKEN_LIMITS: dict[str, int] = {
    "max_input_tokens": 8000,
    "max_output_tokens": 1000,
}


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
        self._target_timeout_seconds = min(target_timeout_seconds, max_deadline_seconds)
        self._max_deadline_seconds = max_deadline_seconds
        self._token_limits = dict(token_bucket_limits or _DEFAULT_TOKEN_LIMITS)
        self._official_rates = dict(official_rates or _DEFAULT_RATES)
        self._fallback_to_baseline = fallback_to_baseline

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate honest cost in USD based on execution token counts."""
        input_rate = self._official_rates.get("input_per_million", 2.50) / 1_000_000.0
        output_rate = self._official_rates.get("output_per_million", 10.00) / 1_000_000.0
        return round(input_tokens * input_rate + output_tokens * output_rate, 6)

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
                model_identity=request.model_id or self._default_model,
                prompt_identity=_DEFAULT_PROMPT_VERSION,
                config_digest=hashlib.sha256(b"admission_denied").hexdigest()[:16],
                latency_ms=(time.monotonic() - start_time) * 1000.0,
            )

        # 2. Configure overall turn budget and retry limit
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

        while attempt < max_attempts:
            elapsed = time.monotonic() - start_time
            remaining = total_deadline - elapsed
            if remaining <= 0:
                last_error_code = ExtractionFailureCode.TIMEOUT
                last_error_msg = f"Turn deadline of {total_deadline:.1f}s exhausted before attempt {attempt + 1}"
                break

            try:
                raw_response = self._dispatch_turn(
                    prompt=prompt,
                    schema=schema,
                    request=request,
                    timeout_seconds=remaining,
                )
                break  # Successful dispatch
            except SemanticExtractionClientError as exc:
                last_error_code = exc.failure_code
                last_error_msg = exc.message
                # Non-retryable failures: invalid schema, wrong tool, refusal, budget breach
                if exc.failure_code in (
                    ExtractionFailureCode.INVALID_SCHEMA,
                    ExtractionFailureCode.WRONG_TOOL,
                    ExtractionFailureCode.REFUSAL,
                    ExtractionFailureCode.BUDGET_BREACH,
                    ExtractionFailureCode.ADMISSION_DENIED,
                ):
                    break
            except Exception as exc:
                last_error_code = ExtractionFailureCode.TRANSPORT_ERROR
                last_error_msg = str(exc)

            attempt += 1
            if attempt < max_attempts:
                # Bounded backoff if deadline permits
                time.sleep(min(0.2, max(0.01, remaining / 10.0)))

        # Handle failed turn dispatch
        if raw_response is None:
            failure_code = last_error_code or ExtractionFailureCode.TRANSPORT_ERROR
            is_timeout = failure_code == ExtractionFailureCode.TIMEOUT
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
                model_identity=request.model_id or self._default_model,
                prompt_identity=_DEFAULT_PROMPT_VERSION,
                config_digest=hashlib.sha256(b"dispatch_failed").hexdigest()[:16],
                latency_ms=(time.monotonic() - start_time) * 1000.0,
                retry_count=max(0, attempt - 1),
            )

        # 3. Parse and validate structured output
        return self._process_structured_output(
            raw_response=raw_response,
            request=request,
            task_type=task_type,
            schema_id=schema_id,
            start_time=start_time,
            retry_count=attempt,
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
            try:
                res = self._provider.invoke_structured(
                    prompt,
                    extraction_schema=schema,
                    model=request.model_id or self._default_model,
                    agent_id="main",
                    mode="user",
                    operator_id=request.operator_id or "system",
                    trace_id=request.trace_id or f"trace-extract-{uuid.uuid4().hex[:8]}",
                    timeout_seconds=timeout_seconds,
                )
                if hasattr(res, "to_dict"):
                    return res.to_dict()
                return {"output": getattr(res, "output", {})}
            except Exception as exc:
                err_str = str(exc)
                if "INVALID_JSON" in err_str or "SCHEMA" in err_str:
                    raise SemanticExtractionClientError(err_str, ExtractionFailureCode.INVALID_SCHEMA, 422) from exc
                if "TOOL" in err_str or "NO_MATCH" in err_str or "MISMATCH" in err_str:
                    raise SemanticExtractionClientError(err_str, ExtractionFailureCode.WRONG_TOOL, 502) from exc
                if "TIMEOUT" in err_str:
                    raise SemanticExtractionClientError(err_str, ExtractionFailureCode.TIMEOUT, 504) from exc
                raise SemanticExtractionClientError(err_str, ExtractionFailureCode.TRANSPORT_ERROR, 500) from exc

        # 3. HTTP Adapter call
        if self._adapter_url:
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
                with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                    raw_data = json.loads(resp.read().decode("utf-8"))
                    data_obj = raw_data.get("data") or {}
                    return data_obj
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")
                if exc.code == 422:
                    raise SemanticExtractionClientError(err_body, ExtractionFailureCode.INVALID_SCHEMA, 422) from exc
                if exc.code == 504:
                    raise SemanticExtractionClientError(err_body, ExtractionFailureCode.TIMEOUT, 504) from exc
                if exc.code == 502:
                    raise SemanticExtractionClientError(err_body, ExtractionFailureCode.WRONG_TOOL, 502) from exc
                raise SemanticExtractionClientError(err_body, ExtractionFailureCode.TRANSPORT_ERROR, exc.code) from exc
            except TimeoutError as exc:
                raise SemanticExtractionClientError("HTTP request timed out", ExtractionFailureCode.TIMEOUT, 504) from exc
            except Exception as exc:
                raise SemanticExtractionClientError(str(exc), ExtractionFailureCode.TRANSPORT_ERROR, 500) from exc

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
        schema_id: str,
        start_time: float,
        retry_count: int,
    ) -> SemanticExtractionResult:
        output_obj = raw_response.get("output") or raw_response.get("data") or raw_response
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
                failure_message="Model returned empty or non-dict structured data.",
                schema_id=schema_id,
                model_identity=request.model_id or self._default_model,
                prompt_identity=_DEFAULT_PROMPT_VERSION,
                config_digest=hashlib.sha256(b"incomplete").hexdigest()[:16],
                latency_ms=(time.monotonic() - start_time) * 1000.0,
                retry_count=retry_count,
            )

        # Token usage and budget checks
        usage = output_obj.get("usage") or {}
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens))

        if (
            input_tokens > self._token_limits["max_input_tokens"]
            or output_tokens > self._token_limits["max_output_tokens"]
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
                failure_message=f"Usage exceeded limits: input {input_tokens} > {self._token_limits['max_input_tokens']} or output {output_tokens} > {self._token_limits['max_output_tokens']}",
                schema_id=schema_id,
                model_identity=request.model_id or self._default_model,
                prompt_identity=_DEFAULT_PROMPT_VERSION,
                config_digest=hashlib.sha256(b"budget_breach").hexdigest()[:16],
                usage={"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens},
                cost_usd=self.calculate_cost(input_tokens, output_tokens),
                latency_ms=(time.monotonic() - start_time) * 1000.0,
                retry_count=retry_count,
            )

        is_abstained = bool(structured.get("is_abstained", False))
        abstention_reason = structured.get("abstention_reason")

        # Parse source spans
        raw_spans = structured.get("source_spans") or []
        valid_spans: list[SourceSpan] = []
        supported_fields: list[str] = []
        missing_fields: list[str] = []

        for item in raw_spans:
            if isinstance(item, dict):
                span = SourceSpan.from_dict(item)
                if span.is_valid(request.text):
                    valid_spans.append(span)
                    if span.field_name not in supported_fields:
                        supported_fields.append(span.field_name)

        # Parse payloads
        intent_payload: Optional[IntentExtractionPayload] = None
        seed_payload: Optional[StrategySeedExtractionPayload] = None
        lesson_payload: Optional[TradeLessonExtractionPayload] = None

        if structured.get("intent") and task_type in (ExtractionTaskType.INTENT, ExtractionTaskType.COMPREHENSIVE):
            try:
                intent_payload = IntentExtractionPayload.from_dict(structured["intent"])
            except Exception as exc:
                logger.warning("Failed to parse intent payload: %s", exc)

        if structured.get("strategy_seed") and task_type in (ExtractionTaskType.STRATEGY_SEED, ExtractionTaskType.COMPREHENSIVE):
            try:
                seed_payload = StrategySeedExtractionPayload.from_dict(structured["strategy_seed"])
            except Exception as exc:
                logger.warning("Failed to parse strategy_seed payload: %s", exc)

        if structured.get("trade_lesson") and task_type in (ExtractionTaskType.TRADE_LESSON, ExtractionTaskType.COMPREHENSIVE):
            try:
                lesson_payload = TradeLessonExtractionPayload.from_dict(structured["trade_lesson"])
            except Exception as exc:
                logger.warning("Failed to parse trade_lesson payload: %s", exc)

        # Critical Field Support Verification (100% requirement)
        missing_support = False
        missing_support_reason = ""

        if task_type in (ExtractionTaskType.INTENT, ExtractionTaskType.COMPREHENSIVE) and intent_payload and not is_abstained:
            # Intent critical field
            has_intent_span = any("intent" in s.field_name for s in valid_spans)
            if not has_intent_span:
                missing_support = True
                missing_support_reason = "Missing valid source span for intent extraction."
                missing_fields.append("intent.primary_intent")

        if task_type in (ExtractionTaskType.STRATEGY_SEED, ExtractionTaskType.COMPREHENSIVE) and seed_payload and not is_abstained:
            # Seed critical field: hypothesis
            has_hypothesis_span = any("hypothesis" in s.field_name for s in valid_spans)
            if not has_hypothesis_span:
                missing_support = True
                missing_support_reason = "Missing valid source span for strategy_seed.hypothesis."
                missing_fields.append("strategy_seed.hypothesis")

        if task_type in (ExtractionTaskType.TRADE_LESSON, ExtractionTaskType.COMPREHENSIVE) and lesson_payload and not is_abstained:
            # Lesson critical field: proposed_change
            has_lesson_span = any("proposed_change" in s.field_name for s in valid_spans)
            if not has_lesson_span:
                missing_support = True
                missing_support_reason = "Missing valid source span for trade_lesson.proposed_change."
                missing_fields.append("trade_lesson.proposed_change")

        if missing_support and not is_abstained:
            is_abstained = True
            abstention_reason = AbstentionReason.MISSING_CRITICAL_SUPPORT.value

        # Check confidence threshold
        if intent_payload and intent_payload.confidence < _LOW_CONFIDENCE_THRESHOLD and not is_abstained:
            is_abstained = True
            abstention_reason = AbstentionReason.CONFIDENCE_BELOW_THRESHOLD.value

        cost_usd = self.calculate_cost(input_tokens, output_tokens)
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
            model_identity=request.model_id or self._default_model,
            prompt_identity=_DEFAULT_PROMPT_VERSION,
            config_digest=hashlib.sha256(json.dumps(self._token_limits, sort_keys=True).encode("utf-8")).hexdigest()[:16],
            failure_code=ExtractionFailureCode.MISSING_SUPPORT.value if missing_support else None,
            failure_message=missing_support_reason or None,
            usage={"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens},
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            retry_count=retry_count,
        )
