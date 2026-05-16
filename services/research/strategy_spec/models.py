"""Canonical StrategySpec domain model.

The JSON schema in ``services/control-plane/specs/strategy_spec.schema.json``
is the wire contract.  This module gives research and registry code a small
typed object model that round-trips to that contract without adding write-side
behavior or execution authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator


class StrategySpecValidationError(ValueError):
    """Raised when a StrategySpec payload or model violates the contract."""


class StrategyLifecycleState(str, Enum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    REVIEW = "review"
    APPROVED = "approved"
    ARCHIVED = "archived"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class StrategyDataDependencyKind(str, Enum):
    DATASET = "dataset"
    FEATURE_SET = "feature_set"
    PAPER = "paper"
    REPO = "repo"
    NOTE = "note"
    SOURCE_RECORD = "source_record"


class StrategyQuantityType(str, Enum):
    SHARES = "SHARES"
    CASH_VALUE = "CASH_VALUE"
    PERCENT_PORTFOLIO = "PERCENT_PORTFOLIO"


class StrategyExecutionModeHint(str, Enum):
    RESEARCH = "research"
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"


class StrategySourceKind(str, Enum):
    MANUAL = "manual"
    PAPER = "paper"
    REPO = "repo"
    NOTE = "note"
    WORKFLOW = "workflow"


class StrategyEvidenceRefType(str, Enum):
    EVIDENCE_BUNDLE = "evidence_bundle"
    EVIDENCE_ITEM = "evidence_item"
    SOURCE_RECORD = "source_record"
    CITATION = "citation"
    EXPERIMENT_ARTIFACT = "experiment_artifact"
    REGISTRY_ENTRY = "registry_entry"


def strategy_spec_schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "control-plane" / "specs" / "strategy_spec.schema.json"


def load_strategy_spec_schema(schema_path: Path | None = None) -> dict[str, Any]:
    return json.loads((schema_path or strategy_spec_schema_path()).read_text(encoding="utf-8"))


def _location(error: Any) -> str:
    return ".".join(str(part) for part in error.path) or "<root>"


def validate_strategy_spec_payload(payload: Mapping[str, Any], schema_path: Path | None = None) -> None:
    """Validate a raw StrategySpec payload against the canonical JSON schema."""
    if not isinstance(payload, Mapping):
        raise StrategySpecValidationError("StrategySpec payload must be a JSON object")
    validator = Draft7Validator(load_strategy_spec_schema(schema_path))
    errors = sorted(validator.iter_errors(dict(payload)), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        raise StrategySpecValidationError(
            f"StrategySpec schema validation failed at {_location(first)}: {first.message}"
        )


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise StrategySpecValidationError(f"{field_name} is required")
    return text


def _optional_text(value: Any, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    return _require_text(value, field_name)


def _optional_positive_int(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise StrategySpecValidationError(f"{field_name} must be a positive integer") from exc
    if normalized <= 0:
        raise StrategySpecValidationError(f"{field_name} must be a positive integer")
    return normalized


def _as_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StrategySpecValidationError(f"{field_name} must be an object")
    return value


def _as_sequence(value: Sequence[Any] | Any | None, field_name: str) -> tuple[Any, ...]:
    if value is None:
        return tuple()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise StrategySpecValidationError(f"{field_name} must be an array")
    return tuple(value)


def _text_sequence(value: Sequence[Any] | Any | None, field_name: str, *, min_items: int = 0) -> tuple[str, ...]:
    items = tuple(_require_text(item, f"{field_name}[]") for item in _as_sequence(value, field_name))
    if len(items) < min_items:
        raise StrategySpecValidationError(f"{field_name} must contain at least {min_items} item(s)")
    return items


def _coerce_enum(enum_type: type[Enum], value: Enum | str | None, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, enum_type):
        return str(value.value)
    try:
        return str(enum_type(str(value)).value)
    except ValueError as exc:
        allowed = ", ".join(str(item.value) for item in enum_type)
        raise StrategySpecValidationError(f"{field_name} must be one of: {allowed}") from exc


@dataclass(frozen=True)
class MarketScope:
    symbols: Sequence[str]
    frequency: str
    asset_classes: Sequence[str] = field(default_factory=tuple)
    venues: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", _text_sequence(self.symbols, "market_scope.symbols", min_items=1))
        object.__setattr__(self, "frequency", _require_text(self.frequency, "market_scope.frequency"))
        object.__setattr__(self, "asset_classes", _text_sequence(self.asset_classes, "market_scope.asset_classes"))
        object.__setattr__(self, "venues", _text_sequence(self.venues, "market_scope.venues"))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "symbols": list(self.symbols),
            "frequency": self.frequency,
        }
        if self.asset_classes:
            payload["asset_classes"] = list(self.asset_classes)
        if self.venues:
            payload["venues"] = list(self.venues)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MarketScope":
        mapping = _as_mapping(data, "market_scope")
        return cls(
            symbols=mapping.get("symbols", ()),
            frequency=mapping.get("frequency", ""),
            asset_classes=mapping.get("asset_classes", ()),
            venues=mapping.get("venues", ()),
        )


@dataclass(frozen=True)
class DataDependency:
    ref: str
    kind: StrategyDataDependencyKind | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", _require_text(self.ref, "data_dependencies[].ref"))
        object.__setattr__(
            self,
            "kind",
            _coerce_enum(StrategyDataDependencyKind, self.kind, "data_dependencies[].kind"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "kind": self.kind}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DataDependency":
        mapping = _as_mapping(data, "data_dependencies[]")
        return cls(ref=mapping.get("ref", ""), kind=mapping.get("kind", ""))


@dataclass(frozen=True)
class ExecutionProfile:
    signal_schema_version: str
    quantity_type: StrategyQuantityType | str
    rebalance_cadence: str | None = None
    execution_mode_hint: StrategyExecutionModeHint | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "signal_schema_version",
            _require_text(self.signal_schema_version, "execution_profile.signal_schema_version"),
        )
        object.__setattr__(
            self,
            "quantity_type",
            _coerce_enum(StrategyQuantityType, self.quantity_type, "execution_profile.quantity_type"),
        )
        object.__setattr__(
            self,
            "rebalance_cadence",
            _optional_text(self.rebalance_cadence, "execution_profile.rebalance_cadence"),
        )
        object.__setattr__(
            self,
            "execution_mode_hint",
            _coerce_enum(
                StrategyExecutionModeHint,
                self.execution_mode_hint,
                "execution_profile.execution_mode_hint",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "signal_schema_version": self.signal_schema_version,
            "quantity_type": self.quantity_type,
        }
        if self.rebalance_cadence:
            payload["rebalance_cadence"] = self.rebalance_cadence
        if self.execution_mode_hint:
            payload["execution_mode_hint"] = self.execution_mode_hint
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionProfile":
        mapping = _as_mapping(data, "execution_profile")
        return cls(
            signal_schema_version=mapping.get("signal_schema_version", ""),
            quantity_type=mapping.get("quantity_type", ""),
            rebalance_cadence=mapping.get("rebalance_cadence"),
            execution_mode_hint=mapping.get("execution_mode_hint"),
        )


@dataclass(frozen=True)
class EvaluationPlan:
    metrics: Sequence[str]
    candidate_gate: str | None = None
    paper_gate: str | None = None
    live_gate: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", _text_sequence(self.metrics, "evaluation_plan.metrics", min_items=1))
        object.__setattr__(self, "candidate_gate", _optional_text(self.candidate_gate, "evaluation_plan.candidate_gate"))
        object.__setattr__(self, "paper_gate", _optional_text(self.paper_gate, "evaluation_plan.paper_gate"))
        object.__setattr__(self, "live_gate", _optional_text(self.live_gate, "evaluation_plan.live_gate"))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"metrics": list(self.metrics)}
        if self.candidate_gate:
            payload["candidate_gate"] = self.candidate_gate
        if self.paper_gate:
            payload["paper_gate"] = self.paper_gate
        if self.live_gate:
            payload["live_gate"] = self.live_gate
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvaluationPlan":
        mapping = _as_mapping(data, "evaluation_plan")
        return cls(
            metrics=mapping.get("metrics", ()),
            candidate_gate=mapping.get("candidate_gate"),
            paper_gate=mapping.get("paper_gate"),
            live_gate=mapping.get("live_gate"),
        )


@dataclass(frozen=True)
class Governance:
    approval_required: bool
    policy_id: str | None = None
    risk_profile: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.approval_required, bool):
            raise StrategySpecValidationError("governance.approval_required must be a boolean")
        object.__setattr__(self, "policy_id", _optional_text(self.policy_id, "governance.policy_id"))
        object.__setattr__(self, "risk_profile", _optional_text(self.risk_profile, "governance.risk_profile"))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"approval_required": self.approval_required}
        if self.policy_id:
            payload["policy_id"] = self.policy_id
        if self.risk_profile:
            payload["risk_profile"] = self.risk_profile
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Governance":
        mapping = _as_mapping(data, "governance")
        return cls(
            approval_required=mapping.get("approval_required"),
            policy_id=mapping.get("policy_id"),
            risk_profile=mapping.get("risk_profile"),
        )


@dataclass(frozen=True)
class Provenance:
    source_kind: StrategySourceKind | str
    created_at: str
    source_refs: Sequence[str] = field(default_factory=tuple)
    created_by: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_kind", _coerce_enum(StrategySourceKind, self.source_kind, "provenance.source_kind"))
        object.__setattr__(self, "created_at", _require_text(self.created_at, "provenance.created_at"))
        object.__setattr__(self, "source_refs", _text_sequence(self.source_refs, "provenance.source_refs"))
        object.__setattr__(self, "created_by", _optional_text(self.created_by, "provenance.created_by"))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_kind": self.source_kind,
            "created_at": self.created_at,
        }
        if self.source_refs:
            payload["source_refs"] = list(self.source_refs)
        if self.created_by:
            payload["created_by"] = self.created_by
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Provenance":
        mapping = _as_mapping(data, "provenance")
        return cls(
            source_kind=mapping.get("source_kind", ""),
            created_at=mapping.get("created_at", ""),
            source_refs=mapping.get("source_refs", ()),
            created_by=mapping.get("created_by"),
        )


@dataclass(frozen=True)
class EvidenceRef:
    ref_type: StrategyEvidenceRefType | str
    ref_id: str
    source_id: str | None = None
    evidence_item_id: str | None = None
    citation_ref: str | None = None
    association: str | None = None
    summary: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "ref_type",
            _coerce_enum(StrategyEvidenceRefType, self.ref_type, "evidence_refs[].ref_type"),
        )
        object.__setattr__(self, "ref_id", _require_text(self.ref_id, "evidence_refs[].ref_id"))
        object.__setattr__(self, "source_id", _optional_text(self.source_id, "evidence_refs[].source_id"))
        object.__setattr__(
            self,
            "evidence_item_id",
            _optional_text(self.evidence_item_id, "evidence_refs[].evidence_item_id"),
        )
        object.__setattr__(self, "citation_ref", _optional_text(self.citation_ref, "evidence_refs[].citation_ref"))
        object.__setattr__(self, "association", _optional_text(self.association, "evidence_refs[].association"))
        object.__setattr__(self, "summary", _optional_text(self.summary, "evidence_refs[].summary"))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ref_type": self.ref_type,
            "ref_id": self.ref_id,
        }
        if self.source_id:
            payload["source_id"] = self.source_id
        if self.evidence_item_id:
            payload["evidence_item_id"] = self.evidence_item_id
        if self.citation_ref:
            payload["citation_ref"] = self.citation_ref
        if self.association:
            payload["association"] = self.association
        if self.summary:
            payload["summary"] = self.summary
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceRef":
        mapping = _as_mapping(data, "evidence_refs[]")
        return cls(
            ref_type=mapping.get("ref_type", ""),
            ref_id=mapping.get("ref_id", ""),
            source_id=mapping.get("source_id"),
            evidence_item_id=mapping.get("evidence_item_id"),
            citation_ref=mapping.get("citation_ref"),
            association=mapping.get("association"),
            summary=mapping.get("summary"),
        )


@dataclass(frozen=True)
class CodeRef:
    repo_ref: str
    path: str
    source_id: str | None = None
    commit: str | None = None
    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    url: str | None = None
    association: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_ref", _require_text(self.repo_ref, "code_refs[].repo_ref"))
        object.__setattr__(self, "path", _require_text(self.path, "code_refs[].path"))
        object.__setattr__(self, "source_id", _optional_text(self.source_id, "code_refs[].source_id"))
        object.__setattr__(self, "commit", _optional_text(self.commit, "code_refs[].commit"))
        object.__setattr__(self, "symbol", _optional_text(self.symbol, "code_refs[].symbol"))
        object.__setattr__(self, "line_start", _optional_positive_int(self.line_start, "code_refs[].line_start"))
        object.__setattr__(self, "line_end", _optional_positive_int(self.line_end, "code_refs[].line_end"))
        if self.line_start is not None and self.line_end is not None and self.line_end < self.line_start:
            raise StrategySpecValidationError("code_refs[].line_end must be >= line_start")
        object.__setattr__(self, "url", _optional_text(self.url, "code_refs[].url"))
        object.__setattr__(self, "association", _optional_text(self.association, "code_refs[].association"))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "repo_ref": self.repo_ref,
            "path": self.path,
        }
        if self.source_id:
            payload["source_id"] = self.source_id
        if self.commit:
            payload["commit"] = self.commit
        if self.symbol:
            payload["symbol"] = self.symbol
        if self.line_start is not None:
            payload["line_start"] = self.line_start
        if self.line_end is not None:
            payload["line_end"] = self.line_end
        if self.url:
            payload["url"] = self.url
        if self.association:
            payload["association"] = self.association
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CodeRef":
        mapping = _as_mapping(data, "code_refs[]")
        return cls(
            repo_ref=mapping.get("repo_ref", ""),
            path=mapping.get("path", ""),
            source_id=mapping.get("source_id"),
            commit=mapping.get("commit"),
            symbol=mapping.get("symbol"),
            line_start=mapping.get("line_start"),
            line_end=mapping.get("line_end"),
            url=mapping.get("url"),
            association=mapping.get("association"),
        )


@dataclass(frozen=True)
class StrategySpec:
    spec_version: str
    strategy_id: str
    title: str
    hypothesis: str
    objective: str
    market_scope: MarketScope | Mapping[str, Any]
    data_dependencies: Sequence[DataDependency | Mapping[str, Any]]
    execution_profile: ExecutionProfile | Mapping[str, Any]
    evaluation_plan: EvaluationPlan | Mapping[str, Any]
    governance: Governance | Mapping[str, Any]
    provenance: Provenance | Mapping[str, Any]
    evidence_refs: Sequence[EvidenceRef | Mapping[str, Any]] = field(default_factory=tuple)
    code_refs: Sequence[CodeRef | Mapping[str, Any]] = field(default_factory=tuple)
    lifecycle_state: StrategyLifecycleState | str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "spec_version", _require_text(self.spec_version, "spec_version"))
        if self.spec_version != "1.0":
            raise StrategySpecValidationError("spec_version must be 1.0")
        object.__setattr__(self, "strategy_id", _require_text(self.strategy_id, "strategy_id"))
        object.__setattr__(self, "title", _require_text(self.title, "title"))
        object.__setattr__(self, "hypothesis", _require_text(self.hypothesis, "hypothesis"))
        object.__setattr__(self, "objective", _require_text(self.objective, "objective"))
        object.__setattr__(
            self,
            "market_scope",
            self.market_scope if isinstance(self.market_scope, MarketScope) else MarketScope.from_dict(self.market_scope),
        )
        object.__setattr__(
            self,
            "data_dependencies",
            tuple(
                item if isinstance(item, DataDependency) else DataDependency.from_dict(_as_mapping(item, "data_dependencies[]"))
                for item in _as_sequence(self.data_dependencies, "data_dependencies")
            ),
        )
        if not self.data_dependencies:
            raise StrategySpecValidationError("data_dependencies must contain at least one item")
        object.__setattr__(
            self,
            "execution_profile",
            self.execution_profile
            if isinstance(self.execution_profile, ExecutionProfile)
            else ExecutionProfile.from_dict(self.execution_profile),
        )
        object.__setattr__(
            self,
            "evaluation_plan",
            self.evaluation_plan if isinstance(self.evaluation_plan, EvaluationPlan) else EvaluationPlan.from_dict(self.evaluation_plan),
        )
        object.__setattr__(
            self,
            "governance",
            self.governance if isinstance(self.governance, Governance) else Governance.from_dict(self.governance),
        )
        object.__setattr__(
            self,
            "provenance",
            self.provenance if isinstance(self.provenance, Provenance) else Provenance.from_dict(self.provenance),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(
                item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(_as_mapping(item, "evidence_refs[]"))
                for item in _as_sequence(self.evidence_refs, "evidence_refs")
            ),
        )
        object.__setattr__(
            self,
            "code_refs",
            tuple(
                item if isinstance(item, CodeRef) else CodeRef.from_dict(_as_mapping(item, "code_refs[]"))
                for item in _as_sequence(self.code_refs, "code_refs")
            ),
        )
        object.__setattr__(
            self,
            "lifecycle_state",
            _coerce_enum(StrategyLifecycleState, self.lifecycle_state, "lifecycle_state"),
        )
        object.__setattr__(self, "metadata", dict(_as_mapping(self.metadata, "metadata")))

        errors = validate_strategy_spec(self)
        if errors:
            raise StrategySpecValidationError("; ".join(errors))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "spec_version": self.spec_version,
            "strategy_id": self.strategy_id,
            "title": self.title,
            "hypothesis": self.hypothesis,
            "objective": self.objective,
            "market_scope": self.market_scope.to_dict(),
            "data_dependencies": [dependency.to_dict() for dependency in self.data_dependencies],
            "execution_profile": self.execution_profile.to_dict(),
            "evaluation_plan": self.evaluation_plan.to_dict(),
            "governance": self.governance.to_dict(),
            "provenance": self.provenance.to_dict(),
        }
        if self.lifecycle_state:
            payload["lifecycle_state"] = self.lifecycle_state
        if self.evidence_refs:
            payload["evidence_refs"] = [ref.to_dict() for ref in self.evidence_refs]
        if self.code_refs:
            payload["code_refs"] = [ref.to_dict() for ref in self.code_refs]
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, validate_schema: bool = True) -> "StrategySpec":
        if validate_schema:
            validate_strategy_spec_payload(data)
        mapping = _as_mapping(data, "StrategySpec")
        return cls(
            spec_version=mapping.get("spec_version", ""),
            strategy_id=mapping.get("strategy_id", ""),
            title=mapping.get("title", ""),
            hypothesis=mapping.get("hypothesis", ""),
            objective=mapping.get("objective", ""),
            market_scope=mapping.get("market_scope", {}),
            data_dependencies=mapping.get("data_dependencies", ()),
            execution_profile=mapping.get("execution_profile", {}),
            evaluation_plan=mapping.get("evaluation_plan", {}),
            governance=mapping.get("governance", {}),
            provenance=mapping.get("provenance", {}),
            evidence_refs=mapping.get("evidence_refs", ()),
            code_refs=mapping.get("code_refs", ()),
            lifecycle_state=mapping.get("lifecycle_state"),
            metadata=mapping.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, payload: str, *, validate_schema: bool = True) -> "StrategySpec":
        return cls.from_dict(json.loads(payload), validate_schema=validate_schema)


def validate_strategy_spec(spec: StrategySpec) -> list[str]:
    """Return semantic validation errors. Empty list means the model is valid."""
    errors: list[str] = []

    dependency_keys = {(dependency.kind, dependency.ref) for dependency in spec.data_dependencies}
    if len(dependency_keys) != len(spec.data_dependencies):
        errors.append("data_dependencies must not contain duplicate kind/ref pairs")

    evidence_ref_keys = {(ref.ref_type, ref.ref_id) for ref in spec.evidence_refs}
    if len(evidence_ref_keys) != len(spec.evidence_refs):
        errors.append("evidence_refs must not contain duplicate ref_type/ref_id pairs")

    code_ref_keys = {
        (
            ref.repo_ref,
            ref.path,
            ref.commit,
            ref.symbol,
            ref.line_start,
            ref.line_end,
        )
        for ref in spec.code_refs
    }
    if len(code_ref_keys) != len(spec.code_refs):
        errors.append("code_refs must not contain duplicate repo/path/commit/symbol/line pairs")

    if spec.execution_profile.execution_mode_hint in {
        StrategyExecutionModeHint.PAPER.value,
        StrategyExecutionModeHint.CANARY.value,
        StrategyExecutionModeHint.LIVE.value,
    } and not spec.governance.approval_required:
        errors.append("paper/canary/live execution_mode_hint requires governance.approval_required=true")

    if spec.provenance.source_kind != StrategySourceKind.MANUAL.value and not spec.provenance.source_refs:
        errors.append("provenance.source_refs is required for non-manual StrategySpec provenance")

    return errors
