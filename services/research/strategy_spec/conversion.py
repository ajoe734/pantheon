"""Source/evidence to StrategySpec conversion service.

This module bridges the SD-03 StrategySpecSeed surface into the canonical
StrategySpec contract and the registry facade payload added for STRAT-002. It
does not write registry state, launch experiments, or create execution routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import copy
from typing import Any, Mapping, Sequence

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.registry.strategy_artifact import (
    StrategyArtifactValidationError,
    strategy_artifact_checksum,
    validate_execution_recipe,
    validate_execution_recipe_against_spec,
    validate_strategy_artifact,
)
from services.research.strategy_spec.lineage import (
    StrategySpecLineageRefs,
    attach_lineage_refs_to_strategy_spec_payload,
    build_strategy_spec_lineage_refs,
)
from services.research.strategy_spec.models import StrategySpec
from services.source_ingestion.connectors.base import SourceRecord, SourceType
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedBuilder,
    build_strategy_spec_seed_lineage_edge,
)


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


class StrategySpecConversionError(ValueError):
    """Raised when source material cannot be converted into a StrategySpec."""


@dataclass(frozen=True)
class StrategySpecConversionResult:
    """Conversion outputs ready for review and registry admission."""

    strategy_spec_seed: StrategySpecSeed
    strategy_spec: StrategySpec
    registry_payload: Mapping[str, Any]
    candidate_advance_request: Mapping[str, Any]
    seed_promotion_lineage_edge: Mapping[str, Any]
    evidence_code_lineage_edge: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_spec_seed": self.strategy_spec_seed.to_dict(),
            "strategy_spec": self.strategy_spec.to_dict(),
            "registry_payload": dict(self.registry_payload),
            "candidate_advance_request": dict(self.candidate_advance_request),
            "seed_promotion_lineage_edge": dict(self.seed_promotion_lineage_edge),
            "evidence_code_lineage_edge": dict(self.evidence_code_lineage_edge),
        }


class StrategySpecConversionService:
    """Build reviewable StrategySpec artifacts from governed source material."""

    def __init__(
        self,
        *,
        created_by: str = "Codex",
        default_version: str = "1.0.0",
        dataset_binding: Mapping[str, Any] | None = None,
    ) -> None:
        # Governed dataset reference (dataset_id, source_dataset_refs, symbols,
        # bar_frequency) a compiled hypothesis rule is bound to. Never records.
        self.dataset_binding = dict(dataset_binding) if dataset_binding else None
        self.created_by = _require_text(created_by, "created_by")
        self.default_version = _validate_semver(default_version)
        self._seed_builder = StrategySpecSeedBuilder()

    def convert_source_material(
        self,
        evidence_bundle: EvidenceBundle | Mapping[str, Any],
        *,
        source_records: Sequence[SourceRecord | Mapping[str, Any]] = (),
        evidence_items: Sequence[EvidenceItem | Mapping[str, Any]] = (),
        strategy_id: str | None = None,
        title: str | None = None,
        version: str | None = None,
        created_by: str | None = None,
        created_at: datetime | str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> StrategySpecConversionResult:
        """Build a seed from source evidence, then convert it into StrategySpec."""

        actor = _require_text(created_by or self.created_by, "created_by")
        try:
            seed = self._seed_builder.build_seed(
                evidence_bundle,
                source_records=source_records,
                evidence_items=evidence_items,
                created_by=actor,
                created_at=created_at,
                metadata={
                    "conversion_requested_by": actor,
                    "conversion_service": "StrategySpecConversionService",
                    **dict(metadata or {}),
                },
            )
            return self.convert_seed(
                seed,
                source_records=source_records,
                evidence_items=evidence_items,
                strategy_id=strategy_id,
                title=title,
                version=version,
                created_by=actor,
                created_at=created_at,
                metadata=metadata,
            )
        except ValueError as exc:
            raise StrategySpecConversionError(str(exc)) from exc

    def convert_seed(
        self,
        seed: StrategySpecSeed | Mapping[str, Any],
        *,
        source_records: Sequence[SourceRecord | Mapping[str, Any]] = (),
        evidence_items: Sequence[EvidenceItem | Mapping[str, Any]] = (),
        strategy_id: str | None = None,
        title: str | None = None,
        version: str | None = None,
        created_by: str | None = None,
        created_at: datetime | str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> StrategySpecConversionResult:
        """Convert an existing StrategySpecSeed into a canonical StrategySpec."""

        actor = _require_text(created_by or self.created_by, "created_by")
        seed_obj = seed if isinstance(seed, StrategySpecSeed) else StrategySpecSeed.from_dict(seed)
        records = tuple(_coerce_source_record(record) for record in source_records)
        items = tuple(_coerce_evidence_item(item) for item in evidence_items)
        effective_version = _validate_semver(version or self.default_version)
        timestamp = _iso(created_at or seed_obj.created_at or _utc_now())
        effective_title = _build_title(seed_obj, records, title=title)
        effective_strategy_id = _require_text(
            strategy_id or _stable_strategy_id(effective_title, seed_obj),
            "strategy_id",
        )

        try:
            lineage_refs = build_strategy_spec_lineage_refs(
                seed_obj,
                source_records=records,
                evidence_items=items,
            )
            payload = self._build_strategy_spec_payload(
                seed=seed_obj,
                lineage_refs=lineage_refs,
                strategy_id=effective_strategy_id,
                title=effective_title,
                created_by=actor,
                created_at=timestamp,
                source_records=records,
                metadata=metadata,
            )
            strategy_spec = StrategySpec.from_dict(payload)
            registry_payload = _build_registry_payload(
                seed=seed_obj,
                strategy_spec=strategy_spec,
                version=effective_version,
            )
            seed_edge = build_strategy_spec_seed_lineage_edge(
                seed_obj,
                strategy_spec_id=effective_strategy_id,
                created_by=actor,
                created_at=timestamp,
            )
            evidence_edge = lineage_refs.to_lineage_edge(
                strategy_spec_id=effective_strategy_id,
                actor_ref=actor,
                created_at=timestamp,
            )
        except ValueError as exc:
            raise StrategySpecConversionError(str(exc)) from exc

        return StrategySpecConversionResult(
            strategy_spec_seed=seed_obj,
            strategy_spec=strategy_spec,
            registry_payload=registry_payload,
            candidate_advance_request={"target_state": "candidate"},
            seed_promotion_lineage_edge=seed_edge,
            evidence_code_lineage_edge=evidence_edge,
        )

    def _build_strategy_spec_payload(
        self,
        *,
        seed: StrategySpecSeed,
        lineage_refs: StrategySpecLineageRefs,
        strategy_id: str,
        title: str,
        created_by: str,
        created_at: str,
        source_records: Sequence[SourceRecord],
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        conversion_metadata = _conversion_metadata(seed, metadata)
        recipe = _execution_recipe(conversion_metadata)
        card_dataset_deps = _apply_hypothesis_card(
            conversion_metadata,
            seed=seed,
            strategy_id=strategy_id,
            dataset_binding=self.dataset_binding,
        )
        payload = {
            "spec_version": "1.0",
            "strategy_id": strategy_id,
            "title": title,
            "hypothesis": seed.hypothesis,
            "objective": _objective(seed, title),
            "lifecycle_state": "draft",
            "market_scope": {
                "symbols": _symbols(seed, conversion_metadata),
                "asset_classes": list(seed.asset_class),
                "venues": list(seed.market_scope),
                "frequency": _frequency(seed, conversion_metadata),
            },
            "data_dependencies": _merge_dependencies(_data_dependencies(seed), card_dataset_deps),
            "execution_profile": {
                "signal_schema_version": str(conversion_metadata.get("signal_schema_version") or "research-signal-v1"),
                "quantity_type": recipe["quantity_type"] if recipe else "PERCENT_PORTFOLIO",
                "execution_mode_hint": "research",
            },
            "evaluation_plan": {
                "metrics": _metrics(conversion_metadata),
                "candidate_gate": "StrategySpec schema, source-seed lineage, and evidence/code refs review pass.",
                "paper_gate": "Requires approved StrategySpec artifact and paper deployment approval.",
                "live_gate": "Requires paper evidence, risk owner approval, and explicit live approval.",
            },
            "governance": {
                "approval_required": True,
                "policy_id": "research-strategy-spec-conversion-v1",
                "risk_profile": "research_only",
            },
            "provenance": {
                "source_kind": _source_kind(source_records),
                "created_at": created_at,
                "source_refs": list(seed.source_ids),
                "created_by": created_by,
            },
            "metadata": conversion_metadata,
        }
        rebalance_cadence = _rebalance_cadence(seed)
        if rebalance_cadence:
            payload["execution_profile"]["rebalance_cadence"] = rebalance_cadence
        if recipe:
            try:
                validate_execution_recipe_against_spec(recipe, payload)
            except StrategyArtifactValidationError as exc:
                raise StrategySpecConversionError(str(exc)) from exc
        return attach_lineage_refs_to_strategy_spec_payload(payload, lineage_refs)


def convert_source_material_to_strategy_spec(
    evidence_bundle: EvidenceBundle | Mapping[str, Any],
    **kwargs: Any,
) -> StrategySpecConversionResult:
    """Convenience wrapper for one-shot Source/Evidence -> StrategySpec conversion."""

    return StrategySpecConversionService().convert_source_material(evidence_bundle, **kwargs)


def convert_strategy_spec_seed_to_strategy_spec(
    seed: StrategySpecSeed | Mapping[str, Any],
    **kwargs: Any,
) -> StrategySpecConversionResult:
    """Convenience wrapper for StrategySpecSeed -> StrategySpec conversion."""

    return StrategySpecConversionService().convert_seed(seed, **kwargs)


CARD_WIRING_KEY = "card_wiring"
CARD_STATUS_GATE_READY = "gate_ready"
CARD_STATUS_RESEARCH_ONLY = "research_only_draft"

_ARTIFACT_ALGORITHM_REF = {
    "engine": "lean",
    "repository": "ajoe734/pantheon",
    "commit": "1cfaf933fbcc119605a455ebea483d54d2521ebb",
    "path": "services/registry/strategy_artifact.py",
    "entrypoint": "services.registry.strategy_artifact:evaluate_strategy_action",
    "logic_interpreter": "services.registry.strategy_artifact:evaluate_strategy_action",
    "signal_interface": "services.execution.lean_runtime.paper_signal_producer:Strategy",
    "signal_schema_version": "1.0",
}


def build_card_strategy_artifact(
    rule: Mapping[str, Any],
    *,
    strategy_id: str,
    dataset_binding: Mapping[str, Any],
    source_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Wrap a compiled ``factor_rule_v1`` in an executable StrategyArtifact.

    The compiled logic, parameters and mutation surface are carried verbatim;
    only the execution envelope declared by the governed dataset binding is
    added where the rule did not state it. Raises ``ValueError`` when invalid.
    """
    logic = copy.deepcopy(dict(rule["strategy_logic"]))
    parameters = copy.deepcopy(dict(rule["parameters"]))
    surface = copy.deepcopy(dict(rule["mutation_surface"]))
    dataset_id = _require_text(dataset_binding.get("dataset_id"), "dataset_id")
    envelope = {
        "market": "TW",
        "symbols": list(dataset_binding.get("symbols") or []),
        "bar_frequency": str(dataset_binding.get("bar_frequency") or "1d"),
        "order_quantity": 10,
        "quantity_type": "SHARES",
    }
    # The governed binding owns the market envelope; model-proposed symbols,
    # frequency and data source are never trusted over it.
    for key in ("market", "symbols", "bar_frequency"):
        parameters[key] = envelope[key]
    for key in ("order_quantity", "quantity_type"):
        parameters.setdefault(key, envelope[key])
    parameters["data_source"] = dataset_id
    immutable = list(surface.get("immutable_parameters") or [])
    controlled = {c.get("parameter_key") for c in surface.get("controls") or [] if isinstance(c, Mapping)}
    for key in (*envelope, "data_source"):
        if key not in controlled and key not in immutable:
            immutable.append(key)
    surface["immutable_parameters"] = immutable
    identity = hashlib.sha256(
        json.dumps([strategy_id, logic, parameters, dataset_id], sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]
    artifact = {
        "artifact_schema_version": "1.0",
        "artifact_id": f"card-rule-{identity}",
        "strategy_id": strategy_id,
        "version": "1.0.0",
        "algorithm_ref": dict(_ARTIFACT_ALGORITHM_REF),
        "strategy_logic": logic,
        "parameters": parameters,
        "mutation_surface": surface,
        "lineage": {
            "source_strategy_spec_id": strategy_id,
            "source_run_ids": [str(rule.get("rule_fingerprint") or identity)],
            "source_dataset_refs": [str(r) for r in dataset_binding.get("source_dataset_refs") or [dataset_id]],
        },
        "provenance_refs": [f"source:{sid}" for sid in source_ids] or [f"rule:{identity}"],
    }
    try:
        validate_strategy_artifact(artifact)
    except StrategyArtifactValidationError as exc:
        raise ValueError(str(exc)) from exc
    return artifact


def _apply_hypothesis_card(
    metadata: dict[str, Any],
    *,
    seed: StrategySpecSeed,
    strategy_id: str,
    dataset_binding: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    """Carry a verified hypothesis card into the Spec and bind its compiled rule.

    A card without a compiled rule, governed dataset binding or valid artifact
    stays a research-only draft and records the reason; nothing is invented.
    Returns the extra dataset data_dependencies the binding requires.
    """
    card = seed.metadata.get("hypothesis_card")
    if not isinstance(card, Mapping):
        return []
    card = copy.deepcopy(dict(card))
    rule = card.get("factor_rule_v1")
    metadata["hypothesis_card"] = {k: v for k, v in card.items() if k != "factor_rule_v1"}
    predictions = [dict(p) for p in card.get("predictions") or [] if isinstance(p, Mapping)]
    if predictions:
        metadata["hypothesis_predictions"] = predictions

    def draft(reason: str) -> list[dict[str, str]]:
        metadata[CARD_WIRING_KEY] = {"status": CARD_STATUS_RESEARCH_ONLY, "reason": reason}
        return []

    if not isinstance(rule, Mapping):
        return draft("no_compiled_rule")
    if not dataset_binding or not dataset_binding.get("dataset_id") or not dataset_binding.get("symbols"):
        return draft("governed_dataset_binding_unavailable")
    try:
        artifact = build_card_strategy_artifact(
            rule, strategy_id=strategy_id, dataset_binding=dataset_binding, source_ids=seed.source_ids
        )
    except (ValueError, KeyError, TypeError) as exc:
        return draft(f"invalid_compiled_rule:{str(exc)[:200]}")
    dataset_id = str(dataset_binding["dataset_id"])
    metadata["factor_rule_v1"] = dict(rule)
    metadata["strategy_artifact"] = artifact
    metadata["strategy_artifact_checksum"] = strategy_artifact_checksum(artifact)
    metadata["backtest_dataset"] = {
        "dataset_id": dataset_id,
        "source_dataset_refs": list(dataset_binding.get("source_dataset_refs") or [dataset_id]),
        "binding": "governed_reference",
    }
    metadata[CARD_WIRING_KEY] = {"status": CARD_STATUS_GATE_READY, "reason": "compiled_rule_bound"}
    return [{"ref": dataset_id, "kind": "dataset"}]


def _merge_dependencies(
    base: list[dict[str, str]], extra: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Governed dataset binding first: the worker pins the *first* dataset dependency."""
    seen = {(d["kind"], d["ref"]) for d in extra}
    return extra + [d for d in base if (d["kind"], d["ref"]) not in seen]


def _build_registry_payload(
    *,
    seed: StrategySpecSeed,
    strategy_spec: StrategySpec,
    version: str,
) -> dict[str, Any]:
    payload = strategy_spec.to_dict()
    checksum = _strategy_spec_checksum(payload)
    return {
        "strategy_id": strategy_spec.strategy_id,
        "version": version,
        "artifact_state": "draft",
        "source_seed_id": seed.seed_id,
        "lineage": {
            "source_run_ids": [seed.seed_id],
            "source_dataset_refs": list(seed.source_ids),
        },
        "storage_ref": {
            "backend": "inline",
            "path": "$.entry.metadata.strategy_spec",
        },
        "checksum": checksum,
        "producer_run_id": seed.seed_id,
        "evaluation_summary": {
            "source_seed_id": seed.seed_id,
            "evidence_bundle_id": seed.evidence_bundle_id,
            "confidence": seed.confidence,
            "candidate_gate": "schema_and_lineage_review",
        },
        "metadata": {
            "conversion_service": "StrategySpecConversionService",
            "conversion_version": "1.0",
            "source_seed_id": seed.seed_id,
            "evidence_bundle_id": seed.evidence_bundle_id,
            "research_only": True,
            "registry_payload_built": True,
            "registry_write_performed": False,
            "execution_route": "none",
        },
        "strategy_spec": payload,
    }


def _strategy_spec_checksum(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _execution_recipe(conversion_metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Validate the explicit recipe and align Spec scope fields it declares.

    Symbols and frequency are only filled from the recipe when the caller gave
    none; a different caller value stays and is rejected as a contradiction.
    """
    if "execution_recipe" not in conversion_metadata:
        return None
    try:
        recipe = validate_execution_recipe(conversion_metadata["execution_recipe"])
    except StrategyArtifactValidationError as exc:
        raise StrategySpecConversionError(str(exc)) from exc
    conversion_metadata["execution_recipe"] = recipe
    if not conversion_metadata.get("symbols") and not conversion_metadata.get("symbol_universe"):
        conversion_metadata["symbols"] = list(recipe["symbols"])
    if not conversion_metadata.get("frequency") and not conversion_metadata.get("bar_frequency"):
        conversion_metadata["frequency"] = recipe["bar_frequency"]
    return recipe


def _conversion_metadata(seed: StrategySpecSeed, metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    source_metadata = dict(seed.metadata)
    payload = {
        "task_id": "STRAT-003",
        "conversion_service": "StrategySpecConversionService",
        "conversion_version": "1.0",
        "source_seed_id": seed.seed_id,
        "evidence_bundle_id": seed.evidence_bundle_id,
        "backend_hint": seed.backend_hint,
        "feature_hints": list(seed.feature_hints),
        "label_hints": list(seed.label_hints),
        "risk_notes": list(seed.risk_notes),
        "required_data": list(seed.required_data),
        "confidence": seed.confidence,
    }
    for key in ("source_license_scope", "access_scope", "entitlement_tags", "execution_recipe"):
        if key in source_metadata:
            payload[key] = source_metadata[key]
    if "execution_recipe" in (metadata or {}):
        # Reserved: the recipe is the Seed's (Source's); callers may not add or
        # rewrite it.
        if source_metadata.get("execution_recipe") != metadata["execution_recipe"]:
            raise StrategySpecConversionError(
                "conversion metadata execution_recipe contradicts or is absent from the Seed recipe"
            )
    payload.update(dict(metadata or {}))
    payload.update(
        {
            "research_only": True,
            "registry_write_performed": False,
            "execution_route": "none",
        }
    )
    return payload


def _build_title(
    seed: StrategySpecSeed,
    records: Sequence[SourceRecord],
    *,
    title: str | None,
) -> str:
    if title:
        return _require_text(title, "title")
    metadata_title = seed.metadata.get("title") or seed.metadata.get("strategy_title") or seed.metadata.get("name")
    if metadata_title:
        return _require_text(metadata_title, "title")
    if records:
        return _require_text(records[0].title, "title")
    return _first_sentence(seed.hypothesis)[:96] or f"StrategySpec from {seed.seed_id}"


def _stable_strategy_id(title: str, seed: StrategySpecSeed) -> str:
    digest = hashlib.sha1(f"{seed.seed_id}:{seed.evidence_bundle_id}".encode("utf-8")).hexdigest()[:8]
    return f"strat-{_slugify(title)[:48]}-{digest}"


def _objective(seed: StrategySpecSeed, title: str) -> str:
    backend = f" using {seed.backend_hint}" if seed.backend_hint else ""
    return (
        f"Evaluate {title}{backend} under governed research gates before any paper, canary, "
        "or live promotion."
    )


def _symbols(seed: StrategySpecSeed, metadata: Mapping[str, Any]) -> list[str]:
    values = _string_list(metadata.get("symbols") or metadata.get("symbol_universe"))
    if values:
        return values
    return ["RESEARCH_UNIVERSE"]


def _frequency(seed: StrategySpecSeed, metadata: Mapping[str, Any]) -> str:
    explicit = metadata.get("frequency") or metadata.get("bar_frequency")
    if explicit:
        return _require_text(explicit, "frequency")
    text = " ".join([str(seed.holding_period or ""), *seed.required_data]).lower()
    if "minute" in text or "intraday" in text:
        return "1m"
    if "hour" in text:
        return "1h"
    if "daily" in text or "day" in text or "ohlcv" in text:
        return "1d"
    return "research"


def _rebalance_cadence(seed: StrategySpecSeed) -> str | None:
    if seed.holding_period:
        return str(seed.holding_period)
    return None


def _metrics(metadata: Mapping[str, Any]) -> list[str]:
    return _string_list(metadata.get("metrics")) or ["sharpe_ratio", "max_drawdown"]


def _data_dependencies(seed: StrategySpecSeed) -> list[dict[str, str]]:
    dependencies: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def append(ref: str, kind: str) -> None:
        key = (kind, ref)
        if key not in seen:
            seen.add(key)
            dependencies.append({"ref": ref, "kind": kind})

    for source_id in seed.source_ids:
        append(str(source_id), "source_record")
    for required_data in seed.required_data:
        ref = str(required_data).strip()
        if ref:
            append(ref if ":" in ref else f"dataset:{_slugify(ref)}", "dataset")
    return dependencies


def _source_kind(records: Sequence[SourceRecord]) -> str:
    source_types = {record.source_type for record in records}
    if len(source_types) != 1:
        return "workflow"
    source_type = next(iter(source_types))
    if source_type == SourceType.PAPER:
        return "paper"
    if source_type == SourceType.REPO:
        return "repo"
    if source_type == SourceType.INTERNAL_NOTE:
        return "note"
    return "workflow"


def _coerce_source_record(value: SourceRecord | Mapping[str, Any]) -> SourceRecord:
    if isinstance(value, SourceRecord):
        return value
    return SourceRecord.from_dict(value)


def _coerce_evidence_item(value: EvidenceItem | Mapping[str, Any]) -> EvidenceItem:
    if isinstance(value, EvidenceItem):
        return value
    return EvidenceItem.from_dict(value)


def _string_list(value: Any) -> list[str]:
    if value in (None, "", [], ()):
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence):
        values = [str(item) for item in value]
    else:
        values = [str(value)]
    return [item.strip() for item in values if item.strip()]


def _require_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise StrategySpecConversionError(f"{field_name} is required")
    return normalized


def _validate_semver(value: str) -> str:
    normalized = _require_text(value, "version")
    if not _SEMVER_RE.match(normalized):
        raise StrategySpecConversionError(f"version must be semver x.y.z: {normalized}")
    return normalized


def _first_sentence(text: str) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return ""
    return re.split(r"(?<=[.!?])\s+", compact, maxsplit=1)[0].strip()


def _slugify(value: str, fallback: str = "strategy") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug or fallback


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime | str) -> str:
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "StrategySpecConversionError",
    "StrategySpecConversionResult",
    "StrategySpecConversionService",
    "convert_source_material_to_strategy_spec",
    "convert_strategy_spec_seed_to_strategy_spec",
]
