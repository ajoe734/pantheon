"""StrategySeedSourceRegistry: product-level registry for strategy idea/seed sources.

This registry is intentionally separate from DataSourceRegistry.  Sources that
produce strategy seeds (papers, repos, internal notes, alpha DBs) are governed
here.  The ``source_kind`` discriminator is always ``"strategy_seed_source"``;
no entry from this registry can be confused with a data supply source.

Design invariant: seeds produced from entries in this registry are
``research_only=True`` until promoted through the registry workflow.  This
registry does NOT hold an execution route.
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
        raise StrategySeedSourceRegistryError(f"{name} is required")
    return s


class StrategySeedSourceRegistryError(ValueError):
    """Raised when StrategySeedSourceRegistry invariants are violated."""


class StrategySeedSourceLifecycleState(str, Enum):
    CANDIDATE = "candidate"
    CONFIGURED_DISABLED = "configured_disabled"
    VALIDATED_DISABLED = "validated_disabled"
    CANARY_PASSED_DISABLED = "canary_passed_disabled"
    ENABLED = "enabled"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    DEGRADED_DISABLED = "degraded_disabled"
    RETIRED = "retired"


class StrategySeedSourceClass(str, Enum):
    PAPER = "paper"
    REPO = "repo"
    INTERNAL_NOTE = "internal_note"
    TELEMETRY = "telemetry"
    PERSONA_PROPOSAL = "persona_proposal"
    ALPHA_DB = "alpha_db"


class StrategySeedSourceScope(str, Enum):
    PUBLIC_METADATA = "public_metadata"
    PUBLIC_FULL_TEXT = "public_full_text"
    ALLOWLISTED_REPO = "allowlisted_repo"
    INTERNAL = "internal"
    RESTRICTED_VENDOR = "restricted_vendor"


_SOURCE_KIND = "strategy_seed_source"
_SCHEMA_VERSION = "strategy_seed_source_registry_entry.v1"

_ALLOWED_USE_VALUES = frozenset({
    "research_discovery",
    "strategy_seed_generation",
    "alpha_template_generation",
    "citation_reference",
    "internal_research_only",
})

_EXPECTED_OUTPUT_VALUES = frozenset({
    "knowledge_object",
    "evidence_bundle",
    "strategy_spec_seed",
    "alpha_template",
})


@dataclass(frozen=True)
class StrategySeedSourceEntry:
    """Immutable product-level registry entry for a strategy idea/seed source."""

    strategy_seed_source_id: str
    provider: str
    source_class: StrategySeedSourceClass | str
    source_scope: StrategySeedSourceScope | str
    license_scope: str
    allowed_use: Sequence[str]
    lifecycle_state: StrategySeedSourceLifecycleState | str = StrategySeedSourceLifecycleState.CANDIDATE
    entitlement_tags: Sequence[str] = field(default_factory=tuple)
    crawler_policy_ref: str | None = None
    expected_outputs: Sequence[str] = field(default_factory=tuple)
    connector_id: str | None = None
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    # Discriminator — always "strategy_seed_source"; immutable after construction.
    source_kind: str = field(default=_SOURCE_KIND, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_kind", _SOURCE_KIND)
        object.__setattr__(self, "strategy_seed_source_id", _require(self.strategy_seed_source_id, "strategy_seed_source_id"))
        object.__setattr__(self, "provider", _require(self.provider, "provider"))
        object.__setattr__(self, "license_scope", _require(self.license_scope, "license_scope"))

        try:
            sc_val = self.source_class.value if isinstance(self.source_class, Enum) else str(self.source_class)
            sc = StrategySeedSourceClass(sc_val)
        except ValueError:
            allowed = ", ".join(c.value for c in StrategySeedSourceClass)
            raise StrategySeedSourceRegistryError(f"source_class must be one of: {allowed}")
        object.__setattr__(self, "source_class", sc)

        try:
            ss_val = self.source_scope.value if isinstance(self.source_scope, Enum) else str(self.source_scope)
            ss = StrategySeedSourceScope(ss_val)
        except ValueError:
            allowed = ", ".join(s.value for s in StrategySeedSourceScope)
            raise StrategySeedSourceRegistryError(f"source_scope must be one of: {allowed}")
        object.__setattr__(self, "source_scope", ss)

        allowed_use = tuple(str(u).strip() for u in self.allowed_use if str(u).strip())
        if not allowed_use:
            raise StrategySeedSourceRegistryError("allowed_use must not be empty")
        for u in allowed_use:
            if u not in _ALLOWED_USE_VALUES:
                raise StrategySeedSourceRegistryError(
                    f"allowed_use value '{u}' is not valid; allowed: {sorted(_ALLOWED_USE_VALUES)}"
                )
        object.__setattr__(self, "allowed_use", allowed_use)

        expected_outputs = tuple(str(o).strip() for o in self.expected_outputs if str(o).strip())
        for o in expected_outputs:
            if o not in _EXPECTED_OUTPUT_VALUES:
                raise StrategySeedSourceRegistryError(
                    f"expected_outputs value '{o}' is not valid; allowed: {sorted(_EXPECTED_OUTPUT_VALUES)}"
                )
        object.__setattr__(self, "expected_outputs", expected_outputs)

        try:
            ls_val = self.lifecycle_state.value if isinstance(self.lifecycle_state, Enum) else str(self.lifecycle_state)
            ls = StrategySeedSourceLifecycleState(ls_val)
        except ValueError:
            allowed = ", ".join(s.value for s in StrategySeedSourceLifecycleState)
            raise StrategySeedSourceRegistryError(f"lifecycle_state must be one of: {allowed}")
        object.__setattr__(self, "lifecycle_state", ls)

        object.__setattr__(self, "entitlement_tags", tuple(
            str(t).strip() for t in self.entitlement_tags if str(t).strip()
        ))

        metadata = dict(self.metadata)
        # Enforce invariant: seeds from this registry are research_only until promoted.
        if "research_only" in metadata and metadata["research_only"] is not True:
            raise StrategySeedSourceRegistryError("metadata.research_only must be true or absent")
        if "execution_route" in metadata and metadata["execution_route"] != "none":
            raise StrategySeedSourceRegistryError("metadata.execution_route must be 'none' or absent")
        object.__setattr__(self, "metadata", metadata)

    @property
    def is_ingestable(self) -> bool:
        """Crawler may ingest only enabled or degraded sources."""
        return self.lifecycle_state in (
            StrategySeedSourceLifecycleState.ENABLED,
            StrategySeedSourceLifecycleState.DEGRADED,
        )

    def with_lifecycle(
        self,
        new_state: StrategySeedSourceLifecycleState | str,
        *,
        updated_at: str | None = None,
    ) -> "StrategySeedSourceEntry":
        """Return a new entry with an updated lifecycle state."""
        try:
            ls_val = new_state.value if isinstance(new_state, Enum) else str(new_state)
            ls = StrategySeedSourceLifecycleState(ls_val)
        except ValueError:
            allowed = ", ".join(s.value for s in StrategySeedSourceLifecycleState)
            raise StrategySeedSourceRegistryError(f"lifecycle_state must be one of: {allowed}")
        return StrategySeedSourceEntry(
            strategy_seed_source_id=self.strategy_seed_source_id,
            provider=self.provider,
            source_class=self.source_class.value,
            source_scope=self.source_scope.value,
            license_scope=self.license_scope,
            allowed_use=list(self.allowed_use),
            lifecycle_state=ls.value,
            entitlement_tags=list(self.entitlement_tags),
            crawler_policy_ref=self.crawler_policy_ref,
            expected_outputs=list(self.expected_outputs),
            connector_id=self.connector_id,
            created_at=self.created_at,
            updated_at=updated_at or _utc_now(),
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "strategy_seed_source_id": self.strategy_seed_source_id,
            "source_kind": self.source_kind,
            "provider": self.provider,
            "source_class": self.source_class.value,
            "source_scope": self.source_scope.value,
            "license_scope": self.license_scope,
            "entitlement_tags": list(self.entitlement_tags),
            "allowed_use": list(self.allowed_use),
            "crawler_policy_ref": self.crawler_policy_ref,
            "expected_outputs": list(self.expected_outputs),
            "lifecycle_state": self.lifecycle_state.value,
            "lineage": {
                "source_record_refs": [],
                "evidence_bundle_refs": [],
                "seed_refs": [],
                "citation_refs": [],
            },
            "connector_id": self.connector_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StrategySeedSourceEntry":
        if str(data.get("source_kind", "")) != _SOURCE_KIND:
            raise StrategySeedSourceRegistryError(
                f"source_kind must be '{_SOURCE_KIND}'; got '{data.get('source_kind')}'. "
                "Did you accidentally load a data_source entry?"
            )
        return cls(
            strategy_seed_source_id=str(data["strategy_seed_source_id"]),
            provider=str(data["provider"]),
            source_class=str(data["source_class"]),
            source_scope=str(data["source_scope"]),
            license_scope=str(data["license_scope"]),
            entitlement_tags=list(data.get("entitlement_tags") or []),
            allowed_use=list(data.get("allowed_use") or []),
            crawler_policy_ref=data.get("crawler_policy_ref"),
            expected_outputs=list(data.get("expected_outputs") or []),
            lifecycle_state=str(data.get("lifecycle_state", StrategySeedSourceLifecycleState.CANDIDATE.value)),
            connector_id=data.get("connector_id"),
            created_at=str(data.get("created_at") or _utc_now()),
            updated_at=str(data.get("updated_at") or _utc_now()),
            metadata=dict(data.get("metadata") or {}),
        )


class StrategySeedSourceRegistry:
    """Product-level registry for strategy idea/seed sources backed by a JSONL dev store."""

    def __init__(self, store: JsonlRegistryStore | None = None) -> None:
        self._index: dict[str, StrategySeedSourceEntry] = {}
        self._store = store
        if store is not None:
            self._load_from_store()

    def _load_from_store(self) -> None:
        for record in self._store.read_all():
            try:
                entry = StrategySeedSourceEntry.from_dict(record)
                self._index[entry.strategy_seed_source_id] = entry
            except (StrategySeedSourceRegistryError, KeyError, TypeError):
                pass

    def add(self, entry: StrategySeedSourceEntry) -> StrategySeedSourceEntry:
        if entry.strategy_seed_source_id in self._index:
            raise StrategySeedSourceRegistryError(
                f"Strategy seed source already registered: {entry.strategy_seed_source_id}"
            )
        self._index[entry.strategy_seed_source_id] = entry
        if self._store is not None:
            self._store.upsert(entry.to_dict())
        return entry

    def upsert(self, entry: StrategySeedSourceEntry) -> StrategySeedSourceEntry:
        self._index[entry.strategy_seed_source_id] = entry
        if self._store is not None:
            self._store.upsert(entry.to_dict())
        return entry

    def get(self, strategy_seed_source_id: str) -> StrategySeedSourceEntry | None:
        return self._index.get(strategy_seed_source_id)

    def list(self) -> list[StrategySeedSourceEntry]:
        return list(self._index.values())

    def set_lifecycle(
        self,
        strategy_seed_source_id: str,
        new_state: StrategySeedSourceLifecycleState | str,
    ) -> StrategySeedSourceEntry:
        entry = self.get(strategy_seed_source_id)
        if entry is None:
            raise StrategySeedSourceRegistryError(f"Unknown strategy seed source: {strategy_seed_source_id}")
        updated = entry.with_lifecycle(new_state)
        self._index[strategy_seed_source_id] = updated
        if self._store is not None:
            self._store.upsert(updated.to_dict())
        return updated

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "StrategySeedSourceRegistry":
        store = JsonlRegistryStore(path, id_field="strategy_seed_source_id")
        return cls(store=store)
