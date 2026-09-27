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
    try:
        clean = ts_str.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(clean)
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
        return cls(
            primary_intent=str(data.get("primary_intent") or InteractionPrimaryIntent.NON_STRATEGY.value),
            confidence=float(data.get("confidence", 0.0)),
            secondary_intents=tuple(str(x) for x in data.get("secondary_intents") or ()),
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
        return cls(
            hypothesis=str(data.get("hypothesis") or ""),
            asset_class=tuple(str(x) for x in data.get("asset_class") or ()),
            market_scope=tuple(str(x) for x in data.get("market_scope") or ()),
            required_data=tuple(str(x) for x in data.get("required_data") or ()),
            confidence=float(data.get("confidence", 0.0)),
            seed_kind=str(data.get("seed_kind") or TrainerSeedKind.NEW_STRATEGY.value),
            status=str(data.get("status") or StrategySpecSeedStatus.DRAFT.value),
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
        return cls(
            scope=str(data.get("scope") or "strategy"),
            proposed_change=str(data.get("proposed_change") or ""),
            confidence=float(data.get("confidence", 0.0)),
            review_state=str(data.get("review_state") or "proposed"),
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
        # 1. Tenant admission
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

        # 2. Source status admission
        status = str(request.source_status or "").strip().lower()
        if status in ("rejected", "prohibited", "quarantined"):
            return AdmissionDecision(
                admitted=False,
                denial_code="SOURCE_STATUS_REJECTED",
                denial_reason=f"source {request.source_id!r} has rejected status {status!r}",
            )

        # 3. License scope admission
        license_scope = str(request.license_scope or "").strip().lower()
        if not license_scope or license_scope in cls.PROHIBITED_LICENSES:
            return AdmissionDecision(
                admitted=False,
                denial_code="LICENSE_SCOPE_PROHIBITED",
                denial_reason=f"license_scope {license_scope!r} is prohibited or missing",
            )

        # 4. Point-in-time (as-of) admission
        if request.as_of and request.event_time:
            as_of_dt = _parse_iso(request.as_of)
            event_dt = _parse_iso(request.event_time)
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

    if task_type in (ExtractionTaskType.INTENT, ExtractionTaskType.COMPREHENSIVE):
        properties["intent"] = {"type": ["object", "null"], **intent_schema}
    if task_type in (ExtractionTaskType.STRATEGY_SEED, ExtractionTaskType.COMPREHENSIVE):
        properties["strategy_seed"] = {"type": ["object", "null"], **seed_schema}
    if task_type in (ExtractionTaskType.TRADE_LESSON, ExtractionTaskType.COMPREHENSIVE):
        properties["trade_lesson"] = {"type": ["object", "null"], **lesson_schema}

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
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
    Produces auditable exact character spans for extracted fields.
    """

    BASELINE_MODEL_ID = "deterministic-baseline.v1"

    # Known asset class mappings
    _ASSET_CLASS_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("equities", ("equity", "equities", "stock", "stocks", "share", "shares", "個股", "股票", "現貨", "台股", "美股")),
        ("futures", ("future", "futures", "期貨", "指期", "台指期", "tx", "es", "nq")),
        ("options", ("option", "options", "選擇權", "期權", "call", "put", "volatility", "iv")),
        ("crypto", ("crypto", "cryptocurrency", "bitcoin", "btc", "ethereum", "eth", "加密貨幣", "虛擬貨幣")),
        ("fx", ("fx", "forex", "currency", "外匯", "匯率", "usdtwd", "eurusd", "usdjpy")),
    )

    _MARKET_SCOPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("tw", ("taiwan", "twse", "tpex", "taiex", "台股", "台灣", "臺灣", "tw")),
        ("us", ("us", "nyse", "nasdaq", "s&p", "sp500", "美股", "美國", "usa")),
        ("global", ("global", "cross-asset", "macro", "全球", "跨市場", "宏觀")),
        ("crypto", ("binance", "coinbase", "defi", "crypto", "鏈上", "交易所")),
    )

    _REQUIRED_DATA_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("ohlcv", ("ohlcv", "price", "volume", "k線", "價量", "收盤價", "成交量", "candlestick")),
        ("orderbook", ("orderbook", "depth", "l2", "tick", "bid", "ask", "盤口", "委託簿")),
        ("fundamental", ("financial", "statement", "revenue", "pe", "pb", "財報", "營收", "基本面")),
        ("macro", ("cpi", "fed", "interest rate", "gdp", "通膨", "利率", "總體經濟")),
        ("alternative", ("sentiment", "news", "social", "舆情", "新聞", "社群")),
    )

    _BILINGUAL_INTENT_RULES: tuple[tuple[InteractionPrimaryIntent, tuple[str, ...], tuple[str, ...]], ...] = (
        (
            InteractionPrimaryIntent.STRATEGY_HYPOTHESIS,
            ("策略假說", "動能策略", "突破策略", "均線策略", "多頭策略", "空頭策略", "alpha假說", "量化策略", "配對交易", "統計套利", "跨市場", "因子", "選股", "買進", "做多", "做空", "動能突破", "均值回歸", "交易策略"),
            ("策略", "假說", "指標", "alpha", "進場", "部位"),
        ),
        (
            InteractionPrimaryIntent.RISK_OVERLAY,
            ("風險覆蓋", "風控規則", "停損", "止損", "停利", "最大回撤", "曝險上限", "槓桿限制", "減倉", "平倉", "風險限制", "敞口上限"),
            ("風控", "風險", "回撤", "drawdown"),
        ),
        (
            InteractionPrimaryIntent.EXECUTION_POLICY,
            ("執行政策", "委託路由", "限價單", "市價單", "twap", "vwap", "滑價", "流動性", "拆單", "掛單", "市價委託"),
            ("執行", "委託", "下單", "成交"),
        ),
        (
            InteractionPrimaryIntent.PORTFOLIO_ALLOCATION,
            ("資產配置", "投資組合", "配置權重", "再平衡", "風險平價", "資金分配"),
            ("配置", "權重", "組合"),
        ),
        (
            InteractionPrimaryIntent.PERSONA_POLICY,
            ("角色設定", "人格設定", "交易風格", "交易員人格", "代理人風格"),
            ("風格", "偏好風格"),
        ),
        (
            InteractionPrimaryIntent.PREFERENCE_EXAMPLE,
            ("偏好範例", "少樣本範例", "範例示範", "示範案例"),
            ("範例", "範式"),
        ),
        (
            InteractionPrimaryIntent.NEGATIVE_MEMORY,
            ("負面記憶", "教訓", "失效教訓", "踩雷紀錄", "避免重複", "虧損反思", "交易失誤", "失敗案例"),
            ("反思", "失誤", "踩雷"),
        ),
        (
            InteractionPrimaryIntent.OPERATIONAL_NOTE,
            ("維運筆記", "系統日誌", "例行維護", "伺服器重啟", "系統維護", "維護公告", "排程作業"),
            ("維護", "重啟", "日誌", "公告"),
        ),
    )

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
        text_lower = text.lower()
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

        # 1. Intent extraction
        # Always run intent classification internally to gate non-strategy / low-confidence sources
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

        # Augment with bilingual / Traditional Chinese intent rules if raw intent is non-strategy or low confidence
        primary_intent_val = raw_intent.primary_intent.value
        confidence_val = raw_intent.confidence
        matched_signals_list = list(raw_intent.matched_signals)
        secondary_intents_list = [x.value for x in raw_intent.secondary_intents]
        requires_human_review = raw_intent.requires_human_review

        if primary_intent_val == InteractionPrimaryIntent.NON_STRATEGY.value or confidence_val < _LOW_CONFIDENCE_THRESHOLD:
            for intent_candidate, strong_kws, weak_kws in cls._BILINGUAL_INTENT_RULES:
                matched_strong = [kw for kw in strong_kws if kw.lower() in text_lower]
                if matched_strong:
                    primary_intent_val = intent_candidate.value
                    confidence_val = 0.90
                    requires_human_review = False
                    matched_signals_list.extend([f"bilingual_strong:{kw}" for kw in matched_strong])
                    break
                matched_weak = [kw for kw in weak_kws if kw.lower() in text_lower]
                if matched_weak:
                    primary_intent_val = intent_candidate.value
                    confidence_val = 0.75
                    requires_human_review = False
                    matched_signals_list.extend([f"bilingual_weak:{kw}" for kw in matched_weak])
                    break

        if task_type in (ExtractionTaskType.INTENT, ExtractionTaskType.COMPREHENSIVE):
            intent_payload = IntentExtractionPayload(
                primary_intent=primary_intent_val,
                confidence=confidence_val,
                secondary_intents=tuple(secondary_intents_list),
                requires_human_review=requires_human_review,
                archive_only=raw_intent.archive_only if primary_intent_val == raw_intent.primary_intent.value else False,
                matched_signals=tuple(matched_signals_list),
                reason=f"Matched signals: {', '.join(matched_signals_list)}" if matched_signals_list else raw_intent.reason,
            )
            supported_fields.append("intent.primary_intent")

            # Find spans for matched signals
            for sig in matched_signals_list:
                sig_clean = sig.split(":")[-1] if ":" in sig else sig
                found = cls._find_spans(text, "intent.matched_signals", sig_clean)
                spans.extend(found)

        # 2. Strategy Spec Seed extraction
        if task_type in (ExtractionTaskType.STRATEGY_SEED, ExtractionTaskType.COMPREHENSIVE):
            # Deterministic asset class detection
            detected_asset_classes: list[str] = []
            for asset, kws in cls._ASSET_CLASS_KEYWORDS:
                for kw in kws:
                    if kw in text_lower:
                        if asset not in detected_asset_classes:
                            detected_asset_classes.append(asset)
                            spans.extend(cls._find_spans(text, "strategy_seed.asset_class", kw))
                        break

            # Deterministic market scope detection
            detected_market_scopes: list[str] = []
            for scope, kws in cls._MARKET_SCOPE_KEYWORDS:
                for kw in kws:
                    if kw in text_lower:
                        if scope not in detected_market_scopes:
                            detected_market_scopes.append(scope)
                            spans.extend(cls._find_spans(text, "strategy_seed.market_scope", kw))
                        break

            # Deterministic required data detection
            detected_required_data: list[str] = []
            for dtag, kws in cls._REQUIRED_DATA_KEYWORDS:
                for kw in kws:
                    if kw in text_lower:
                        if dtag not in detected_required_data:
                            detected_required_data.append(dtag)
                            spans.extend(cls._find_spans(text, "strategy_seed.required_data", kw))
                        break

            # Hypothesis detection: first sentence or salient line
            sentences = re.split(r"[。\n.!?]", text)
            hypothesis = sentences[0].strip() if sentences else text[:100]
            if len(hypothesis) < 5 and len(sentences) > 1:
                hypothesis = sentences[1].strip()

            if hypothesis:
                h_spans = cls._find_spans(text, "strategy_seed.hypothesis", hypothesis)
                if h_spans:
                    spans.extend(h_spans)
                    supported_fields.append("strategy_seed.hypothesis")
                else:
                    spans.append(SourceSpan("strategy_seed.hypothesis", 0, min(len(hypothesis), len(text)), text[:min(len(hypothesis), len(text))]))
                    supported_fields.append("strategy_seed.hypothesis")

            if detected_asset_classes:
                supported_fields.append("strategy_seed.asset_class")
            else:
                missing_fields.append("strategy_seed.asset_class")
                detected_asset_classes = ["equities"]

            if detected_market_scopes:
                supported_fields.append("strategy_seed.market_scope")
            else:
                missing_fields.append("strategy_seed.market_scope")
                detected_market_scopes = ["tw"]

            if detected_required_data:
                supported_fields.append("strategy_seed.required_data")
            else:
                missing_fields.append("strategy_seed.required_data")
                detected_required_data = ["ohlcv"]

            confidence = 0.85 if ("strategy_seed.hypothesis" in supported_fields and not missing_fields) else 0.55

            seed_payload = StrategySeedExtractionPayload(
                hypothesis=hypothesis,
                asset_class=tuple(detected_asset_classes),
                market_scope=tuple(detected_market_scopes),
                required_data=tuple(detected_required_data),
                confidence=confidence,
                seed_kind=TrainerSeedKind.NEW_STRATEGY.value,
                status=StrategySpecSeedStatus.DRAFT.value,
            )

        # 3. Trade lesson extraction
        if task_type in (ExtractionTaskType.TRADE_LESSON, ExtractionTaskType.COMPREHENSIVE):
            # Detect lesson scope
            scope = "strategy"
            if any(w in text_lower for w in ("risk", "風控", "止損", "drawdown", "回撤")):
                scope = "risk"
            elif any(w in text_lower for w in ("regime", "牛市", "熊市", "盤整", "震盪")):
                scope = "regime"
            elif any(w in text_lower for w in ("execution", "滑價", "slippage", "latency", "委託")):
                scope = "execution"

            proposed_change = text[:150]
            lesson_spans = cls._find_spans(text, "trade_lesson.proposed_change", proposed_change[:50])
            if lesson_spans:
                spans.extend(lesson_spans)
                supported_fields.append("trade_lesson.proposed_change")
            else:
                spans.append(SourceSpan("trade_lesson.proposed_change", 0, min(50, len(text)), text[:min(50, len(text))]))
                supported_fields.append("trade_lesson.proposed_change")

            lesson_payload = TradeLessonExtractionPayload(
                scope=scope,
                proposed_change=proposed_change,
                confidence=0.80 if intent_payload and intent_payload.primary_intent == InteractionPrimaryIntent.NEGATIVE_MEMORY.value else 0.60,
                review_state="proposed",
                reflection_version="v1",
                rationale="Extracted by deterministic baseline from lesson keywords.",
            )
            supported_fields.append("trade_lesson.scope")

        # Determine abstention
        is_abstained = False
        abstention_reason: Optional[str] = None
        if raw_intent.primary_intent == InteractionPrimaryIntent.NON_STRATEGY and task_type in (
            ExtractionTaskType.STRATEGY_SEED,
            ExtractionTaskType.TRADE_LESSON,
        ):
            is_abstained = True
            abstention_reason = AbstentionReason.UNSUPPORTED_SOURCE.value
        elif intent_payload and intent_payload.confidence < _LOW_CONFIDENCE_THRESHOLD:
            is_abstained = True
            abstention_reason = AbstentionReason.CONFIDENCE_BELOW_THRESHOLD.value
        elif seed_payload and seed_payload.confidence < _LOW_CONFIDENCE_THRESHOLD:
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
