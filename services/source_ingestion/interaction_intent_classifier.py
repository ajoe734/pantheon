"""Deterministic intent classification for interaction-derived seed safety.

The classifier consumes governed InteractionSourceRecord summaries only. It
does not read raw interaction evidence, create seed candidates, write registry
state, or open any execution route.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Mapping, Sequence

from services.source_ingestion.interaction_source_store import (
    InteractionRedactionStatus,
    InteractionSourceRecord,
    InteractionSourceRecordError,
)


_SCHEMA_VERSION = "interaction_intent_classification.v1"
_RECORD_KIND = "intent_classification"
_CLASSIFIER_VERSION = "deterministic.v1"
_LOW_CONFIDENCE_THRESHOLD = 0.60
_AMBIGUITY_MARGIN = 1.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class InteractionIntentClassificationError(ValueError):
    """Raised when an intent classification object is invalid."""


class InteractionPrimaryIntent(str, Enum):
    STRATEGY_HYPOTHESIS = "strategy_hypothesis"
    RISK_OVERLAY = "risk_overlay"
    EXECUTION_POLICY = "execution_policy"
    PORTFOLIO_ALLOCATION = "portfolio_allocation"
    PERSONA_POLICY = "persona_policy"
    PREFERENCE_EXAMPLE = "preference_example"
    NEGATIVE_MEMORY = "negative_memory"
    OPERATIONAL_NOTE = "operational_note"
    NON_STRATEGY = "non_strategy"


@dataclass(frozen=True)
class IntentClassification:
    """Stable classification result for an InteractionSourceRecord."""

    interaction_id: str
    primary_intent: InteractionPrimaryIntent | str
    confidence: float
    requires_human_review: bool
    archive_only: bool
    reason: str
    matched_signals: Sequence[str]
    secondary_intents: Sequence[InteractionPrimaryIntent | str] = field(default_factory=tuple)
    source_surface: str | None = None
    redaction_status: str | None = None
    created_at: str = field(default_factory=_utc_now)
    classifier_version: str = _CLASSIFIER_VERSION
    metadata: Mapping[str, Any] = field(default_factory=dict)
    record_kind: str = field(default=_RECORD_KIND, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_kind", _RECORD_KIND)
        object.__setattr__(self, "interaction_id", _require_text(self.interaction_id, "interaction_id"))
        object.__setattr__(self, "primary_intent", _coerce_intent(self.primary_intent))
        object.__setattr__(self, "confidence", _bounded_confidence(self.confidence))
        object.__setattr__(self, "requires_human_review", bool(self.requires_human_review))
        object.__setattr__(self, "archive_only", bool(self.archive_only))
        object.__setattr__(self, "reason", _require_text(self.reason, "reason"))
        object.__setattr__(self, "matched_signals", _strings(self.matched_signals))
        object.__setattr__(
            self,
            "secondary_intents",
            tuple(_coerce_intent(intent) for intent in self.secondary_intents),
        )
        if not self.classifier_version:
            raise InteractionIntentClassificationError("classifier_version is required")
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "record_kind": self.record_kind,
            "interaction_id": self.interaction_id,
            "primary_intent": self.primary_intent.value,
            "confidence": self.confidence,
            "requires_human_review": self.requires_human_review,
            "archive_only": self.archive_only,
            "reason": self.reason,
            "matched_signals": list(self.matched_signals),
            "secondary_intents": [intent.value for intent in self.secondary_intents],
            "source_surface": self.source_surface,
            "redaction_status": self.redaction_status,
            "created_at": self.created_at,
            "classifier_version": self.classifier_version,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IntentClassification":
        if str(data.get("schema_version", "")) != _SCHEMA_VERSION:
            raise InteractionIntentClassificationError(
                f"schema_version must be '{_SCHEMA_VERSION}'; got '{data.get('schema_version')}'"
            )
        if str(data.get("record_kind", "")) != _RECORD_KIND:
            raise InteractionIntentClassificationError(
                f"record_kind must be '{_RECORD_KIND}'; got '{data.get('record_kind')}'"
            )
        return cls(
            interaction_id=str(data["interaction_id"]),
            primary_intent=str(data["primary_intent"]),
            confidence=float(data["confidence"]),
            requires_human_review=bool(data["requires_human_review"]),
            archive_only=bool(data["archive_only"]),
            reason=str(data["reason"]),
            matched_signals=list(data.get("matched_signals") or []),
            secondary_intents=list(data.get("secondary_intents") or []),
            source_surface=data.get("source_surface"),
            redaction_status=data.get("redaction_status"),
            created_at=str(data.get("created_at") or _utc_now()),
            classifier_version=str(data.get("classifier_version") or _CLASSIFIER_VERSION),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class _IntentRule:
    strong: tuple[str, ...]
    weak: tuple[str, ...] = ()


_RULES: dict[InteractionPrimaryIntent, _IntentRule] = {
    InteractionPrimaryIntent.STRATEGY_HYPOTHESIS: _IntentRule(
        strong=(
            "strategy hypothesis",
            "investable hypothesis",
            "trading hypothesis",
            "alpha hypothesis",
            "alpha signal",
            "factor signal",
            "strategy seed",
            "strategy spec",
            "trading strategy",
            "new strategy",
            "mean reversion",
            "momentum breakout",
            "pairs trade",
            "pair trade",
            "stat arb",
            "statistical arbitrage",
            "predict returns",
            "forward return",
            "rank equities",
            "long short",
            "long/short",
            "cross sectional",
            "cross-sectional",
            "market anomaly",
            "carry trade",
            "backtest alpha",
        ),
        weak=(
            "alpha",
            "factor",
            "signal",
            "backtest",
            "hypothesis",
            "equities",
            "futures",
            "options",
            "crypto",
            "forex",
            "ohlcv",
            "returns",
        ),
    ),
    InteractionPrimaryIntent.RISK_OVERLAY: _IntentRule(
        strong=(
            "risk overlay",
            "risk constraint",
            "risk budget",
            "max drawdown",
            "drawdown cap",
            "exposure cap",
            "position limit",
            "leverage limit",
            "volatility cap",
            "correlation cap",
            "concentration limit",
            "stop loss",
            "tail risk",
            "hedge overlay",
        ),
        weak=(
            "drawdown",
            "risk",
            "exposure",
            "var",
            "cvar",
            "hedge",
            "leverage",
            "concentration",
            "correlation",
        ),
    ),
    InteractionPrimaryIntent.EXECUTION_POLICY: _IntentRule(
        strong=(
            "execution policy",
            "execution constraint",
            "order routing",
            "order route",
            "order type",
            "limit order",
            "market order",
            "broker route",
            "paper route",
            "paper-only",
            "fill policy",
            "slippage guard",
            "lean execution",
        ),
        weak=(
            "broker",
            "ibkr",
            "orders",
            "fills",
            "slippage",
            "latency",
            "route",
            "execution",
        ),
    ),
    InteractionPrimaryIntent.PORTFOLIO_ALLOCATION: _IntentRule(
        strong=(
            "portfolio allocation",
            "capital allocation",
            "allocation policy",
            "persona allocation",
            "target weight",
            "target weights",
            "portfolio weights",
            "rebalance policy",
            "capital pool",
            "risk parity",
        ),
        weak=(
            "allocate",
            "allocation",
            "rebalance",
            "weights",
            "portfolio",
            "sleeve",
            "capital",
        ),
    ),
    InteractionPrimaryIntent.PERSONA_POLICY: _IntentRule(
        strong=(
            "persona policy",
            "coaching note",
            "coach persona",
            "persona coaching",
            "response style",
            "communication style",
            "persona style",
            "persona behavior",
            "persona should",
            "answer style",
            "teaching style",
            "tone guidance",
        ),
        weak=(
            "coach",
            "coaching",
            "style",
            "tone",
            "voice",
            "respond",
            "explain",
            "format",
            "persona",
            "concise",
            "skeptical",
            "mentoring",
        ),
    ),
    InteractionPrimaryIntent.PREFERENCE_EXAMPLE: _IntentRule(
        strong=(
            "preference example",
            "preference learning",
            "preferred response",
            "i prefer",
            "thumbs up",
            "thumbs down",
            "approval example",
            "rejection example",
            "rank this response",
            "better answer",
        ),
        weak=(
            "prefer",
            "preferred",
            "like",
            "dislike",
            "approve",
            "reject",
            "ranking",
            "feedback",
        ),
    ),
    InteractionPrimaryIntent.NEGATIVE_MEMORY: _IntentRule(
        strong=(
            "negative memory",
            "do not repeat",
            "don't repeat",
            "never repeat",
            "never use",
            "retired strategy",
            "rejected candidate",
            "failed experiment",
            "past failure",
            "blocked strategy",
            "avoid repeating",
        ),
        weak=(
            "avoid",
            "retired",
            "rejected",
            "failed",
            "postmortem",
            "regression",
            "incident",
        ),
    ),
    InteractionPrimaryIntent.OPERATIONAL_NOTE: _IntentRule(
        strong=(
            "operational note",
            "runbook",
            "deployment note",
            "incident update",
            "monitoring note",
            "health check",
            "supervisor health",
            "ci failure",
            "pull request",
            "merge queue",
        ),
        weak=(
            "deploy",
            "deployment",
            "monitor",
            "dashboard",
            "logging",
            "restart",
            "status",
            "task",
            "pr",
            "merge",
            "docs",
        ),
    ),
    InteractionPrimaryIntent.NON_STRATEGY: _IntentRule(
        strong=(
            "not strategy related",
            "non strategy",
            "non-strategy",
            "schedule lunch",
            "team lunch",
            "vacation plan",
            "calendar invite",
            "hello",
            "thanks",
            "thank you",
            "weather",
            "joke",
        ),
        weak=(
            "lunch",
            "vacation",
            "calendar",
            "meeting",
            "typo",
            "thanks",
            "hello",
        ),
    ),
}

_METADATA_HINT_KEYS = (
    "intent_hint",
    "primary_intent",
    "seed_kind",
    "surface_name",
    "interaction_kind",
    "tags",
    "labels",
)
_SEED_KIND_INTENT_MAP = {
    "new_strategy": InteractionPrimaryIntent.STRATEGY_HYPOTHESIS,
    "strategy_hypothesis": InteractionPrimaryIntent.STRATEGY_HYPOTHESIS,
    "mutation": InteractionPrimaryIntent.STRATEGY_HYPOTHESIS,
    "risk_constraint": InteractionPrimaryIntent.RISK_OVERLAY,
    "risk_overlay": InteractionPrimaryIntent.RISK_OVERLAY,
    "execution_constraint": InteractionPrimaryIntent.EXECUTION_POLICY,
    "execution_policy": InteractionPrimaryIntent.EXECUTION_POLICY,
    "portfolio_allocation": InteractionPrimaryIntent.PORTFOLIO_ALLOCATION,
    "allocation_policy": InteractionPrimaryIntent.PORTFOLIO_ALLOCATION,
    "persona_policy": InteractionPrimaryIntent.PERSONA_POLICY,
    "preference_example": InteractionPrimaryIntent.PREFERENCE_EXAMPLE,
    "negative": InteractionPrimaryIntent.NEGATIVE_MEMORY,
    "negative_memory": InteractionPrimaryIntent.NEGATIVE_MEMORY,
    "operational_note": InteractionPrimaryIntent.OPERATIONAL_NOTE,
    "non_strategy": InteractionPrimaryIntent.NON_STRATEGY,
}


class InteractionIntentClassifier:
    """Keyword and metadata-hint classifier for IDS-003 v1."""

    def classify(self, record: InteractionSourceRecord | Mapping[str, Any]) -> IntentClassification:
        interaction = _coerce_record(record)
        text = _classification_text(interaction)
        scores, signals = _score_text(text)
        _apply_metadata_hints(interaction.metadata, scores, signals)

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0].value))
        top_intent, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0

        if top_score <= 0:
            top_intent = InteractionPrimaryIntent.NON_STRATEGY
            confidence = 0.20
            reason = "no deterministic strategy-seed intent signals matched"
        else:
            confidence = _confidence(top_score, top_score - second_score)
            reason = _reason(top_intent, top_score, second_score)

        ambiguous = second_score > 0 and (top_score - second_score) <= _AMBIGUITY_MARGIN
        requires_human_review = confidence < _LOW_CONFIDENCE_THRESHOLD or ambiguous
        archive_only = top_intent == InteractionPrimaryIntent.NON_STRATEGY
        if interaction.redaction_status == InteractionRedactionStatus.FAILED:
            requires_human_review = True
            reason = f"{reason}; redaction_status=failed requires safety review"

        secondary_intents = tuple(intent for intent, score in ranked[1:4] if score > 0)
        return IntentClassification(
            interaction_id=interaction.interaction_id,
            primary_intent=top_intent,
            confidence=confidence,
            requires_human_review=requires_human_review,
            archive_only=archive_only,
            reason=reason,
            matched_signals=signals.get(top_intent, ()),
            secondary_intents=secondary_intents,
            source_surface=interaction.source_surface.value,
            redaction_status=interaction.redaction_status.value,
            metadata={
                "classifier": "InteractionIntentClassifier",
                "deterministic_first": True,
                "embedding_used": False,
                "seed_created": False,
                "registry_write_performed": False,
                "execution_route": "none",
            },
        )


def classify_interaction_intent(record: InteractionSourceRecord | Mapping[str, Any]) -> IntentClassification:
    """Classify one InteractionSourceRecord with the default deterministic classifier."""

    return InteractionIntentClassifier().classify(record)


def _coerce_record(value: InteractionSourceRecord | Mapping[str, Any]) -> InteractionSourceRecord:
    if isinstance(value, InteractionSourceRecord):
        return value
    try:
        return InteractionSourceRecord.from_dict(value)
    except InteractionSourceRecordError:
        raise
    except Exception as exc:  # pragma: no cover - defensive wrapper for callers.
        raise InteractionIntentClassificationError("record must be an InteractionSourceRecord payload") from exc


def _classification_text(record: InteractionSourceRecord) -> str:
    parts: list[str] = [record.summary]
    for key in _METADATA_HINT_KEYS:
        parts.extend(_flatten_strings(record.metadata.get(key)))
    return _normalize(" ".join(part for part in parts if part))


def _score_text(text: str) -> tuple[dict[InteractionPrimaryIntent, float], dict[InteractionPrimaryIntent, tuple[str, ...]]]:
    scores = {intent: 0.0 for intent in InteractionPrimaryIntent}
    matched: dict[InteractionPrimaryIntent, list[str]] = {intent: [] for intent in InteractionPrimaryIntent}
    for intent, rule in _RULES.items():
        for phrase in rule.strong:
            if _contains_phrase(text, phrase):
                scores[intent] += 4.0
                matched[intent].append(f"strong:{phrase}")
        for phrase in rule.weak:
            if _contains_phrase(text, phrase):
                scores[intent] += 1.0
                matched[intent].append(f"weak:{phrase}")
    return scores, {intent: tuple(values) for intent, values in matched.items()}


def _apply_metadata_hints(
    metadata: Mapping[str, Any],
    scores: dict[InteractionPrimaryIntent, float],
    signals: dict[InteractionPrimaryIntent, tuple[str, ...]],
) -> None:
    for key in ("intent_hint", "primary_intent", "seed_kind"):
        for value in _flatten_strings(metadata.get(key)):
            normalized = _normalize_key(value)
            intent = _SEED_KIND_INTENT_MAP.get(normalized)
            if intent is not None:
                scores[intent] += 5.0
                signals[intent] = (*signals[intent], f"metadata:{key}={normalized}")


def _confidence(top_score: float, margin: float) -> float:
    confidence = 0.35 + min(top_score, 8.0) * 0.07 + min(max(margin, 0.0), 4.0) * 0.03
    return round(max(0.20, min(confidence, 0.95)), 2)


def _reason(intent: InteractionPrimaryIntent, top_score: float, second_score: float) -> str:
    if intent == InteractionPrimaryIntent.NON_STRATEGY:
        return "clear non-strategy interaction; archive only"
    if second_score > 0 and (top_score - second_score) <= _AMBIGUITY_MARGIN:
        return f"{intent.value} selected with close secondary intent; human review required"
    return f"{intent.value} selected by deterministic summary and metadata signals"


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized = _normalize(phrase)
    if " " in normalized or "/" in normalized or "-" in normalized:
        return normalized in text
    return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text) is not None


def _normalize(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("_", " ")
    text = re.sub(r"[^a-z0-9/\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _flatten_strings(value: Any) -> tuple[str, ...]:
    if value in (None, "", [], ()):
        return ()
    if isinstance(value, Mapping):
        values: list[str] = []
        for nested in value.values():
            values.extend(_flatten_strings(nested))
        return tuple(values)
    if isinstance(value, Sequence) and not isinstance(value, str):
        values = []
        for nested in value:
            values.extend(_flatten_strings(nested))
        return tuple(values)
    text = str(value).strip()
    return (text,) if text else ()


def _strings(values: Sequence[Any] | Any | None) -> tuple[str, ...]:
    if values in (None, "", [], ()):
        return ()
    if isinstance(values, (str, int, float)):
        values = (values,)
    return tuple(str(value).strip() for value in values if str(value).strip())


def _require_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise InteractionIntentClassificationError(f"{field_name} is required")
    return normalized


def _coerce_intent(value: InteractionPrimaryIntent | str) -> InteractionPrimaryIntent:
    if isinstance(value, InteractionPrimaryIntent):
        return value
    try:
        return InteractionPrimaryIntent(str(value))
    except ValueError as exc:
        allowed = ", ".join(intent.value for intent in InteractionPrimaryIntent)
        raise InteractionIntentClassificationError(f"primary_intent must be one of: {allowed}") from exc


def _bounded_confidence(value: float) -> float:
    confidence = float(value)
    if confidence < 0.0 or confidence > 1.0:
        raise InteractionIntentClassificationError("confidence must be between 0.0 and 1.0")
    return confidence
