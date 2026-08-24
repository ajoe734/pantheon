"""DataSourceRegistry: product-level registry for market/filing/news/macro data sources.

This registry is intentionally separate from StrategySeedSourceRegistry. A
vendor may appear in both registries only with distinct role-specific entries
(design invariant §14.2). The ``source_kind`` discriminator is always
``"data_source"``; no entry from this registry can be mistaken for a strategy
seed source.

SD-SRCM-01 provides additive v2 support (schema: data_source_registry_entry.v2)
while preserving complete backward compatibility with v1 entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .jsonl_store import JsonlRegistryStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require(value: Any, name: str) -> str:
    s = str(value or "").strip()
    if not s:
        raise DataSourceRegistryError(f"{name} is required")
    return s


class DataSourceRegistryError(ValueError):
    """Raised when DataSourceRegistry invariants are violated."""


class DataSourceLifecycleState(str, Enum):
    CANDIDATE = "candidate"
    CONFIGURED_DISABLED = "configured_disabled"
    VALIDATED_DISABLED = "validated_disabled"
    CANARY_PASSED_DISABLED = "canary_passed_disabled"
    ENABLED = "enabled"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    DEGRADED_DISABLED = "degraded_disabled"
    RETIRED = "retired"


class DataSourceClass(str, Enum):
    MARKET_DAILY = "market_daily"
    INTRADAY_QUOTE = "intraday_quote"
    FILING_EVENT = "filing_event"
    FINANCIAL_FUNDAMENTAL = "financial_fundamental"
    TAIWAN_CHIP = "taiwan_chip"
    NEWS = "news"
    SOCIAL = "social"
    MACRO = "macro"
    SHORT_INTEREST = "short_interest"
    VENDOR_BACKFILL = "vendor_backfill"
    BROKER_READBACK = "broker_readback"


_SOURCE_KIND = "data_source"
_SCHEMA_VERSION_V1 = "data_source_registry_entry.v1"
_SCHEMA_VERSION_V2 = "data_source_registry_entry.v2"

_ALLOWED_USE_VALUES = frozenset({
    "research_data",
    "backtest_data",
    "feature_generation",
    "monitoring",
    "paper_runtime",
    "canary_runtime",
    "live_runtime",
    "execution_sync",
    "audit_evidence",
    "citation_reference",
    "internal_research_only",
})


@dataclass(frozen=True)
class DataSourceEntry:
    """Immutable product-level registry entry for a data supply source (v1)."""

    data_source_id: str
    provider: str
    source_class: DataSourceClass | str
    datasets: Sequence[Mapping[str, Any]]
    license_scope: str
    allowed_use: Sequence[str]
    update_frequency: str
    lifecycle_state: DataSourceLifecycleState | str = DataSourceLifecycleState.CANDIDATE
    entitlement_tags: Sequence[str] = field(default_factory=tuple)
    universe_policy_ref: str | None = None
    connector_id: str | None = None
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    # Discriminator — always "data_source"; immutable after construction.
    source_kind: str = field(default=_SOURCE_KIND, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_kind", _SOURCE_KIND)
        object.__setattr__(self, "data_source_id", _require(self.data_source_id, "data_source_id"))
        object.__setattr__(self, "provider", _require(self.provider, "provider"))
        object.__setattr__(self, "license_scope", _require(self.license_scope, "license_scope"))
        object.__setattr__(self, "update_frequency", _require(self.update_frequency, "update_frequency"))

        try:
            sc_val = self.source_class.value if isinstance(self.source_class, Enum) else str(self.source_class)
            sc = DataSourceClass(sc_val)
        except ValueError:
            allowed = ", ".join(c.value for c in DataSourceClass)
            raise DataSourceRegistryError(f"source_class must be one of: {allowed}")
        object.__setattr__(self, "source_class", sc)

        datasets = list(self.datasets)
        if not datasets:
            raise DataSourceRegistryError("datasets must contain at least one entry")
        for ds in datasets:
            if not str(ds.get("dataset_id", "")).strip():
                raise DataSourceRegistryError("each dataset entry must have a non-empty dataset_id")
            if not str(ds.get("dataset_class", "")).strip():
                raise DataSourceRegistryError("each dataset entry must have a non-empty dataset_class")
        object.__setattr__(self, "datasets", tuple(dict(ds) for ds in datasets))

        allowed_use = tuple(str(u).strip() for u in self.allowed_use if str(u).strip())
        if not allowed_use:
            raise DataSourceRegistryError("allowed_use must not be empty")
        for u in allowed_use:
            if u not in _ALLOWED_USE_VALUES:
                raise DataSourceRegistryError(
                    f"allowed_use value '{u}' is not valid; allowed: {sorted(_ALLOWED_USE_VALUES)}"
                )
        object.__setattr__(self, "allowed_use", allowed_use)

        try:
            ls_val = self.lifecycle_state.value if isinstance(self.lifecycle_state, Enum) else str(self.lifecycle_state)
            ls = DataSourceLifecycleState(ls_val)
        except ValueError:
            allowed = ", ".join(s.value for s in DataSourceLifecycleState)
            raise DataSourceRegistryError(f"lifecycle_state must be one of: {allowed}")
        object.__setattr__(self, "lifecycle_state", ls)

        object.__setattr__(self, "entitlement_tags", tuple(
            str(t).strip() for t in self.entitlement_tags if str(t).strip()
        ))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def is_ingestable(self) -> bool:
        """Scheduler may ingest only enabled or degraded sources."""
        return self.lifecycle_state in (DataSourceLifecycleState.ENABLED, DataSourceLifecycleState.DEGRADED)

    def with_lifecycle(self, new_state: DataSourceLifecycleState | str, *, updated_at: str | None = None) -> "DataSourceEntry":
        """Return a new entry with an updated lifecycle state."""
        try:
            ls_val = new_state.value if isinstance(new_state, Enum) else str(new_state)
            ls = DataSourceLifecycleState(ls_val)
        except ValueError:
            allowed = ", ".join(s.value for s in DataSourceLifecycleState)
            raise DataSourceRegistryError(f"lifecycle_state must be one of: {allowed}")
        return DataSourceEntry(
            data_source_id=self.data_source_id,
            provider=self.provider,
            source_class=self.source_class.value,
            datasets=list(self.datasets),
            license_scope=self.license_scope,
            allowed_use=list(self.allowed_use),
            update_frequency=self.update_frequency,
            lifecycle_state=ls.value,
            entitlement_tags=list(self.entitlement_tags),
            universe_policy_ref=self.universe_policy_ref,
            connector_id=self.connector_id,
            created_at=self.created_at,
            updated_at=updated_at or _utc_now(),
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION_V1,
            "data_source_id": self.data_source_id,
            "source_kind": self.source_kind,
            "provider": self.provider,
            "source_class": self.source_class.value,
            "datasets": [dict(ds) for ds in self.datasets],
            "license_scope": self.license_scope,
            "entitlement_tags": list(self.entitlement_tags),
            "allowed_use": list(self.allowed_use),
            "update_frequency": self.update_frequency,
            "universe_policy_ref": self.universe_policy_ref,
            "lifecycle_state": self.lifecycle_state.value,
            "lineage": {
                "source_record_refs": [],
                "evidence_refs": [],
                "dependent_strategy_refs": [],
            },
            "connector_id": self.connector_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    def to_v2(
        self,
        *,
        definition_id: str | None = None,
        markets: Sequence[str] = ("TW",),
        retention_policy_ref: str | None = None,
        deletion_policy_ref: str | None = None,
        freshness_sla_seconds: int = 86400,
        sensitivity: str = "public",
        revision: int = 1,
        created_by: str = "system",
        updated_by: str = "system",
    ) -> "DataSourceEntryV2":
        """Convert this v1 entry into an additive v2 entry."""
        def_id = definition_id or self.connector_id or self.data_source_id
        return DataSourceEntryV2(
            data_source_id=self.data_source_id,
            definition_id=def_id,
            connector_id=self.connector_id,
            provider=self.provider,
            source_class=self.source_class,
            datasets=list(self.datasets),
            markets=list(markets),
            license_scope=self.license_scope,
            entitlement_tags=list(self.entitlement_tags),
            allowed_use=list(self.allowed_use),
            retention_policy_ref=retention_policy_ref or f"source-retention://{self.provider.lower()}",
            deletion_policy_ref=deletion_policy_ref or f"source-deletion://{self.provider.lower()}",
            freshness_sla_seconds=freshness_sla_seconds,
            sensitivity=sensitivity,
            lifecycle_state=self.lifecycle_state,
            revision=revision,
            created_by=created_by,
            created_at=self.created_at,
            updated_by=updated_by,
            updated_at=self.updated_at,
            lineage={"source_record_refs": [], "evidence_refs": [], "dependent_strategy_refs": [], "consumer_refs": []},
            update_frequency=self.update_frequency,
            universe_policy_ref=self.universe_policy_ref,
            metadata=dict(self.metadata),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DataSourceEntry":
        if str(data.get("source_kind", "")) != _SOURCE_KIND:
            raise DataSourceRegistryError(
                f"source_kind must be '{_SOURCE_KIND}'; got '{data.get('source_kind')}'. "
                "Did you accidentally load a strategy_seed_source entry?"
            )
        return cls(
            data_source_id=str(data["data_source_id"]),
            provider=str(data["provider"]),
            source_class=str(data["source_class"]),
            datasets=list(data.get("datasets") or []),
            license_scope=str(data["license_scope"]),
            entitlement_tags=list(data.get("entitlement_tags") or []),
            allowed_use=list(data.get("allowed_use") or []),
            update_frequency=str(data.get("update_frequency") or "daily"),
            lifecycle_state=str(data.get("lifecycle_state", DataSourceLifecycleState.CANDIDATE.value)),
            universe_policy_ref=data.get("universe_policy_ref"),
            connector_id=data.get("connector_id"),
            created_at=str(data.get("created_at") or _utc_now()),
            updated_at=str(data.get("updated_at") or _utc_now()),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class DataSourceEntryV2:
    """Immutable product-level registry entry for a data supply source instance (v2 contract)."""

    data_source_id: str
    definition_id: str
    connector_id: str | None
    provider: str
    source_class: DataSourceClass | str
    datasets: Sequence[Mapping[str, Any]]
    markets: Sequence[str]
    license_scope: str
    allowed_use: Sequence[str]
    retention_policy_ref: str
    deletion_policy_ref: str
    freshness_sla_seconds: int = 86400
    sensitivity: str = "public"
    lifecycle_state: DataSourceLifecycleState | str = DataSourceLifecycleState.CONFIGURED_DISABLED
    revision: int = 1
    created_by: str = "system"
    created_at: str = field(default_factory=_utc_now)
    updated_by: str = "system"
    updated_at: str = field(default_factory=_utc_now)
    provider_account_ref: str | None = None
    entitlement_tags: Sequence[str] = field(default_factory=tuple)
    universe_policy_ref: str | None = None
    update_frequency: str = ""
    lineage: Mapping[str, Any] = field(default_factory=lambda: {
        "source_record_refs": [],
        "evidence_refs": [],
        "dependent_strategy_refs": [],
        "consumer_refs": [],
    })
    metadata: Mapping[str, Any] = field(default_factory=dict)

    # Discriminator — always "data_source"; immutable after construction.
    source_kind: str = field(default=_SOURCE_KIND, init=False)
    schema_version: str = field(default=_SCHEMA_VERSION_V2, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_kind", _SOURCE_KIND)
        object.__setattr__(self, "schema_version", _SCHEMA_VERSION_V2)
        object.__setattr__(self, "data_source_id", _require(self.data_source_id, "data_source_id"))
        object.__setattr__(self, "definition_id", _require(self.definition_id, "definition_id"))
        object.__setattr__(self, "provider", _require(self.provider, "provider"))
        object.__setattr__(self, "license_scope", _require(self.license_scope, "license_scope"))
        object.__setattr__(self, "retention_policy_ref", _require(self.retention_policy_ref, "retention_policy_ref"))
        object.__setattr__(self, "deletion_policy_ref", _require(self.deletion_policy_ref, "deletion_policy_ref"))
        object.__setattr__(self, "created_by", _require(self.created_by, "created_by"))
        object.__setattr__(self, "updated_by", _require(self.updated_by, "updated_by"))

        if self.revision < 1:
            raise DataSourceRegistryError("revision must be >= 1")
        if self.freshness_sla_seconds < 0:
            raise DataSourceRegistryError("freshness_sla_seconds must be >= 0")

        if self.sensitivity not in ("public", "internal", "confidential", "restricted"):
            raise DataSourceRegistryError("sensitivity must be one of: public, internal, confidential, restricted")

        try:
            sc_val = self.source_class.value if isinstance(self.source_class, Enum) else str(self.source_class)
            sc = DataSourceClass(sc_val)
        except ValueError:
            allowed = ", ".join(c.value for c in DataSourceClass)
            raise DataSourceRegistryError(f"source_class must be one of: {allowed}")
        object.__setattr__(self, "source_class", sc)

        datasets = list(self.datasets)
        if not datasets:
            raise DataSourceRegistryError("datasets must contain at least one entry")
        for ds in datasets:
            if not str(ds.get("dataset_id", "")).strip():
                raise DataSourceRegistryError("each dataset entry must have a non-empty dataset_id")
            if not str(ds.get("dataset_class", "")).strip():
                raise DataSourceRegistryError("each dataset entry must have a non-empty dataset_class")
        object.__setattr__(self, "datasets", tuple(dict(ds) for ds in datasets))

        allowed_use = tuple(str(u).strip() for u in self.allowed_use if str(u).strip())
        if not allowed_use:
            raise DataSourceRegistryError("allowed_use must not be empty")
        for u in allowed_use:
            if u not in _ALLOWED_USE_VALUES:
                raise DataSourceRegistryError(
                    f"allowed_use value '{u}' is not valid; allowed: {sorted(_ALLOWED_USE_VALUES)}"
                )
        object.__setattr__(self, "allowed_use", allowed_use)

        try:
            ls_val = self.lifecycle_state.value if isinstance(self.lifecycle_state, Enum) else str(self.lifecycle_state)
            ls = DataSourceLifecycleState(ls_val)
        except ValueError:
            allowed = ", ".join(s.value for s in DataSourceLifecycleState)
            raise DataSourceRegistryError(f"lifecycle_state must be one of: {allowed}")
        object.__setattr__(self, "lifecycle_state", ls)

        object.__setattr__(self, "markets", tuple(str(m).strip() for m in self.markets if str(m).strip()))
        object.__setattr__(self, "entitlement_tags", tuple(str(t).strip() for t in self.entitlement_tags if str(t).strip()))
        object.__setattr__(self, "lineage", dict(self.lineage))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def is_ingestable(self) -> bool:
        """Scheduler may ingest only enabled or degraded sources."""
        return self.lifecycle_state in (DataSourceLifecycleState.ENABLED, DataSourceLifecycleState.DEGRADED)

    def with_lifecycle(
        self,
        new_state: DataSourceLifecycleState | str,
        *,
        updated_by: str | None = None,
        updated_at: str | None = None,
        increment_revision: bool = False,
    ) -> "DataSourceEntryV2":
        """Return a new v2 entry with an updated lifecycle state."""
        try:
            ls_val = new_state.value if isinstance(new_state, Enum) else str(new_state)
            ls = DataSourceLifecycleState(ls_val)
        except ValueError:
            allowed = ", ".join(s.value for s in DataSourceLifecycleState)
            raise DataSourceRegistryError(f"lifecycle_state must be one of: {allowed}")
        return DataSourceEntryV2(
            data_source_id=self.data_source_id,
            definition_id=self.definition_id,
            connector_id=self.connector_id,
            provider=self.provider,
            provider_account_ref=self.provider_account_ref,
            source_class=self.source_class.value,
            datasets=list(self.datasets),
            markets=list(self.markets),
            license_scope=self.license_scope,
            entitlement_tags=list(self.entitlement_tags),
            allowed_use=list(self.allowed_use),
            retention_policy_ref=self.retention_policy_ref,
            deletion_policy_ref=self.deletion_policy_ref,
            freshness_sla_seconds=self.freshness_sla_seconds,
            sensitivity=self.sensitivity,
            lifecycle_state=ls.value,
            revision=self.revision + 1 if increment_revision else self.revision,
            created_by=self.created_by,
            created_at=self.created_at,
            updated_by=updated_by or self.updated_by,
            updated_at=updated_at or _utc_now(),
            lineage=dict(self.lineage),
            update_frequency=self.update_frequency,
            universe_policy_ref=self.universe_policy_ref,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "data_source_id": self.data_source_id,
            "source_kind": self.source_kind,
            "definition_id": self.definition_id,
            "connector_id": self.connector_id,
            "provider": self.provider,
            "provider_account_ref": self.provider_account_ref,
            "source_class": self.source_class.value,
            "datasets": [dict(ds) for ds in self.datasets],
            "markets": list(self.markets),
            "license_scope": self.license_scope,
            "entitlement_tags": list(self.entitlement_tags),
            "allowed_use": list(self.allowed_use),
            "retention_policy_ref": self.retention_policy_ref,
            "deletion_policy_ref": self.deletion_policy_ref,
            "freshness_sla_seconds": self.freshness_sla_seconds,
            "sensitivity": self.sensitivity,
            "lifecycle_state": self.lifecycle_state.value,
            "revision": self.revision,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
            "lineage": dict(self.lineage),
            "update_frequency": self.update_frequency,
            "universe_policy_ref": self.universe_policy_ref,
            "metadata": dict(self.metadata),
        }

    def to_v1(self) -> DataSourceEntry:
        """Convert this v2 entry back to a v1 entry."""
        return DataSourceEntry(
            data_source_id=self.data_source_id,
            provider=self.provider,
            source_class=self.source_class.value,
            datasets=list(self.datasets),
            license_scope=self.license_scope,
            allowed_use=list(self.allowed_use),
            update_frequency=self.update_frequency or "daily",
            lifecycle_state=self.lifecycle_state.value,
            entitlement_tags=list(self.entitlement_tags),
            universe_policy_ref=self.universe_policy_ref,
            connector_id=self.connector_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            metadata=dict(self.metadata),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DataSourceEntryV2":
        if str(data.get("source_kind", "")) != _SOURCE_KIND:
            raise DataSourceRegistryError(
                f"source_kind must be '{_SOURCE_KIND}'; got '{data.get('source_kind')}'. "
                "Did you accidentally load a strategy_seed_source entry?"
            )
        def_id = str(data.get("definition_id") or data.get("connector_id") or data.get("data_source_id"))
        return cls(
            data_source_id=str(data["data_source_id"]),
            definition_id=def_id,
            connector_id=data.get("connector_id"),
            provider=str(data["provider"]),
            provider_account_ref=data.get("provider_account_ref"),
            source_class=str(data["source_class"]),
            datasets=list(data.get("datasets") or []),
            markets=list(data.get("markets") or ["TW"]),
            license_scope=str(data["license_scope"]),
            entitlement_tags=list(data.get("entitlement_tags") or []),
            allowed_use=list(data.get("allowed_use") or []),
            retention_policy_ref=str(data.get("retention_policy_ref") or f"source-retention://{str(data.get('provider')).lower()}"),
            deletion_policy_ref=str(data.get("deletion_policy_ref") or f"source-deletion://{str(data.get('provider')).lower()}"),
            freshness_sla_seconds=int(data.get("freshness_sla_seconds", 86400)),
            sensitivity=str(data.get("sensitivity", "public")),
            lifecycle_state=str(data.get("lifecycle_state", DataSourceLifecycleState.CONFIGURED_DISABLED.value)),
            revision=int(data.get("revision", 1)),
            created_by=str(data.get("created_by", "system")),
            created_at=str(data.get("created_at") or _utc_now()),
            updated_by=str(data.get("updated_by", "system")),
            updated_at=str(data.get("updated_at") or _utc_now()),
            lineage=dict(data.get("lineage") or {
                "source_record_refs": [],
                "evidence_refs": [],
                "dependent_strategy_refs": [],
                "consumer_refs": [],
            }),
            update_frequency=str(data.get("update_frequency", "")),
            universe_policy_ref=data.get("universe_policy_ref"),
            metadata=dict(data.get("metadata") or {}),
        )


class DataSourceRegistry:
    """Product-level registry for data supply sources backed by a JSONL dev store."""

    def __init__(self, store: JsonlRegistryStore | None = None) -> None:
        self._index: dict[str, DataSourceEntry | DataSourceEntryV2] = {}
        self._store = store
        if store is not None:
            self._load_from_store()

    def _load_from_store(self) -> None:
        for record in self._store.read_all():
            try:
                schema_ver = str(record.get("schema_version", ""))
                if schema_ver == _SCHEMA_VERSION_V2:
                    entry: DataSourceEntry | DataSourceEntryV2 = DataSourceEntryV2.from_dict(record)
                else:
                    entry = DataSourceEntry.from_dict(record)
                self._index[entry.data_source_id] = entry
            except (DataSourceRegistryError, KeyError, TypeError):
                pass

    def add(self, entry: DataSourceEntry | DataSourceEntryV2) -> DataSourceEntry | DataSourceEntryV2:
        if entry.data_source_id in self._index:
            raise DataSourceRegistryError(f"Data source already registered: {entry.data_source_id}")
        self._index[entry.data_source_id] = entry
        if self._store is not None:
            self._store.upsert(entry.to_dict())
        return entry

    def upsert(self, entry: DataSourceEntry | DataSourceEntryV2) -> DataSourceEntry | DataSourceEntryV2:
        self._index[entry.data_source_id] = entry
        if self._store is not None:
            self._store.upsert(entry.to_dict())
        return entry

    def get(self, data_source_id: str) -> DataSourceEntry | DataSourceEntryV2 | None:
        return self._index.get(data_source_id)

    def get_v2(self, data_source_id: str) -> DataSourceEntryV2 | None:
        entry = self.get(data_source_id)
        if entry is None:
            return None
        if isinstance(entry, DataSourceEntryV2):
            return entry
        return entry.to_v2()

    def list(self) -> list[DataSourceEntry | DataSourceEntryV2]:
        return list(self._index.values())

    def list_v2(self) -> list[DataSourceEntryV2]:
        return [entry if isinstance(entry, DataSourceEntryV2) else entry.to_v2() for entry in self._index.values()]

    def set_lifecycle(
        self,
        data_source_id: str,
        new_state: DataSourceLifecycleState | str,
        *,
        updated_by: str | None = None,
    ) -> DataSourceEntry | DataSourceEntryV2:
        entry = self.get(data_source_id)
        if entry is None:
            raise DataSourceRegistryError(f"Unknown data source: {data_source_id}")
        if isinstance(entry, DataSourceEntryV2):
            updated = entry.with_lifecycle(new_state, updated_by=updated_by, increment_revision=True)
        else:
            updated = entry.with_lifecycle(new_state)
        self._index[data_source_id] = updated
        if self._store is not None:
            self._store.upsert(updated.to_dict())
        return updated

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "DataSourceRegistry":
        store = JsonlRegistryStore(path, id_field="data_source_id")
        return cls(store=store)
