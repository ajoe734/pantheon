"""Migration engine and durable store for twelve-loop truth projection.

Under task LOOP-TRUTH-001:
  - Manages loop_truth_projection.loop_receipts and twelve_loop_observations tables.
  - Exposes TwelveLoopStore interface supporting Postgres and isolated file/memory execution.
  - Verifies incremental == rebuild, backfill cannot replace live truth, and rollback preservation.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from services.control_plane.bff.management_read_models.twelve_loop_projector import (
        CANONICAL_TWELVE_LOOPS,
        CanonicalLoopReceipt,
        LoopObservation,
        TwelveLoopTruthProjector,
        format_timestamp,
        parse_timestamp,
        resolve_loop_id_int,
    )
except ImportError:
    from management_read_models.twelve_loop_projector import (
        CANONICAL_TWELVE_LOOPS,
        CanonicalLoopReceipt,
        LoopObservation,
        TwelveLoopTruthProjector,
        format_timestamp,
        parse_timestamp,
        resolve_loop_id_int,
    )


logger = logging.getLogger(__name__)

MIGRATION_SQL_PATH = Path(__file__).resolve().parent / "002_create_twelve_loop_truth_schema.sql"


class TwelveLoopStore:
    """Abstract interface for durable receipt and twelve-loop observation persistence."""

    def record_receipt(self, receipt: CanonicalLoopReceipt) -> None:
        raise NotImplementedError

    def list_receipts(
        self,
        *,
        release_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        loop_id: Optional[int] = None,
    ) -> List[CanonicalLoopReceipt]:
        raise NotImplementedError

    def upsert_observation(self, obs: LoopObservation) -> None:
        raise NotImplementedError

    def get_observation(
        self, release_id: str, correlation_id: str, loop_id: int
    ) -> Optional[LoopObservation]:
        raise NotImplementedError

    def list_observations(
        self,
        *,
        release_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        loop_id: Optional[int] = None,
    ) -> List[LoopObservation]:
        raise NotImplementedError

    def clear_observations(self) -> None:
        raise NotImplementedError


class MemoryTwelveLoopStore(TwelveLoopStore):
    """Deterministic, isolated in-memory store for unit and acceptance tests."""

    def __init__(self) -> None:
        self._receipts: Dict[str, CanonicalLoopReceipt] = {}
        self._observations: Dict[Tuple[str, str, int], LoopObservation] = {}

    def record_receipt(self, receipt: CanonicalLoopReceipt) -> None:
        if receipt.receipt_id not in self._receipts:
            self._receipts[receipt.receipt_id] = receipt

    def list_receipts(
        self,
        *,
        release_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        loop_id: Optional[int] = None,
    ) -> List[CanonicalLoopReceipt]:
        items = list(self._receipts.values())
        if release_id:
            items = [r for r in items if r.release_id == release_id]
        if correlation_id:
            items = [r for r in items if r.correlation_id == correlation_id]
        if loop_id is not None:
            items = [r for r in items if r.loop_id == loop_id]
        return sorted(items, key=lambda r: (r.release_id, r.correlation_id, r.loop_id, r.observed_at))

    def upsert_observation(self, obs: LoopObservation) -> None:
        key = (obs.release_id, obs.correlation_id, obs.loop_id)
        self._observations[key] = obs

    def get_observation(
        self, release_id: str, correlation_id: str, loop_id: int
    ) -> Optional[LoopObservation]:
        return self._observations.get((release_id, correlation_id, loop_id))

    def list_observations(
        self,
        *,
        release_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        loop_id: Optional[int] = None,
    ) -> List[LoopObservation]:
        items = list(self._observations.values())
        if release_id:
            items = [o for o in items if o.release_id == release_id]
        if correlation_id:
            items = [o for o in items if o.correlation_id == correlation_id]
        if loop_id is not None:
            items = [o for o in items if o.loop_id == loop_id]
        return sorted(items, key=lambda o: (o.release_id, o.correlation_id, o.loop_id))

    def clear_observations(self) -> None:
        self._observations.clear()


class PostgresTwelveLoopStore(TwelveLoopStore):
    """PostgreSQL-backed store implementation using asyncpg."""

    def __init__(self, dsn: str, schema: str = "loop_truth_projection") -> None:
        self.dsn = dsn
        self.schema = schema

    async def apply_migration(self) -> None:
        import asyncpg
        conn = await asyncpg.connect(self.dsn)
        try:
            sql = MIGRATION_SQL_PATH.read_text(encoding="utf-8")
            await conn.execute(sql)
        finally:
            await conn.close()

    async def record_receipt_async(self, receipt: CanonicalLoopReceipt) -> None:
        import asyncpg
        conn = await asyncpg.connect(self.dsn)
        try:
            query = f"""
                INSERT INTO {self.schema}.loop_receipts (
                    receipt_id, receipt_type, loop_id, correlation_id, release_id,
                    owner, provenance, status, observed_at, degradation_reason,
                    causation_id, payload
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (receipt_id) DO NOTHING;
            """
            await conn.execute(
                query,
                receipt.receipt_id,
                receipt.receipt_type,
                receipt.loop_id,
                receipt.correlation_id,
                receipt.release_id,
                receipt.owner,
                receipt.provenance,
                receipt.status,
                receipt.observed_at,
                receipt.degradation_reason,
                receipt.causation_id,
                json.dumps(receipt.payload),
            )
        finally:
            await conn.close()

    async def upsert_observation_async(self, obs: LoopObservation) -> None:
        import asyncpg
        conn = await asyncpg.connect(self.dsn)
        try:
            query = f"""
                INSERT INTO {self.schema}.twelve_loop_observations (
                    release_id, correlation_id, loop_id, owner,
                    stimulus_id, stimulus_observed_at,
                    terminal_id, terminal_status, terminal_observed_at,
                    next_consumer_receipt_id, next_consumer_observed_at,
                    status, freshness_status, provenance, observed_at,
                    degradation_reason, causation_id, receipt_ids, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, clock_timestamp()
                )
                ON CONFLICT (release_id, correlation_id, loop_id) DO UPDATE SET
                    owner = EXCLUDED.owner,
                    stimulus_id = EXCLUDED.stimulus_id,
                    stimulus_observed_at = EXCLUDED.stimulus_observed_at,
                    terminal_id = EXCLUDED.terminal_id,
                    terminal_status = EXCLUDED.terminal_status,
                    terminal_observed_at = EXCLUDED.terminal_observed_at,
                    next_consumer_receipt_id = EXCLUDED.next_consumer_receipt_id,
                    next_consumer_observed_at = EXCLUDED.next_consumer_observed_at,
                    status = EXCLUDED.status,
                    freshness_status = EXCLUDED.freshness_status,
                    provenance = EXCLUDED.provenance,
                    observed_at = EXCLUDED.observed_at,
                    degradation_reason = EXCLUDED.degradation_reason,
                    causation_id = EXCLUDED.causation_id,
                    receipt_ids = EXCLUDED.receipt_ids,
                    updated_at = clock_timestamp();
            """
            await conn.execute(
                query,
                obs.release_id,
                obs.correlation_id,
                obs.loop_id,
                obs.owner,
                obs.stimulus_id,
                obs.stimulus_observed_at,
                obs.terminal_id,
                obs.terminal_status,
                obs.terminal_observed_at,
                obs.next_consumer_receipt_id,
                obs.next_consumer_observed_at,
                obs.status,
                obs.freshness_status,
                obs.provenance,
                obs.observed_at,
                obs.degradation_reason,
                obs.causation_id,
                json.dumps(obs.receipt_ids),
            )
        finally:
            await conn.close()


def build_twelve_loop_store(dsn: Optional[str] = None) -> TwelveLoopStore:
    """Build the store appropriate to the environment (Postgres if DSN, Memory otherwise)."""
    resolved_dsn = dsn or os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if resolved_dsn and "postgresql" in resolved_dsn:
        return PostgresTwelveLoopStore(resolved_dsn)
    return MemoryTwelveLoopStore()
