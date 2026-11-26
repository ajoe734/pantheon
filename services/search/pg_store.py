"""Optional Postgres-backed stores for search service.

Enabled by env; JSONL remains the default.  Search is the write owner
of search snapshots.  Source evidence is read-only for search — write
ownership stays with source-ingest.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from services.knowledge.evidence import (
    EvidenceBundle,
    EvidenceItem,
    InMemoryEvidenceRepository,
    JsonlEvidenceRepository,
    KnowledgeObject,
)
from services.knowledge.evidence.models import EvidenceValidationError
from services.knowledge.evidence.repository import _UNSCOPED
from services.source_ingestion.connectors.base import SourceRecord

from .index_store import (
    SNAPSHOT_MAX_AGE_SECONDS,
    SNAPSHOT_MAX_COUNT,
    JsonlSearchIndexStore,
    SearchIndexSnapshot,
    bound_snapshots,
)

_PAGE_SIZE = int(os.getenv("SEARCH_EVIDENCE_PAGE_SIZE", "2000") or 2000)
_KO_RESYNC_SECONDS = float(os.getenv("SEARCH_EVIDENCE_KO_RESYNC_SECONDS", "900") or 900)

_PG_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_EVIDENCE_LOADERS: Dict[str, Any] = {
    "source_record": SourceRecord.from_dict,
    "evidence_item": EvidenceItem.from_dict,
    "evidence_bundle": EvidenceBundle.from_dict,
    "knowledge_object": KnowledgeObject.from_dict,
}

# Source-ingest upserts preserve each row's original append_id. A stable
# knowledge object can therefore be updated to reference a bundle appended
# later. Preserve append order within a record type, but replay dependency
# types before their consumers so the read-only search view can restart from
# the same valid dataset as the source-ingest owner.
_EVIDENCE_REPLAY_ORDER = (
    "source_record",
    "evidence_item",
    "evidence_bundle",
    "knowledge_object",
)


_OBJECT_ID_EXPR = (
    "(CASE record_type "
    "WHEN 'source_record' THEN payload->>'source_id' "
    "WHEN 'evidence_item' THEN payload->>'evidence_item_id' "
    "WHEN 'evidence_bundle' THEN payload->>'evidence_bundle_id' "
    "WHEN 'knowledge_object' THEN payload->>'knowledge_object_id' "
    "END)"
)
# Same precedence the pipeline's _object_effective_time uses.
_KO_EFFECTIVE_TIME_EXPR = (
    "COALESCE(NULLIF(payload->'metadata'->>'updated_at', ''), NULLIF(payload->'metadata'->>'indexed_at', ''), "
    "NULLIF(payload->'metadata'->>'created_at', ''), NULLIF(payload->>'created_at', ''), '')"
)
_KO_NOT_COLLECTOR_BUNDLE = (
    "NOT (payload->>'source_type' = 'market' AND "
    "COALESCE(payload->'metadata'->>'collector_market_bundle', '') NOT IN ('', 'false', 'False', '0'))"
)
_STREAM_PAGE_SIZE = int(os.getenv("SEARCH_EVIDENCE_STREAM_PAGE_SIZE", "200") or 200)
_STREAM_LIST_CAP = int(os.getenv("SEARCH_EVIDENCE_LIST_CAP", "2000") or 2000)
_SUMMARY_TTL_SECONDS = float(os.getenv("SEARCH_EVIDENCE_SUMMARY_TTL_SECONDS", "30") or 30)


def _scoped_record_id(tenant_id: str | None, record_id: str) -> str:
    """Mirror of source-ingest's owner key so point reads hit the unique index."""
    if tenant_id is None:
        return f"@{record_id}" if record_id.startswith("@") else record_id
    return f"@t{len(tenant_id)}:{tenant_id}:{record_id}"


def _quote_pg(identifier: str) -> str:
    parts = identifier.split(".")
    if not parts or any(_PG_IDENTIFIER_RE.fullmatch(part) is None for part in parts):
        raise ValueError(f"Invalid Postgres identifier: {identifier}")
    return ".".join(f'"{part}"' for part in parts)


class PostgresReadOnlyEvidenceRepository(InMemoryEvidenceRepository):
    """Read-only Postgres view of source evidence for search.

    Source-ingest is the write owner.  This class never writes to the
    source_evidence table; it only reads on reload.
    """

    def __init__(
        self,
        dsn: str,
        table: str = "source_ingest.source_evidence",
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for PostgresReadOnlyEvidenceRepository")
        self.dsn = dsn
        self.table = _quote_pg(table)
        self.path = f"postgres:{table}"
        super().__init__()
        self._load_lock = threading.RLock()
        self._watermark = 0
        self._loaded_seq = 0
        self._last_ko_resync = time.monotonic()
        self.reload()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required when SEARCH_EVIDENCE_BACKEND=postgres"
            ) from exc
        return psycopg.connect(self.dsn)

    # -- loading ---------------------------------------------------------
    #
    # ``reload()`` is the explicit full resync (startup, admin reload, deletes).
    # ``refresh()`` is what request paths use: it only reads rows whose
    # append_id is above the watermark, concurrent callers share one load
    # (single-flight), and nothing here is called from /health or /status.
    # Rows are paged by append_id so no more than _PAGE_SIZE raw rows are ever
    # held next to the repository.

    def _fetch_page(self, conn: Any, record_type: str, after_append_id: int) -> list[tuple[int, Any]]:
        cursor = conn.execute(
            f"SELECT append_id, payload FROM {self.table} "
            "WHERE record_type = %s AND append_id > %s ORDER BY append_id ASC LIMIT %s",
            (record_type, after_append_id, _PAGE_SIZE),
        )
        page = []
        for row in cursor.fetchall():
            append_id = row[0] if isinstance(row, tuple) else row.get("append_id")
            payload = row[1] if isinstance(row, tuple) else row.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            page.append((int(append_id), payload))
        return page

    def _apply(self, record_type: str, payload: Any, target: InMemoryEvidenceRepository) -> None:
        loader = _EVIDENCE_LOADERS.get(record_type)
        if loader is None or not isinstance(payload, dict):
            raise EvidenceValidationError(
                f"Unsupported source evidence record type from Postgres: {record_type!r}"
            )
        obj = loader(payload)
        if isinstance(obj, SourceRecord):
            InMemoryEvidenceRepository.add_source_record(target, obj)
        elif isinstance(obj, EvidenceItem):
            InMemoryEvidenceRepository.add_evidence_item(target, obj)
        elif isinstance(obj, EvidenceBundle):
            InMemoryEvidenceRepository.add_bundle(target, obj)
        elif isinstance(obj, KnowledgeObject):
            InMemoryEvidenceRepository.add_knowledge_object(target, obj)

    def _load_types(
        self,
        target: InMemoryEvidenceRepository,
        record_types: tuple[str, ...],
        watermark: int,
    ) -> int:
        """Replay record_types in dependency order above watermark; return max append_id seen."""
        high = watermark
        with self._connect() as conn:
            for record_type in record_types:
                cursor_id = watermark
                while True:
                    page = self._fetch_page(conn, record_type, cursor_id)
                    for append_id, payload in page:
                        self._apply(record_type, payload, target)
                        cursor_id = append_id
                        high = max(high, append_id)
                    if len(page) < _PAGE_SIZE:
                        break
        return high

    def reload(self) -> None:
        """Full resync. Builds the new state aside, then swaps it in."""
        with self._load_lock:
            fresh = InMemoryEvidenceRepository()
            high = self._load_types(fresh, _EVIDENCE_REPLAY_ORDER, 0)
            self._adopt(fresh)
            self._watermark = high
            self._loaded_seq += 1
            self._last_ko_resync = time.monotonic()

    def _adopt(self, fresh: InMemoryEvidenceRepository) -> None:
        for name, value in vars(fresh).items():
            setattr(self, name, value)

    def refresh(self) -> None:
        """Incremental, single-flight load of rows appended since the last load."""
        seen_seq = self._loaded_seq
        with self._load_lock:
            if self._loaded_seq != seen_seq:
                # Another caller finished a load while we waited; share its result.
                return
            self._watermark = self._load_types(self, _EVIDENCE_REPLAY_ORDER, self._watermark)
            # Upserts keep their original append_id, so an updated knowledge object
            # is invisible to the watermark; re-read only that (small) type on a timer.
            if time.monotonic() - self._last_ko_resync >= _KO_RESYNC_SECONDS:
                self._load_types(self, ("knowledge_object",), 0)
                self._last_ko_resync = time.monotonic()
            self._loaded_seq += 1

    def add_source_record(self, source: SourceRecord) -> SourceRecord:
        raise EvidenceValidationError(
            "PostgresReadOnlyEvidenceRepository is read-only; source-ingest is the write owner"
        )

    def add_evidence_item(self, item: EvidenceItem) -> EvidenceItem:
        raise EvidenceValidationError(
            "PostgresReadOnlyEvidenceRepository is read-only; source-ingest is the write owner"
        )

    def add_bundle(self, bundle: EvidenceBundle) -> EvidenceBundle:
        raise EvidenceValidationError(
            "PostgresReadOnlyEvidenceRepository is read-only; source-ingest is the write owner"
        )

    def add_knowledge_object(self, knowledge_object: KnowledgeObject) -> KnowledgeObject:
        raise EvidenceValidationError(
            "PostgresReadOnlyEvidenceRepository is read-only; source-ingest is the write owner"
        )


class PostgresStreamingEvidenceRepository(PostgresReadOnlyEvidenceRepository):
    """Read-through evidence view whose memory does not depend on the table size.

    The full-load parent keeps every row in dicts (86.5k rows, ~290 MB of JSON, growing 5k-19k
    rows/day) and a drain doubles it. This class holds no rows at all: startup, ``reload`` and
    ``refresh`` are O(1); ``get_*`` are indexed point reads; the pipeline walks knowledge objects
    through ``fetch_knowledge_object_page`` (at most ``_STREAM_PAGE_SIZE`` rows alive); scalar
    summaries (count, watermarks, source types) are SQL aggregates cached for a short TTL.
    Memory bound: O(page size), never O(rows).
    """

    def __init__(self, dsn: str, table: str = "source_ingest.source_evidence") -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for PostgresStreamingEvidenceRepository")
        self.dsn = dsn
        self.table = _quote_pg(table)
        self.path = f"postgres:{table}"
        InMemoryEvidenceRepository.__init__(self)  # empty dicts that are never filled
        self._load_lock = threading.RLock()
        self._watermark = 0
        self._loaded_seq = 0
        self._last_ko_resync = time.monotonic()
        self._summary_cache: Dict[str, tuple[float, Any]] = {}

    # -- lifecycle: nothing to load -------------------------------------
    def reload(self) -> None:
        with self._load_lock:
            self._summary_cache.clear()
            self._loaded_seq += 1

    def refresh(self) -> None:
        with self._load_lock:
            self._loaded_seq += 1

    # -- point reads ----------------------------------------------------
    def _query(self, sql: str, params: tuple[Any, ...]) -> list[Any]:
        with self._connect() as conn:
            return list(conn.execute(sql, params).fetchall())

    @staticmethod
    def _payload_of(row: Any) -> Any:
        payload = row[-1] if isinstance(row, tuple) else (row.get("payload") if isinstance(row, dict) else row)
        return json.loads(payload) if isinstance(payload, str) else payload

    def _read_one(self, record_type: str, object_id: str, tenant_id: Any = _UNSCOPED) -> Any | None:
        if not object_id:
            return None
        if tenant_id is not _UNSCOPED:
            rows = self._query(
                f"SELECT payload FROM {self.table} WHERE record_type = %s AND record_id = %s",
                (record_type, _scoped_record_id(tenant_id, object_id)),
            )
        else:
            rows = self._query(
                f"SELECT payload FROM {self.table} WHERE record_type = %s AND {_OBJECT_ID_EXPR} = %s "
                "ORDER BY append_id DESC LIMIT 1",
                (record_type, object_id),
            )
        if not rows:
            return None
        payload = self._payload_of(rows[0])
        if not isinstance(payload, dict):
            return None
        obj = _EVIDENCE_LOADERS[record_type](payload)
        if tenant_id is not _UNSCOPED and getattr(obj, "tenant_id", None) != tenant_id:
            return None
        return obj

    def get_source_record(self, source_id: str, tenant_id: Any = _UNSCOPED) -> SourceRecord | None:
        return self._read_one("source_record", source_id, tenant_id)

    def get_evidence_item(self, evidence_item_id: str, tenant_id: Any = _UNSCOPED) -> EvidenceItem | None:
        return self._read_one("evidence_item", evidence_item_id, tenant_id)

    def get_bundle(self, evidence_bundle_id: str, tenant_id: Any = _UNSCOPED) -> EvidenceBundle | None:
        return self._read_one("evidence_bundle", evidence_bundle_id, tenant_id)

    def get_knowledge_object(self, knowledge_object_id: str, tenant_id: Any = _UNSCOPED) -> KnowledgeObject | None:
        return self._read_one("knowledge_object", knowledge_object_id, tenant_id)

    # -- bounded streaming ------------------------------------------------
    def fetch_knowledge_object_page(
        self,
        after_append_id: int,
        limit: int | None = None,
        *,
        max_append_id: int | None = None,
        touched_since: str | None = None,
    ) -> list[tuple[int, KnowledgeObject]]:
        """At most ``limit`` (<= page size) knowledge objects above ``after_append_id``, append order.

        ``touched_since`` is a conservative SQL prefilter (date prefix of the effective time) for
        objects that were updated in place; callers still apply the exact timestamp comparison.
        """
        size = max(1, min(int(limit or _STREAM_PAGE_SIZE), _STREAM_PAGE_SIZE))
        where = "record_type = 'knowledge_object' AND append_id > %s"
        params: list[Any] = [int(after_append_id)]
        if max_append_id is not None:
            where += " AND append_id <= %s"
            params.append(int(max_append_id))
        if touched_since:
            where += (
                f" AND ({_KO_EFFECTIVE_TIME_EXPR} !~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' "
                f"OR left({_KO_EFFECTIVE_TIME_EXPR}, 10) >= %s)"
            )
            params.append(str(touched_since)[:10])
        params.append(size)
        rows = self._query(
            f"SELECT append_id, payload FROM {self.table} WHERE {where} ORDER BY append_id ASC LIMIT %s",
            tuple(params),
        )
        page: list[tuple[int, KnowledgeObject]] = []
        for row in rows:
            append_id = row[0] if isinstance(row, tuple) else row.get("append_id")
            payload = self._payload_of(row)
            if isinstance(payload, dict):
                page.append((int(append_id), KnowledgeObject.from_dict(payload)))
        return page

    def count_knowledge_objects(
        self, after_append_id: int = 0, *, max_append_id: int | None = None, indexable_only: bool = False
    ) -> int:
        where = "record_type = 'knowledge_object' AND append_id > %s"
        params: list[Any] = [int(after_append_id)]
        if max_append_id is not None:
            where += " AND append_id <= %s"
            params.append(int(max_append_id))
        if indexable_only:
            where += f" AND {_KO_NOT_COLLECTOR_BUNDLE}"
        rows = self._query(f"SELECT count(*) FROM {self.table} WHERE {where}", tuple(params))
        return int(rows[0][0] if rows and isinstance(rows[0], tuple) else (list(rows[0].values())[0] if rows else 0))

    def _cached(self, key: str, compute: Any) -> Any:
        now = time.monotonic()
        hit = self._summary_cache.get(key)
        if hit is not None and now - hit[0] < _SUMMARY_TTL_SECONDS:
            return hit[1]
        value = compute()
        self._summary_cache[key] = (now, value)
        return value

    def knowledge_object_count(self) -> int:
        return self._cached("ko_count", lambda: self.count_knowledge_objects())

    def source_watermarks(self) -> Dict[str, str | None]:
        def compute() -> Dict[str, str | None]:
            rows = self._query(
                "SELECT payload->>'source_type', MAX(COALESCE(payload->>'created_at', '')) "
                f"FROM {self.table} WHERE record_type = 'source_record' GROUP BY 1",
                (),
            )
            return {str(r[0]): (str(r[1]) or None) for r in rows if r[0] is not None}
        return self._cached("source_watermarks", compute)

    def knowledge_object_index_summary(self) -> tuple[list[str], str | None]:
        """(distinct source types, latest effective time text) via one aggregate."""
        def compute() -> tuple[list[str], str | None]:
            rows = self._query(
                f"SELECT payload->>'source_type', MAX({_KO_EFFECTIVE_TIME_EXPR}) "
                f"FROM {self.table} WHERE record_type = 'knowledge_object' GROUP BY 1",
                (),
            )
            types = sorted(str(r[0]) for r in rows if r[0] is not None)
            times = [str(r[1]) for r in rows if r[1]]
            return types, (max(times) if times else None)
        return self._cached("ko_summary", compute)

    # -- whole-collection lists are refused beyond a fixed cap ----------------
    def list_knowledge_objects(self, tenant_id: Any = _UNSCOPED) -> List[KnowledgeObject]:
        out: List[KnowledgeObject] = []
        cursor = 0
        while True:
            page = self.fetch_knowledge_object_page(cursor)
            for append_id, ko in page:
                cursor = append_id
                if tenant_id is _UNSCOPED or ko.tenant_id == tenant_id:
                    out.append(ko)
                    if len(out) > _STREAM_LIST_CAP:
                        raise EvidenceValidationError(
                            "list_knowledge_objects exceeds the bounded streaming cap; "
                            "use fetch_knowledge_object_page"
                        )
            if len(page) < _STREAM_PAGE_SIZE:
                return out

    def _refuse_list(self, *args: Any, **kwargs: Any) -> Any:
        raise EvidenceValidationError("streaming evidence repository does not list whole record types")

    list_source_records = list_evidence_items = list_bundles = _refuse_list  # type: ignore[assignment]


class PostgresSearchIndexStore:
    """Postgres-backed search snapshot store.

    Write owner: search-svc.  Append semantics match JsonlSearchIndexStore:
    the latest upsert per request_id wins on read.
    """

    def __init__(
        self,
        dsn: str,
        table: str = "search_svc.search_index_snapshots",
        bootstrap: bool = True,
        max_count: int | None = None,
        max_age_seconds: int | None = None,
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for PostgresSearchIndexStore")
        self.max_count = max_count if max_count is not None else SNAPSHOT_MAX_COUNT
        self.max_age_seconds = max_age_seconds if max_age_seconds is not None else SNAPSHOT_MAX_AGE_SECONDS
        self.dsn = dsn
        self.table = _quote_pg(table)
        self.schema = table.split(".", 1)[0] if "." in table else ""
        self.path = f"postgres:{table}"
        self._snapshots: Dict[str, SearchIndexSnapshot] = {}
        if bootstrap:
            self._bootstrap()
        self.reload()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required when SEARCH_INDEX_STORE_BACKEND=postgres"
            ) from exc
        return psycopg.connect(self.dsn)

    def _bootstrap(self) -> None:
        with self._connect() as conn:
            if self.schema:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_pg(self.schema)}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    append_id  BIGSERIAL PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    payload    JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    def reload(self) -> None:
        self._snapshots = {}
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT request_id, payload FROM ("
                f"SELECT append_id, request_id, payload FROM {self.table} "
                "WHERE created_at >= now() - make_interval(secs => %s) "
                "ORDER BY append_id DESC LIMIT %s"
                ") recent ORDER BY append_id ASC",
                (self.max_age_seconds, self.max_count),
            )
            rows = cursor.fetchall()
        for row in rows:
            request_id = row[0] if isinstance(row, tuple) else row.get("request_id")
            payload = row[1] if isinstance(row, tuple) else row.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                snapshot = SearchIndexSnapshot.from_dict(payload)
                self._snapshots.pop(snapshot.request_id, None)
                self._snapshots[snapshot.request_id] = snapshot
        self._snapshots = bound_snapshots(self._snapshots, self.max_count, self.max_age_seconds)

    def append_snapshot(self, snapshot: SearchIndexSnapshot) -> SearchIndexSnapshot:
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table} (request_id, payload)
                VALUES (%s, %s::jsonb)
                """,
                (
                    snapshot.request_id,
                    json.dumps(snapshot.to_dict(), ensure_ascii=True, sort_keys=True),
                ),
            )
        self._snapshots.pop(snapshot.request_id, None)
        self._snapshots[snapshot.request_id] = snapshot
        self._snapshots = bound_snapshots(self._snapshots, self.max_count, self.max_age_seconds)
        return snapshot

    def get_snapshot(self, request_id: str) -> SearchIndexSnapshot | None:
        return self._snapshots.get(request_id)

    def list_snapshots(self) -> List[SearchIndexSnapshot]:
        return list(self._snapshots.values())


def build_search_index_store(jsonl_path: Path) -> JsonlSearchIndexStore | PostgresSearchIndexStore:
    """Factory: returns JsonlSearchIndexStore unless SEARCH_INDEX_STORE_BACKEND=postgres."""
    backend = os.getenv("SEARCH_INDEX_STORE_BACKEND", "jsonl").strip().lower()
    if backend in ("", "jsonl"):
        return JsonlSearchIndexStore(jsonl_path)
    if backend != "postgres":
        raise ValueError("SEARCH_INDEX_STORE_BACKEND must be jsonl or postgres")
    dsn = os.getenv("SEARCH_INDEX_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError(
            "SEARCH_INDEX_STORE_DSN or DATABASE_URL is required for Postgres search index store"
        )
    table = os.getenv("SEARCH_INDEX_STORE_TABLE", "search_svc.search_index_snapshots")
    bootstrap = (
        os.getenv("SEARCH_INDEX_STORE_BOOTSTRAP", "1").strip().lower()
        not in ("0", "false", "no")
    )
    return PostgresSearchIndexStore(dsn=dsn, table=table, bootstrap=bootstrap)


def build_search_evidence_repository(
    jsonl_path: Path,
) -> InMemoryEvidenceRepository:
    """Factory: returns JsonlEvidenceRepository unless SEARCH_EVIDENCE_BACKEND=postgres.

    In Postgres mode, returns a read-only view of source-ingest's evidence table.
    Source-ingest remains the sole write owner.
    """
    backend = os.getenv("SEARCH_EVIDENCE_BACKEND", "jsonl").strip().lower()
    if backend in ("", "jsonl"):
        return JsonlEvidenceRepository(jsonl_path)
    if backend != "postgres":
        raise ValueError("SEARCH_EVIDENCE_BACKEND must be jsonl or postgres")
    dsn = os.getenv("SEARCH_EVIDENCE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError(
            "SEARCH_EVIDENCE_DSN or DATABASE_URL is required for Postgres evidence store"
        )
    table = os.getenv("SEARCH_EVIDENCE_TABLE", "source_ingest.source_evidence")
    mode = os.getenv("SEARCH_EVIDENCE_LOAD_MODE", "streaming").strip().lower()
    if mode == "full":  # legacy whole-table load; only for small datasets and tests
        return PostgresReadOnlyEvidenceRepository(dsn=dsn, table=table)
    if mode not in ("", "streaming"):
        raise ValueError("SEARCH_EVIDENCE_LOAD_MODE must be streaming or full")
    return PostgresStreamingEvidenceRepository(dsn=dsn, table=table)
