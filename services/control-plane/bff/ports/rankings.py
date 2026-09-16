"""Typed write port for Rankings generation-3 snapshot records.

The BFF never holds durable ranking-snapshot state locally. Every write
crosses to the canonical ``services.rankings.store.RankingWriteStore`` write
owner, matching ``DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md``. This is
the sole entrypoint that used to be a local-overlay mutation method on
``ReadSurfacePorts``.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict

from services.rankings.store import (
    RankingConflictError,
    RankingSnapshotRecord,
    RankingWriteStore,
    build_rankings_store,
)


class RankingSnapshotWriteOwnerPort:
    """The sole BFF-side entrypoint for durable ranking-snapshot writes."""

    def __init__(self, store: RankingWriteStore) -> None:
        self._store = store

    def put_ranking_snapshot(self, record: Dict[str, Any]) -> Dict[str, Any]:
        snapshot = RankingSnapshotRecord(
            ranking_snapshot_id=str(record["ranking_snapshot_id"]),
            surface=str(record.get("surface", "")),
            period=str(record.get("period", "")),
            formula_version=str(record.get("formula_version", "")),
            content_digest=str(record.get("content_digest", "")),
            items=record.get("items") or [],
            evidence_assertion_digests=record.get("evidence_assertion_digests") or {},
            created_at=str(record.get("created_at", "")),
        )
        try:
            created = self._store.create_ranking_snapshot(snapshot)
        except RankingConflictError:
            # Ranking GETs recompute content-addressed snapshots with today's
            # request time. An existing snapshot keeps its original creation
            # time; every other field must still match. Reading the immutable
            # winner also handles two simultaneous first requests without a
            # local cache, overwrite, or weaker owner-store conflict policy.
            existing = self._store.get_ranking_snapshot(snapshot.ranking_snapshot_id)
            if existing is None:
                raise
            replay = replace(snapshot, created_at=existing.created_at)
            if replay.to_canonical_dict() != existing.to_canonical_dict():
                raise
            created = existing
        return created.to_canonical_dict()


def create_ranking_write_owner() -> RankingSnapshotWriteOwnerPort:
    """Build the production Rankings write-owner port from environment configuration.

    Fails closed via ``build_rankings_store`` when no DSN is configured;
    there is no in-memory or local-overlay fallback for a durable write path.
    """

    return RankingSnapshotWriteOwnerPort(build_rankings_store())


__all__ = [
    "RankingSnapshotWriteOwnerPort",
    "create_ranking_write_owner",
]
