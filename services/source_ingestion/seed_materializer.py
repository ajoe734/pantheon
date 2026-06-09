"""Seed materialization service: EvidenceBundle -> StrategySpecSeed -> Store.

The materializer orchestrates the full first-segment pipeline:
  EvidenceBundle -> StrategySpecSeedBuilder -> StrategySpecSeedStore

It enforces idempotency, rejection guards, and the no-direct-execution invariant.
It does NOT write registry state, launch experiments, or create execution routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedBuilder,
    StrategySpecSeedError,
)
from services.source_ingestion.strategy_seed_store import (
    StrategySpecSeedStore,
    StrategySpecSeedStoreError,
)


class MaterializationMode(str, Enum):
    CREATE_IF_ABSENT = "create_if_absent"
    REFRESH = "refresh"
    FORCE_NEW_VERSION = "force_new_version"


class SeedMaterializationError(ValueError):
    """Raised when materialization cannot complete safely."""


@dataclass(frozen=True)
class SeedMaterializationRequest:
    evidence_bundle_id: str
    source_ids: tuple[str, ...]
    mode: MaterializationMode
    requested_by: str
    idempotency_key: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SeedMaterializationRequest":
        mode_raw = data.get("mode", MaterializationMode.CREATE_IF_ABSENT.value)
        try:
            mode = MaterializationMode(mode_raw)
        except ValueError as exc:
            allowed = ", ".join(m.value for m in MaterializationMode)
            raise SeedMaterializationError(f"mode must be one of: {allowed}") from exc
        return cls(
            evidence_bundle_id=_require_text(data.get("evidence_bundle_id"), "evidence_bundle_id"),
            source_ids=tuple(_require_text(s, "source_ids item") for s in (data.get("source_ids") or [])),
            mode=mode,
            requested_by=_require_text(data.get("requested_by"), "requested_by"),
            idempotency_key=_require_text(data.get("idempotency_key"), "idempotency_key"),
        )


@dataclass(frozen=True)
class SeedMaterializationResult:
    seed: StrategySpecSeed
    was_created: bool
    idempotency_key: str
    mode: MaterializationMode
    materialized_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed.to_dict(),
            "was_created": self.was_created,
            "idempotency_key": self.idempotency_key,
            "mode": self.mode.value,
            "materialized_at": self.materialized_at,
        }


class SeedMaterializationService:
    """Orchestrate EvidenceBundle -> StrategySpecSeed -> StrategySpecSeedStore.

    Idempotency: for CREATE_IF_ABSENT, a seed already present in the store for
    the same evidence_bundle_id + source_ids is returned without re-building.
    For REFRESH the seed is rebuilt from evidence and upserted.
    For FORCE_NEW_VERSION a fresh seed_id suffix is generated (not yet
    implemented; falls back to REFRESH to avoid data duplication).
    """

    def __init__(
        self,
        store: StrategySpecSeedStore | None = None,
        *,
        created_by: str = "Claude",
    ) -> None:
        self._store = store or StrategySpecSeedStore()
        self._builder = StrategySpecSeedBuilder()
        self._created_by = created_by

    def materialize(
        self,
        evidence_bundle: EvidenceBundle | Mapping[str, Any],
        *,
        source_records: Sequence[SourceRecord | Mapping[str, Any]] = (),
        evidence_items: Sequence[EvidenceItem | Mapping[str, Any]] = (),
        mode: MaterializationMode | str = MaterializationMode.CREATE_IF_ABSENT,
        requested_by: str | None = None,
        idempotency_key: str | None = None,
        created_at: datetime | str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SeedMaterializationResult:
        """Materialize one EvidenceBundle into one idempotent StrategySpecSeed."""

        if isinstance(mode, str):
            try:
                mode = MaterializationMode(mode)
            except ValueError as exc:
                allowed = ", ".join(m.value for m in MaterializationMode)
                raise SeedMaterializationError(f"mode must be one of: {allowed}") from exc

        bundle = evidence_bundle if isinstance(evidence_bundle, EvidenceBundle) else EvidenceBundle.from_dict(evidence_bundle)
        actor = requested_by or self._created_by
        ikey = idempotency_key or f"{bundle.evidence_bundle_id}:{mode.value}:{actor}"

        if mode == MaterializationMode.CREATE_IF_ABSENT:
            existing = self._store.get_by_bundle_idempotent(
                bundle.evidence_bundle_id,
                bundle.source_ids,
            )
            if existing is not None:
                return SeedMaterializationResult(
                    seed=existing,
                    was_created=False,
                    idempotency_key=ikey,
                    mode=mode,
                    materialized_at=_utc_now_iso(),
                )

        try:
            seed = self._builder.build_seed(
                bundle,
                source_records=source_records,
                evidence_items=evidence_items,
                created_by=actor,
                created_at=created_at,
                metadata={
                    "materialization_mode": mode.value,
                    "idempotency_key": ikey,
                    "requested_by": actor,
                    **(dict(metadata) if metadata else {}),
                },
            )
        except StrategySpecSeedError as exc:
            raise SeedMaterializationError(str(exc)) from exc

        try:
            self._store.save(seed)
        except StrategySpecSeedStoreError as exc:
            raise SeedMaterializationError(str(exc)) from exc

        return SeedMaterializationResult(
            seed=seed,
            was_created=True,
            idempotency_key=ikey,
            mode=mode,
            materialized_at=_utc_now_iso(),
        )


def materialize_seed_from_bundle(
    evidence_bundle: EvidenceBundle | Mapping[str, Any],
    *,
    store: StrategySpecSeedStore | None = None,
    source_records: Sequence[SourceRecord | Mapping[str, Any]] = (),
    evidence_items: Sequence[EvidenceItem | Mapping[str, Any]] = (),
    mode: MaterializationMode | str = MaterializationMode.CREATE_IF_ABSENT,
    requested_by: str = "Claude",
    idempotency_key: str | None = None,
    created_at: datetime | str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> SeedMaterializationResult:
    """Convenience wrapper for one-shot EvidenceBundle -> StrategySpecSeed materialization."""
    return SeedMaterializationService(store=store, created_by=requested_by).materialize(
        evidence_bundle,
        source_records=source_records,
        evidence_items=evidence_items,
        mode=mode,
        requested_by=requested_by,
        idempotency_key=idempotency_key,
        created_at=created_at,
        metadata=metadata,
    )


def _require_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise SeedMaterializationError(f"{field_name} is required")
    return normalized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "MaterializationMode",
    "SeedMaterializationError",
    "SeedMaterializationRequest",
    "SeedMaterializationResult",
    "SeedMaterializationService",
    "materialize_seed_from_bundle",
]
