"""Dataset lineage models.

Canonical definitions of RawDataset, NormalizedDataset, FeatureDataset, and DatasetVersion.
Based on Pantheon_Market_Data_Scope_and_Source_Plan_v1 §6.4–6.7

Includes event_time / available_time / ingest_time discipline.
"""

from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime, timezone
from enum import Enum
import json


class SourceClass(str, Enum):
    """Data source classification."""
    OFFICIAL_REFERENCE = "official_reference"
    BROKER_EXECUTION = "broker_execution"
    RESEARCH_GRADE = "research_grade"
    DERIVATIVE_ANALYTICS = "derivative_analytics"
    CRYPTO_ANALYTICS = "crypto_analytics"
    INTERNAL_CANONICAL = "internal_can"


class AvailableTimePolicy(str, Enum):
    """How available_time is determined."""
    AT_OPEN = "at_open"
    AT_REPORTED = "at_reported"
    AT_INGEST = "at_ingest"
    DELAYED_MINUTES = "delayed_minutes"
    CUSTOM = "custom"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class RawDataset:
    """Ingested dataset before any normalization.

    Attributes:
        dataset_id: Globally unique dataset identifier.
        source_class: One of the SourceClass enum values.
        market: Market code (e.g., "US", "TW", "CRYPTO").
        instrument_scope: List of security_id or contract_id values this dataset covers.
        coverage_start: Earliest data point date in the dataset.
        coverage_end: Latest data point date in the dataset.
        ingest_time: ISO 8601 timestamp when this dataset was ingested.
        storage_ref: Reference to storage location (e.g., GCS URI, object store path).
        checksum: SHA-256 checksum of the raw data file(s).
        metadata_json: Free-form ingestion metadata.
        created_at: ISO 8601 timestamp of record creation.
    """
    dataset_id: str
    source_class: str
    market: str
    instrument_scope: list[str]
    coverage_start: str
    coverage_end: str
    ingest_time: str
    storage_ref: str
    checksum: str
    metadata_json: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "source_class": self.source_class,
            "market": self.market,
            "instrument_scope": self.instrument_scope,
            "coverage_start": self.coverage_start,
            "coverage_end": self.coverage_end,
            "ingest_time": self.ingest_time,
            "storage_ref": self.storage_ref,
            "checksum": self.checksum,
            "metadata_json": self.metadata_json,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RawDataset":
        return cls(
            dataset_id=data["dataset_id"],
            source_class=data["source_class"],
            market=data["market"],
            instrument_scope=data["instrument_scope"],
            coverage_start=data["coverage_start"],
            coverage_end=data["coverage_end"],
            ingest_time=data["ingest_time"],
            storage_ref=data["storage_ref"],
            checksum=data["checksum"],
            metadata_json=data.get("metadata_json", {}),
            created_at=data.get("created_at") or _utc_now_iso(),
        )

    @staticmethod
    def validate(ds: "RawDataset") -> tuple[bool, list[str]]:
        errors = []
        if not ds.dataset_id:
            errors.append("Missing dataset_id")
        if not ds.source_class:
            errors.append("Missing source_class")
        elif ds.source_class not in {item.value for item in SourceClass}:
            errors.append(f"Invalid source_class: {ds.source_class}")
        if not ds.market:
            errors.append("Missing market")
        if not ds.instrument_scope:
            errors.append("Missing instrument_scope")
        if not ds.coverage_start:
            errors.append("Missing coverage_start")
        if not ds.coverage_end:
            errors.append("Missing coverage_end")
        if not ds.ingest_time:
            errors.append("Missing ingest_time")
        if not ds.storage_ref:
            errors.append("Missing storage_ref")
        if not ds.checksum:
            errors.append("Missing checksum")
        return len(errors) == 0, errors


@dataclass
class NormalizedDataset:
    """Dataset after normalization and symbol mapping.

    Attributes:
        dataset_id: Globally unique dataset identifier.
        parent_raw_dataset_id: Reference to the source RawDataset dataset_id.
        normalization_version: Version string for this normalization pass.
        symbol_mapping_version: Version of the symbol mapping used.
        corp_action_version: Version of corporate action adjustments applied.
        calendar_version: Version of the market calendar used.
        available_time_policy: One of the AvailableTimePolicy enum values.
        storage_ref: Reference to storage location.
        checksum: SHA-256 checksum of the normalized data file(s).
        metadata_json: Free-form normalization metadata.
        created_at: ISO 8601 timestamp of record creation.
    """
    dataset_id: str
    parent_raw_dataset_id: str
    normalization_version: str
    storage_ref: str
    checksum: str
    symbol_mapping_version: Optional[str] = None
    corp_action_version: Optional[str] = None
    calendar_version: Optional[str] = None
    available_time_policy: str = AvailableTimePolicy.AT_INGEST.value
    metadata_json: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "parent_raw_dataset_id": self.parent_raw_dataset_id,
            "normalization_version": self.normalization_version,
            "symbol_mapping_version": self.symbol_mapping_version,
            "corp_action_version": self.corp_action_version,
            "calendar_version": self.calendar_version,
            "available_time_policy": self.available_time_policy,
            "storage_ref": self.storage_ref,
            "checksum": self.checksum,
            "metadata_json": self.metadata_json,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NormalizedDataset":
        return cls(
            dataset_id=data["dataset_id"],
            parent_raw_dataset_id=data["parent_raw_dataset_id"],
            normalization_version=data["normalization_version"],
            storage_ref=data["storage_ref"],
            checksum=data["checksum"],
            symbol_mapping_version=data.get("symbol_mapping_version"),
            corp_action_version=data.get("corp_action_version"),
            calendar_version=data.get("calendar_version"),
            available_time_policy=data.get("available_time_policy", AvailableTimePolicy.AT_INGEST.value),
            metadata_json=data.get("metadata_json", {}),
            created_at=data.get("created_at") or _utc_now_iso(),
        )

    @staticmethod
    def validate(ds: "NormalizedDataset") -> tuple[bool, list[str]]:
        errors = []
        if not ds.dataset_id:
            errors.append("Missing dataset_id")
        if not ds.parent_raw_dataset_id:
            errors.append("Missing parent_raw_dataset_id")
        if not ds.normalization_version:
            errors.append("Missing normalization_version")
        if not ds.storage_ref:
            errors.append("Missing storage_ref")
        if not ds.checksum:
            errors.append("Missing checksum")
        if ds.available_time_policy not in {item.value for item in AvailableTimePolicy}:
            errors.append(f"Invalid available_time_policy: {ds.available_time_policy}")
        return len(errors) == 0, errors


@dataclass
class FeatureDataset:
    """Feature-engineered dataset ready for research or training.

    Attributes:
        dataset_id: Globally unique dataset identifier.
        parent_normalized_dataset_id: Reference to the source NormalizedDataset dataset_id.
        feature_spec_version: Version of the feature specification used.
        label_spec_version: Version of the label specification used.
        point_in_time_rule: Rule description ensuring no look-ahead bias
            (e.g., "available_time <= event_time + 0d").
        storage_ref: Reference to storage location.
        checksum: SHA-256 checksum of the feature data file(s).
        metadata_json: Free-form feature engineering metadata.
        created_at: ISO 8601 timestamp of record creation.
    """
    dataset_id: str
    parent_normalized_dataset_id: str
    feature_spec_version: str
    label_spec_version: str
    point_in_time_rule: str
    storage_ref: str
    checksum: str
    metadata_json: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "parent_normalized_dataset_id": self.parent_normalized_dataset_id,
            "feature_spec_version": self.feature_spec_version,
            "label_spec_version": self.label_spec_version,
            "point_in_time_rule": self.point_in_time_rule,
            "storage_ref": self.storage_ref,
            "checksum": self.checksum,
            "metadata_json": self.metadata_json,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeatureDataset":
        return cls(
            dataset_id=data["dataset_id"],
            parent_normalized_dataset_id=data["parent_normalized_dataset_id"],
            feature_spec_version=data["feature_spec_version"],
            label_spec_version=data["label_spec_version"],
            point_in_time_rule=data["point_in_time_rule"],
            storage_ref=data["storage_ref"],
            checksum=data["checksum"],
            metadata_json=data.get("metadata_json", {}),
            created_at=data.get("created_at") or _utc_now_iso(),
        )

    @staticmethod
    def validate(ds: "FeatureDataset") -> tuple[bool, list[str]]:
        errors = []
        if not ds.dataset_id:
            errors.append("Missing dataset_id")
        if not ds.parent_normalized_dataset_id:
            errors.append("Missing parent_normalized_dataset_id")
        if not ds.feature_spec_version:
            errors.append("Missing feature_spec_version")
        if not ds.label_spec_version:
            errors.append("Missing label_spec_version")
        if not ds.point_in_time_rule:
            errors.append("Missing point_in_time_rule")
        if not ds.storage_ref:
            errors.append("Missing storage_ref")
        if not ds.checksum:
            errors.append("Missing checksum")
        return len(errors) == 0, errors


@dataclass
class DatasetVersion:
    """Frozen lineage snapshot across raw, normalized, and feature datasets.

    This is the unit of replay — pinning a research run to exact data truth.

    Attributes:
        dataset_version_id: Globally unique version identifier.
        market_scope: List of market codes covered.
        instrument_scope: List of security_id or contract_id values covered.
        raw_dataset_refs: List of RawDataset dataset_id values.
        normalized_dataset_refs: List of NormalizedDataset dataset_id values.
        feature_dataset_refs: List of FeatureDataset dataset_id values.
        created_at: ISO 8601 timestamp of record creation.
        frozen_at: ISO 8601 timestamp when this version was frozen.
        metadata_json: Free-form version metadata.
    """
    dataset_version_id: str
    market_scope: list[str]
    instrument_scope: list[str]
    raw_dataset_refs: list[str]
    normalized_dataset_refs: list[str]
    feature_dataset_refs: list[str]
    frozen_at: Optional[str] = None
    metadata_json: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_version_id": self.dataset_version_id,
            "market_scope": self.market_scope,
            "instrument_scope": self.instrument_scope,
            "raw_dataset_refs": self.raw_dataset_refs,
            "normalized_dataset_refs": self.normalized_dataset_refs,
            "feature_dataset_refs": self.feature_dataset_refs,
            "frozen_at": self.frozen_at,
            "metadata_json": self.metadata_json,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatasetVersion":
        return cls(
            dataset_version_id=data["dataset_version_id"],
            market_scope=data["market_scope"],
            instrument_scope=data["instrument_scope"],
            raw_dataset_refs=data["raw_dataset_refs"],
            normalized_dataset_refs=data["normalized_dataset_refs"],
            feature_dataset_refs=data["feature_dataset_refs"],
            frozen_at=data.get("frozen_at"),
            metadata_json=data.get("metadata_json", {}),
            created_at=data.get("created_at") or _utc_now_iso(),
        )

    def freeze(self) -> None:
        """Mark this version as frozen for replay."""
        self.frozen_at = _utc_now_iso()

    @staticmethod
    def validate(dv: "DatasetVersion") -> tuple[bool, list[str]]:
        errors = []
        if not dv.dataset_version_id:
            errors.append("Missing dataset_version_id")
        if not dv.market_scope:
            errors.append("Missing market_scope")
        if not dv.instrument_scope:
            errors.append("Missing instrument_scope")
        if not dv.raw_dataset_refs:
            errors.append("Missing raw_dataset_refs")
        if not dv.normalized_dataset_refs:
            errors.append("Missing normalized_dataset_refs")
        if not dv.feature_dataset_refs:
            errors.append("Missing feature_dataset_refs")
        return len(errors) == 0, errors
