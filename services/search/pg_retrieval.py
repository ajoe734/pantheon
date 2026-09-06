"""Postgres-backed retrieval engine combining native FTS and pgvector via native SQL RRF."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row

try:
    import pgvector.psycopg
    _PGVECTOR_AVAILABLE = True
except ImportError:
    _PGVECTOR_AVAILABLE = False

from services.search.filters import (
    SearchAccessContext,
    SearchCapabilityUnavailableError,
    SearchFilters,
    SearchPolicyError,
)
from services.search.local_embeddings import LocalEmbeddingEngine


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _format_lexical_query(query: str) -> str:
    """Space-separate CJK characters so simple FTS parser indexes and queries unigrams."""
    return re.sub(r"([\u4e00-\u9fff])", r" \1 ", str(query or "")).strip()


@dataclass
class RetrievalIndexRecord:
    id: str
    record_kind: str
    title: str
    search_text: str
    tenant_id: str = "default"
    persona_id: str | None = None
    workspace_id: str | None = None
    environment_scope: Sequence[str] = ("paper",)
    access_scope: Sequence[str] = ("public",)
    license_scope: str = "internal"
    role_scope: Sequence[str] = ()
    sensitivity: str = "internal"
    capital_pool_scope: Sequence[str] = ()
    source_type: str = "internal_note"
    asset_class: Sequence[str] = ()
    strategy_id: str | None = None
    content_ref: str | None = None
    citation_label: str | None = None
    evidence_bundle_id: str | None = None
    evidence_item_id: str | None = None
    event_time: datetime | str | None = None
    available_time: datetime | str | None = None
    relevance_score: float = 0.0
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    is_active: bool = True
    indexed_at: datetime | str | None = None
    updated_at: datetime | str | None = None


@dataclass(frozen=True)
class RetrievalHitItem:
    id: str
    record_kind: str
    score: float
    title: str
    search_text: str
    content_ref: str | None
    citation_label: str | None
    evidence_bundle_id: str | None
    evidence_item_id: str | None
    component_scores: dict[str, Any]
    metadata: dict[str, Any]
    updated_at: str | None
    ranker_version: str
    matched_terms: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "record_kind": self.record_kind,
            "score": self.score,
            "title": self.title,
            "search_text": self.search_text,
            "content_ref": self.content_ref,
            "citation_label": self.citation_label,
            "evidence_bundle_id": self.evidence_bundle_id,
            "evidence_item_id": self.evidence_item_id,
            "component_scores": dict(self.component_scores),
            "metadata": dict(self.metadata),
            "updated_at": self.updated_at,
            "ranker_version": self.ranker_version,
            "matched_terms": list(self.matched_terms),
        }


class PostgresRetrievalBackend:
    """Production retrieval backend utilizing Postgres FTS + pgvector."""

    def __init__(
        self,
        dsn: str | None = None,
        embedding_engine: LocalEmbeddingEngine | None = None,
    ) -> None:
        self.dsn = dsn or os.getenv(
            "PANTHEON_SEARCH_POSTGRES_DSN",
            "postgresql://postgres:postgres@localhost:25432/pantheon_search",
        )
        self.embedding_engine = embedding_engine or LocalEmbeddingEngine()

    def _get_connection(self) -> psycopg.Connection:
        try:
            conn = psycopg.connect(self.dsn, row_factory=dict_row)
            if _PGVECTOR_AVAILABLE:
                pgvector.psycopg.register_vector(conn)
            with conn.cursor() as cur:
                cur.execute("SET random_page_cost = 1.1;")
            return conn
        except Exception as exc:
            raise SearchCapabilityUnavailableError(f"Cannot connect to Postgres at {self.dsn}: {exc}") from exc

    def check_health(self) -> dict[str, Any]:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
                    row = cur.fetchone()
                    vector_version = row["extversion"] if row else None
                    cur.execute("SELECT count(*) as count FROM search_retrieval_index WHERE is_active=TRUE")
                    doc_count = cur.fetchone()["count"]
            return {
                "status": "ok",
                "backend": "postgres_pgvector",
                "pgvector_version": vector_version,
                "vector_dimension": self.embedding_engine.dimension,
                "document_count": doc_count,
                "embedding_ready": self.embedding_engine.is_ready(),
            }
        except Exception as exc:
            return {"status": "degraded", "backend": "postgres_pgvector", "error": str(exc)}

    def setup_schema(self, schema_file: str | None = None) -> None:
        sql_path = schema_file or str(Path(__file__).parent / "sql" / "retrieval_index.sql")
        if not os.path.exists(sql_path):
            return
        with open(sql_path, "r", encoding="utf-8") as f:
            sql = f.read()
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute("""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM pg_constraint
                            WHERE conrelid = 'search_retrieval_index'::regclass
                              AND conname = 'search_retrieval_index_pkey'
                              AND pg_get_constraintdef(oid) = 'PRIMARY KEY (id)'
                        ) THEN
                            ALTER TABLE search_retrieval_index DROP CONSTRAINT search_retrieval_index_pkey;
                            ALTER TABLE search_retrieval_index ADD PRIMARY KEY (tenant_id, id);
                        END IF;
                    END $$;
                """)
                cur.execute("ALTER TABLE search_retrieval_index FORCE ROW LEVEL SECURITY;")
            conn.commit()

    def index_documents(self, records: Sequence[RetrievalIndexRecord]) -> int:
        if not records:
            return 0
        texts_to_embed = [r.search_text for r in records if r.embedding is None]
        embeddings = []
        if texts_to_embed:
            embeddings = self.embedding_engine.embed_documents(texts_to_embed)

        emb_idx = 0
        final_records = []
        for rec in records:
            if rec.embedding is None:
                final_records.append(
                    RetrievalIndexRecord(
                        id=rec.id,
                        record_kind=rec.record_kind,
                        tenant_id=rec.tenant_id,
                        persona_id=rec.persona_id,
                        workspace_id=rec.workspace_id,
                        environment_scope=rec.environment_scope,
                        access_scope=rec.access_scope,
                        license_scope=rec.license_scope,
                        role_scope=rec.role_scope,
                        sensitivity=rec.sensitivity,
                        capital_pool_scope=rec.capital_pool_scope,
                        source_type=rec.source_type,
                        asset_class=rec.asset_class,
                        strategy_id=rec.strategy_id,
                        title=rec.title,
                        search_text=rec.search_text,
                        content_ref=rec.content_ref,
                        citation_label=rec.citation_label,
                        evidence_bundle_id=rec.evidence_bundle_id,
                        evidence_item_id=rec.evidence_item_id,
                        event_time=rec.event_time,
                        available_time=rec.available_time,
                        relevance_score=rec.relevance_score,
                        embedding=embeddings[emb_idx],
                        metadata=rec.metadata,
                        version=rec.version,
                        is_active=rec.is_active,
                    )
                )
                emb_idx += 1
            else:
                final_records.append(rec)

        return self.upsert_documents(final_records)

    def upsert_documents(self, records: Sequence[RetrievalIndexRecord]) -> int:
        if not records:
            return 0

        insert_sql = """
        INSERT INTO search_retrieval_index (
            id, record_kind, tenant_id, persona_id, workspace_id,
            environment_scope, access_scope, license_scope, role_scope,
            sensitivity, capital_pool_scope, source_type, asset_class,
            strategy_id, title, search_text, content_ref, citation_label,
            evidence_bundle_id, evidence_item_id, event_time, available_time,
            relevance_score, embedding, metadata, version, is_active
        ) VALUES (
            %(id)s, %(record_kind)s, %(tenant_id)s, %(persona_id)s, %(workspace_id)s,
            %(environment_scope)s, %(access_scope)s, %(license_scope)s, %(role_scope)s,
            %(sensitivity)s, %(capital_pool_scope)s, %(source_type)s, %(asset_class)s,
            %(strategy_id)s, %(title)s, %(search_text)s, %(content_ref)s, %(citation_label)s,
            %(evidence_bundle_id)s, %(evidence_item_id)s, %(event_time)s, %(available_time)s,
            %(relevance_score)s, %(embedding)s, %(metadata)s, %(version)s, %(is_active)s
        )
        ON CONFLICT (tenant_id, id) DO UPDATE SET
            record_kind = EXCLUDED.record_kind,
            persona_id = EXCLUDED.persona_id,
            workspace_id = EXCLUDED.workspace_id,
            environment_scope = EXCLUDED.environment_scope,
            access_scope = EXCLUDED.access_scope,
            license_scope = EXCLUDED.license_scope,
            role_scope = EXCLUDED.role_scope,
            sensitivity = EXCLUDED.sensitivity,
            capital_pool_scope = EXCLUDED.capital_pool_scope,
            source_type = EXCLUDED.source_type,
            asset_class = EXCLUDED.asset_class,
            strategy_id = EXCLUDED.strategy_id,
            title = EXCLUDED.title,
            search_text = EXCLUDED.search_text,
            content_ref = EXCLUDED.content_ref,
            citation_label = EXCLUDED.citation_label,
            evidence_bundle_id = EXCLUDED.evidence_bundle_id,
            evidence_item_id = EXCLUDED.evidence_item_id,
            event_time = EXCLUDED.event_time,
            available_time = EXCLUDED.available_time,
            relevance_score = EXCLUDED.relevance_score,
            embedding = COALESCE(EXCLUDED.embedding, search_retrieval_index.embedding),
            metadata = EXCLUDED.metadata,
            version = search_retrieval_index.version + 1,
            is_active = EXCLUDED.is_active,
            updated_at = NOW();
        """

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                current_tenant = None
                for rec in records:
                    rec_tenant = rec.tenant_id or "default"
                    if rec_tenant != current_tenant:
                        cur.execute("SELECT set_config('pantheon.current_tenant', %s, true)", (rec_tenant,))
                        current_tenant = rec_tenant
                    params = {
                        "id": rec.id,
                        "record_kind": rec.record_kind,
                        "tenant_id": rec.tenant_id,
                        "persona_id": rec.persona_id,
                        "workspace_id": rec.workspace_id,
                        "environment_scope": list(rec.environment_scope),
                        "access_scope": list(rec.access_scope),
                        "license_scope": rec.license_scope,
                        "role_scope": list(rec.role_scope),
                        "sensitivity": rec.sensitivity,
                        "capital_pool_scope": list(rec.capital_pool_scope),
                        "source_type": rec.source_type,
                        "asset_class": list(rec.asset_class),
                        "strategy_id": rec.strategy_id,
                        "title": rec.title,
                        "search_text": rec.search_text,
                        "content_ref": rec.content_ref,
                        "citation_label": rec.citation_label,
                        "evidence_bundle_id": rec.evidence_bundle_id,
                        "evidence_item_id": rec.evidence_item_id,
                        "event_time": _parse_iso(rec.event_time),
                        "available_time": _parse_iso(rec.available_time),
                        "relevance_score": rec.relevance_score,
                        "embedding": list(rec.embedding) if rec.embedding is not None else None,
                        "metadata": json.dumps(rec.metadata),
                        "version": rec.version,
                        "is_active": rec.is_active,
                    }
                    cur.execute(insert_sql, params)
            conn.commit()
        return len(records)

    def delete_document(
        self,
        document_id: str,
        tenant_id: str = "default",
        hard_delete: bool = False,
        hard: bool = False,
    ) -> bool:
        is_hard = hard or hard_delete
        effective_tenant = tenant_id or "default"
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('pantheon.current_tenant', %s, true)", (effective_tenant,))
                if is_hard:
                    cur.execute(
                        "DELETE FROM search_retrieval_index WHERE id = %s AND tenant_id = %s",
                        (document_id, effective_tenant),
                    )
                else:
                    cur.execute(
                        "UPDATE search_retrieval_index SET is_active = FALSE, updated_at = NOW() WHERE id = %s AND tenant_id = %s",
                        (document_id, effective_tenant),
                    )
                affected = cur.rowcount
            conn.commit()
        return affected > 0

    def search(
        self,
        query: str,
        context: SearchAccessContext | None = None,
        filters: SearchFilters | None = None,
        top_k: int = 10,
        mode: str = "hybrid",
        record_kind: str | None = None,
        *,
        access_context: SearchAccessContext | None = None,
        limit: int | None = None,
    ) -> list[RetrievalHitItem]:
        ctx = context or access_context
        if ctx is None:
            ctx = SearchAccessContext()
        actual_top_k = limit if limit is not None else top_k

        mode = mode.lower().strip()
        if mode not in {"keyword", "full_text", "semantic", "hybrid"}:
            raise SearchPolicyError(f"Unsupported retrieval mode '{mode}'")

        now = datetime.now(timezone.utc)
        effective_now = now
        if ctx.as_of:
            ctx_as_of = _parse_iso(ctx.as_of)
            if ctx_as_of:
                effective_now = min(effective_now, ctx_as_of)
        cutoff_dt = effective_now
        if filters and filters.available_time_lte:
            user_cutoff = _parse_iso(filters.available_time_lte)
            if user_cutoff:
                cutoff_dt = min(cutoff_dt, user_cutoff)

        lex_query = _format_lexical_query(query)
        effective_tenant = getattr(ctx, "tenant_id", "default") or "default"

        # Body filters can ONLY narrow server identity context grants (ceiling)
        effective_licenses = set(ctx.license_scopes)
        if filters and filters.license_scopes:
            effective_licenses = effective_licenses.intersection(set(filters.license_scopes))
        if not effective_licenses:
            return []

        effective_sensitivities = set(ctx.sensitivity_scopes)
        if filters and filters.sensitivity:
            effective_sensitivities = effective_sensitivities.intersection(set(filters.sensitivity))
        if not effective_sensitivities:
            return []

        effective_access = set(ctx.access_scopes)
        if filters and hasattr(filters, "access_scopes") and getattr(filters, "access_scopes"):
            effective_access = effective_access.intersection(set(getattr(filters, "access_scopes")))
        if not effective_access:
            return []

        effective_pools = set(ctx.capital_pool_scopes) if ctx.capital_pool_scopes else None
        if filters and filters.capital_pool_scope:
            if effective_pools is not None:
                effective_pools = effective_pools.intersection(set(filters.capital_pool_scope))
                if not effective_pools:
                    return []
            else:
                effective_pools = set(filters.capital_pool_scope)

        params: dict[str, Any] = {
            "query": query,
            "lexical_query": lex_query,
            "tenant_id": effective_tenant,
            "environment": ctx.environment,
            "allowed_access_scopes": list(effective_access),
            "license_scopes": list(effective_licenses),
            "persona_id": ctx.persona_id,
            "workspace_id": ctx.workspace_id,
            "role_refs": list(ctx.role_refs),
            "sensitivity": list(effective_sensitivities),
            "capital_pool_scope": list(effective_pools) if effective_pools is not None else None,
            "source_types": list(filters.source_types) if (filters and filters.source_types) else None,
            "asset_class": list(filters.asset_class) if (filters and filters.asset_class) else None,
            "strategy_id": filters.strategy_id if (filters and filters.strategy_id) else None,
            "event_time_gte": _parse_iso(filters.event_time_gte) if (filters and filters.event_time_gte) else None,
            "event_time_lte": _parse_iso(filters.event_time_lte) if (filters and filters.event_time_lte) else None,
            "available_time_cutoff": cutoff_dt,
            "record_kind": record_kind,
            "top_k": actual_top_k,
        }

        where_clause = """
            is_active = TRUE
            AND tenant_id = %(tenant_id)s::text
            AND (%(record_kind)s::text IS NULL OR record_kind = %(record_kind)s::text)
            AND (%(environment)s::text = ANY(environment_scope) OR environment_scope = '{}')
            AND ('public' = ANY(access_scope) OR access_scope && %(allowed_access_scopes)s::text[])
            AND (license_scope = ANY(%(license_scopes)s::text[]))
            AND (persona_id IS NULL OR (%(persona_id)s::text IS NOT NULL AND persona_id = %(persona_id)s::text))
            AND (workspace_id IS NULL OR (%(workspace_id)s::text IS NOT NULL AND workspace_id = %(workspace_id)s::text))
            AND (cardinality(role_scope) = 0 OR role_scope && %(role_refs)s::text[])
            AND (sensitivity = ANY(%(sensitivity)s::text[]))
            AND (%(capital_pool_scope)s::text[] IS NULL OR capital_pool_scope && %(capital_pool_scope)s::text[])
            AND (%(source_types)s::text[] IS NULL OR source_type = ANY(%(source_types)s::text[]))
            AND (%(asset_class)s::text[] IS NULL OR asset_class && %(asset_class)s::text[])
            AND (%(strategy_id)s::text IS NULL OR strategy_id = %(strategy_id)s::text)
            AND (%(event_time_gte)s::timestamptz IS NULL OR event_time >= %(event_time_gte)s::timestamptz)
            AND (%(event_time_lte)s::timestamptz IS NULL OR event_time <= %(event_time_lte)s::timestamptz)
            AND (available_time IS NULL OR available_time <= %(available_time_cutoff)s::timestamptz)
        """

        tsquery_sql = "plainto_tsquery('simple', %(lexical_query)s)"

        if mode in ("keyword", "full_text"):
            sql = f"""
            WITH raw_lex AS (
                SELECT id, record_kind, title, search_text, content_ref, citation_label,
                       evidence_bundle_id, evidence_item_id, relevance_score, metadata,
                       updated_at, tsv,
                       ts_rank_cd(tsv, {tsquery_sql}) AS lex_score
                FROM search_retrieval_index
                WHERE {where_clause}
                  AND tsv @@ {tsquery_sql}
                ORDER BY lex_score DESC, updated_at DESC
                LIMIT %(top_k)s
            )
            SELECT *,
                   ROW_NUMBER() OVER (ORDER BY lex_score DESC, updated_at DESC) AS lex_rank
            FROM raw_lex;
            """
        elif mode == "semantic":
            query_vec = self.embedding_engine.embed_query(query)
            params["query_vector"] = query_vec
            sql = f"""
            WITH raw_sem AS (
                SELECT id, record_kind, title, search_text, content_ref, citation_label,
                       evidence_bundle_id, evidence_item_id, relevance_score, metadata,
                       updated_at, embedding,
                       (1.0 - (embedding <=> %(query_vector)s::vector)) AS sem_score
                FROM search_retrieval_index
                WHERE {where_clause}
                  AND embedding IS NOT NULL
                ORDER BY (embedding <=> %(query_vector)s::vector) ASC, updated_at DESC
                LIMIT %(top_k)s
            )
            SELECT *,
                   ROW_NUMBER() OVER (ORDER BY sem_score DESC, updated_at DESC) AS sem_rank
            FROM raw_sem;
            """
        else:
            query_vec = self.embedding_engine.embed_query(query)
            params["query_vector"] = query_vec
            params["rrf_k"] = 60
            params["lex_weight"] = 1.0
            params["sem_weight"] = 1.0
            params["candidate_limit"] = max(60, actual_top_k * 5)
            sql = f"""
            WITH lexical_raw AS (
                SELECT id,
                       ts_rank_cd(tsv, {tsquery_sql}) AS lex_score
                FROM search_retrieval_index
                WHERE {where_clause}
                  AND tsv @@ {tsquery_sql}
                ORDER BY lex_score DESC, updated_at DESC
                LIMIT %(candidate_limit)s
            ),
            lexical AS (
                SELECT id, lex_score, ROW_NUMBER() OVER (ORDER BY lex_score DESC) AS lex_rank
                FROM lexical_raw
            ),
            semantic_raw AS (
                SELECT id,
                       (1.0 - (embedding <=> %(query_vector)s::vector)) AS sem_score
                FROM search_retrieval_index
                WHERE {where_clause}
                  AND embedding IS NOT NULL
                ORDER BY (embedding <=> %(query_vector)s::vector) ASC, updated_at DESC
                LIMIT %(candidate_limit)s
            ),
            semantic AS (
                SELECT id, sem_score, ROW_NUMBER() OVER (ORDER BY sem_score DESC) AS sem_rank
                FROM semantic_raw
            ),
            fused AS (
                SELECT COALESCE(l.id, s.id) AS id,
                       l.lex_score,
                       s.sem_score,
                       l.lex_rank,
                       s.sem_rank,
                       (COALESCE(%(lex_weight)s / (%(rrf_k)s + l.lex_rank), 0.0) +
                        COALESCE(%(sem_weight)s / (%(rrf_k)s + s.sem_rank), 0.0)) AS raw_rrf
                FROM lexical l
                FULL OUTER JOIN semantic s ON l.id = s.id
            )
            SELECT p.id, p.record_kind, p.title, p.search_text, p.content_ref,
                   p.citation_label, p.evidence_bundle_id, p.evidence_item_id,
                   p.relevance_score, p.metadata, p.updated_at,
                   f.lex_score, f.sem_score, f.lex_rank, f.sem_rank, f.raw_rrf
            FROM fused f
            JOIN search_retrieval_index p ON f.id = p.id AND p.tenant_id = %(tenant_id)s::text
            ORDER BY f.raw_rrf DESC, p.updated_at DESC
            LIMIT %(top_k)s;
            """

        hits: list[RetrievalHitItem] = []
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('pantheon.current_tenant', %s, true)", (effective_tenant,))
                cur.execute(sql, params)
                rows = cur.fetchall()

        for row in rows:
            rec_id = row["id"]
            rec_kind = row["record_kind"]
            title = row["title"]
            text = row["search_text"]
            content_ref = row["content_ref"]
            citation = row["citation_label"]
            bundle_id = row["evidence_bundle_id"]
            item_id = row["evidence_item_id"]
            meta = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"] or "{}")
            upd = row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"] or "")

            comp_scores: dict[str, Any] = {}
            if mode in ("keyword", "full_text"):
                lex_score = float(row.get("lex_score") or 0.0)
                norm_score = round(min(0.999, max(0.01, 0.5 + lex_score * 0.1)), 4)
                comp_scores = {"full_text_score": lex_score, "rank": row.get("lex_rank")}
                ranker_ver = "postgres-fts-v1"
            elif mode == "semantic":
                sem_score = float(row.get("sem_score") or 0.0)
                norm_score = round(max(0.0, min(1.0, (sem_score + 1.0) / 2.0)), 4)
                comp_scores = {"semantic_cosine_score": sem_score, "rank": row.get("sem_rank")}
                ranker_ver = "postgres-pgvector-v1"
            else:
                raw_rrf = float(row.get("raw_rrf") or 0.0)
                max_rrf = 2.0 / 61.0
                norm_score = round(min(0.999, max(0.01, raw_rrf / max_rrf)), 4)
                comp_scores = {
                    "rrf_score": round(raw_rrf, 6),
                    "lexical_score": float(row.get("lex_score") or 0.0),
                    "semantic_score": float(row.get("sem_score") or 0.0),
                    "lexical_rank": row.get("lex_rank"),
                    "semantic_rank": row.get("sem_rank"),
                }
                ranker_ver = "postgres-hybrid-rrf-v1"

            query_tokens = [tok for tok in re.findall(r"\w+", query.lower()) if tok]
            combined_text = f"{title} {text}".lower()
            matched_terms = tuple(tok for tok in query_tokens if tok in combined_text)

            hits.append(
                RetrievalHitItem(
                    id=rec_id,
                    record_kind=rec_kind,
                    score=norm_score,
                    title=title,
                    search_text=text,
                    content_ref=content_ref,
                    citation_label=citation,
                    evidence_bundle_id=bundle_id,
                    evidence_item_id=item_id,
                    component_scores=comp_scores,
                    metadata=meta,
                    updated_at=upd,
                    ranker_version=ranker_ver,
                    matched_terms=matched_terms,
                )
            )

        return hits
