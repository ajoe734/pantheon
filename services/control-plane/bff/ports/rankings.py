"""Typed write port for Rankings generation-3 snapshot records.

The BFF never holds durable ranking-snapshot state locally. Every write
crosses to the canonical ``services.rankings.store.RankingWriteStore`` write
owner, matching ``DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md``. This is
the sole entrypoint that used to be a local-overlay mutation method on
``ReadSurfacePorts``.
"""
from __future__ import annotations

from typing import Any, Dict

from services.rankings.store import (
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
        created = self._store.create_ranking_snapshot(snapshot)
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
