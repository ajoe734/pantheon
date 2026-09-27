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
        validate_scope,
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
        validate_scope,
    )


logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent
MIGRATION_SQL_PATH = MIGRATIONS_DIR / "002_create_twelve_loop_truth_schema.sql"
MIGRATION_002_SQL_PATH = MIGRATIONS_DIR / "002_create_twelve_loop_truth_schema.sql"
MIGRATION_003_SQL_PATH = MIGRATIONS_DIR / "003_scope_twelve_loop_truth_receipts.sql"


class TwelveLoopStore:
    """Abstract interface for durable receipt and twelve-loop observation persistence."""

    def record_receipt(self, receipt: CanonicalLoopReceipt) -> None:
        raise NotImplementedError

    def get_receipt(
        self,
        receipt_id: str,
        *,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        all_scopes: bool = True,
    ) -> Optional[CanonicalLoopReceipt]:
        raise NotImplementedError

    def list_receipts(
        self,
        *,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        release_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        loop_id: Optional[int] = None,
        all_scopes: bool = False,
    ) -> List[CanonicalLoopReceipt]:
        raise NotImplementedError

    def upsert_observation(self, obs: LoopObservation) -> None:
        raise NotImplementedError

    def get_observation(
        self,
        release_id: str,
        correlation_id: str,
        loop_id: int,
        *,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> Optional[LoopObservation]:
        raise NotImplementedError

    def list_observations(
        self,
        *,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        release_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        loop_id: Optional[int] = None,
        all_scopes: bool = False,
    ) -> List[LoopObservation]:
        raise NotImplementedError

    def clear_observations(self) -> None:
        raise NotImplementedError

    def rollback_to_002_schema_sync(self) -> None:
        raise NotImplementedError

    async def rollback_to_002_schema(self) -> None:
        raise NotImplementedError


class MemoryTwelveLoopStore(TwelveLoopStore):
    """Deterministic, isolated in-memory store for unit and acceptance tests."""

    def __init__(self) -> None:
        self._receipts: Dict[Tuple[Optional[str], Optional[str], str], CanonicalLoopReceipt] = {}
        self._observations: Dict[Tuple[Optional[str], Optional[str], str, str, int], LoopObservation] = {}
        self._scoped_backup: Dict[Tuple[Optional[str], Optional[str], str, str, int], LoopObservation] = {}
        self._scoped_receipts_backup: Dict[Tuple[Optional[str], Optional[str], str], CanonicalLoopReceipt] = {}

    def record_receipt(self, receipt: CanonicalLoopReceipt) -> None:
        validate_scope(receipt.tenant_id, receipt.environment)
        key = (receipt.tenant_id, receipt.environment, receipt.receipt_id)
        if key not in self._receipts:
            self._receipts[key] = receipt

    def get_receipt(
        self,
        receipt_id: str,
        *,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        all_scopes: bool = True,
    ) -> Optional[CanonicalLoopReceipt]:
        if not all_scopes:
            validate_scope(tenant_id, environment)
            return self._receipts.get((tenant_id, environment, receipt_id))
        if tenant_id is not None or environment is not None:
            return self._receipts.get((tenant_id, environment, receipt_id))
        for (t, e, rid), r in self._receipts.items():
            if rid == receipt_id:
                return r
        return None

    def list_receipts(
        self,
        *,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        release_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        loop_id: Optional[int] = None,
        all_scopes: bool = False,
    ) -> List[CanonicalLoopReceipt]:
        validate_scope(tenant_id, environment, allow_all_scopes=all_scopes)
        items = list(self._receipts.values())
        if not all_scopes:
            if tenant_id is None and environment is None:
                items = [r for r in items if r.tenant_id is None and r.environment is None]
            else:
                items = [r for r in items if r.tenant_id == tenant_id and r.environment == environment]
        else:
            if tenant_id is not None:
                items = [r for r in items if r.tenant_id == tenant_id]
            if environment is not None:
                items = [r for r in items if r.environment == environment]
        if release_id:
            items = [r for r in items if r.release_id == release_id]
        if correlation_id:
            items = [r for r in items if r.correlation_id == correlation_id]
        if loop_id is not None:
            items = [r for r in items if r.loop_id == loop_id]
        return sorted(items, key=lambda r: (r.tenant_id or "", r.environment or "", r.release_id, r.correlation_id, r.loop_id, r.observed_at))

    def upsert_observation(self, obs: LoopObservation) -> None:
        validate_scope(obs.tenant_id, obs.environment)
        key = (obs.tenant_id, obs.environment, obs.release_id, obs.correlation_id, obs.loop_id)
        existing = self._observations.get(key)
        if existing is not None:
            # 1. Receipt-set ordering: new observation must contain all receipts of existing observation
            existing_receipts = set(existing.receipt_ids or [])
            new_receipts = set(obs.receipt_ids or [])
            if not existing_receipts.issubset(new_receipts):
                return
            # 2. Provenance fencing: new observation cannot have lower provenance than existing observation
            prov_rank = {"backfill": 0, "replay": 1, "live": 2}
            new_prov = prov_rank.get(obs.provenance, 0)
            cur_prov = prov_rank.get(existing.provenance, 0)
            if new_prov < cur_prov:
                return
        import copy
        self._observations[key] = copy.copy(obs)

    def get_observation(
        self,
        release_id: str,
        correlation_id: str,
        loop_id: int,
        *,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> Optional[LoopObservation]:
        validate_scope(tenant_id, environment)
        return self._observations.get((tenant_id, environment, release_id, correlation_id, loop_id))

    def list_observations(
        self,
        *,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        release_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        loop_id: Optional[int] = None,
        all_scopes: bool = False,
    ) -> List[LoopObservation]:
        validate_scope(tenant_id, environment, allow_all_scopes=all_scopes)
        items = list(self._observations.values())
        if not all_scopes:
            if tenant_id is None and environment is None:
                items = [o for o in items if o.tenant_id is None and o.environment is None]
            else:
                items = [o for o in items if o.tenant_id == tenant_id and o.environment == environment]
        else:
            if tenant_id is not None:
                items = [o for o in items if o.tenant_id == tenant_id]
            if environment is not None:
                items = [o for o in items if o.environment == environment]
        if release_id:
            items = [o for o in items if o.release_id == release_id]
        if correlation_id:
            items = [o for o in items if o.correlation_id == correlation_id]
        if loop_id is not None:
            items = [o for o in items if o.loop_id == loop_id]
        return sorted(items, key=lambda o: (o.tenant_id or "", o.environment or "", o.release_id, o.correlation_id, o.loop_id))

    def clear_observations(self) -> None:
        self._observations.clear()

    def rollback_to_002_schema_sync(self) -> None:
        self._scoped_backup = {k: v for k, v in self._observations.items() if k[0] is not None or k[1] is not None}
        self._observations = {k: v for k, v in self._observations.items() if k[0] is None and k[1] is None}
        self._scoped_receipts_backup = {k: v for k, v in self._receipts.items() if v.tenant_id is not None or v.environment is not None}
        self._receipts = {k: v for k, v in self._receipts.items() if v.tenant_id is None and v.environment is None}

    async def rollback_to_002_schema(self) -> None:
        self.rollback_to_002_schema_sync()

    def restore_from_002_rollback(self) -> None:
        if hasattr(self, "_scoped_backup"):
            for k, v in self._scoped_backup.items():
                if k not in self._observations:
                    self._observations[k] = v
            del self._scoped_backup
        if hasattr(self, "_scoped_receipts_backup"):
            for k, v in self._scoped_receipts_backup.items():
                if k not in self._receipts:
                    self._receipts[k] = v
            del self._scoped_receipts_backup


class PostgresTwelveLoopStore(TwelveLoopStore):
    """PostgreSQL-backed store implementation using psycopg."""

    def __init__(self, dsn: str, schema: str = "loop_truth_projection") -> None:
        self.dsn = dsn
        self.schema = schema

    def _connect(self) -> Any:
        import psycopg
        return psycopg.connect(self.dsn)

    def apply_migration_sync(self) -> None:
        sql_002 = MIGRATION_002_SQL_PATH.read_text(encoding="utf-8")
        sql_003 = MIGRATION_003_SQL_PATH.read_text(encoding="utf-8")
        if self.schema != "loop_truth_projection":
            sql_002 = sql_002.replace("loop_truth_projection", self.schema)
            sql_003 = sql_003.replace("loop_truth_projection", self.schema)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_002)
                cur.execute(sql_003)
            conn.commit()

    async def apply_migration(self) -> None:
        try:
            import asyncpg
            conn = await asyncpg.connect(self.dsn)
            try:
                sql_002 = MIGRATION_002_SQL_PATH.read_text(encoding="utf-8")
                sql_003 = MIGRATION_003_SQL_PATH.read_text(encoding="utf-8")
                if self.schema != "loop_truth_projection":
                    sql_002 = sql_002.replace("loop_truth_projection", self.schema)
                    sql_003 = sql_003.replace("loop_truth_projection", self.schema)
                await conn.execute(sql_002)
                await conn.execute(sql_003)
            finally:
                await conn.close()
        except Exception:
            self.apply_migration_sync()

    def record_receipt(self, receipt: CanonicalLoopReceipt) -> None:
        validate_scope(receipt.tenant_id, receipt.environment)
        query = f"""
            INSERT INTO {self.schema}.loop_receipts (
                receipt_id, receipt_type, loop_id, correlation_id, release_id,
                owner, provenance, status, observed_at, degradation_reason,
                causation_id, payload, tenant_id, environment
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, environment, receipt_id) DO NOTHING;
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (
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
                        receipt.tenant_id,
                        receipt.environment,
                    ),
                )
            conn.commit()

    async def record_receipt_async(self, receipt: CanonicalLoopReceipt) -> None:
        validate_scope(receipt.tenant_id, receipt.environment)
        try:
            import asyncpg
            conn = await asyncpg.connect(self.dsn)
            try:
                query = f"""
                    INSERT INTO {self.schema}.loop_receipts (
                        receipt_id, receipt_type, loop_id, correlation_id, release_id,
                        owner, provenance, status, observed_at, degradation_reason,
                        causation_id, payload, tenant_id, environment
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    ON CONFLICT (tenant_id, environment, receipt_id) DO NOTHING;
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
                    receipt.tenant_id,
                    receipt.environment,
                )
            finally:
                await conn.close()
        except Exception:
            self.record_receipt(receipt)

    def get_receipt(
        self,
        receipt_id: str,
        *,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        all_scopes: bool = True,
    ) -> Optional[CanonicalLoopReceipt]:
        clauses = ["receipt_id = %s"]
        params: List[Any] = [receipt_id]
        if not all_scopes:
            validate_scope(tenant_id, environment)
            if tenant_id is None and environment is None:
                clauses.append("tenant_id IS NULL")
                clauses.append("environment IS NULL")
            else:
                clauses.append("tenant_id = %s")
                params.append(tenant_id)
                clauses.append("environment = %s")
                params.append(environment)
        else:
            if tenant_id is not None:
                clauses.append("tenant_id = %s")
                params.append(tenant_id)
            if environment is not None:
                clauses.append("environment = %s")
                params.append(environment)
        where_clause = "WHERE " + " AND ".join(clauses)
        query = f"""
            SELECT receipt_id, receipt_type, loop_id, correlation_id, release_id,
                   owner, provenance, status, observed_at, degradation_reason,
                   causation_id, payload, tenant_id, environment
            FROM {self.schema}.loop_receipts
            {where_clause};
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                row = cur.fetchone()
                if not row:
                    return None
                payload = row[11]
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                return CanonicalLoopReceipt(
                    receipt_id=row[0],
                    receipt_type=row[1],
                    loop_id=row[2],
                    correlation_id=row[3],
                    release_id=row[4],
                    owner=row[5],
                    provenance=row[6],
                    status=row[7],
                    observed_at=row[8],
                    degradation_reason=row[9],
                    causation_id=row[10],
                    payload=payload if isinstance(payload, dict) else {},
                    tenant_id=row[12],
                    environment=row[13],
                )

    async def get_receipt_async(
        self,
        receipt_id: str,
        *,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        all_scopes: bool = True,
    ) -> Optional[CanonicalLoopReceipt]:
        try:
            import asyncpg
            conn = await asyncpg.connect(self.dsn)
            try:
                clauses = ["receipt_id = $1"]
                params: List[Any] = [receipt_id]
                idx = 2
                if not all_scopes:
                    validate_scope(tenant_id, environment)
                    if tenant_id is None and environment is None:
                        clauses.append("tenant_id IS NULL")
                        clauses.append("environment IS NULL")
                    else:
                        clauses.append(f"tenant_id = ${idx}")
                        params.append(tenant_id)
                        idx += 1
                        clauses.append(f"environment = ${idx}")
                        params.append(environment)
                        idx += 1
                else:
                    if tenant_id is not None:
                        clauses.append(f"tenant_id = ${idx}")
                        params.append(tenant_id)
                        idx += 1
                    if environment is not None:
                        clauses.append(f"environment = ${idx}")
                        params.append(environment)
                        idx += 1
                where_clause = "WHERE " + " AND ".join(clauses)
                query = f"""
                    SELECT receipt_id, receipt_type, loop_id, correlation_id, release_id,
                   owner, provenance, status, observed_at, degradation_reason,
                   causation_id, payload, tenant_id, environment
                    FROM {self.schema}.loop_receipts
                    {where_clause};
                """
                row = await conn.fetchrow(query, *params)
                if not row:
                    return None
                payload = row["payload"]
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                return CanonicalLoopReceipt(
                    receipt_id=row["receipt_id"],
                    receipt_type=row["receipt_type"],
                    loop_id=row["loop_id"],
                    correlation_id=row["correlation_id"],
                    release_id=row["release_id"],
                    owner=row["owner"],
                    provenance=row["provenance"],
                    status=row["status"],
                    observed_at=row["observed_at"],
                    degradation_reason=row["degradation_reason"],
                    causation_id=row["causation_id"],
                    payload=payload if isinstance(payload, dict) else {},
                    tenant_id=row["tenant_id"],
                    environment=row["environment"],
                )
            finally:
                await conn.close()
        except Exception:
            return self.get_receipt(receipt_id, tenant_id=tenant_id, environment=environment, all_scopes=all_scopes)

    def list_receipts(
        self,
        *,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        release_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        loop_id: Optional[int] = None,
        all_scopes: bool = False,
    ) -> List[CanonicalLoopReceipt]:
        validate_scope(tenant_id, environment, allow_all_scopes=all_scopes)
        clauses = []
        params: List[Any] = []
        if not all_scopes:
            if tenant_id is None and environment is None:
                clauses.append("tenant_id IS NULL")
                clauses.append("environment IS NULL")
            else:
                clauses.append("tenant_id = %s")
                params.append(tenant_id)
                clauses.append("environment = %s")
                params.append(environment)
        else:
            if tenant_id is not None:
                clauses.append("tenant_id = %s")
                params.append(tenant_id)
            if environment is not None:
                clauses.append("environment = %s")
                params.append(environment)
        if release_id:
            clauses.append("release_id = %s")
            params.append(release_id)
        if correlation_id:
            clauses.append("correlation_id = %s")
            params.append(correlation_id)
        if loop_id is not None:
            clauses.append("loop_id = %s")
            params.append(loop_id)
        where_clause = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        query = f"""
            SELECT receipt_id, receipt_type, loop_id, correlation_id, release_id,
                   owner, provenance, status, observed_at, degradation_reason,
                   causation_id, payload, tenant_id, environment
            FROM {self.schema}.loop_receipts
            {where_clause}
            ORDER BY observed_at ASC, receipt_id ASC;
        """
        results: List[CanonicalLoopReceipt] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
                for row in rows:
                    payload = row[11]
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except Exception:
                            payload = {}
                    results.append(
                        CanonicalLoopReceipt(
                            receipt_id=row[0],
                            receipt_type=row[1],
                            loop_id=row[2],
                            correlation_id=row[3],
                            release_id=row[4],
                            owner=row[5],
                            provenance=row[6],
                            status=row[7],
                            observed_at=row[8],
                            degradation_reason=row[9],
                            causation_id=row[10],
                            payload=payload if isinstance(payload, dict) else {},
                            tenant_id=row[12],
                            environment=row[13],
                        )
                    )
        return results

    def upsert_observation(self, obs: LoopObservation) -> None:
        validate_scope(obs.tenant_id, obs.environment)
        query = f"""
            INSERT INTO {self.schema}.twelve_loop_observations (
                tenant_id, environment, release_id, correlation_id, loop_id, owner,
                stimulus_id, stimulus_observed_at,
                terminal_id, terminal_status, terminal_observed_at,
                next_consumer_receipt_id, next_consumer_observed_at,
                status, freshness_status, provenance, observed_at,
                degradation_reason, causation_id, receipt_ids, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp()
            )
            ON CONFLICT (tenant_id, environment, release_id, correlation_id, loop_id) DO UPDATE SET
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
                updated_at = clock_timestamp()
            WHERE EXCLUDED.receipt_ids @> COALESCE({self.schema}.twelve_loop_observations.receipt_ids, '[]'::jsonb)
            AND (
                CASE EXCLUDED.provenance WHEN 'live' THEN 2 WHEN 'replay' THEN 1 ELSE 0 END >=
                CASE {self.schema}.twelve_loop_observations.provenance WHEN 'live' THEN 2 WHEN 'replay' THEN 1 ELSE 0 END
            );
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (
                        obs.tenant_id,
                        obs.environment,
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
                    ),
                )
            conn.commit()

    async def upsert_observation_async(self, obs: LoopObservation) -> None:
        validate_scope(obs.tenant_id, obs.environment)
        try:
            import asyncpg
            conn = await asyncpg.connect(self.dsn)
            try:
                query = f"""
                    INSERT INTO {self.schema}.twelve_loop_observations (
                        tenant_id, environment, release_id, correlation_id, loop_id, owner,
                        stimulus_id, stimulus_observed_at,
                        terminal_id, terminal_status, terminal_observed_at,
                        next_consumer_receipt_id, next_consumer_observed_at,
                        status, freshness_status, provenance, observed_at,
                        degradation_reason, causation_id, receipt_ids, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, clock_timestamp()
                    )
                    ON CONFLICT (tenant_id, environment, release_id, correlation_id, loop_id) DO UPDATE SET
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
                        updated_at = clock_timestamp()
                    WHERE EXCLUDED.receipt_ids @> COALESCE({self.schema}.twelve_loop_observations.receipt_ids, '[]'::jsonb)
                    AND (
                        CASE EXCLUDED.provenance WHEN 'live' THEN 2 WHEN 'replay' THEN 1 ELSE 0 END >=
                        CASE {self.schema}.twelve_loop_observations.provenance WHEN 'live' THEN 2 WHEN 'replay' THEN 1 ELSE 0 END
                    );
                """
                await conn.execute(
                    query,
                    obs.tenant_id,
                    obs.environment,
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
        except Exception:
            self.upsert_observation(obs)

    def get_observation(
        self,
        release_id: str,
        correlation_id: str,
        loop_id: int,
        *,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> Optional[LoopObservation]:
        validate_scope(tenant_id, environment)
        clauses = ["release_id = %s", "correlation_id = %s", "loop_id = %s"]
        params: List[Any] = [release_id, correlation_id, loop_id]
        if tenant_id is not None:
            clauses.append("tenant_id = %s")
            params.append(tenant_id)
        else:
            clauses.append("tenant_id IS NULL")
        if environment is not None:
            clauses.append("environment = %s")
            params.append(environment)
        else:
            clauses.append("environment IS NULL")
        where_clause = "WHERE " + " AND ".join(clauses)
        query = f"""
            SELECT release_id, correlation_id, loop_id, owner,
                   stimulus_id, stimulus_observed_at,
                   terminal_id, terminal_status, terminal_observed_at,
                   next_consumer_receipt_id, next_consumer_observed_at,
                   status, freshness_status, provenance, observed_at,
                   degradation_reason, causation_id, receipt_ids,
                   tenant_id, environment
            FROM {self.schema}.twelve_loop_observations
            {where_clause};
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                row = cur.fetchone()
                if not row:
                    return None
                receipt_ids = row[17]
                if isinstance(receipt_ids, str):
                    try:
                        receipt_ids = json.loads(receipt_ids)
                    except Exception:
                        receipt_ids = []
                return LoopObservation(
                    release_id=row[0],
                    correlation_id=row[1],
                    loop_id=row[2],
                    owner=row[3],
                    stimulus_id=row[4],
                    stimulus_observed_at=row[5],
                    terminal_id=row[6],
                    terminal_status=row[7] or "unobserved",
                    terminal_observed_at=row[8],
                    next_consumer_receipt_id=row[9],
                    next_consumer_observed_at=row[10],
                    status=row[11],
                    freshness_status=row[12],
                    provenance=row[13],
                    observed_at=row[14],
                    degradation_reason=row[15],
                    causation_id=row[16],
                    receipt_ids=list(receipt_ids or []),
                    tenant_id=row[18],
                    environment=row[19],
                )

    def list_observations(
        self,
        *,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        release_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        loop_id: Optional[int] = None,
        all_scopes: bool = False,
    ) -> List[LoopObservation]:
        validate_scope(tenant_id, environment, allow_all_scopes=all_scopes)
        clauses = []
        params: List[Any] = []
        if not all_scopes:
            if tenant_id is None and environment is None:
                clauses.append("tenant_id IS NULL")
                clauses.append("environment IS NULL")
            else:
                clauses.append("tenant_id = %s")
                params.append(tenant_id)
                clauses.append("environment = %s")
                params.append(environment)
        else:
            if tenant_id is not None:
                clauses.append("tenant_id = %s")
                params.append(tenant_id)
            if environment is not None:
                clauses.append("environment = %s")
                params.append(environment)
        if release_id:
            clauses.append("release_id = %s")
            params.append(release_id)
        if correlation_id:
            clauses.append("correlation_id = %s")
            params.append(correlation_id)
        if loop_id is not None:
            clauses.append("loop_id = %s")
            params.append(loop_id)
        where_clause = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        query = f"""
            SELECT release_id, correlation_id, loop_id, owner,
                   stimulus_id, stimulus_observed_at,
                   terminal_id, terminal_status, terminal_observed_at,
                   next_consumer_receipt_id, next_consumer_observed_at,
                   status, freshness_status, provenance, observed_at,
                   degradation_reason, causation_id, receipt_ids,
                   tenant_id, environment
            FROM {self.schema}.twelve_loop_observations
            {where_clause}
            ORDER BY release_id ASC, correlation_id ASC, loop_id ASC;
        """
        results: List[LoopObservation] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
                for row in rows:
                    receipt_ids = row[17]
                    if isinstance(receipt_ids, str):
                        try:
                            receipt_ids = json.loads(receipt_ids)
                        except Exception:
                            receipt_ids = []
                    results.append(
                        LoopObservation(
                            release_id=row[0],
                            correlation_id=row[1],
                            loop_id=row[2],
                            owner=row[3],
                            stimulus_id=row[4],
                            stimulus_observed_at=row[5],
                            terminal_id=row[6],
                            terminal_status=row[7] or "unobserved",
                            terminal_observed_at=row[8],
                            next_consumer_receipt_id=row[9],
                            next_consumer_observed_at=row[10],
                            status=row[11],
                            freshness_status=row[12],
                            provenance=row[13],
                            observed_at=row[14],
                            degradation_reason=row[15],
                            causation_id=row[16],
                            receipt_ids=list(receipt_ids or []),
                            tenant_id=row[18],
                            environment=row[19],
                        )
                    )
        return results

    def clear_observations(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self.schema}.twelve_loop_observations;")
            conn.commit()

    @staticmethod
    def _build_rollback_to_002_sql(schema: str) -> str:
        return f"""
        CREATE TABLE IF NOT EXISTS {schema}.loop_receipts_scoped_backup (
            LIKE {schema}.loop_receipts INCLUDING ALL
        );
        INSERT INTO {schema}.loop_receipts_scoped_backup
        SELECT * FROM {schema}.loop_receipts
        WHERE tenant_id IS NOT NULL OR environment IS NOT NULL
        ON CONFLICT (tenant_id, environment, receipt_id) DO NOTHING;

        DELETE FROM {schema}.loop_receipts
        WHERE tenant_id IS NOT NULL OR environment IS NOT NULL;

        DROP INDEX IF EXISTS {schema}.idx_loop_receipts_scoped_key;
        DROP INDEX IF EXISTS {schema}.idx_loop_receipts_scope_key;
        DROP INDEX IF EXISTS {schema}.idx_loop_receipts_scope_correlation;

        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'loop_receipts_pkey'
                  AND conrelid = '{schema}.loop_receipts'::regclass
            ) THEN
                ALTER TABLE {schema}.loop_receipts
                    ADD CONSTRAINT loop_receipts_pkey
                    PRIMARY KEY (receipt_id);
            END IF;
        END $$;

        CREATE TABLE IF NOT EXISTS {schema}.twelve_loop_observations_scoped_backup (
            LIKE {schema}.twelve_loop_observations INCLUDING ALL
        );
        INSERT INTO {schema}.twelve_loop_observations_scoped_backup
        SELECT * FROM {schema}.twelve_loop_observations
        WHERE tenant_id IS NOT NULL OR environment IS NOT NULL
        ON CONFLICT (tenant_id, environment, release_id, correlation_id, loop_id) DO NOTHING;

        DELETE FROM {schema}.twelve_loop_observations
        WHERE tenant_id IS NOT NULL OR environment IS NOT NULL;

        DROP INDEX IF EXISTS {schema}.idx_loop_obs_scoped_key;
        DROP INDEX IF EXISTS {schema}.idx_loop_obs_scope_release_corr;

        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'twelve_loop_observations_pkey'
                  AND conrelid = '{schema}.twelve_loop_observations'::regclass
            ) THEN
                ALTER TABLE {schema}.twelve_loop_observations
                    ADD CONSTRAINT twelve_loop_observations_pkey
                    PRIMARY KEY (release_id, correlation_id, loop_id);
            END IF;
        END $$;
    """


    def rollback_to_002_schema_sync(self) -> None:
        """Rollback schema to 002 compatibility non-lossily.
        Archives scoped loop receipts into `loop_receipts_scoped_backup`,
        archives scoped observations into `twelve_loop_observations_scoped_backup`,
        drops scoped indexes, and restores the pre-003 `twelve_loop_observations_pkey` constraint.
        """
        sql = self._build_rollback_to_002_sql(self.schema)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()

    async def rollback_to_002_schema(self) -> None:
        try:
            import asyncpg
            conn = await asyncpg.connect(self.dsn)
            try:
                sql = self._build_rollback_to_002_sql(self.schema)
                await conn.execute(sql)
            finally:
                await conn.close()
        except Exception:
            self.rollback_to_002_schema_sync()


def build_twelve_loop_store(dsn: Optional[str] = None) -> TwelveLoopStore:
    """Build the store appropriate to the environment (Postgres if DSN, Memory otherwise)."""
    resolved_dsn = dsn or os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if resolved_dsn and "postgresql" in resolved_dsn:
        return PostgresTwelveLoopStore(resolved_dsn)
    return MemoryTwelveLoopStore()
