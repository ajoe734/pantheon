"""StrategySpecSeed builder for governed SD-03 evidence.

The builder distills a research-only StrategySpecSeed from an EvidenceBundle.
It does not write registry state, launch experiments, open broker sessions, or
create execution routes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.source_ingestion.connectors.base import SourceRecord, SourceRecordStatus
from services.source_ingestion.negative_memory import (
    NegativeMemoryError,
    NegativeMemoryMatch,
    match_negative_memory,
    no_negative_memory_match,
)


class StrategySpecSeedError(ValueError):
    """Raised when evidence cannot be distilled into a StrategySpecSeed."""


class StrategySpecSeedStatus(str, Enum):
    DRAFT = "draft"
    PROMOTED_TO_STRATEGY_SPEC = "promoted_to_strategy_spec"
    REJECTED = "rejected"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass(frozen=True)
class StrategySpecSeed:
    """Research-only StrategySpec seed linked back to governed evidence."""

    seed_id: str
    source_id: str
    evidence_bundle_id: str
    hypothesis: str
    asset_class: Sequence[str]
    market_scope: Sequence[str]
    holding_period: str | None
    required_data: Sequence[str]
    backend_hint: str | None
    feature_hints: Sequence[str]
    label_hints: Sequence[str]
    risk_notes: Sequence[str]
    confidence: float
    status: StrategySpecSeedStatus | str = StrategySpecSeedStatus.DRAFT
    negative_memory_match: Mapping[str, Any] | NegativeMemoryMatch | None = None
    source_ids: Sequence[str] = field(default_factory=tuple)
    evidence_item_ids: Sequence[str] = field(default_factory=tuple)
    citation_refs: Sequence[str] = field(default_factory=tuple)
    trace_refs: Sequence[str] = field(default_factory=tuple)
    lineage: Mapping[str, Any] = field(default_factory=dict)
    created_by: str = "Codex"
    created_at: datetime | str = field(default_factory=_utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        seed_id = _require_text(self.seed_id, "seed_id")
        source_id = _require_text(self.source_id, "source_id")
        evidence_bundle_id = _require_text(self.evidence_bundle_id, "evidence_bundle_id")
        status = _coerce_status(self.status)
        source_ids = _unique_strings((*_strings(self.source_ids), source_id))
        if not source_ids:
            raise StrategySpecSeedError("source_ids is required")
        object.__setattr__(self, "seed_id", seed_id)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "evidence_bundle_id", evidence_bundle_id)
        object.__setattr__(self, "hypothesis", _require_text(self.hypothesis, "hypothesis"))
        object.__setattr__(self, "asset_class", _non_empty_strings(self.asset_class, "asset_class"))
        object.__setattr__(self, "market_scope", _non_empty_strings(self.market_scope, "market_scope"))
        object.__setattr__(self, "required_data", _non_empty_strings(self.required_data, "required_data"))
        object.__setattr__(self, "feature_hints", _strings(self.feature_hints))
        object.__setattr__(self, "label_hints", _strings(self.label_hints))
        object.__setattr__(self, "risk_notes", _strings(self.risk_notes))
        object.__setattr__(self, "confidence", _bounded_confidence(self.confidence))
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "negative_memory_match", _coerce_negative_memory_match(self.negative_memory_match))
        object.__setattr__(self, "source_ids", source_ids)
        object.__setattr__(self, "evidence_item_ids", _strings(self.evidence_item_ids))
        object.__setattr__(self, "citation_refs", _strings(self.citation_refs))
        object.__setattr__(self, "trace_refs", _strings(self.trace_refs))
        object.__setattr__(self, "lineage", dict(self.lineage))
        object.__setattr__(self, "created_by", _require_text(self.created_by, "created_by"))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_id": self.seed_id,
            "source_id": self.source_id,
            "evidence_bundle_id": self.evidence_bundle_id,
            "hypothesis": self.hypothesis,
            "asset_class": list(self.asset_class),
            "market_scope": list(self.market_scope),
            "holding_period": self.holding_period,
            "required_data": list(self.required_data),
            "backend_hint": self.backend_hint,
            "feature_hints": list(self.feature_hints),
            "label_hints": list(self.label_hints),
            "risk_notes": list(self.risk_notes),
            "confidence": self.confidence,
            "status": self.status.value,
            "negative_memory_match": dict(self.negative_memory_match),
            "source_ids": list(self.source_ids),
            "evidence_item_ids": list(self.evidence_item_ids),
            "citation_refs": list(self.citation_refs),
            "trace_refs": list(self.trace_refs),
            "lineage": dict(self.lineage),
            "created_by": self.created_by,
            "created_at": _iso(self.created_at),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StrategySpecSeed":
        return cls(
            seed_id=str(data["seed_id"]),
            source_id=str(data["source_id"]),
            evidence_bundle_id=str(data["evidence_bundle_id"]),
            hypothesis=str(data["hypothesis"]),
            asset_class=list(data.get("asset_class") or ()),
            market_scope=list(data.get("market_scope") or ()),
            holding_period=data.get("holding_period"),
            required_data=list(data.get("required_data") or ()),
            backend_hint=data.get("backend_hint"),
            feature_hints=list(data.get("feature_hints") or ()),
            label_hints=list(data.get("label_hints") or ()),
            risk_notes=list(data.get("risk_notes") or ()),
            confidence=float(data.get("confidence", 0.0)),
            status=str(data.get("status", StrategySpecSeedStatus.DRAFT.value)),
            negative_memory_match=data.get("negative_memory_match"),
            source_ids=list(data.get("source_ids") or ()),
            evidence_item_ids=list(data.get("evidence_item_ids") or ()),
            citation_refs=list(data.get("citation_refs") or ()),
            trace_refs=list(data.get("trace_refs") or ()),
            lineage=dict(data.get("lineage") or {}),
            created_by=str(data.get("created_by") or "Codex"),
            created_at=data.get("created_at") or _utc_now(),
            metadata=dict(data.get("metadata") or {}),
        )


class StrategySpecSeedBuilder:
    """Build StrategySpecSeed objects from governed EvidenceBundle material."""

    def build_seed(
        self,
        evidence_bundle: EvidenceBundle | Mapping[str, Any],
        *,
        source_records: Sequence[SourceRecord | Mapping[str, Any]] = (),
        evidence_items: Sequence[EvidenceItem | Mapping[str, Any]] = (),
        seed_id: str | None = None,
        created_by: str = "Codex",
        created_at: datetime | str | None = None,
        metadata: Mapping[str, Any] | None = None,
        negative_memory_records: Sequence[Mapping[str, Any] | Any] = (),
    ) -> StrategySpecSeed:
        bundle = _coerce_bundle(evidence_bundle)
        if not bundle.evidence_bundle_id:
            raise StrategySpecSeedError("evidence_bundle_id is required")
        if not bundle.source_ids:
            raise StrategySpecSeedError("EvidenceBundle requires source_ids before seed building")
        if not bundle.citation_refs:
            raise StrategySpecSeedError("StrategySpecSeed requires citation_refs")

        records = tuple(_coerce_source_record(record) for record in source_records)
        items = tuple(_coerce_evidence_item(item) for item in evidence_items)
        _validate_lineage_inputs(bundle, records, items)

        contexts = _collect_contexts(bundle, records, items)
        strategy_metadata = _strategy_metadata(contexts)
        text = _combined_text(bundle, records, items)
        source_id = str(bundle.source_ids[0])
        source_ids = _unique_strings(bundle.source_ids)
        evidence_item_ids = _unique_strings(bundle.evidence_item_ids)
        citation_refs = _unique_strings(bundle.citation_refs)
        trace_refs = _unique_strings((*bundle.trace_refs, *[record.trace_id for record in records]))
        hypothesis = _metadata_text(strategy_metadata, ("hypothesis", "strategy_hypothesis")) or _infer_hypothesis(
            bundle.summary,
            text,
        )
        asset_class = _extract_list(
            strategy_metadata,
            contexts,
            ("asset_class", "asset_classes", "assets"),
            fallback=_infer_asset_class(text),
        )
        market_scope = _extract_list(
            strategy_metadata,
            contexts,
            ("market_scope", "markets", "market", "venues", "exchange_segments", "symbols"),
            fallback=_infer_market_scope(text),
        )
        required_data = _extract_list(
            strategy_metadata,
            contexts,
            (
                "required_data",
                "data_dependencies",
                "dataset_refs",
                "source_dataset_refs",
                "ohlcv_fields",
                "market_data_fields",
            ),
            fallback=_infer_required_data(text, records),
        )
        feature_hints = _extract_list(
            strategy_metadata,
            contexts,
            ("feature_hints", "features", "signals", "factors", "keywords"),
            fallback=_infer_feature_hints(text, records),
        )
        label_hints = _extract_list(
            strategy_metadata,
            contexts,
            ("label_hints", "labels", "target", "targets", "objective_label"),
            fallback=_infer_label_hints(text),
        )
        risk_notes = _extract_list(
            strategy_metadata,
            contexts,
            ("risk_notes", "risks", "risk_constraints"),
            fallback=_infer_risk_notes(bundle, records, text),
        )
        backend_hint = _metadata_text(strategy_metadata, ("backend_hint", "backend", "framework")) or _infer_backend_hint(
            text,
            feature_hints,
        )
        holding_period = _metadata_text(strategy_metadata, ("holding_period", "horizon", "target_horizon")) or _infer_holding_period(
            text
        )
        confidence = _effective_confidence(bundle, records, items)
        effective_seed_id = seed_id or _stable_seed_id(bundle.evidence_bundle_id, source_ids, hypothesis)
        lineage = {
            "created_from": "evidence_bundle",
            "evidence_bundle_id": bundle.evidence_bundle_id,
            "source_ids": list(source_ids),
            "evidence_item_ids": list(evidence_item_ids),
            "citation_refs": list(citation_refs),
            "trace_refs": list(trace_refs),
            "promotion_requires": ["evidence_bundle_id", "strategy_spec_review"],
            "registry_write_performed": False,
            "execution_route": "none",
        }
        if records:
            lineage["source_record_refs"] = [record.content_ref for record in records]
        seed_metadata = {
            "builder": "StrategySpecSeedBuilder",
            "builder_version": "1.0",
            "source_license_scope": bundle.license_scope,
            "access_scope": list(bundle.access_scope),
            "entitlement_tags": list(bundle.entitlement_tags),
            "research_only": True,
            "registry_write_performed": False,
            "execution_route": "none",
            **dict(metadata or {}),
        }
        negative_memory_match = match_negative_memory(
            {
                "seed_id": effective_seed_id,
                "source_id": source_id,
                "evidence_bundle_id": bundle.evidence_bundle_id,
                "hypothesis": hypothesis,
                "asset_class": list(asset_class),
                "market_scope": list(market_scope),
                "holding_period": holding_period,
                "required_data": list(required_data),
                "backend_hint": backend_hint,
                "feature_hints": list(feature_hints),
                "label_hints": list(label_hints),
                "risk_notes": list(risk_notes),
                "confidence": confidence,
                "status": StrategySpecSeedStatus.DRAFT.value,
                "source_ids": list(source_ids),
                "evidence_item_ids": list(evidence_item_ids),
                "citation_refs": list(citation_refs),
                "trace_refs": list(trace_refs),
                "lineage": lineage,
                "metadata": seed_metadata,
            },
            negative_memory_records,
        ).to_dict()

        return StrategySpecSeed(
            seed_id=effective_seed_id,
            source_id=source_id,
            evidence_bundle_id=bundle.evidence_bundle_id,
            hypothesis=hypothesis,
            asset_class=asset_class,
            market_scope=market_scope,
            holding_period=holding_period,
            required_data=required_data,
            backend_hint=backend_hint,
            feature_hints=feature_hints,
            label_hints=label_hints,
            risk_notes=risk_notes,
            confidence=confidence,
            status=StrategySpecSeedStatus.DRAFT,
            negative_memory_match=negative_memory_match,
            source_ids=source_ids,
            evidence_item_ids=evidence_item_ids,
            citation_refs=citation_refs,
            trace_refs=trace_refs,
            lineage=lineage,
            created_by=created_by,
            created_at=created_at or _utc_now(),
            metadata=seed_metadata,
        )


def build_strategy_spec_seed(
    evidence_bundle: EvidenceBundle | Mapping[str, Any],
    **kwargs: Any,
) -> StrategySpecSeed:
    """Convenience wrapper for building one StrategySpecSeed."""

    return StrategySpecSeedBuilder().build_seed(evidence_bundle, **kwargs)


def build_strategy_spec_seed_lineage_edge(
    seed: StrategySpecSeed | Mapping[str, Any],
    *,
    strategy_spec_id: str,
    created_by: str,
    created_at: datetime | str | None = None,
    edge_id: str | None = None,
) -> dict[str, Any]:
    """Build the lineage edge used when a seed is promoted to StrategySpec."""

    seed_obj = seed if isinstance(seed, StrategySpecSeed) else StrategySpecSeed.from_dict(seed)
    strategy_ref = _require_text(strategy_spec_id, "strategy_spec_id")
    timestamp = created_at or _utc_now()
    return {
        "edge_id": edge_id or f"lineage-{_short_hash([seed_obj.seed_id, strategy_ref])}",
        "edge_type": "strategy_spec_seed_promoted",
        "from_type": "strategy_spec_seed",
        "from_id": seed_obj.seed_id,
        "to_type": "strategy_spec",
        "to_id": strategy_ref,
        "evidence_bundle_id": seed_obj.evidence_bundle_id,
        "source_ids": list(seed_obj.source_ids),
        "citation_refs": list(seed_obj.citation_refs),
        "created_by": _require_text(created_by, "created_by"),
        "created_at": _iso(timestamp),
        "metadata": {
            "seed_status_before": seed_obj.status.value,
            "promotion_requires_review": True,
            "registry_write_authority": "registry_service_only",
        },
    }


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise StrategySpecSeedError(f"{field_name} is required")
    return normalized


def _coerce_status(value: StrategySpecSeedStatus | str) -> StrategySpecSeedStatus:
    if isinstance(value, StrategySpecSeedStatus):
        return value
    try:
        return StrategySpecSeedStatus(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in StrategySpecSeedStatus)
        raise StrategySpecSeedError(f"status must be one of: {allowed}") from exc


def _coerce_negative_memory_match(value: Mapping[str, Any] | NegativeMemoryMatch | None) -> dict[str, Any]:
    try:
        match = value if isinstance(value, NegativeMemoryMatch) else NegativeMemoryMatch.from_dict(value)
    except NegativeMemoryError as exc:
        raise StrategySpecSeedError(str(exc)) from exc
    if match is None:
        match = no_negative_memory_match()
    return match.to_dict()


def _strings(values: Sequence[Any] | Any | None) -> tuple[str, ...]:
    if values in (None, "", [], ()):
        return ()
    if isinstance(values, (str, int, float)):
        values = (values,)
    return tuple(str(value).strip() for value in values if str(value).strip())


def _non_empty_strings(values: Sequence[Any] | Any | None, field_name: str) -> tuple[str, ...]:
    normalized = _strings(values)
    if not normalized:
        raise StrategySpecSeedError(f"{field_name} is required")
    return normalized


def _unique_strings(values: Sequence[Any] | Any | None) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in _strings(values):
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


def _bounded_confidence(value: float) -> float:
    confidence = float(value)
    if confidence < 0.0 or confidence > 1.0:
        raise StrategySpecSeedError("confidence must be between 0.0 and 1.0")
    return confidence


def _coerce_bundle(value: EvidenceBundle | Mapping[str, Any]) -> EvidenceBundle:
    if isinstance(value, EvidenceBundle):
        return value
    return EvidenceBundle.from_dict(value)


def _coerce_source_record(value: SourceRecord | Mapping[str, Any]) -> SourceRecord:
    if isinstance(value, SourceRecord):
        return value
    return SourceRecord.from_dict(value)


def _coerce_evidence_item(value: EvidenceItem | Mapping[str, Any]) -> EvidenceItem:
    if isinstance(value, EvidenceItem):
        return value
    return EvidenceItem.from_dict(value)


def _validate_lineage_inputs(
    bundle: EvidenceBundle,
    records: Sequence[SourceRecord],
    items: Sequence[EvidenceItem],
) -> None:
    bundle_source_ids = set(bundle.source_ids)
    bundle_item_ids = set(bundle.evidence_item_ids)
    rejected = [record.source_id for record in records if record.status == SourceRecordStatus.REJECTED]
    if rejected:
        raise StrategySpecSeedError(f"Rejected sources cannot build StrategySpecSeed: {rejected}")
    outside_records = [record.source_id for record in records if record.source_id not in bundle_source_ids]
    if outside_records:
        raise StrategySpecSeedError(f"Source records outside evidence bundle: {outside_records}")
    outside_items = [
        item.evidence_item_id
        for item in items
        if item.evidence_item_id not in bundle_item_ids or item.source_id not in bundle_source_ids
    ]
    if outside_items:
        raise StrategySpecSeedError(f"Evidence items outside evidence bundle: {outside_items}")


def _collect_contexts(
    bundle: EvidenceBundle,
    records: Sequence[SourceRecord],
    items: Sequence[EvidenceItem],
) -> tuple[Mapping[str, Any], ...]:
    contexts: list[Mapping[str, Any]] = [bundle.metadata]
    contexts.extend(record.metadata for record in records)
    contexts.extend(item.metadata for item in items)
    return tuple(context for context in contexts if isinstance(context, Mapping))


def _strategy_metadata(contexts: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    merged: dict[str, Any] = {}
    for context in contexts:
        nested = context.get("strategy_seed")
        has_nested = isinstance(nested, Mapping)
        if isinstance(nested, Mapping):
            merged.update(nested)
        for key in (
            "hypothesis",
            "strategy_hypothesis",
            "asset_class",
            "asset_classes",
            "market_scope",
            "markets",
            "market",
            "venues",
            "exchange_segments",
            "symbols",
            "holding_period",
            "horizon",
            "target_horizon",
            "required_data",
            "data_dependencies",
            "dataset_refs",
            "source_dataset_refs",
            "ohlcv_fields",
            "market_data_fields",
            "backend_hint",
            "backend",
            "framework",
            "feature_hints",
            "features",
            "signals",
            "factors",
            "keywords",
            "label_hints",
            "labels",
            "target",
            "targets",
            "objective_label",
            "risk_notes",
            "risks",
            "risk_constraints",
        ):
            if has_nested and key not in nested:
                continue
            if key in context and key not in merged:
                merged[key] = context[key]
    return merged


def _combined_text(
    bundle: EvidenceBundle,
    records: Sequence[SourceRecord],
    items: Sequence[EvidenceItem],
) -> str:
    parts: list[str] = [bundle.summary]
    for item in items:
        parts.append(item.body)
        parts.extend(str(value) for value in item.metadata.values() if isinstance(value, str))
    for record in records:
        parts.append(record.title)
        for key in ("body", "excerpt", "abstract", "summary"):
            if isinstance(record.metadata.get(key), str):
                parts.append(str(record.metadata[key]))
        parts.extend(str(value) for value in record.metadata.get("keywords", []) if value)
    return " ".join(part for part in parts if part).strip()


def _metadata_text(metadata: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value in (None, "", [], ()):
            continue
        if isinstance(value, Sequence) and not isinstance(value, str):
            strings = _strings(value)
            if strings:
                return strings[0]
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


def _extract_list(
    strategy_metadata: Mapping[str, Any],
    contexts: Sequence[Mapping[str, Any]],
    keys: Sequence[str],
    *,
    fallback: Sequence[str],
) -> tuple[str, ...]:
    values: list[str] = []
    for key in keys:
        values.extend(_flatten_strings(strategy_metadata.get(key)))
    explicit_values = _unique_strings(values)
    if explicit_values:
        return explicit_values
    for context in contexts:
        for key in keys:
            values.extend(_flatten_strings(context.get(key)))
    return _unique_strings(values) or _unique_strings(fallback)


def _flatten_strings(value: Any) -> tuple[str, ...]:
    if value in (None, "", [], ()):
        return ()
    if isinstance(value, Mapping):
        values: list[str] = []
        for item in value.values():
            values.extend(_flatten_strings(item))
        return tuple(values)
    if isinstance(value, Sequence) and not isinstance(value, str):
        values = []
        for item in value:
            values.extend(_flatten_strings(item))
        return tuple(values)
    return (str(value).strip(),) if str(value).strip() else ()


def _infer_hypothesis(summary: str, text: str) -> str:
    first = _first_sentence(summary) or _first_sentence(text)
    if not first:
        raise StrategySpecSeedError("hypothesis is required")
    return f"Evidence suggests: {first}"


def _first_sentence(text: str) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return ""
    return re.split(r"(?<=[.!?])\s+", compact, maxsplit=1)[0].strip()


def _infer_asset_class(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    matches: list[str] = []
    for token, label in (
        ("equity", "equity"),
        ("stock", "equity"),
        ("twse", "equity"),
        ("tpex", "equity"),
        ("future", "futures"),
        ("option", "options"),
        ("crypto", "crypto"),
        ("fx", "fx"),
        ("forex", "fx"),
        ("bond", "fixed_income"),
    ):
        if token in lowered and label not in matches:
            matches.append(label)
    return tuple(matches or ("unspecified",))


def _infer_market_scope(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    matches: list[str] = []
    for token, label in (
        ("taiwan", "Taiwan"),
        ("twse", "TWSE"),
        ("tpex", "TPEx"),
        ("us ", "US"),
        ("s&p", "US"),
        ("nasdaq", "NASDAQ"),
        ("crypto", "crypto"),
    ):
        if token in lowered and label not in matches:
            matches.append(label)
    return tuple(matches or ("unspecified",))


def _infer_required_data(text: str, records: Sequence[SourceRecord]) -> tuple[str, ...]:
    lowered = text.lower()
    data: list[str] = []
    if "ohlcv" in lowered or {"open", "high", "low", "close", "volume"}.issubset(set(re.findall(r"[a-z]+", lowered))):
        data.append("point-in-time OHLCV")
    if "forward return" in lowered or "returns" in lowered:
        data.append("return labels")
    if "fundamental" in lowered:
        data.append("fundamental data")
    if "news" in lowered or any(record.source_type.value == "news" for record in records):
        data.append("news events")
    if "filing" in lowered or any(record.source_type.value == "filing" for record in records):
        data.append("filing events")
    return tuple(data or ("governed source evidence",))


def _infer_feature_hints(text: str, records: Sequence[SourceRecord]) -> tuple[str, ...]:
    lowered = text.lower()
    hints: list[str] = []
    for token, label in (
        ("momentum", "momentum"),
        ("volatility", "volatility"),
        ("volume", "volume"),
        ("liquidity", "liquidity"),
        ("reversal", "reversal"),
        ("value", "value"),
        ("quality", "quality"),
        ("sentiment", "sentiment"),
        ("macro", "macro"),
        ("regime", "regime"),
    ):
        if token in lowered and label not in hints:
            hints.append(label)
    for record in records:
        hints.extend(str(value).strip() for value in record.metadata.get("keywords", []) if str(value).strip())
    return tuple(hints[:12])


def _infer_label_hints(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    labels: list[str] = []
    if "forward return" in lowered or "future return" in lowered:
        labels.append("forward_return")
    if "rank" in lowered and "return" in lowered:
        labels.append("cross_sectional_return_rank")
    if "sharpe" in lowered:
        labels.append("risk_adjusted_return")
    if "drawdown" in lowered:
        labels.append("drawdown_control")
    return tuple(labels)


def _infer_risk_notes(bundle: EvidenceBundle, records: Sequence[SourceRecord], text: str) -> tuple[str, ...]:
    notes = [
        "research_only_not_execution_route",
        "requires_strategy_spec_review_before_promotion",
    ]
    if bundle.license_scope:
        notes.append(f"license_scope:{bundle.license_scope}")
    lowered = text.lower()
    if "survivorship" in lowered:
        notes.append("check_survivorship_bias")
    if "lookahead" in lowered or "look-ahead" in lowered:
        notes.append("check_lookahead_bias")
    for record in records:
        governance = record.metadata.get("governance")
        if isinstance(governance, Mapping) and governance.get("direct_execution_allowed") is False:
            notes.append("source_governance_blocks_direct_execution")
        allowed_use = set(_flatten_strings(record.metadata.get("allowed_use")))
        if allowed_use and "strategy_seed" not in allowed_use:
            notes.append("source_allowed_use_missing_strategy_seed")
    return _unique_strings(notes)


def _infer_backend_hint(text: str, feature_hints: Sequence[str]) -> str | None:
    lowered = " ".join((text, " ".join(feature_hints))).lower()
    for token, backend in (
        ("qlib", "qlib"),
        ("lightgbm", "qlib"),
        ("cross-sectional", "qlib"),
        ("cross sectional", "qlib"),
        ("vectorbt", "vectorbt"),
        ("backtest", "vectorbt"),
        ("statsmodels", "statsmodels"),
        ("cointegration", "statsmodels"),
        ("regression", "statsmodels"),
        ("quantlib", "quantlib"),
        ("option", "quantlib"),
        ("finrl", "finrl"),
        ("reinforcement learning", "finrl"),
        ("imitation", "imitation"),
    ):
        if token in lowered:
            return backend
    return None


def _infer_holding_period(text: str) -> str | None:
    lowered = text.lower()
    match = re.search(r"\b(\d+\s*(?:trading\s*)?(?:minute|minutes|hour|hours|day|days|week|weeks|month|months))\b", lowered)
    if match:
        return match.group(1)
    for token in ("intraday", "overnight", "daily", "weekly", "monthly"):
        if token in lowered:
            return token
    return None


def _effective_confidence(
    bundle: EvidenceBundle,
    records: Sequence[SourceRecord],
    items: Sequence[EvidenceItem],
) -> float:
    confidences = [float(bundle.confidence)]
    confidences.extend(float(item.confidence) for item in items)
    for record in records:
        trust_score = record.metadata.get("trust_score")
        if isinstance(trust_score, (int, float)):
            confidences.append(float(trust_score))
    return _bounded_confidence(round(min(confidences), 4))


def _stable_seed_id(evidence_bundle_id: str, source_ids: Sequence[str], hypothesis: str) -> str:
    return f"seed-{_short_hash([evidence_bundle_id, *source_ids, hypothesis])}"


def _short_hash(values: Sequence[str]) -> str:
    payload = json.dumps(list(values), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
