"""Persistent store for StrategySpecSeed objects.

JSONL dev implementation backed by JsonlRegistryStore. Production uses Postgres.
The store is the write boundary: only the materializer and seed review flows
write seeds. All other services read through the owner API.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

from services.source_ingestion.registry.jsonl_store import JsonlRegistryStore
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedStatus,
)

_FORBIDDEN_EXECUTION_HINTS = frozenset(
    [
        "broker",
        "live",
        "order_router",
        "execution",
        "runtime_direct",
        "lean_direct",
        "live_trading",
        "order_routing",
    ]
)


def _assert_no_direct_execution_route(seed: StrategySpecSeed) -> None:
    """Raise if seed metadata requests a direct execution route."""
    meta = seed.metadata
    if meta.get("execution_route") not in (None, "none", ""):
        route = meta["execution_route"]
        if str(route).lower() not in ("none", "research"):
            raise StrategySpecSeedStoreError(
                f"Seed metadata requests forbidden execution route: {route!r}"
            )
    backend = str(seed.backend_hint or "").lower()
    for forbidden in _FORBIDDEN_EXECUTION_HINTS:
        if forbidden in backend:
            raise StrategySpecSeedStoreError(
                f"Seed backend_hint contains forbidden execution keyword: {seed.backend_hint!r}"
            )
    lineage = seed.lineage
    if lineage.get("execution_route") not in (None, "none", ""):
        route = lineage["execution_route"]
        if str(route).lower() not in ("none",):
            raise StrategySpecSeedStoreError(
                f"Seed lineage requests forbidden execution route: {route!r}"
            )


class StrategySpecSeedStoreError(ValueError):
    """Raised when a store invariant is violated."""


class StrategySpecSeedStore:
    """JSONL-backed store for StrategySpecSeed persistence.

    One record per seed_id (upsert semantics).  Idempotency: the builder
    derives seed_id from a stable hash of evidence_bundle_id + source_ids +
    hypothesis, so re-materializing the same evidence produces the same key
    and upserts rather than duplicates.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        resolved = (
            Path(path)
            if path
            else Path(
                os.environ.get(
                    "STRATEGY_SEED_STORE_PATH",
                    "data/strategy_seed_store/seeds.jsonl",
                )
            )
        )
        self._store = JsonlRegistryStore(resolved, id_field="seed_id")

    @property
    def path(self) -> Path:
        return self._store.path

    def save(self, seed: StrategySpecSeed) -> None:
        """Persist seed (upsert).  Validates governance invariants before writing."""
        _assert_no_direct_execution_route(seed)
        record = seed.to_dict()
        # Promote license_scope and allowed_use to top-level queryable fields.
        record.setdefault("license_scope", seed.metadata.get("source_license_scope", ""))
        record.setdefault("allowed_use", list(seed.metadata.get("access_scope") or []))
        self._store.upsert(record)

    def get(self, seed_id: str) -> StrategySpecSeed | None:
        record = self._store.read_by_id(seed_id)
        if record is None:
            return None
        return StrategySpecSeed.from_dict(record)

    def list_all(self) -> list[StrategySpecSeed]:
        return [StrategySpecSeed.from_dict(r) for r in self._store.read_all()]

    def list_by_status(self, status: str | StrategySpecSeedStatus) -> list[StrategySpecSeed]:
        target = status.value if isinstance(status, StrategySpecSeedStatus) else str(status)
        return [
            StrategySpecSeed.from_dict(r)
            for r in self._store.read_all()
            if r.get("status") == target
        ]

    def list_by_bundle(self, evidence_bundle_id: str) -> list[StrategySpecSeed]:
        return [
            StrategySpecSeed.from_dict(r)
            for r in self._store.read_all()
            if r.get("evidence_bundle_id") == evidence_bundle_id
        ]

    def get_by_bundle_idempotent(
        self,
        evidence_bundle_id: str,
        source_ids: Sequence[str],
    ) -> StrategySpecSeed | None:
        """Return an existing seed for this bundle+sources if one exists.

        Used by the materializer to detect idempotent re-runs without
        re-building the full seed payload.
        """
        source_set = set(source_ids)
        for r in self._store.read_all():
            if r.get("evidence_bundle_id") != evidence_bundle_id:
                continue
            stored_sources = set(r.get("source_ids") or [])
            if stored_sources == source_set:
                return StrategySpecSeed.from_dict(r)
        return None

    def count(self) -> int:
        return len(self._store.read_all())
