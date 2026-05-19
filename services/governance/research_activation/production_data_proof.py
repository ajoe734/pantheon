"""Generic ProductionDataProof schema for research activation tier R3.

The schema is intentionally adapter-neutral. Qlib, TRL, FinRL, W&B, and future
research adapters provide their own evidence instances, while this module
keeps the shared governance shape and fail-closed F3 output boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "ProductionDataProof.v1"
PRODUCTION_DATA_TIER = "R3"
ACTIVATION_TIERS = ("R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7")

ALLOWED_ADAPTER_OUTPUT_TYPES = frozenset(
    {
        "strategy_spec",
        "experiment_run",
        "evaluation_result",
        "model_artifact",
        "signal_snapshot",
        "optimizer_result",
        "registry_admission_packet",
        "candidate_packet",
    }
)

FORBIDDEN_ADAPTER_OUTPUT_TYPES = frozenset(
    {
        "order",
        "orders",
        "runtimebinding",
        "runtime_binding",
        "broker_route",
        "broker_order_route",
        "live_capital_binding",
        "capital_binding",
        "deployment_stage",
        "deployment_stage_mutation",
        "paper_deployment_stage_mutation",
        "canary_deployment_stage_mutation",
        "live_deployment_stage_mutation",
    }
)

ORDER_CAPABLE_TARGETS = frozenset(
    {
        "broker",
        "capital",
        "canary",
        "lean",
        "live",
        "order",
        "order_route",
        "order_routing",
        "paper",
        "runtime",
    }
)


class ProductionDataProofError(ValueError):
    """Raised when a ProductionDataProof is incomplete or unsafe."""


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass(frozen=True)
class EvidenceRef:
    key: str
    ref_type: str
    ref_id: str | None = None
    path: str | None = None
    checksum: str | None = None
    status: str = "provided"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvidenceRef":
        return cls(
            key=_text(value.get("key") or value.get("name")),
            ref_type=_text(value.get("ref_type") or value.get("type")),
            ref_id=_optional_text(value.get("ref_id") or value.get("id")),
            path=_optional_text(value.get("path")),
            checksum=_optional_text(value.get("checksum")),
            status=_optional_text(value.get("status")) or "provided",
            metadata=dict(_mapping(value.get("metadata"))),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "key": self.key,
            "ref_type": self.ref_type,
            "status": self.status,
            "metadata": dict(self.metadata),
        }
        _put_if_present(payload, "ref_id", self.ref_id)
        _put_if_present(payload, "path", self.path)
        _put_if_present(payload, "checksum", self.checksum)
        return payload


@dataclass(frozen=True)
class ProviderProof:
    name: str
    dataset_id: str
    source_class: str
    provider_id: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProviderProof":
        return cls(
            name=_text(value.get("name") or value.get("provider_name")),
            dataset_id=_text(value.get("dataset_id")),
            source_class=_text(value.get("source_class")),
            provider_id=_optional_text(value.get("provider_id")),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "dataset_id": self.dataset_id,
            "source_class": self.source_class,
        }
        _put_if_present(payload, "provider_id", self.provider_id)
        return payload


@dataclass(frozen=True)
class EntitlementProof:
    license_scope: str
    allowed_use: tuple[str, ...]
    entitlement_ref: str | None = None
    entitlement_tags: tuple[str, ...] = ()
    restrictions: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EntitlementProof":
        return cls(
            entitlement_ref=_optional_text(value.get("entitlement_ref")),
            entitlement_tags=tuple(_strings(value.get("entitlement_tags"))),
            license_scope=_text(value.get("license_scope")),
            allowed_use=tuple(_normalized_tokens(value.get("allowed_use"))),
            restrictions=tuple(_strings(value.get("restrictions"))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entitlement_ref": self.entitlement_ref,
            "entitlement_tags": list(self.entitlement_tags),
            "license_scope": self.license_scope,
            "allowed_use": list(self.allowed_use),
            "restrictions": list(self.restrictions),
        }


@dataclass(frozen=True)
class FreshnessProof:
    status: str
    as_of: str
    last_ingested_at: str
    freshness_sla_seconds: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FreshnessProof":
        return cls(
            status=_normalized_text(value.get("status")),
            as_of=_text(value.get("as_of")),
            last_ingested_at=_text(value.get("last_ingested_at")),
            freshness_sla_seconds=_positive_int_or_none(value.get("freshness_sla_seconds")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "as_of": self.as_of,
            "last_ingested_at": self.last_ingested_at,
            "freshness_sla_seconds": self.freshness_sla_seconds,
        }


@dataclass(frozen=True)
class PointInTimeProof:
    point_in_time: bool
    event_time_field: str
    available_time_field: str
    source_watermark: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PointInTimeProof":
        return cls(
            point_in_time=value.get("point_in_time") is True,
            event_time_field=_text(value.get("event_time_field")),
            available_time_field=_text(value.get("available_time_field")),
            source_watermark=_text(value.get("source_watermark")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "point_in_time": self.point_in_time,
            "event_time_field": self.event_time_field,
            "available_time_field": self.available_time_field,
            "source_watermark": self.source_watermark,
        }


@dataclass(frozen=True)
class DurableStorageProof:
    backend: str
    dataset_ref: str
    snapshot_ref: str
    path: str
    checksum: str
    durable: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DurableStorageProof":
        return cls(
            backend=_text(value.get("backend")),
            dataset_ref=_text(value.get("dataset_ref")),
            snapshot_ref=_text(value.get("snapshot_ref")),
            path=_text(value.get("path")),
            checksum=_text(value.get("checksum")),
            durable=value.get("durable") is True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "dataset_ref": self.dataset_ref,
            "snapshot_ref": self.snapshot_ref,
            "path": self.path,
            "checksum": self.checksum,
            "durable": self.durable,
        }


@dataclass(frozen=True)
class AuditProof:
    evidence_bundle_ref: str
    audit_ref: str | None = None
    ingest_run_id: str | None = None
    normalization_run_id: str | None = None
    rate_limit_policy_ref: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AuditProof":
        return cls(
            audit_ref=_optional_text(value.get("audit_ref")),
            ingest_run_id=_optional_text(value.get("ingest_run_id")),
            normalization_run_id=_optional_text(value.get("normalization_run_id")),
            evidence_bundle_ref=_text(value.get("evidence_bundle_ref")),
            rate_limit_policy_ref=_optional_text(value.get("rate_limit_policy_ref")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_ref": self.audit_ref,
            "ingest_run_id": self.ingest_run_id,
            "normalization_run_id": self.normalization_run_id,
            "evidence_bundle_ref": self.evidence_bundle_ref,
            "rate_limit_policy_ref": self.rate_limit_policy_ref,
        }


@dataclass(frozen=True)
class NoOrderRouteProof:
    no_order_route: bool
    produced_artifact_types: tuple[str, ...]
    execution_targets: tuple[str, ...] = ()
    attempted_mutation_types: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NoOrderRouteProof":
        return cls(
            no_order_route=value.get("no_order_route") is True,
            produced_artifact_types=tuple(
                _normalized_tokens(
                    value.get("produced_artifact_types")
                    or value.get("output_artifact_types")
                    or value.get("allowed_outputs")
                )
            ),
            execution_targets=tuple(_normalized_tokens(value.get("execution_targets"))),
            attempted_mutation_types=tuple(
                _normalized_tokens(
                    value.get("attempted_mutation_types")
                    or value.get("mutation_types")
                    or value.get("mutations")
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "no_order_route": self.no_order_route,
            "produced_artifact_types": list(self.produced_artifact_types),
            "execution_targets": list(self.execution_targets),
            "attempted_mutation_types": list(self.attempted_mutation_types),
        }


@dataclass(frozen=True)
class AdapterEvidence:
    adapter_id: str
    adapter_kind: str
    evidence_refs: tuple[EvidenceRef, ...]
    backend: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AdapterEvidence":
        refs = tuple(
            EvidenceRef.from_mapping(item)
            for item in _sequence_of_mappings(value.get("evidence_refs"))
        )
        return cls(
            adapter_id=_text(value.get("adapter_id")),
            adapter_kind=_normalized_text(value.get("adapter_kind") or value.get("kind")),
            backend=_optional_text(value.get("backend")),
            evidence_refs=refs,
            metadata=dict(_mapping(value.get("metadata"))),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "adapter_id": self.adapter_id,
            "adapter_kind": self.adapter_kind,
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "metadata": dict(self.metadata),
        }
        _put_if_present(payload, "backend", self.backend)
        return payload


@dataclass(frozen=True)
class ProductionDataProof:
    proof_id: str
    adapter_id: str
    adapter_kind: str
    source_dataset_refs: tuple[str, ...]
    provider: ProviderProof
    entitlement: EntitlementProof
    freshness: FreshnessProof
    point_in_time: PointInTimeProof
    storage: DurableStorageProof
    audit: AuditProof
    no_order_route: NoOrderRouteProof
    adapter_evidence: tuple[AdapterEvidence, ...]
    schema_version: str = SCHEMA_VERSION
    activation_tier: str = PRODUCTION_DATA_TIER
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProductionDataProof":
        controls = _mapping(value.get("no_order_route") or value.get("controls"))
        return cls(
            proof_id=_text(value.get("proof_id") or value.get("id")),
            schema_version=_text(value.get("schema_version")) or SCHEMA_VERSION,
            activation_tier=_text(value.get("activation_tier") or value.get("tier")) or PRODUCTION_DATA_TIER,
            created_at=_optional_text(value.get("created_at")),
            adapter_id=_text(value.get("adapter_id")),
            adapter_kind=_normalized_text(value.get("adapter_kind") or value.get("kind")),
            source_dataset_refs=tuple(_strings(value.get("source_dataset_refs"))),
            provider=ProviderProof.from_mapping(_mapping(value.get("provider"))),
            entitlement=EntitlementProof.from_mapping(_mapping(value.get("entitlement"))),
            freshness=FreshnessProof.from_mapping(_mapping(value.get("freshness"))),
            point_in_time=PointInTimeProof.from_mapping(
                _mapping(value.get("point_in_time") or value.get("pit"))
            ),
            storage=DurableStorageProof.from_mapping(_mapping(value.get("storage"))),
            audit=AuditProof.from_mapping(_mapping(value.get("audit"))),
            no_order_route=NoOrderRouteProof.from_mapping(controls),
            adapter_evidence=tuple(
                AdapterEvidence.from_mapping(item)
                for item in _sequence_of_mappings(
                    value.get("adapter_evidence") or value.get("adapter_evidence_instances")
                )
            ),
            metadata=dict(_mapping(value.get("metadata"))),
        )

    def validate(self) -> ValidationResult:
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        add = errors.append

        if self.schema_version != SCHEMA_VERSION:
            add(_issue("schema_version_mismatch", "schema_version", f"expected {SCHEMA_VERSION}"))
        if self.activation_tier != PRODUCTION_DATA_TIER:
            add(_issue("activation_tier_not_r3", "activation_tier", "ProductionDataProof may only claim R3"))
        if not self.proof_id:
            add(_issue("missing_proof_id", "proof_id", "proof_id is required"))
        if not self.adapter_id:
            add(_issue("missing_adapter_id", "adapter_id", "adapter_id is required"))
        if not self.adapter_kind:
            add(_issue("missing_adapter_kind", "adapter_kind", "adapter_kind is required"))
        if not self.source_dataset_refs:
            add(_issue("missing_source_dataset_refs", "source_dataset_refs", "at least one source dataset ref is required"))

        self._validate_provider(add)
        self._validate_entitlement(add)
        self._validate_freshness(add)
        self._validate_point_in_time(add)
        self._validate_storage(add)
        self._validate_audit(add)
        self._validate_no_order_route(add)
        self._validate_adapter_evidence(add)

        return ValidationResult(
            passed=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    def require_valid(self) -> "ProductionDataProof":
        result = self.validate()
        if not result.passed:
            codes = ", ".join(issue.code for issue in result.errors)
            raise ProductionDataProofError(f"ProductionDataProof failed validation: {codes}")
        return self

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "proof_id": self.proof_id,
            "activation_tier": self.activation_tier,
            "adapter_id": self.adapter_id,
            "adapter_kind": self.adapter_kind,
            "source_dataset_refs": list(self.source_dataset_refs),
            "provider": self.provider.to_dict(),
            "entitlement": self.entitlement.to_dict(),
            "freshness": self.freshness.to_dict(),
            "point_in_time": self.point_in_time.to_dict(),
            "storage": self.storage.to_dict(),
            "audit": self.audit.to_dict(),
            "no_order_route": self.no_order_route.to_dict(),
            "adapter_evidence": [entry.to_dict() for entry in self.adapter_evidence],
            "metadata": dict(self.metadata),
        }
        _put_if_present(payload, "created_at", self.created_at)
        return payload

    def _validate_provider(self, add: Any) -> None:
        if not self.provider.name:
            add(_issue("missing_provider_name", "provider.name", "provider name is required"))
        if not self.provider.dataset_id:
            add(_issue("missing_provider_dataset_id", "provider.dataset_id", "provider dataset_id is required"))
        if not self.provider.source_class:
            add(_issue("missing_provider_source_class", "provider.source_class", "provider source_class is required"))

    def _validate_entitlement(self, add: Any) -> None:
        if not self.entitlement.entitlement_ref and not self.entitlement.entitlement_tags:
            add(_issue("missing_entitlement_ref", "entitlement", "entitlement_ref or entitlement_tags is required"))
        if not self.entitlement.license_scope:
            add(_issue("missing_license_scope", "entitlement.license_scope", "license_scope is required"))
        allowed_use = set(self.entitlement.allowed_use)
        if not allowed_use:
            add(_issue("missing_allowed_use", "entitlement.allowed_use", "allowed_use is required"))
        if "research" not in allowed_use:
            add(_issue("research_use_not_allowed", "entitlement.allowed_use", "allowed_use must include research"))
        forbidden = sorted(allowed_use & ORDER_CAPABLE_TARGETS)
        if forbidden:
            add(_issue("order_capable_allowed_use", "entitlement.allowed_use", f"order-capable use is forbidden: {', '.join(forbidden)}"))

    def _validate_freshness(self, add: Any) -> None:
        if self.freshness.status != "fresh":
            add(_issue("freshness_not_fresh", "freshness.status", "freshness.status must be fresh"))
        if not self.freshness.as_of:
            add(_issue("missing_freshness_as_of", "freshness.as_of", "freshness.as_of is required"))
        if not self.freshness.last_ingested_at:
            add(_issue("missing_last_ingested_at", "freshness.last_ingested_at", "freshness.last_ingested_at is required"))
        if self.freshness.freshness_sla_seconds is None:
            add(_issue("missing_freshness_sla_seconds", "freshness.freshness_sla_seconds", "freshness SLA is required"))

    def _validate_point_in_time(self, add: Any) -> None:
        if self.point_in_time.point_in_time is not True:
            add(_issue("pit_not_proven", "point_in_time.point_in_time", "point-in-time correctness must be proven"))
        if not self.point_in_time.event_time_field:
            add(_issue("missing_event_time_field", "point_in_time.event_time_field", "event_time_field is required"))
        if not self.point_in_time.available_time_field:
            add(_issue("missing_available_time_field", "point_in_time.available_time_field", "available_time_field is required"))
        if not self.point_in_time.source_watermark:
            add(_issue("missing_source_watermark", "point_in_time.source_watermark", "source_watermark is required"))

    def _validate_storage(self, add: Any) -> None:
        if self.storage.durable is not True:
            add(_issue("storage_not_durable", "storage.durable", "durable storage must be proven"))
        for field_name in ("backend", "dataset_ref", "snapshot_ref", "path"):
            if not getattr(self.storage, field_name):
                add(_issue(f"missing_storage_{field_name}", f"storage.{field_name}", f"storage.{field_name} is required"))
        if not self.storage.checksum.startswith("sha256:"):
            add(_issue("storage_checksum_not_sha256", "storage.checksum", "sha256-prefixed checksum is required"))
        if self.storage.dataset_ref and self.source_dataset_refs and self.storage.dataset_ref not in set(self.source_dataset_refs):
            add(_issue("storage_dataset_ref_not_in_sources", "storage.dataset_ref", "storage dataset_ref must match a source_dataset_refs entry"))

    def _validate_audit(self, add: Any) -> None:
        if not self.audit.audit_ref and not self.audit.evidence_bundle_ref:
            add(_issue("missing_audit_ref", "audit", "audit_ref or evidence_bundle_ref is required"))
        if not self.audit.evidence_bundle_ref:
            add(_issue("missing_evidence_bundle_ref", "audit.evidence_bundle_ref", "evidence_bundle_ref is required"))

    def _validate_no_order_route(self, add: Any) -> None:
        proof = self.no_order_route
        if proof.no_order_route is not True:
            add(_issue("no_order_route_not_proven", "no_order_route.no_order_route", "no_order_route must be true"))
        if not proof.produced_artifact_types:
            add(_issue("missing_produced_artifact_types", "no_order_route.produced_artifact_types", "produced artifact types are required"))
        forbidden_outputs = sorted(set(proof.produced_artifact_types) & FORBIDDEN_ADAPTER_OUTPUT_TYPES)
        unknown_outputs = sorted(
            item
            for item in set(proof.produced_artifact_types)
            if item not in ALLOWED_ADAPTER_OUTPUT_TYPES and item not in FORBIDDEN_ADAPTER_OUTPUT_TYPES
        )
        if forbidden_outputs:
            add(_issue("forbidden_adapter_output", "no_order_route.produced_artifact_types", f"forbidden F3 output: {', '.join(forbidden_outputs)}"))
        if unknown_outputs:
            add(_issue("unknown_adapter_output", "no_order_route.produced_artifact_types", f"unknown output type: {', '.join(unknown_outputs)}"))
        forbidden_targets = sorted(set(proof.execution_targets) & ORDER_CAPABLE_TARGETS)
        if forbidden_targets:
            add(_issue("order_capable_execution_target", "no_order_route.execution_targets", f"order-capable target is forbidden: {', '.join(forbidden_targets)}"))
        forbidden_mutations = sorted(set(proof.attempted_mutation_types) & FORBIDDEN_ADAPTER_OUTPUT_TYPES)
        if forbidden_mutations:
            add(_issue("forbidden_mutation_type", "no_order_route.attempted_mutation_types", f"forbidden mutation type: {', '.join(forbidden_mutations)}"))

    def _validate_adapter_evidence(self, add: Any) -> None:
        if not self.adapter_evidence:
            add(_issue("missing_adapter_evidence", "adapter_evidence", "at least one adapter evidence instance is required"))
            return
        for index, entry in enumerate(self.adapter_evidence):
            path = f"adapter_evidence[{index}]"
            if not entry.adapter_id:
                add(_issue("missing_adapter_evidence_adapter_id", f"{path}.adapter_id", "adapter evidence adapter_id is required"))
            elif entry.adapter_id != self.adapter_id:
                add(_issue("adapter_evidence_id_mismatch", f"{path}.adapter_id", "adapter evidence must match proof adapter_id"))
            if not entry.adapter_kind:
                add(_issue("missing_adapter_evidence_kind", f"{path}.adapter_kind", "adapter evidence adapter_kind is required"))
            elif entry.adapter_kind != self.adapter_kind:
                add(_issue("adapter_evidence_kind_mismatch", f"{path}.adapter_kind", "adapter evidence must match proof adapter_kind"))
            if not entry.evidence_refs:
                add(_issue("missing_adapter_evidence_refs", f"{path}.evidence_refs", "adapter evidence refs are required"))
            for ref_index, ref in enumerate(entry.evidence_refs):
                ref_path = f"{path}.evidence_refs[{ref_index}]"
                if not ref.key:
                    add(_issue("missing_evidence_key", f"{ref_path}.key", "evidence key is required"))
                if not ref.ref_type:
                    add(_issue("missing_evidence_ref_type", f"{ref_path}.ref_type", "evidence ref_type is required"))
                if not ref.ref_id and not ref.path:
                    add(_issue("missing_evidence_ref_target", ref_path, "evidence ref_id or path is required"))


def validate_production_data_proof(value: Mapping[str, Any] | ProductionDataProof) -> ProductionDataProof:
    """Return a valid ProductionDataProof or raise ProductionDataProofError."""

    proof = value if isinstance(value, ProductionDataProof) else ProductionDataProof.from_mapping(value)
    return proof.require_valid()


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, message=message)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _normalized_text(value: Any) -> str:
    return _normalize_token(_text(value))


def _strings(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _normalized_tokens(value: Any) -> list[str]:
    return [_normalize_token(item) for item in _strings(value)]


def _normalize_token(value: str) -> str:
    text = value.strip().lower()
    compact = text.replace("-", "").replace("_", "").replace(" ", "")
    if compact in {"w&b", "wandb", "weights&biases", "weightsandbiases"}:
        return "wandb"
    return text.replace("-", "_").replace(" ", "_")


def _positive_int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value > 0:
        return int(value)
    return None


def _sequence_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _put_if_present(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        payload[key] = value
