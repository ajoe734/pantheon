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
from services.source_ingestion.negative_memory import (
    is_blocking_negative_memory_match,
    negative_memory_record_from_seed,
)
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


def _assert_no_blocking_negative_memory(seed: StrategySpecSeed) -> None:
    if is_blocking_negative_memory_match(seed.negative_memory_match):
        match = dict(seed.negative_memory_match)
        memory_id = str(match.get("matched_memory_id") or "negative-memory-record")
        raise StrategySpecSeedStoreError(
            f"Seed has blocking negative_memory_match against {memory_id}: {match.get('reason')}"
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
        _assert_no_blocking_negative_memory(seed)
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

    def list_negative_memory_records(self) -> list[dict[str, Any]]:
        """Return rejected/retired/failed seed records as matcher inputs."""
        records: list[dict[str, Any]] = []
        for raw in self._store.read_all():
            record = negative_memory_record_from_seed(raw)
            if record is not None:
                records.append(record)
        return records

    def record_replication_submission(
        self,
        seed_id: str,
        *,
        replication_ref: str,
        experiment_task_id: str,
        strategy_id: str,
        strategy_spec_version: str,
        submitted_by: str,
        submitted_at: str,
        idempotency_key: str,
        research_task_ref: str | None = None,
    ) -> StrategySpecSeed:
        """Attach a research replication submission ref to seed lineage."""
        seed = self.get(seed_id)
        if seed is None:
            raise StrategySpecSeedStoreError(f"StrategySpecSeed not found: {seed_id}")

        lineage = dict(seed.lineage)
        existing_ref = str(lineage.get("replication_ref") or "").strip()
        existing_task_id = str(lineage.get("experiment_task_id") or "").strip()
        if existing_ref and existing_task_id:
            return seed

        submission = {
            "replication_ref": _require_text(replication_ref, "replication_ref"),
            "experiment_task_id": _require_text(experiment_task_id, "experiment_task_id"),
            "strategy_id": _require_text(strategy_id, "strategy_id"),
            "strategy_spec_version": _require_text(strategy_spec_version, "strategy_spec_version"),
            "submitted_by": _require_text(submitted_by, "submitted_by"),
            "submitted_at": _require_text(submitted_at, "submitted_at"),
            "idempotency_key": _require_text(idempotency_key, "idempotency_key"),
            "registry_write_performed": False,
            "execution_route": "none",
        }
        if research_task_ref:
            submission["research_task_ref"] = str(research_task_ref)

        lineage.update(submission)
        lineage["registry_write_performed"] = False
        lineage["execution_route"] = "none"
        submissions = list(lineage.get("replication_submissions") or [])
        already_recorded = any(
            item.get("replication_ref") == submission["replication_ref"]
            for item in submissions
            if isinstance(item, dict)
        )
        if not already_recorded:
            submissions.append(dict(submission))
        lineage["replication_submissions"] = submissions

        payload = seed.to_dict()
        payload["lineage"] = lineage
        updated = StrategySpecSeed.from_dict(payload)
        self.save(updated)
        return updated

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


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise StrategySpecSeedStoreError(f"{field_name} is required")
    return text
