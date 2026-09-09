"""Qdrant-backed retrieval engine comparison candidate.

Implements dense vector and hybrid prefetch retrieval in self-hosted Qdrant.
Used for evaluation and benchmark against PostgreSQL default.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Sequence

from qdrant_client import QdrantClient, models

from services.search.filters import (
    SearchAccessContext,
    SearchCapabilityUnavailableError,
    SearchFilters,
    SearchPolicyError,
)
from services.search.local_embeddings import LocalEmbeddingEngine
from services.search.pg_retrieval import RetrievalHitItem, RetrievalIndexRecord, _parse_iso


class QdrantRetrievalBackend:
    """Candidate Qdrant retrieval backend for comparative evaluation."""

    def __init__(
        self,
        url: str | None = None,
        collection_name: str = "search_retrieval_index",
        embedding_engine: LocalEmbeddingEngine | None = None,
    ) -> None:
        self.url = url or os.getenv("PANTHEON_SEARCH_QDRANT_URL", "http://localhost:26333")
        self.collection_name = collection_name
        self.embedding_engine = embedding_engine or LocalEmbeddingEngine()
        self._client: QdrantClient | None = None

    def _get_client(self) -> QdrantClient:
        if self._client is not None:
            return self._client
        try:
            if self.url == ":memory:":
                self._client = QdrantClient(":memory:", check_compatibility=False)
            else:
                self._client = QdrantClient(url=self.url, timeout=5.0, check_compatibility=False)
            return self._client
        except Exception as exc:
            raise SearchCapabilityUnavailableError(f"Cannot connect to Qdrant at {self.url}: {exc}") from exc

    def check_health(self) -> dict[str, Any]:
        try:
            client = self._get_client()
            collections = client.get_collections().collections
            exists = any(c.name == self.collection_name for c in collections)
            doc_count = 0
            if exists:
                doc_count = client.count(self.collection_name, exact=True).count
            return {
                "status": "ok",
                "backend": "qdrant",
                "url": self.url,
                "vector_dimension": self.embedding_engine.dimension,
                "collection_exists": exists,
                "document_count": doc_count,
                "embedding_ready": self.embedding_engine.is_ready(),
            }
        except Exception as exc:
            return {"status": "degraded", "backend": "qdrant", "error": str(exc)}

    def setup_schema(self) -> None:
        client = self._get_client()
        collections = client.get_collections().collections
        if not any(c.name == self.collection_name for c in collections):
            client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.embedding_engine.dimension,
                    distance=models.Distance.COSINE,
                ),
            )
            # Create payload indexes for pre-retrieval filtering
            for field_name, field_type in [
                ("tenant_id", models.PayloadSchemaType.KEYWORD),
                ("persona_id", models.PayloadSchemaType.KEYWORD),
                ("workspace_id", models.PayloadSchemaType.KEYWORD),
                ("environment_scope", models.PayloadSchemaType.KEYWORD),
                ("access_scope", models.PayloadSchemaType.KEYWORD),
                ("license_scope", models.PayloadSchemaType.KEYWORD),
                ("role_scope", models.PayloadSchemaType.KEYWORD),
                ("source_type", models.PayloadSchemaType.KEYWORD),
                ("record_kind", models.PayloadSchemaType.KEYWORD),
                ("is_active", models.PayloadSchemaType.BOOL),
                ("search_text", models.TextIndexParams(type=models.TextIndexType.TEXT, tokenizer=models.TokenizerType.MULTILINGUAL)),
            ]:
                try:
                    client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name=field_name,
                        field_schema=field_type,
                    )
                except Exception:
                    pass

    def index_documents(
        self,
        records: Sequence[RetrievalIndexRecord],
        compute_embeddings: bool = True,
    ) -> int:
        if not records:
            return 0
        client = self._get_client()
        self.setup_schema()

        if compute_embeddings:
            texts_to_embed = [r.search_text or r.title for r in records if r.embedding is None]
            if texts_to_embed:
                embeddings = self.embedding_engine.embed_documents(texts_to_embed)
                emb_idx = 0
                for r in records:
                    if r.embedding is None:
                        r.embedding = embeddings[emb_idx]
                        emb_idx += 1

        points = []
        for rec in records:
            # Deterministic UUID from rec.id for Qdrant point_id
            import uuid
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pantheon:{rec.id}"))
            payload = {
                "record_id": rec.id,
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
                "event_time": rec.event_time.isoformat() if isinstance(rec.event_time, datetime) else str(rec.event_time or ""),
                "available_time": rec.available_time.isoformat() if isinstance(rec.available_time, datetime) else str(rec.available_time or ""),
                "relevance_score": rec.relevance_score,
                "metadata": rec.metadata,
                "version": rec.version,
                "is_active": rec.is_active,
                "updated_at": rec.updated_at.isoformat() if isinstance(rec.updated_at, datetime) else str(rec.updated_at or ""),
            }
            points.append(models.PointStruct(id=point_id, vector=rec.embedding or [0.0] * self.embedding_engine.dimension, payload=payload))

        client.upsert(collection_name=self.collection_name, points=points, wait=True)
        return len(records)

    def delete_document(self, document_id: str, hard_delete: bool = False) -> bool:
        import uuid
        client = self._get_client()
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pantheon:{document_id}"))
        if hard_delete:
            res = client.delete(collection_name=self.collection_name, points_selector=[point_id], wait=True)
            return res.status == models.UpdateStatus.COMPLETED
        else:
            client.set_payload(collection_name=self.collection_name, payload={"is_active": False}, points=[point_id], wait=True)
            return True

    def _build_filter(
        self,
        context: SearchAccessContext,
        filters: SearchFilters | None = None,
        record_kind: str | None = None,
    ) -> models.Filter:
        must_conditions: list[Any] = [
            models.FieldCondition(key="is_active", match=models.MatchValue(value=True)),
            models.FieldCondition(key="environment_scope", match=models.MatchAny(any=[context.environment])),
            models.FieldCondition(key="access_scope", match=models.MatchAny(any=list(context.access_scopes))),
        ]

        if record_kind:
            must_conditions.append(models.FieldCondition(key="record_kind", match=models.MatchValue(value=record_kind)))

        if context.persona_id:
            must_conditions.append(
                models.Filter(
                    should=[
                        models.FieldCondition(key="persona_id", match=models.MatchValue(value=context.persona_id)),
                        models.IsEmptyCondition(is_empty=models.PayloadField(key="persona_id")),
                    ]
                )
            )

        if context.workspace_id:
            must_conditions.append(
                models.Filter(
                    should=[
                        models.FieldCondition(key="workspace_id", match=models.MatchValue(value=context.workspace_id)),
                        models.IsEmptyCondition(is_empty=models.PayloadField(key="workspace_id")),
                    ]
                )
            )

        lic_scopes = list(filters.license_scopes) if (filters and filters.license_scopes) else list(context.license_scopes)
        if lic_scopes:
            must_conditions.append(models.FieldCondition(key="license_scope", match=models.MatchAny(any=lic_scopes)))

        if filters and filters.source_types:
            must_conditions.append(models.FieldCondition(key="source_type", match=models.MatchAny(any=list(filters.source_types))))

        return models.Filter(must=must_conditions)

    def search(
        self,
        query: str,
        context: SearchAccessContext,
        filters: SearchFilters | None = None,
        top_k: int = 10,
        mode: str = "hybrid",
        record_kind: str | None = None,
    ) -> list[RetrievalHitItem]:
        client = self._get_client()
        query_filter = self._build_filter(context, filters=filters, record_kind=record_kind)
        query_vec = self.embedding_engine.embed_query(query)

        results = client.query_points(
            collection_name=self.collection_name,
            query=query_vec,
            query_filter=query_filter,
            limit=top_k,
        ).points

        hits: list[RetrievalHitItem] = []
        for pt in results:
            payload = pt.payload or {}
            score = float(pt.score) if pt.score is not None else 0.0
            norm_score = round(max(0.0, min(1.0, (score + 1.0) / 2.0)), 4)
            hits.append(
                RetrievalHitItem(
                    id=str(payload.get("record_id") or pt.id),
                    record_kind=str(payload.get("record_kind") or "knowledge_object"),
                    score=norm_score,
                    title=str(payload.get("title") or ""),
                    search_text=str(payload.get("search_text") or ""),
                    content_ref=payload.get("content_ref"),
                    citation_label=payload.get("citation_label"),
                    evidence_bundle_id=payload.get("evidence_bundle_id"),
                    evidence_item_id=payload.get("evidence_item_id"),
                    component_scores={"qdrant_cosine_score": score},
                    metadata=dict(payload.get("metadata") or {}),
                    updated_at=str(payload.get("updated_at") or ""),
                    ranker_version="qdrant-dense-v1",
                )
            )
        return hits
