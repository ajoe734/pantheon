"""Optional Postgres-backed evidence store for source-ingest.

Enabled by env; JSONL remains the default.  Source-ingest is the sole
write owner of the source_evidence table.  Other services must read
through the owner API or a read-only database role.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

from services.knowledge.evidence import (
    EvidenceBundle,
    EvidenceItem,
    InMemoryEvidenceRepository,
    JsonlEvidenceRepository,
    KnowledgeObject,
)
from services.knowledge.evidence.repository import _UNSCOPED, _Unscoped, _capture_datetime, evidence_item_in_capture_window, evidence_item_linked_entity_types
from services.knowledge.evidence.models import EVIDENCE_LIST_OMITTED_METADATA_KEYS, EvidenceValidationError, strip_list_heavy_metadata
from services.source_ingestion.connectors.base import SourceRecord, SourceRecordStatus, SourceType

_PG_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_EVIDENCE_LOADERS: Dict[str, Any] = {
    "source_record": SourceRecord.from_dict,
    "evidence_item": EvidenceItem.from_dict,
    "evidence_bundle": EvidenceBundle.from_dict,
    "knowledge_object": KnowledgeObject.from_dict,
}

# Payload upserts preserve their original append_id.  A stable knowledge object
# may therefore be updated to reference a bundle appended later.  Replay must
# retain append order within a record type while loading dependency types first.
_EVIDENCE_REPLAY_ORDER = (
    "source_record",
    "evidence_item",
    "evidence_bundle",
    "knowledge_object",
)

_DEDUPE_KEY_EXPR = (
    "(CASE record_type "
    "WHEN 'source_record' THEN payload->'metadata'->>'source_dedupe_key' "
    "WHEN 'evidence_item' THEN payload->'metadata'->>'evidence_dedupe_key' "
    "END)"
)
_TENANT_SCOPE_EXPR = "COALESCE(payload->'metadata'->>'tenant_id', '')"
_OBJECT_ID_EXPR = (
    "(CASE record_type "
    "WHEN 'source_record' THEN payload->>'source_id' "
    "WHEN 'evidence_item' THEN payload->>'evidence_item_id' "
    "WHEN 'evidence_bundle' THEN payload->>'evidence_bundle_id' "
    "WHEN 'knowledge_object' THEN payload->>'knowledge_object_id' "
    "END)"
)
_SOURCE_CONNECTOR_EXPR = "payload->>'connector_id'"
_DEDUPE_FIELDS_BY_RECORD_TYPE: Dict[str, str] = {
    "source_record": "source_dedupe_key",
    "evidence_item": "evidence_dedupe_key",
}
_CONCURRENT_LOOKUP_INDEX_SPECS: tuple[tuple[str, str], ...] = (
    (
        "source_evidence_dedupe_lookup_idx",
        f"(record_type, {_DEDUPE_KEY_EXPR}, append_id DESC, {_TENANT_SCOPE_EXPR})",
    ),
    (
        "source_evidence_object_id_lookup_idx",
        f"(record_type, {_OBJECT_ID_EXPR}, append_id DESC)",
    ),
    (
        # Connector-scoped newest-first source-record pages
        # (AGORA-PROJECTOR-SOURCE-RECORDS-BOUNDED-READ-20261126): one tenant's
        # one connector is an index range, never a scan of the tenant table.
        "source_evidence_source_connector_page_idx",
        f"(evidence_tenant_id, ({_SOURCE_CONNECTOR_EXPR}), append_id DESC) "
        "WHERE record_type = 'source_record'",
    ),
)
# Hard owner cap for one connector-scoped source-record page.
SOURCE_RECORD_PAGE_MAX = 1000


def _quote_pg(identifier: str) -> str:
    parts = identifier.split(".")
    if not parts or any(_PG_IDENTIFIER_RE.fullmatch(part) is None for part in parts):
        raise ValueError(f"Invalid Postgres identifier: {identifier}")
    return ".".join(f'"{part}"' for part in parts)


def _scoped_record_id(tenant_id: str | None, record_id: str) -> str:
    """Collision-safe conflict key for the shared (record_type, record_id) unique index.

    Untenanted (legacy) records keep their bare ``record_id`` unchanged so existing
    rows stay reachable without modification. Tenanted records receive a distinguished
    length-prefixed scope prefix (``@t{len}:{tenant}:{record_id}``) that cannot
    alias to another tenanted pair. Any legacy record whose ID starts with ``@`` is
    safely escaped (``@{record_id}``) so legacy and tenanted record keys can never
    collide under the ``UNIQUE (record_type, record_id)`` index.
    """
    if tenant_id is None:
        if record_id.startswith("@"):
            return f"@{record_id}"
        return record_id
    return f"@t{len(tenant_id)}:{tenant_id}:{record_id}"


def _safe_close(conn: Any) -> None:
    try:
        if hasattr(conn, "close"):
            conn.close()
    except Exception:
        pass


class PostgresSourceEvidenceRepository(InMemoryEvidenceRepository):
    """Postgres-backed source evidence store for source-ingest.

    Write owner: source-ingest-svc.  Non-owner services must use the
    owner API or a read-only DB role — never write directly.
    """

    def __init__(
        self,
        dsn: str,
        table: str = "source_ingest.source_evidence",
        bootstrap: bool = True,
        max_connections: int = 5,
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for PostgresSourceEvidenceRepository")
        self.dsn = dsn
        self.table = _quote_pg(table)
        self.schema = table.split(".", 1)[0] if "." in table else ""
        self.path = f"postgres:{table}"
        self._pool: queue.LifoQueue = queue.LifoQueue(maxsize=max_connections)
        self._replaying = False
        super().__init__()
        if bootstrap:
            self._bootstrap()
        self.reload()

    def _connect(self):
        psycopg = getattr(self, "_psycopg", None)
        if psycopg is None:
            try:
                import psycopg  # type: ignore[import]
                self._psycopg = psycopg
            except ImportError as exc:
                raise RuntimeError(
                    "psycopg is required when SOURCE_INGEST_EVIDENCE_BACKEND=postgres"
                ) from exc
        conn = self._psycopg.connect(self.dsn)
        try:
            conn.autocommit = True
        except Exception:
            pass
        return conn

    @contextmanager
    def _get_connection(self):
        conn = None
        while not self._pool.empty():
            try:
                candidate = self._pool.get_nowait()
            except queue.Empty:
                break
            if getattr(candidate, "_pool_dsn", None) != self.dsn or getattr(candidate, "closed", False):
                _safe_close(candidate)
                continue
            conn = candidate
            break

        if conn is None:
            conn = self._connect()
            setattr(conn, "_pool_dsn", self.dsn)

        try:
            yield conn
        except Exception:
            _safe_close(conn)
            raise
        else:
            if not getattr(conn, "closed", False) and getattr(conn, "_pool_dsn", None) == self.dsn:
                try:
                    self._pool.put_nowait(conn)
                except queue.Full:
                    _safe_close(conn)

    def close(self) -> None:
        while not self._pool.empty():
            try:
                _safe_close(self._pool.get_nowait())
            except queue.Empty:
                break

    def _acquire_bootstrap_advisory_lock(
        self,
        conn: Any,
        lock_namespace: str,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 0.05,
    ) -> bool:
        """Acquire session advisory lock without holding a virtual transaction while waiting.

        A blocking SQL ``SELECT pg_advisory_lock(...)`` keeps a virtual transaction
        open while waiting, which deadlocks against ``CREATE INDEX CONCURRENTLY`` /
        ``DROP INDEX CONCURRENTLY`` waiting for older virtual transactions to finish.
        Polling ``pg_try_advisory_lock`` on an autocommit connection ends each probe
        statement immediately so the waiting session stays idle between probes.
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            rows = conn.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s))",
                (lock_namespace, self.table),
            ).fetchall()
            if (
                not rows
                or not isinstance(rows[0], (tuple, list))
                or len(rows[0]) != 1
                or not isinstance(rows[0][0], bool)
            ):
                return True
            if rows[0][0]:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_interval_seconds)

    def _bootstrap(self) -> None:
        lock_namespace = "source_ingest.source_evidence.bootstrap"
        with self._get_connection() as conn:
            lock_acquired = self._acquire_bootstrap_advisory_lock(conn, lock_namespace)
            try:
                if self.schema:
                    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_pg(self.schema)}")
                conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.table} (
                        append_id   BIGSERIAL PRIMARY KEY,
                        record_id   TEXT NOT NULL,
                        record_type TEXT NOT NULL,
                        payload     JSONB NOT NULL,
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                        UNIQUE (record_type, record_id)
                    )
                    """
                )

                # Stored scalar projections avoid repeatedly detoasting the payload in
                # GROUP BY. PostgreSQL maintains them on every insert/upsert, including
                # writes from independent owner processes; no cache invalidation path.
                # A connector confidence is a score, not a verification: "verified"
                # requires a recorded verification with a method and a time
                # (BFF-EVIDENCE-DETAIL-AND-PAGING-20261125). Null confidence no
                # longer reads as verified.
                verified_expr = (
                    "CASE WHEN LENGTH(BTRIM(COALESCE(payload->'metadata'->'verification'->>'method', ''))) > 0 "
                    "AND LENGTH(BTRIM(COALESCE(payload->'metadata'->'verification'->>'verified_at', "
                    "payload->'metadata'->'verification'->>'time', ''))) > 0 THEN TRUE ELSE FALSE END"
                )
                marker = "verification-record-v2"
                existing_rows = conn.execute(
                    "SELECT col_description(a.attrelid, a.attnum) FROM pg_attribute a "
                    "WHERE a.attrelid = %s::regclass AND a.attname = 'evidence_verified' AND NOT a.attisdropped",
                    (self.table,),
                ).fetchall()
                existing = existing_rows[0] if existing_rows else None
                rewrite_columns = existing is not None and (
                    existing[0] if isinstance(existing, tuple) else list(existing.values())[0]
                ) != marker
                # Bound the wait for ACCESS EXCLUSIVE so a busy old container cannot hang
                # service start; the one-time rewrite below is a single atomic ALTER
                # (drop + re-add in one statement), so an old container reading during
                # the switch sees either the old or the new column, never none.
                conn.execute("SET lock_timeout = '15s'")
                entity_values = " || ".join([
                    "jsonb_path_query_array(document, '$.metadata.linked_entity_type')",
                    "jsonb_path_query_array(document, '$.metadata.linked_object_summary.entity_type')",
                    *[
                        f"CASE WHEN jsonb_typeof(document->'metadata'->'{key}') = 'array' "
                        f"THEN jsonb_path_query_array(document, '$.metadata.{key}[*].entity_type') ELSE '[]'::jsonb END"
                        for key in ("linked_decisions", "linked_entities", "related_entities")
                    ],
                ])
                link_types_fn = _quote_pg(f"{self.schema or 'public'}.source_evidence_link_types")
                # SQL functions allow the immutable array reduction a generated
                # column cannot express with a subquery. Normalize at write time so
                # overlap reads use GIN without parsing/detoasting any payload.
                conn.execute(
                    f"CREATE OR REPLACE FUNCTION {link_types_fn}(document JSONB) RETURNS TEXT[] "
                    "LANGUAGE SQL IMMUTABLE PARALLEL SAFE AS $$ "
                    "SELECT COALESCE(array_agg(DISTINCT LOWER(BTRIM(value))) FILTER (WHERE value IS NOT NULL), ARRAY[]::text[]) "
                    f"FROM jsonb_array_elements_text({entity_values}) links(value) $$"
                )
                projections = {
                    "evidence_tenant_id": ("TEXT", "payload->'metadata'->>'tenant_id'"),
                    "evidence_capture_time": ("TEXT", "COALESCE(NULLIF(payload->>'available_time', ''), NULLIF(payload->>'event_time', ''))"),
                    "evidence_linked_types": ("TEXT[]", f"{link_types_fn}(payload)"),
                    "evidence_source_type": ("TEXT", "COALESCE(NULLIF(BTRIM(payload->'metadata'->>'source_class'), ''), NULLIF(BTRIM(payload->>'item_type'), ''), 'internal')"),
                    "evidence_link_type": ("TEXT", "COALESCE(NULLIF(BTRIM(payload->'metadata'->>'link_type'), ''), 'supporting')"),
                    "evidence_credibility_tier": ("TEXT", f"COALESCE(NULLIF(BTRIM(payload->'metadata'->>'credibility_tier'), ''), CASE WHEN ({verified_expr}) THEN 'verified' ELSE 'unverified' END)"),
                    "evidence_verified": ("BOOLEAN", verified_expr),
                }
                rewritten = ("evidence_verified", "evidence_credibility_tier")
                alterations = ", ".join(
                    ("DROP COLUMN " + name + ", ADD COLUMN " if rewrite_columns and name in rewritten else "ADD COLUMN IF NOT EXISTS ")
                    + f"{name} {kind} GENERATED ALWAYS AS ({expr}) STORED"
                    for name, (kind, expr) in projections.items()
                )
                conn.execute(f"ALTER TABLE {self.table} {alterations}")
                conn.execute("RESET lock_timeout")
                conn.execute(f"COMMENT ON COLUMN {self.table}.evidence_verified IS '{marker}'")
                # Paging selects ids from this index only (no payload read for rows
                # skipped by the offset); size/build time recorded in evidence.
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS source_evidence_page_idx ON {self.table} "
                    "(evidence_tenant_id, append_id DESC) WHERE record_type = 'evidence_item'"
                )
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS source_evidence_summary_idx ON {self.table} "
                    "(evidence_tenant_id, evidence_source_type, evidence_link_type, "
                    "evidence_credibility_tier, evidence_verified) WHERE record_type = 'evidence_item'"
                )
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS source_evidence_linked_types_idx ON {self.table} "
                    "USING GIN (evidence_linked_types) WHERE record_type = 'evidence_item'"
                )
                self._ensure_concurrent_lookup_indexes(conn)
            finally:
                if lock_acquired:
                    try:
                        conn.execute(
                            "SELECT pg_advisory_unlock(hashtext(%s), hashtext(%s))",
                            (lock_namespace, self.table),
                        )
                    except Exception:
                        pass

    def _ensure_concurrent_lookup_indexes(self, conn: Any) -> None:
        """Create or repair non-blocking concurrent expression lookup indexes.

        CREATE INDEX CONCURRENTLY takes SHARE UPDATE EXCLUSIVE instead of SHARE
        or ACCESS EXCLUSIVE so active service writers are not blocked while
        building on the ~203 MB hosted table. If an earlier concurrent build
        was interrupted (process killed or connection lost), PostgreSQL leaves
        an index entry with indisvalid = false (or indisready = false) that
        CREATE INDEX CONCURRENTLY IF NOT EXISTS would otherwise skip on later
        starts and that the planner never uses. Check pg_index.indisvalid and
        pg_index.indisready first; if an index exists but is not valid/ready,
        drop it with DROP INDEX CONCURRENTLY IF EXISTS and rebuild it.
        """
        index_names = tuple(name for name, _ in _CONCURRENT_LOOKUP_INDEX_SPECS)
        rows = conn.execute(
            """
            SELECT c.relname, i.indisvalid, i.indisready
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = COALESCE(NULLIF(%s, ''), current_schema())
              AND c.relname = ANY(%s)
            """,
            (self.schema, list(index_names)),
        ).fetchall()
        index_states: Dict[str, tuple[bool, bool]] = {}
        for row in rows or ():
            if isinstance(row, dict):
                name = row.get("relname")
                valid = row.get("indisvalid")
                ready = row.get("indisready")
            elif isinstance(row, (tuple, list)) and len(row) == 3:
                name, valid, ready = row[0], row[1], row[2]
            else:
                continue
            if isinstance(name, str) and isinstance(valid, bool) and isinstance(ready, bool):
                index_states[name] = (valid, ready)

        for index_name, index_expr in _CONCURRENT_LOOKUP_INDEX_SPECS:
            qualified_index = (
                _quote_pg(f"{self.schema}.{index_name}")
                if self.schema
                else _quote_pg(index_name)
            )
            state = index_states.get(index_name)
            if state is not None:
                is_valid, is_ready = state
                if not (is_valid and is_ready):
                    logger.warning(
                        "Lookup index %s on %s is invalid or not ready (indisvalid=%s, indisready=%s); "
                        "dropping with DROP INDEX CONCURRENTLY IF EXISTS and rebuilding",
                        index_name,
                        self.table,
                        is_valid,
                        is_ready,
                    )
                    conn.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {qualified_index}")
                    logger.info(
                        "Rebuilding lookup index %s on %s with CREATE INDEX CONCURRENTLY IF NOT EXISTS",
                        index_name,
                        self.table,
                    )
                    conn.execute(
                        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} ON {self.table} {index_expr}"
                    )
                    continue
                logger.info(
                    "Lookup index %s on %s is already valid and ready (indisvalid=%s, indisready=%s)",
                    index_name,
                    self.table,
                    is_valid,
                    is_ready,
                )
                continue
            logger.info(
                "Creating lookup index %s on %s with CREATE INDEX CONCURRENTLY IF NOT EXISTS",
                index_name,
                self.table,
            )
            conn.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} ON {self.table} {index_expr}"
            )

    def reload(self) -> None:
        self.clear()
        self._replaying = True
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    f"SELECT record_type, payload FROM {self.table} ORDER BY append_id ASC"
                )
                rows = cursor.fetchall()
            records_by_type: Dict[str, list[Any]] = {
                record_type: [] for record_type in _EVIDENCE_REPLAY_ORDER
            }
            for row in rows:
                record_type = row[0] if isinstance(row, tuple) else row.get("record_type")
                payload = row[1] if isinstance(row, tuple) else row.get("payload")
                if isinstance(payload, str):
                    payload = json.loads(payload)
                normalized_type = str(record_type or "")
                loader = _EVIDENCE_LOADERS.get(normalized_type)
                if loader is None or not isinstance(payload, dict):
                    raise EvidenceValidationError(
                        f"Unsupported source evidence record type from Postgres: {record_type!r}"
                    )
                records_by_type[normalized_type].append(loader(payload))
            for record_type in _EVIDENCE_REPLAY_ORDER:
                for obj in records_by_type[record_type]:
                    if isinstance(obj, SourceRecord):
                        InMemoryEvidenceRepository.add_source_record(self, obj)
                    elif isinstance(obj, EvidenceItem):
                        InMemoryEvidenceRepository.add_evidence_item(self, obj)
                    elif isinstance(obj, EvidenceBundle):
                        InMemoryEvidenceRepository.add_bundle(self, obj)
                    elif isinstance(obj, KnowledgeObject):
                        InMemoryEvidenceRepository.add_knowledge_object(self, obj)
        finally:
            self._replaying = False

    def _upsert(self, record_type: str, record_id: str, payload: Dict[str, Any], tenant_id: str | None = None) -> None:
        scoped_id = _scoped_record_id(tenant_id, record_id)
        with self._get_connection() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table} (record_id, record_type, payload)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (record_type, record_id)
                DO UPDATE SET payload = EXCLUDED.payload
                """,
                (
                    scoped_id,
                    record_type,
                    json.dumps(payload, ensure_ascii=True, sort_keys=True),
                ),
            )

    def _rollback_source_record(self, source: SourceRecord) -> None:
        self._source_records.pop(source.source_id, None)
        self._source_records_by_tenant.pop((source.tenant_id, source.source_id), None)
        dedupe_key = source.metadata.get("source_dedupe_key") if isinstance(source.metadata, dict) else None
        if dedupe_key:
            self._source_dedupe_index.pop(str(dedupe_key), None)
            self._source_dedupe_by_tenant.pop((source.tenant_id, str(dedupe_key)), None)

    def _rollback_evidence_item(self, item: EvidenceItem) -> None:
        self._evidence_items.pop(item.evidence_item_id, None)
        self._evidence_items_by_tenant.pop((item.tenant_id, item.evidence_item_id), None)
        dedupe_key = item.metadata.get("evidence_dedupe_key") if isinstance(item.metadata, dict) else None
        if dedupe_key:
            self._evidence_dedupe_index.pop(str(dedupe_key), None)
            self._evidence_dedupe_by_tenant.pop((item.tenant_id, str(dedupe_key)), None)

    def _rollback_bundle(self, bundle: EvidenceBundle) -> None:
        self._bundles.pop(bundle.evidence_bundle_id, None)
        self._bundles_by_tenant.pop((bundle.tenant_id, bundle.evidence_bundle_id), None)

    def _rollback_knowledge_object(self, knowledge_object: KnowledgeObject) -> None:
        self._knowledge_objects.pop(knowledge_object.knowledge_object_id, None)
        self._knowledge_objects_by_tenant.pop((knowledge_object.tenant_id, knowledge_object.knowledge_object_id), None)

    def add_source_record(self, source: SourceRecord) -> SourceRecord:
        stored = super().add_source_record(source)
        if stored.source_id == source.source_id:
            try:
                self._upsert("source_record", stored.source_id, stored.to_dict(), tenant_id=stored.tenant_id)
            except Exception:
                self._rollback_source_record(stored)
                raise
        return stored

    def add_evidence_item(self, item: EvidenceItem) -> EvidenceItem:
        stored = super().add_evidence_item(item)
        if stored.evidence_item_id == item.evidence_item_id:
            try:
                self._upsert("evidence_item", stored.evidence_item_id, stored.to_dict(), tenant_id=stored.tenant_id)
            except Exception:
                self._rollback_evidence_item(stored)
                raise
        return stored

    def add_bundle(self, bundle: EvidenceBundle) -> EvidenceBundle:
        stored = super().add_bundle(bundle)
        try:
            self._upsert("evidence_bundle", stored.evidence_bundle_id, stored.to_dict(), tenant_id=stored.tenant_id)
        except Exception:
            self._rollback_bundle(stored)
            raise
        return stored

    def add_knowledge_object(self, knowledge_object: KnowledgeObject) -> KnowledgeObject:
        stored = super().add_knowledge_object(knowledge_object)
        try:
            self._upsert("knowledge_object", stored.knowledge_object_id, stored.to_dict(), tenant_id=stored.tenant_id)
        except Exception:
            self._rollback_knowledge_object(stored)
            raise
        return stored

    def _fetch_records(self, sql: str, params: tuple[Any, ...], r_type: str, tenant_id: str | None | _Unscoped = _UNSCOPED) -> list[Any]:
        try:
            with self._get_connection() as conn:
                rows = conn.execute(sql, params).fetchall()
        except Exception as exc:
            raise RuntimeError(f"PostgreSQL evidence query failed: {type(exc).__name__}") from None
        loader = _EVIDENCE_LOADERS.get(r_type)
        if not loader:
            return []
        results: list[Any] = []
        for row in rows:
            if isinstance(row, tuple) and len(row) > 1:
                if row[0] != r_type:
                    continue
                payload = row[1]
            else:
                payload = row[0] if isinstance(row, tuple) else (row.get("payload") if isinstance(row, dict) else row)
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                continue
            if tenant_id is not _UNSCOPED:
                t = payload.get("metadata", {}).get("tenant_id")
                if (tenant_id is None and t) or (tenant_id is not None and t != tenant_id):
                    continue
            results.append(loader(payload))
        return results

    def _read_one(self, r_type: str, r_id: str, tenant_id: str | None | _Unscoped = _UNSCOPED) -> Any | None:
        if tenant_id is not _UNSCOPED:
            sql, params = f"SELECT payload FROM {self.table} WHERE record_type = %s AND record_id = %s", (r_type, _scoped_record_id(tenant_id, r_id))
            records = self._fetch_records(sql, params, r_type, tenant_id=tenant_id)
            if not records and _scoped_record_id(tenant_id, r_id) != r_id:
                # A tenant item the list shows may be stored under the bare id; the
                # payload tenant claim is still re-checked by _fetch_records.
                sql, params = f"SELECT payload FROM {self.table} WHERE record_type = %s AND record_id = %s", (r_type, r_id)
                records = self._fetch_records(sql, params, r_type, tenant_id=tenant_id)
            k = "source_id" if r_type == "source_record" else f"{r_type}_id"
            return next((r for r in records if getattr(r, k, None) == r_id), records[0] if records else None)
        else:
            sql = (
                "SELECT payload FROM ("
                f"(SELECT append_id, payload FROM {self.table} WHERE record_type = %s AND record_id = %s ORDER BY append_id DESC LIMIT 1) "
                "UNION ALL "
                f"(SELECT append_id, payload FROM {self.table} WHERE record_type = %s AND {_OBJECT_ID_EXPR} = %s ORDER BY append_id DESC LIMIT 1)"
                ") AS matched ORDER BY append_id DESC LIMIT 1"
            )
            params = (r_type, r_id, r_type, r_id)
        records = self._fetch_records(sql, params, r_type, tenant_id=tenant_id)
        k = "source_id" if r_type == "source_record" else f"{r_type}_id"
        return next((r for r in records if getattr(r, k, None) == r_id), records[0] if records else None)

    def _read_many(self, r_type: str, tenant_id: str | None | _Unscoped = _UNSCOPED) -> list[Any]:
        if tenant_id is not _UNSCOPED:
            where = "payload->'metadata'->>'tenant_id' = %s" if tenant_id is not None else "(payload->'metadata'->>'tenant_id' IS NULL OR payload->'metadata'->>'tenant_id' = '')"
            params = (r_type, tenant_id) if tenant_id is not None else (r_type,)
            sql = f"SELECT payload FROM {self.table} WHERE record_type = %s AND {where} ORDER BY append_id ASC"
        else:
            sql, params = f"SELECT payload FROM {self.table} WHERE record_type = %s ORDER BY append_id ASC", (r_type,)
        return self._fetch_records(sql, params, r_type, tenant_id=tenant_id)

    def _read_by_dedupe(self, r_type: str, field: str, key: str, tenant_id: str | None | _Unscoped = _UNSCOPED) -> Any | None:
        if _DEDUPE_FIELDS_BY_RECORD_TYPE.get(r_type) == field:
            if tenant_id is not _UNSCOPED:
                sql = (
                    f"SELECT payload FROM {self.table} "
                    f"WHERE record_type = %s AND {_DEDUPE_KEY_EXPR} = %s AND {_TENANT_SCOPE_EXPR} = %s "
                    "ORDER BY append_id DESC LIMIT 1"
                )
                params: tuple[Any, ...] = (r_type, key, tenant_id or "")
            else:
                sql = (
                    f"SELECT payload FROM {self.table} "
                    f"WHERE record_type = %s AND {_DEDUPE_KEY_EXPR} = %s "
                    "ORDER BY append_id DESC LIMIT 1"
                )
                params = (r_type, key)
        elif tenant_id is not _UNSCOPED:
            where = "AND payload->'metadata'->>'tenant_id' = %s" if tenant_id is not None else "AND (payload->'metadata'->>'tenant_id' IS NULL OR payload->'metadata'->>'tenant_id' = '')"
            params = (r_type, field, key, tenant_id) if tenant_id is not None else (r_type, field, key)
            sql = f"SELECT payload FROM {self.table} WHERE record_type = %s AND payload->'metadata'->>%s = %s {where} ORDER BY append_id DESC LIMIT 1"
        else:
            sql, params = f"SELECT payload FROM {self.table} WHERE record_type = %s AND payload->'metadata'->>%s = %s ORDER BY append_id DESC LIMIT 1", (r_type, field, key)
        records = self._fetch_records(sql, params, r_type, tenant_id=tenant_id)
        return next((r for r in records if str(r.metadata.get(field) or "") == key), None)

    def get_source_record(self, source_id: str, tenant_id: str | None | _Unscoped = _UNSCOPED) -> SourceRecord | None:
        if getattr(self, "_replaying", False):
            return super().get_source_record(source_id, tenant_id=tenant_id)
        return self._read_one("source_record", source_id, tenant_id)

    def get_source_record_by_dedupe_key(self, source_dedupe_key: str, tenant_id: str | None | _Unscoped = _UNSCOPED) -> SourceRecord | None:
        if getattr(self, "_replaying", False):
            return super().get_source_record_by_dedupe_key(source_dedupe_key, tenant_id=tenant_id)
        return self._read_by_dedupe("source_record", "source_dedupe_key", source_dedupe_key, tenant_id)

    def list_source_records(self, tenant_id: str | None | _Unscoped = _UNSCOPED) -> List[SourceRecord]:
        return self._read_many("source_record", tenant_id)

    def list_source_records_page(
        self,
        tenant_id: str,
        *,
        connector_id: str,
        limit: int,
        before_append_id: int | None = None,
    ) -> tuple[List[SourceRecord], int | None]:
        """One tenant's one connector, newest append first, at most ``limit`` rows.

        Served by ``source_evidence_source_connector_page_idx``; the cursor is the
        last returned ``append_id`` (keyset, so page cost does not grow with depth).
        Returns ``(records, next_cursor)``; ``next_cursor`` is None on the last page.
        """
        tenant = str(tenant_id or "").strip()
        connector = str(connector_id or "").strip()
        if not tenant or tenant == "*":
            raise ValueError("an explicit non-wildcard tenant_id is required for source record pages")
        if not connector:
            raise ValueError("connector_id is required for source record pages")
        page = max(1, min(int(limit), SOURCE_RECORD_PAGE_MAX))
        where = f"record_type = 'source_record' AND evidence_tenant_id = %s AND {_SOURCE_CONNECTOR_EXPR} = %s"
        params: tuple[Any, ...] = (tenant, connector)
        if before_append_id is not None:
            where += " AND append_id < %s"
            params += (int(before_append_id),)
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    f"SELECT append_id, payload FROM {self.table} WHERE {where} "
                    "ORDER BY append_id DESC LIMIT %s",
                    (*params, page + 1),
                ).fetchall()
        except Exception as exc:
            raise RuntimeError(f"PostgreSQL evidence query failed: {type(exc).__name__}") from None
        records: list[SourceRecord] = []
        last_append_id: int | None = None
        for row in rows[:page]:
            append_id = row[0] if isinstance(row, (tuple, list)) else row.get("append_id")
            payload = row[1] if isinstance(row, (tuple, list)) else row.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                raise EvidenceValidationError("Source evidence owner returned a malformed source record")
            source = SourceRecord.from_dict(payload)
            if source.tenant_id != tenant or source.connector_id != connector:
                raise EvidenceValidationError("Source evidence owner returned a record outside the requested scope")
            records.append(source)
            last_append_id = int(append_id)
        return records, (last_append_id if len(rows) > page else None)

    def get_evidence_item(self, evidence_item_id: str, tenant_id: str | None | _Unscoped = _UNSCOPED) -> EvidenceItem | None:
        if getattr(self, "_replaying", False):
            return super().get_evidence_item(evidence_item_id, tenant_id=tenant_id)
        return self._read_one("evidence_item", evidence_item_id, tenant_id)

    def get_evidence_item_by_dedupe_key(self, evidence_dedupe_key: str, tenant_id: str | None | _Unscoped = _UNSCOPED) -> EvidenceItem | None:
        if getattr(self, "_replaying", False):
            return super().get_evidence_item_by_dedupe_key(evidence_dedupe_key, tenant_id=tenant_id)
        return self._read_by_dedupe("evidence_item", "evidence_dedupe_key", evidence_dedupe_key, tenant_id)

    def list_evidence_items(self, tenant_id: str | None | _Unscoped = _UNSCOPED) -> List[EvidenceItem]:
        return self._read_many("evidence_item", tenant_id)

    def list_evidence_items_page(
        self,
        tenant_id: str | None | _Unscoped = _UNSCOPED,
        *,
        limit: int,
        offset: int = 0,
        captured_from: str | None = None,
        captured_before: str | None = None,
        linked_entity_types: tuple[str, ...] | None = None,
    ) -> tuple[List[EvidenceItem], int]:
        if getattr(self, "_replaying", False):
            return super().list_evidence_items_page(
                tenant_id=tenant_id, limit=limit, offset=offset,
                captured_from=captured_from, captured_before=captured_before,
                linked_entity_types=linked_entity_types,
            )
        if tenant_id is not _UNSCOPED:
            where = "evidence_tenant_id = %s" if tenant_id is not None else "(evidence_tenant_id IS NULL OR evidence_tenant_id = '')"
            scope_params: tuple[Any, ...] = (tenant_id,) if tenant_id is not None else ()
        else:
            where, scope_params = "TRUE", ()
        if linked_entity_types is not None:
            # Array overlap counts/paginates each item once even with many links.
            where += " AND evidence_linked_types && %s::text[]"
            scope_params += (list(linked_entity_types),)
        windowed = captured_from is not None or captured_before is not None
        if windowed:
            try:
                return self._list_evidence_items_window(
                    where, scope_params, tenant_id, limit, offset, captured_from, captured_before, linked_entity_types=linked_entity_types, sql_cast=True
                )
            except RuntimeError:
                # A stored timestamp the SQL cast rejects must not fail the whole
                # read: filter the same population in Python with identical rules.
                return self._list_evidence_items_window(
                    where, scope_params, tenant_id, limit, offset, captured_from, captured_before, linked_entity_types=linked_entity_types, sql_cast=False
                )
        # The heavy metadata keys are dropped in SQL so they never leave the DB.
        stripped = "payload" + "".join(f" #- '{{metadata,{k}}}'" for k in EVIDENCE_LIST_OMITTED_METADATA_KEYS)
        base = f"FROM {self.table} WHERE record_type = %s AND {where}"
        try:
            with self._get_connection() as conn:
                count_row = conn.execute(f"SELECT COUNT(*) {base}", ("evidence_item", *scope_params)).fetchone()
        except Exception as exc:
            raise RuntimeError(f"PostgreSQL evidence query failed: {type(exc).__name__}") from None
        total = int((count_row[0] if isinstance(count_row, tuple) else list(count_row.values())[0]) if count_row else 0)
        # Ids first (index-only walk), payloads only for the page: the heap and
        # TOAST cost no longer grows with the offset.
        items = self._fetch_records(
            f"SELECT {stripped} FROM {self.table} t JOIN ("
            f"SELECT append_id {base} ORDER BY append_id DESC LIMIT %s OFFSET %s"
            ") page USING (append_id) ORDER BY t.append_id DESC",
            ("evidence_item", *scope_params, int(limit), int(offset)),
            "evidence_item",
            tenant_id=tenant_id,
        )
        return items, total

    def _list_evidence_items_window(
        self,
        where: str,
        scope_params: tuple[Any, ...],
        tenant_id: str | None | _Unscoped,
        limit: int,
        offset: int,
        captured_from: str | None,
        captured_before: str | None,
        *,
        sql_cast: bool,
        linked_entity_types: tuple[str, ...] | None = None,
    ) -> tuple[List[EvidenceItem], int]:
        """Page the evidence items captured in ``[captured_from, captured_before)``.

        Capture time is ``available_time`` then ``event_time`` (the BFF rule);
        rows with no parsable time never match. ``sql_cast`` filters in SQL
        behind a strict ISO guard; otherwise the tenant population is filtered
        in Python with the shared repository predicate.
        """
        if not sql_cast:
            items = [
                i for i in self._read_many("evidence_item", tenant_id)
                if evidence_item_in_capture_window(i, captured_from, captured_before)
                and (linked_entity_types is None or evidence_item_linked_entity_types(i) & set(linked_entity_types))
            ]
            window = list(reversed(items))[int(offset): int(offset) + int(limit)]
            return [strip_list_heavy_metadata(i) for i in window], len(items)
        # No BTRIM: legacy selection only falls back on an absent/empty value, so a
        # blank or padded string stays "odd" and is judged by the Python predicate.
        raw_ts = "evidence_capture_time"
        # Strict: hours 00-23, a zone only after a time, <=6 fraction digits. Anything
        # else (date+zone, 24:00, compact forms, ...) goes to the Python predicate.
        iso_guard = (
            r"'^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
            r"(([T ]([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9]([.][0-9]{1,6})?)?(Z|[+-][0-9]{2}:[0-9]{2})?)?)$'"
        )
        has_zone = r"'(Z|[+-][0-9]{2}:[0-9]{2})$'"
        captured = (
            f"(CASE WHEN {raw_ts} ~ {iso_guard} THEN "
            f"(CASE WHEN {raw_ts} ~ {has_zone} THEN REPLACE({raw_ts}, 'Z', '+00:00')::timestamptz "
            f"ELSE ({raw_ts})::timestamp AT TIME ZONE 'UTC' END) END)"
        )
        # Stored timestamps the strict ISO guard does not recognise (compact
        # dates, ``+0000`` offsets, ...) still parse under the legacy
        # ``datetime.fromisoformat`` rule. Those rare rows are judged in Python
        # with the shared predicate and joined into the SQL window by id.
        bounds: list[Any] = []
        for value in (captured_from, captured_before):
            parsed = _capture_datetime(value) if value is not None else None
            if value is not None and parsed is None:
                return [], 0
            bounds.append(parsed)
        try:
            with self._get_connection() as conn:
                odd_rows = conn.execute(
                    f"SELECT append_id, {raw_ts} FROM {self.table} "
                    f"WHERE record_type = %s AND {where} AND {raw_ts} IS NOT NULL AND NOT ({raw_ts} ~ {iso_guard})",
                    ("evidence_item", *scope_params),
                ).fetchall()
        except Exception as exc:
            raise RuntimeError(f"PostgreSQL evidence query failed: {type(exc).__name__}") from None
        odd_ids: list[int] = []
        for row in odd_rows:
            append_id, raw = (row[0], row[1]) if isinstance(row, tuple) else list(row.values())[:2]
            moment = _capture_datetime(raw)
            if moment is None:
                continue
            if (bounds[0] is None or bounds[0] <= moment) and (bounds[1] is None or moment < bounds[1]):
                odd_ids.append(append_id)
        in_window = [f"{captured} IS NOT NULL"]
        params: list[Any] = []
        for parsed, op in ((bounds[0], ">="), (bounds[1], "<")):
            if parsed is None:
                continue
            in_window.append(f"{captured} {op} %s")
            params.append(parsed)
        clause = "(" + " AND ".join(in_window) + ")"
        if odd_ids:
            clause = f"({clause} OR append_id = ANY(%s))"
            params.append(odd_ids)
        clauses = [clause]
        stripped = "payload" + "".join(f" #- '{{metadata,{k}}}'" for k in EVIDENCE_LIST_OMITTED_METADATA_KEYS)
        base = f"FROM {self.table} WHERE record_type = %s AND {where} AND " + " AND ".join(clauses)
        base_params = ("evidence_item", *scope_params, *params)
        try:
            with self._get_connection() as conn:
                count_row = conn.execute(f"SELECT COUNT(*) {base}", base_params).fetchone()
        except Exception as exc:
            raise RuntimeError(f"PostgreSQL evidence query failed: {type(exc).__name__}") from None
        total = int((count_row[0] if isinstance(count_row, tuple) else list(count_row.values())[0]) if count_row else 0)
        items = self._fetch_records(
            f"SELECT {stripped} {base} ORDER BY append_id DESC LIMIT %s OFFSET %s",
            (*base_params, int(limit), int(offset)),
            "evidence_item",
            tenant_id=tenant_id,
        )
        return items, total

    def summarize_evidence_items(
        self,
        tenant_id: str | None | _Unscoped = _UNSCOPED,
    ) -> dict[str, Any]:
        if getattr(self, "_replaying", False):
            return super().summarize_evidence_items(tenant_id=tenant_id)
        if tenant_id is not _UNSCOPED:
            where = "evidence_tenant_id = %s" if tenant_id is not None else "(evidence_tenant_id IS NULL OR evidence_tenant_id = '')"
            scope_params: tuple[Any, ...] = (tenant_id,) if tenant_id is not None else ()
        else:
            where, scope_params = "TRUE", ()
        sql = (
            "SELECT evidence_source_type AS source_type, evidence_link_type AS link_type, evidence_credibility_tier AS credibility_tier, "
            "COUNT(*) AS row_count "
            f"FROM {self.table} WHERE record_type = %s AND {where} "
            "GROUP BY 1, 2, 3"
        )
        try:
            with self._get_connection() as conn:
                rows = conn.execute(sql, ("evidence_item", *scope_params)).fetchall()
        except Exception as exc:
            raise RuntimeError(f"PostgreSQL evidence query failed: {type(exc).__name__}") from None
        total = 0
        by_source_type: dict[str, int] = {}
        by_link_type: dict[str, int] = {}
        by_credibility_tier: dict[str, int] = {}
        for row in rows or ():
            if isinstance(row, dict):
                source_type = str(row.get("source_type") or "internal").strip() or "unknown"
                link_type = str(row.get("link_type") or "supporting").strip() or "unknown"
                credibility_tier = str(row.get("credibility_tier") or "unverified").strip() or "unknown"
                count = int(row.get("row_count") or 0)
            else:
                source_type = str(row[0] or "internal").strip() or "unknown"
                link_type = str(row[1] or "supporting").strip() or "unknown"
                credibility_tier = str(row[2] or "unverified").strip() or "unknown"
                count = int(row[3] or 0)
            total += count
            by_source_type[source_type] = by_source_type.get(source_type, 0) + count
            by_link_type[link_type] = by_link_type.get(link_type, 0) + count
            by_credibility_tier[credibility_tier] = by_credibility_tier.get(credibility_tier, 0) + count
        return {
            "total": total,
            "by_source_type": by_source_type,
            "by_link_type": by_link_type,
            "by_credibility_tier": by_credibility_tier,
        }

    def get_bundle(self, evidence_bundle_id: str, tenant_id: str | None | _Unscoped = _UNSCOPED) -> EvidenceBundle | None:
        if getattr(self, "_replaying", False):
            return super().get_bundle(evidence_bundle_id, tenant_id=tenant_id)
        return self._read_one("evidence_bundle", evidence_bundle_id, tenant_id)

    def list_bundles(self, tenant_id: str | None | _Unscoped = _UNSCOPED) -> List[EvidenceBundle]:
        return self._read_many("evidence_bundle", tenant_id)

    def get_knowledge_object(self, knowledge_object_id: str, tenant_id: str | None | _Unscoped = _UNSCOPED) -> KnowledgeObject | None:
        if getattr(self, "_replaying", False):
            return super().get_knowledge_object(knowledge_object_id, tenant_id=tenant_id)
        return self._read_one("knowledge_object", knowledge_object_id, tenant_id)

    def list_knowledge_objects(self, tenant_id: str | None | _Unscoped = _UNSCOPED) -> List[KnowledgeObject]:
        return self._read_many("knowledge_object", tenant_id)


def read_source_records_page(
    repository: Any,
    tenant_id: str,
    *,
    connector_id: str,
    limit: int,
    cursor: int | None = None,
) -> tuple[List[SourceRecord], int | None]:
    """Connector-scoped newest-first page from any source evidence owner.

    PostgreSQL owners answer from the connector page index with an ``append_id``
    keyset cursor. In-memory/JSONL owners (local and test configurations) page
    the tenant's records in reverse insertion order with the same contract; there
    the cursor is the count of newest records already returned.
    """
    page = max(1, min(int(limit), SOURCE_RECORD_PAGE_MAX))
    if isinstance(repository, PostgresSourceEvidenceRepository):
        return repository.list_source_records_page(
            tenant_id, connector_id=connector_id, limit=page, before_append_id=cursor
        )
    tenant = str(tenant_id or "").strip()
    connector = str(connector_id or "").strip()
    if not tenant or tenant == "*":
        raise ValueError("an explicit non-wildcard tenant_id is required for source record pages")
    if not connector:
        raise ValueError("connector_id is required for source record pages")
    matching = [
        source for source in reversed(repository.list_source_records(tenant_id=tenant))
        if source.connector_id == connector
    ]
    start = max(0, int(cursor or 0))
    end = start + page
    return matching[start:end], (end if end < len(matching) else None)


def read_source_records_for_tenant(
    *,
    jsonl_path: Path,
    tenant_id: str,
    limit: int | None = None,
    already_admitted: Callable[[SourceRecord], bool] | None = None,
    summary: dict[str, Any] | None = None,
) -> list[SourceRecord]:
    """Read normalized records for one tenant without taking source write ownership.

    Postgres reads query the owner table directly in an explicitly read-only
    transaction and constrain rows by their persisted tenant claim. JSONL is
    retained for local/test configurations, but the caller still supplies an
    explicit tenant and the repository's tenant index enforces that scope.
    With ``limit``, SQL filters eligibility and each payload query is limited to
    that batch size. ``already_admitted`` uses the existing durable version
    receipts; ``summary`` receives a scalar eligible count from the same snapshot.
    Without ``limit`` the original read API remains available to other callers.
    """
    tenant = str(tenant_id or "").strip()
    if not tenant or tenant == "*":
        raise ValueError("an explicit non-wildcard tenant_id is required for source evidence reads")

    if limit is not None and limit < 1:
        raise ValueError("source read limit must be positive")

    def eligible(source: SourceRecord) -> bool:
        return source.status == SourceRecordStatus.NORMALIZED and not (
            source.source_type == SourceType.MARKET and source.metadata.get("collector_market_bundle")
        )

    backend = os.getenv("SOURCE_INGEST_EVIDENCE_BACKEND", "jsonl").strip().lower()
    if backend in ("", "jsonl"):
        records = JsonlEvidenceRepository(jsonl_path).list_source_records(tenant_id=tenant)
        if limit is None:
            return records
        candidates = [source for source in records if eligible(source)]
        selected = []
        for source in candidates:
            if already_admitted is None or not already_admitted(source):
                selected.append(source)
                if len(selected) == limit:
                    break
        if summary is not None:
            summary.update(eligible_source_count=len(candidates), source_read_limit=limit,
                           source_summary_origin="source-ingest.tenant_eligible_records_jsonl")
        return selected
    if backend != "postgres":
        raise ValueError("SOURCE_INGEST_EVIDENCE_BACKEND must be jsonl or postgres")

    dsn = os.getenv("SOURCE_INGEST_EVIDENCE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("SOURCE_INGEST_EVIDENCE_DSN or DATABASE_URL is required for Postgres evidence reads")
    table = _quote_pg(os.getenv("SOURCE_INGEST_EVIDENCE_TABLE", "source_ingest.source_evidence"))
    try:
        import psycopg  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("psycopg is required when SOURCE_INGEST_EVIDENCE_BACKEND=postgres") from exc

    # No schema or writes belong to this consumer. A bounded catch-up read
    # pages eligible rows by append order. Known versions do not consume the
    # admission budget; a revised payload at an old append_id is seen next tick.
    records: list[SourceRecord] = []
    where = "record_type = %s AND payload->'metadata'->>'tenant_id' = %s"
    params: tuple[Any, ...] = ("source_record", tenant)
    if limit is not None:
        # Match SourceRecord's missing-status default and Python truthiness for
        # the collector marker, including legacy boolean/container values.
        where += (
            " AND COALESCE(payload->>'status', 'normalized') = 'normalized'"
            " AND NOT (payload->>'source_type' = 'market' AND "
            "COALESCE(payload->'metadata'->'collector_market_bundle', 'null'::jsonb) "
            "NOT IN ('null'::jsonb, 'false'::jsonb, '0'::jsonb, "
            "'\"\"'::jsonb, '[]'::jsonb, '{}'::jsonb))"
        )
    try:
        with psycopg.connect(dsn) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            if limit is not None and summary is not None:
                count_row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()
                total = count_row[0] if isinstance(count_row, tuple) else next(iter(count_row.values()))
                summary.update(eligible_source_count=int(total), source_read_limit=limit,
                               source_summary_origin="source-ingest.postgres.tenant_eligible_count")
            if limit is not None and hasattr(conn, "cursor"):
                # One read-only server-side cursor in the same snapshot replaces
                # a Limit/Sort/Seq Scan per keyset page; still bounded to 100.
                with conn.cursor(name="distillation_eligible_sources") as cur:
                    cur.execute(
                        f"SELECT payload, append_id FROM {table} WHERE {where} ORDER BY append_id ASC",
                        params,
                    )
                    while True:
                        rows = cur.fetchmany(limit)
                        if not rows:
                            break
                        for row in rows:
                            payload = row[0] if isinstance(row, tuple) else row.get("payload")
                            if isinstance(payload, str):
                                payload = json.loads(payload)
                            if not isinstance(payload, dict):
                                raise EvidenceValidationError("Source evidence owner returned a malformed source record")
                            source = SourceRecord.from_dict(payload)
                            if source.tenant_id != tenant:
                                raise EvidenceValidationError("Source evidence owner returned a record outside the requested tenant")
                            if eligible(source) and (
                                already_admitted is None or not already_admitted(source)
                            ):
                                records.append(source)
                                if len(records) == limit:
                                    return records
                        del rows
                return records
            after = -1
            while True:
                if limit is None:
                    rows = conn.execute(
                        f"SELECT payload FROM {table} WHERE {where} ORDER BY append_id ASC", params,
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"SELECT payload, append_id FROM {table} WHERE {where} "
                        "AND append_id > %s ORDER BY append_id ASC LIMIT %s",
                        (*params, after, limit),
                    ).fetchall()
                if not rows:
                    break
                for row in rows:
                    payload = row[0] if isinstance(row, tuple) else row.get("payload")
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    if not isinstance(payload, dict):
                        raise EvidenceValidationError("Source evidence owner returned a malformed source record")
                    source = SourceRecord.from_dict(payload)
                    if source.tenant_id != tenant:
                        raise EvidenceValidationError("Source evidence owner returned a record outside the requested tenant")
                    if limit is None or (eligible(source) and (
                        already_admitted is None or not already_admitted(source)
                    )):
                        records.append(source)
                        if limit is not None and len(records) == limit:
                            return records
                if limit is None:
                    break
                last = rows[-1]
                after = int(last[1] if isinstance(last, tuple) else last["append_id"])
                # Release this payload batch before fetching another.
                del rows, row, last, payload, source
    except EvidenceValidationError:
        raise
    except Exception as exc:
        raise RuntimeError(f"PostgreSQL evidence query failed: {type(exc).__name__}") from None
    return records


def build_source_evidence_repository(
    jsonl_path: Path,
) -> InMemoryEvidenceRepository:
    """Factory: returns JsonlEvidenceRepository unless SOURCE_INGEST_EVIDENCE_BACKEND=postgres."""
    backend = os.getenv("SOURCE_INGEST_EVIDENCE_BACKEND", "jsonl").strip().lower()
    if backend in ("", "jsonl"):
        return JsonlEvidenceRepository(jsonl_path)
    if backend != "postgres":
        raise ValueError("SOURCE_INGEST_EVIDENCE_BACKEND must be jsonl or postgres")
    dsn = os.getenv("SOURCE_INGEST_EVIDENCE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError(
            "SOURCE_INGEST_EVIDENCE_DSN or DATABASE_URL is required for Postgres evidence store"
        )
    table = os.getenv("SOURCE_INGEST_EVIDENCE_TABLE", "source_ingest.source_evidence")
    bootstrap = (
        os.getenv("SOURCE_INGEST_EVIDENCE_BOOTSTRAP", "1").strip().lower()
        not in ("0", "false", "no")
    )
    return PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=bootstrap)
