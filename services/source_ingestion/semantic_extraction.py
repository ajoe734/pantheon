"""Single typed semantic extraction contract and deterministic baseline.

SIMPLIFY-EXTRACTION-CONTRACT-FOUNDATION-001:
Delivers the single typed semantic extraction contract, enums, admission guard,
and deterministic baseline extractor before consumer migration.

Reuses existing intent, seed, and lesson enums without introducing duplicate
stores, duplicate projectors, or second writers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import uuid

from services.source_ingestion.interaction_intent_classifier import (
    InteractionPrimaryIntent,
    classify_interaction_intent,
)
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeedStatus,
)
from services.source_ingestion.trainer_seed_bridge import (
    TrainerSeedKind,
)
from services.source_ingestion.interaction_source_store import (
    InteractionRedactionStatus,
    InteractionSourceRecord,
    InteractionSourceSurface,
    InteractionVisibility,
)
from services.source_ingestion.redaction_guard import (
    _BROKER_REF_PATTERNS,
    _CAPITAL_AMOUNT_PATTERNS,
    _CREDENTIAL_PATTERNS,
    _PII_PATTERNS,
    _PRIVATE_NOTE_MARKERS,
    _RAW_TRANSCRIPT_PATTERNS,
)


_SCHEMA_VERSION = "semantic_extraction.v1"
_DEFAULT_PROMPT_VERSION = "semantic_extraction_prompt.v1"
_LOW_CONFIDENCE_THRESHOLD = 0.60
_MAX_TURN_DEADLINE_SECONDS = 15.0
_TARGET_P95_SECONDS = 10.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(ts_str: str) -> Optional[datetime]:
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        clean = ts_str.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ExtractionTaskType(str, Enum):
    INTENT = "intent"
    STRATEGY_SEED = "strategy_seed"
    TRADE_LESSON = "trade_lesson"
    COMPREHENSIVE = "comprehensive"


class AbstentionReason(str, Enum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    AMBIGUOUS_INTENT = "ambiguous_intent"
    CONFIDENCE_BELOW_THRESHOLD = "confidence_below_threshold"
    UNSUPPORTED_SOURCE = "unsupported_source"
    ADMISSION_DENIED = "admission_denied"
    MODEL_REFUSAL = "model_refusal"
    SCHEMA_VIOLATION = "schema_violation"
    MISSING_CRITICAL_SUPPORT = "missing_critical_support"
    TIMEOUT = "timeout"
    BUDGET_BREACH = "budget_breach"


class ExtractionFailureCode(str, Enum):
    ADMISSION_DENIED = "admission_denied"
    INVALID_SCHEMA = "invalid_schema"
    WRONG_TOOL = "wrong_tool"
    MISSING_SUPPORT = "missing_support"
    REFUSAL = "refusal"
    INCOMPLETE_RESPONSE = "incomplete_response"
    TIMEOUT = "timeout"
    BUDGET_BREACH = "budget_breach"
    TRANSPORT_ERROR = "transport_error"


# ---------------------------------------------------------------------------
# Data Models: Spans and Payloads
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceSpan:
    """Exact source text character span supporting an extracted field."""

    field_name: str
    start_char: int
    end_char: int
    exact_text: str

    def is_valid(self, source_text: str) -> bool:
        if self.start_char < 0 or self.end_char > len(source_text) or self.start_char >= self.end_char:
            return False
        return source_text[self.start_char:self.end_char] == self.exact_text

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "exact_text": self.exact_text,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceSpan:
        return cls(
            field_name=str(data.get("field_name") or ""),
            start_char=int(data.get("start_char", 0)),
            end_char=int(data.get("end_char", 0)),
            exact_text=str(data.get("exact_text") or ""),
        )


@dataclass(frozen=True)
class IntentExtractionPayload:
    """Structured intent classification payload."""

    primary_intent: str
    confidence: float
    secondary_intents: Tuple[str, ...] = field(default_factory=tuple)
    requires_human_review: bool = False
    archive_only: bool = False
    matched_signals: Tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_intent": self.primary_intent,
            "confidence": self.confidence,
            "secondary_intents": list(self.secondary_intents),
            "requires_human_review": self.requires_human_review,
            "archive_only": self.archive_only,
            "matched_signals": list(self.matched_signals),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IntentExtractionPayload:
        raw_intent = data.get("primary_intent")
        if not raw_intent or not isinstance(raw_intent, str):
            raise ValueError("IntentExtractionPayload requires a valid string primary_intent")
        valid_intents = {i.value for i in InteractionPrimaryIntent}
        if raw_intent not in valid_intents:
            raise ValueError(f"Invalid primary_intent {raw_intent!r}; must be one of {sorted(valid_intents)}")
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid confidence: {exc}") from exc
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"Confidence {confidence} out of valid bounds [0.0, 1.0]")
        secondary = tuple(str(x) for x in data.get("secondary_intents") or ())
        for sec in secondary:
            if sec not in valid_intents:
                raise ValueError(f"Invalid secondary_intent {sec!r}")

        return cls(
            primary_intent=raw_intent,
            confidence=confidence,
            secondary_intents=secondary,
            requires_human_review=bool(data.get("requires_human_review", False)),
            archive_only=bool(data.get("archive_only", False)),
            matched_signals=tuple(str(x) for x in data.get("matched_signals") or ()),
            reason=str(data.get("reason") or ""),
        )


@dataclass(frozen=True)
class StrategySeedExtractionPayload:
    """Structured strategy spec seed extraction payload."""

    hypothesis: str
    asset_class: Tuple[str, ...]
    market_scope: Tuple[str, ...]
    required_data: Tuple[str, ...]
    confidence: float
    seed_kind: str = TrainerSeedKind.NEW_STRATEGY.value
    status: str = StrategySpecSeedStatus.DRAFT.value
    holding_period: Optional[str] = None
    backend_hint: Optional[str] = None
    feature_hints: Tuple[str, ...] = field(default_factory=tuple)
    label_hints: Tuple[str, ...] = field(default_factory=tuple)
    risk_notes: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "asset_class": list(self.asset_class),
            "market_scope": list(self.market_scope),
            "required_data": list(self.required_data),
            "confidence": self.confidence,
            "seed_kind": self.seed_kind,
            "status": self.status,
            "holding_period": self.holding_period,
            "backend_hint": self.backend_hint,
            "feature_hints": list(self.feature_hints),
            "label_hints": list(self.label_hints),
            "risk_notes": list(self.risk_notes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StrategySeedExtractionPayload:
        hypothesis = str(data.get("hypothesis") or "").strip()
        if not hypothesis:
            raise ValueError("StrategySeedExtractionPayload requires non-empty hypothesis")
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid confidence: {exc}") from exc
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"Confidence {confidence} out of valid bounds [0.0, 1.0]")
        seed_kind = str(data.get("seed_kind") or TrainerSeedKind.NEW_STRATEGY.value)
        valid_kinds = {k.value for k in TrainerSeedKind}
        if seed_kind not in valid_kinds:
            raise ValueError(f"Invalid seed_kind {seed_kind!r}")
        status = str(data.get("status") or StrategySpecSeedStatus.DRAFT.value)
        valid_statuses = {s.value for s in StrategySpecSeedStatus}
        if status not in valid_statuses:
            raise ValueError(f"Invalid status {status!r}")

        return cls(
            hypothesis=hypothesis,
            asset_class=tuple(str(x) for x in data.get("asset_class") or ()),
            market_scope=tuple(str(x) for x in data.get("market_scope") or ()),
            required_data=tuple(str(x) for x in data.get("required_data") or ()),
            confidence=confidence,
            seed_kind=seed_kind,
            status=status,
            holding_period=str(data["holding_period"]) if data.get("holding_period") is not None else None,
            backend_hint=str(data["backend_hint"]) if data.get("backend_hint") is not None else None,
            feature_hints=tuple(str(x) for x in data.get("feature_hints") or ()),
            label_hints=tuple(str(x) for x in data.get("label_hints") or ()),
            risk_notes=tuple(str(x) for x in data.get("risk_notes") or ()),
        )


@dataclass(frozen=True)
class TradeLessonExtractionPayload:
    """Structured trade lesson candidate payload."""

    scope: str
    proposed_change: str
    confidence: float
    review_state: str = "proposed"
    reflection_version: str = "v1"
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "proposed_change": self.proposed_change,
            "confidence": self.confidence,
            "review_state": self.review_state,
            "reflection_version": self.reflection_version,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TradeLessonExtractionPayload:
        proposed_change = str(data.get("proposed_change") or "").strip()
        if not proposed_change:
            raise ValueError("TradeLessonExtractionPayload requires non-empty proposed_change")
        scope = str(data.get("scope") or "strategy").strip()
        if not scope:
            raise ValueError("TradeLessonExtractionPayload requires non-empty scope")
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid confidence: {exc}") from exc
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"Confidence {confidence} out of valid bounds [0.0, 1.0]")
        review_state = str(data.get("review_state") or "proposed")
        valid_review_states = {"proposed", "pending_review", "endorsed", "merged", "quarantined", "rejected", "expired"}
        if review_state not in valid_review_states:
            raise ValueError(f"Invalid review_state {review_state!r}")

        return cls(
            scope=scope,
            proposed_change=proposed_change,
            confidence=confidence,
            review_state=review_state,
            reflection_version=str(data.get("reflection_version") or "v1"),
            rationale=str(data.get("rationale") or ""),
        )


# ---------------------------------------------------------------------------
# Admission Decision & Checker
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdmissionDecision:
    """Structured decision returned by pre-call admission checks."""

    admitted: bool
    denial_code: Optional[str] = None
    denial_reason: Optional[str] = None
    findings: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "denial_code": self.denial_code,
            "denial_reason": self.denial_reason,
            "findings": list(self.findings),
        }


class SemanticExtractionAdmission:
    """Enforces source, redaction, tenant, license, and point-in-time admission.

    Must run BEFORE any model call. If admission fails, model dispatch is
    strictly prohibited (zero model turns).
    """

    PROHIBITED_LICENSES: frozenset[str] = frozenset({
        "prohibited",
        "restricted_commercial",
        "expired",
        "none",
        "unauthorized",
        "unlicensed",
        "proprietary_unlicensed",
    })

    PERMITTED_LICENSES: frozenset[str] = frozenset({
        "internal",
        "open",
        "vendor_research",
        "official_reference",
        "enterprise_research",
        "openalex",
        "mit",
        "apache-2.0",
        "cc-by-4.0",
        "dev_paper_simulation",
    })

    @classmethod
    def check(cls, request: SemanticExtractionRequest) -> AdmissionDecision:
        # 1. Source ID admission
        source_id = str(request.source_id or "").strip()
        if not source_id:
            return AdmissionDecision(
                admitted=False,
                denial_code="SOURCE_ID_REQUIRED",
                denial_reason="source_id is required for semantic extraction",
            )

        # 2. Tenant admission
        tenant_id = str(request.tenant_id or "").strip()
        if not tenant_id:
            return AdmissionDecision(
                admitted=False,
                denial_code="TENANT_REQUIRED",
                denial_reason="tenant_id is required for tenant-isolated semantic extraction",
            )
        if re.search(r"[\s/\\'\"]", tenant_id):
            return AdmissionDecision(
                admitted=False,
                denial_code="INVALID_TENANT_ID",
                denial_reason=f"tenant_id {tenant_id!r} contains invalid characters",
            )

        # 3. Source status admission
        status = str(request.source_status or "").strip().lower()
        if not status or status in ("rejected", "prohibited", "quarantined", "expired", "archived", "unverified", "unadmitted", "deleted"):
            return AdmissionDecision(
                admitted=False,
                denial_code="SOURCE_STATUS_REJECTED",
                denial_reason=f"source {request.source_id!r} has rejected or expired status {status!r}",
            )

        # 4. License scope admission
        license_scope = str(request.license_scope or "").strip().lower()
        if not license_scope or license_scope in cls.PROHIBITED_LICENSES or license_scope not in cls.PERMITTED_LICENSES:
            return AdmissionDecision(
                admitted=False,
                denial_code="LICENSE_SCOPE_PROHIBITED",
                denial_reason=f"license_scope {license_scope!r} is prohibited, unlicensed, or not permitted",
            )

        # 5. Point-in-time (as-of) admission
        as_of_dt: Optional[datetime] = None
        event_dt: Optional[datetime] = None
        if request.as_of:
            as_of_dt = _parse_iso(request.as_of)
            if as_of_dt is None:
                return AdmissionDecision(
                    admitted=False,
                    denial_code="MALFORMED_TIMESTAMP",
                    denial_reason=f"as_of timestamp {request.as_of!r} is not a valid ISO-8601 timestamp",
                )
        if request.event_time:
            event_dt = _parse_iso(request.event_time)
            if event_dt is None:
                return AdmissionDecision(
                    admitted=False,
                    denial_code="MALFORMED_TIMESTAMP",
                    denial_reason=f"event_time timestamp {request.event_time!r} is not a valid ISO-8601 timestamp",
                )
        if as_of_dt is not None and event_dt is not None:
            if event_dt > as_of_dt:
                return AdmissionDecision(
                    admitted=False,
                    denial_code="POINT_IN_TIME_VIOLATION",
                    denial_reason=(
                        f"event_time {request.event_time} is after as_of boundary {request.as_of} "
                        "(lookahead breach)"
                    ),
                )

        # 5. Redaction / sensitive data admission
        findings: list[str] = []
        text = request.text or ""

        # Check private note markers
        for marker in _PRIVATE_NOTE_MARKERS:
            if marker in text:
                findings.append(f"private_marker:{marker}")

        # Check raw transcript markers (unredacted raw chat/transcript)
        for pattern in _RAW_TRANSCRIPT_PATTERNS:
            if pattern.search(text):
                findings.append("raw_transcript_prompt")
                break

        # Check PII
        for name, pattern in _PII_PATTERNS:
            if pattern.search(text):
                findings.append(f"pii:{name}")

        # Check credentials
        for name, pattern in _CREDENTIAL_PATTERNS:
            if pattern.search(text):
                findings.append(f"credential:{name}")

        # Check capital amounts
        for name, pattern in _CAPITAL_AMOUNT_PATTERNS:
            if pattern.search(text):
                findings.append(f"capital_amount:{name}")

        # Check broker references
        for name, pattern in _BROKER_REF_PATTERNS:
            if pattern.search(text):
                findings.append(f"broker_ref:{name}")

        if findings:
            return AdmissionDecision(
                admitted=False,
                denial_code="REDACTION_VIOLATION",
                denial_reason=f"text contains sensitive or unredacted information: {', '.join(findings)}",
                findings=tuple(findings),
            )

        return AdmissionDecision(admitted=True)


# ---------------------------------------------------------------------------
# Request & Result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemanticExtractionRequest:
    """Request object for typed semantic extraction."""

    source_id: str
    text: str
    task_type: ExtractionTaskType | str = ExtractionTaskType.COMPREHENSIVE
    tenant_id: str = "default_tenant"
    source_type: str = "internal_note"
    source_status: str = "raw"
    license_scope: str = "internal"
    event_time: Optional[str] = None
    as_of: Optional[str] = None
    visibility: Optional[str] = "internal"
    trace_id: Optional[str] = None
    operator_id: Optional[str] = None
    model_id: Optional[str] = None
    timeout_seconds: Optional[float] = _TARGET_P95_SECONDS
    max_retries: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def normalized_task_type(self) -> ExtractionTaskType:
        if isinstance(self.task_type, ExtractionTaskType):
            return self.task_type
        try:
            return ExtractionTaskType(str(self.task_type).strip().lower())
        except ValueError:
            return ExtractionTaskType.COMPREHENSIVE


@dataclass(frozen=True)
class SemanticExtractionResult:
    """Auditable result of a semantic extraction request."""

    extraction_id: str
    source_id: str
    tenant_id: str
    task_type: str
    status: str  # "completed", "abstained", "failed"
    is_abstained: bool = False
    abstention_reason: Optional[str] = None
    intent: Optional[IntentExtractionPayload] = None
    strategy_seed: Optional[StrategySeedExtractionPayload] = None
    trade_lesson: Optional[TradeLessonExtractionPayload] = None
    supported_fields: Tuple[str, ...] = field(default_factory=tuple)
    source_spans: Tuple[SourceSpan, ...] = field(default_factory=tuple)
    missing_fields: Tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = _SCHEMA_VERSION
    schema_id: str = ""
    model_identity: str = ""
    prompt_identity: str = ""
    config_digest: str = ""
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    retry_count: int = 0
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "extraction_id": self.extraction_id,
            "source_id": self.source_id,
            "tenant_id": self.tenant_id,
            "task_type": self.task_type,
            "status": self.status,
            "is_abstained": self.is_abstained,
            "abstention_reason": self.abstention_reason,
            "intent": self.intent.to_dict() if self.intent else None,
            "strategy_seed": self.strategy_seed.to_dict() if self.strategy_seed else None,
            "trade_lesson": self.trade_lesson.to_dict() if self.trade_lesson else None,
            "supported_fields": list(self.supported_fields),
            "source_spans": [span.to_dict() for span in self.source_spans],
            "missing_fields": list(self.missing_fields),
            "schema_version": self.schema_version,
            "schema_id": self.schema_id,
            "model_identity": self.model_identity,
            "prompt_identity": self.prompt_identity,
            "config_digest": self.config_digest,
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
            "usage": dict(self.usage),
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "retry_count": self.retry_count,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SemanticExtractionResult:
        intent_data = data.get("intent")
        seed_data = data.get("strategy_seed")
        lesson_data = data.get("trade_lesson")
        spans_data = data.get("source_spans") or []

        return cls(
            extraction_id=str(data.get("extraction_id") or str(uuid.uuid4())),
            source_id=str(data.get("source_id") or ""),
            tenant_id=str(data.get("tenant_id") or ""),
            task_type=str(data.get("task_type") or ExtractionTaskType.COMPREHENSIVE.value),
            status=str(data.get("status") or "completed"),
            is_abstained=bool(data.get("is_abstained", False)),
            abstention_reason=str(data["abstention_reason"]) if data.get("abstention_reason") else None,
            intent=IntentExtractionPayload.from_dict(intent_data) if intent_data else None,
            strategy_seed=StrategySeedExtractionPayload.from_dict(seed_data) if seed_data else None,
            trade_lesson=TradeLessonExtractionPayload.from_dict(lesson_data) if lesson_data else None,
            supported_fields=tuple(str(x) for x in data.get("supported_fields") or ()),
            source_spans=tuple(SourceSpan.from_dict(x) for x in spans_data),
            missing_fields=tuple(str(x) for x in data.get("missing_fields") or ()),
            schema_version=str(data.get("schema_version") or _SCHEMA_VERSION),
            schema_id=str(data.get("schema_id") or ""),
            model_identity=str(data.get("model_identity") or ""),
            prompt_identity=str(data.get("prompt_identity") or ""),
            config_digest=str(data.get("config_digest") or ""),
            failure_code=str(data["failure_code"]) if data.get("failure_code") else None,
            failure_message=str(data["failure_message"]) if data.get("failure_message") else None,
            usage=dict(data.get("usage") or {}),
            cost_usd=float(data.get("cost_usd", 0.0)),
            latency_ms=float(data.get("latency_ms", 0.0)),
            retry_count=int(data.get("retry_count", 0)),
            created_at=str(data.get("created_at") or _utc_now()),
        )


# ---------------------------------------------------------------------------
# Strict Extraction Tool Schema for emit_extraction
# ---------------------------------------------------------------------------

def get_semantic_extraction_json_schema(task_type: ExtractionTaskType) -> dict[str, Any]:
    """Generates the strict JSON Schema for emit_extraction parameters."""
    intent_schema = {
        "type": "object",
        "properties": {
            "primary_intent": {
                "type": "string",
                "enum": [i.value for i in InteractionPrimaryIntent],
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "secondary_intents": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [i.value for i in InteractionPrimaryIntent],
                },
            },
            "requires_human_review": {"type": "boolean"},
            "archive_only": {"type": "boolean"},
            "matched_signals": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
        },
        "required": ["primary_intent", "confidence", "reason"],
        "additionalProperties": False,
    }

    seed_schema = {
        "type": "object",
        "properties": {
            "hypothesis": {"type": "string", "minLength": 1},
            "asset_class": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "market_scope": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "required_data": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "seed_kind": {
                "type": "string",
                "enum": [k.value for k in TrainerSeedKind],
            },
            "status": {
                "type": "string",
                "enum": [s.value for s in StrategySpecSeedStatus],
            },
            "holding_period": {"type": ["string", "null"]},
            "backend_hint": {"type": ["string", "null"]},
            "feature_hints": {"type": "array", "items": {"type": "string"}},
            "label_hints": {"type": "array", "items": {"type": "string"}},
            "risk_notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["hypothesis", "asset_class", "market_scope", "required_data", "confidence"],
        "additionalProperties": False,
    }

    lesson_schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "minLength": 1},
            "proposed_change": {"type": "string", "minLength": 1},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "review_state": {
                "type": "string",
                "enum": ["proposed", "pending_review", "endorsed", "merged", "quarantined", "rejected", "expired"],
            },
            "reflection_version": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": ["scope", "proposed_change", "confidence"],
        "additionalProperties": False,
    }

    span_schema = {
        "type": "object",
        "properties": {
            "field_name": {"type": "string"},
            "start_char": {"type": "integer", "minimum": 0},
            "end_char": {"type": "integer", "minimum": 0},
            "exact_text": {"type": "string"},
        },
        "required": ["field_name", "start_char", "end_char", "exact_text"],
        "additionalProperties": False,
    }

    properties: dict[str, Any] = {
        "is_abstained": {"type": "boolean"},
        "abstention_reason": {
            "type": ["string", "null"],
            "enum": [r.value for r in AbstentionReason] + [None],
        },
        "source_spans": {"type": "array", "items": span_schema},
    }
    required: list[str] = ["is_abstained", "source_spans"]

    then_clause: dict[str, Any] = {
        "properties": {
            "source_spans": {"minItems": 1},
        }
    }

    if task_type == ExtractionTaskType.INTENT:
        properties["intent"] = {"anyOf": [{"type": "null"}, intent_schema]}
        then_clause["required"] = ["intent"]
        then_clause["properties"]["intent"] = intent_schema
    elif task_type == ExtractionTaskType.STRATEGY_SEED:
        properties["strategy_seed"] = {"anyOf": [{"type": "null"}, seed_schema]}
        then_clause["required"] = ["strategy_seed"]
        then_clause["properties"]["strategy_seed"] = seed_schema
    elif task_type == ExtractionTaskType.TRADE_LESSON:
        properties["trade_lesson"] = {"anyOf": [{"type": "null"}, lesson_schema]}
        then_clause["required"] = ["trade_lesson"]
        then_clause["properties"]["trade_lesson"] = lesson_schema
    elif task_type == ExtractionTaskType.COMPREHENSIVE:
        properties["intent"] = {"anyOf": [{"type": "null"}, intent_schema]}
        properties["strategy_seed"] = {"anyOf": [{"type": "null"}, seed_schema]}
        properties["trade_lesson"] = {"anyOf": [{"type": "null"}, lesson_schema]}
        then_clause["anyOf"] = [
            {"required": ["intent"], "properties": {"intent": intent_schema}},
            {"required": ["strategy_seed"], "properties": {"strategy_seed": seed_schema}},
            {"required": ["trade_lesson"], "properties": {"trade_lesson": lesson_schema}},
        ]

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
        "allOf": [
            {
                "if": {
                    "properties": {"is_abstained": {"const": False}}
                },
                "then": then_clause,
                "else": {
                    "required": ["abstention_reason"],
                },
            }
        ],
    }


def compute_schema_id(schema: dict[str, Any]) -> str:
    serialized = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Deterministic Baseline Extractor
# ---------------------------------------------------------------------------

class DeterministicBaselineExtractor:
    """Deterministic, rule-based baseline extractor.

    Serves as the reference baseline for evaluation and fallback.
    Executes the existing production classifier unchanged.
    """

    BASELINE_MODEL_ID = "deterministic-baseline.v1"

    @classmethod
    def _find_spans(cls, text: str, field_name: str, query: str) -> list[SourceSpan]:
        spans: list[SourceSpan] = []
        if not query or not query.strip():
            return spans
        pos = 0
        text_lower = text.lower()
        q_lower = query.lower()
        while True:
            idx = text_lower.find(q_lower, pos)
            if idx == -1:
                break
            end = idx + len(query)
            spans.append(SourceSpan(
                field_name=field_name,
                start_char=idx,
                end_char=end,
                exact_text=text[idx:end],
            ))
            pos = end
            if len(spans) >= 3:  # limit to top 3 spans per field
                break
        return spans

    @classmethod
    def extract(cls, request: SemanticExtractionRequest) -> SemanticExtractionResult:
        start_time = datetime.now(timezone.utc)
        text = request.text.strip()
        task_type = request.normalized_task_type()

        # Check admission first
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
                model_identity=cls.BASELINE_MODEL_ID,
                prompt_identity=_DEFAULT_PROMPT_VERSION,
                schema_id=compute_schema_id(get_semantic_extraction_json_schema(task_type)),
            )

        if not text:
            return SemanticExtractionResult(
                extraction_id=str(uuid.uuid4()),
                source_id=request.source_id,
                tenant_id=request.tenant_id,
                task_type=task_type.value,
                status="abstained",
                is_abstained=True,
                abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE.value,
                failure_code=ExtractionFailureCode.MISSING_SUPPORT.value,
                failure_message="Source text is empty.",
                model_identity=cls.BASELINE_MODEL_ID,
                prompt_identity=_DEFAULT_PROMPT_VERSION,
                schema_id=compute_schema_id(get_semantic_extraction_json_schema(task_type)),
            )

        spans: list[SourceSpan] = []
        supported_fields: list[str] = []
        missing_fields: list[str] = []

        intent_payload: Optional[IntentExtractionPayload] = None
        seed_payload: Optional[StrategySeedExtractionPayload] = None
        lesson_payload: Optional[TradeLessonExtractionPayload] = None

        # 1. Intent extraction via existing production classifier unchanged
        mock_record = InteractionSourceRecord(
            interaction_id=request.source_id,
            source_surface=InteractionSourceSurface.TRAINER.value if hasattr(InteractionSourceSurface, "TRAINER") else "trainer",
            actor_type="user",
            persona_refs=["persona-default"],
            session_id=f"session-{request.source_id}",
            raw_ref="evidence://source/deterministic-baseline",
            summary=text,
            evidence_refs=[
                {
                    "ref": "evidence://source/deterministic-baseline",
                    "kind": "raw_interaction",
                    "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            ],
            visibility=InteractionVisibility.SHARED.value if hasattr(InteractionVisibility, "SHARED") else "shared",
            redaction_status=InteractionRedactionStatus.PASSED.value,
        )
        raw_intent = classify_interaction_intent(mock_record)

        primary_intent_val = raw_intent.primary_intent.value
        confidence_val = raw_intent.confidence
        matched_signals_list = list(raw_intent.matched_signals)
        secondary_intents_list = [x.value for x in raw_intent.secondary_intents]
        requires_human_review = raw_intent.requires_human_review
        archive_only = raw_intent.archive_only
        reason = raw_intent.reason

        if task_type in (ExtractionTaskType.INTENT, ExtractionTaskType.COMPREHENSIVE):
            intent_payload = IntentExtractionPayload(
                primary_intent=primary_intent_val,
                confidence=confidence_val,
                secondary_intents=tuple(secondary_intents_list),
                requires_human_review=requires_human_review,
                archive_only=archive_only,
                matched_signals=tuple(matched_signals_list),
                reason=reason,
            )

            # Find spans for matched signals
            for sig in matched_signals_list:
                sig_clean = sig.split(":")[-1] if ":" in sig else sig
                found = cls._find_spans(text, "intent.matched_signals", sig_clean)
                spans.extend(found)

            if spans:
                supported_fields.append("intent.primary_intent")

        # 2. Strategy Spec Seed and Trade Lesson:
        # The existing production baseline has no NLP seed/lesson extractor.
        # Run unchanged: leave seed_payload and lesson_payload as None.
        if task_type in (ExtractionTaskType.STRATEGY_SEED, ExtractionTaskType.COMPREHENSIVE):
            missing_fields.append("strategy_seed.hypothesis")
        if task_type in (ExtractionTaskType.TRADE_LESSON, ExtractionTaskType.COMPREHENSIVE):
            missing_fields.append("trade_lesson.proposed_change")

        # Determine abstention
        is_abstained = False
        abstention_reason: Optional[str] = None
        if task_type in (ExtractionTaskType.STRATEGY_SEED, ExtractionTaskType.TRADE_LESSON):
            # Production baseline does not support unstructured seed/lesson extraction
            is_abstained = True
            abstention_reason = AbstentionReason.UNSUPPORTED_SOURCE.value
        elif primary_intent_val == InteractionPrimaryIntent.NON_STRATEGY.value:
            is_abstained = True
            abstention_reason = AbstentionReason.INSUFFICIENT_EVIDENCE.value
        elif confidence_val < _LOW_CONFIDENCE_THRESHOLD:
            is_abstained = True
            abstention_reason = AbstentionReason.CONFIDENCE_BELOW_THRESHOLD.value
        elif requires_human_review:
            is_abstained = True
            abstention_reason = AbstentionReason.AMBIGUOUS_INTENT.value

        end_time = datetime.now(timezone.utc)
        latency_ms = (end_time - start_time).total_seconds() * 1000.0

        schema = get_semantic_extraction_json_schema(task_type)
        schema_id = compute_schema_id(schema)

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
            source_spans=tuple(spans),
            missing_fields=tuple(missing_fields),
            schema_version=_SCHEMA_VERSION,
            schema_id=schema_id,
            model_identity=cls.BASELINE_MODEL_ID,
            prompt_identity=_DEFAULT_PROMPT_VERSION,
            config_digest=hashlib.sha256(b"deterministic").hexdigest()[:16],
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            cost_usd=0.0,
            latency_ms=latency_ms,
            retry_count=0,
        )
