"""Governed evidence-backed search gateway supporting keyword, full-text, semantic, hybrid, and structured-alpha modes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from services.knowledge.evidence.repository import InMemoryEvidenceRepository

from .filters import (
    SearchAccessContext,
    SearchCapabilityUnavailableError,
    SearchFilters,
    SearchPolicyError,
    SearchRequest,
)
from .hybrid_retriever import HybridRetriever
from .index_adapter import KeywordIndexAdapter
from .index_store import JsonlSearchIndexStore, SearchIndexSnapshot
from .retriever import FullTextRetriever, KeywordRetriever, SemanticRetriever
from .structured_alpha import StructuredAlphaEngine, StructuredAlphaQuery


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RetrievalResult:
    result_id: str
    request_id: str
    evidence_bundle_id: str
    matched_items: list[dict[str, Any]]
    answer_context: str
    citations: list[str]
    filters_applied: Mapping[str, Any]
    rejected_items_count: int
    relevance_score: float
    created_at: str
    component_scores: Mapping[str, Any] = field(default_factory=dict)
    ranker_version: str = "keyword-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "request_id": self.request_id,
            "evidence_bundle_id": self.evidence_bundle_id,
            "matched_items": list(self.matched_items),
            "answer_context": self.answer_context,
            "citations": list(self.citations),
            "filters_applied": dict(self.filters_applied),
            "rejected_items_count": self.rejected_items_count,
            "relevance_score": self.relevance_score,
            "component_scores": dict(self.component_scores),
            "ranker_version": self.ranker_version,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class GovernedSearchResponse:
    request_id: str
    trace_id: str
    results: list[RetrievalResult]
    rejected_items_count: int
    filters_applied: Mapping[str, Any]
    created_at: str
    retrieval_mode: str = "keyword"
    rejected_by_reason: Mapping[str, int] = field(default_factory=dict)
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    fingerprints: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "retrieval_mode": self.retrieval_mode,
            "results": [result.to_dict() for result in self.results],
            "rejected_items_count": self.rejected_items_count,
            "rejected_by_reason": dict(self.rejected_by_reason),
            "filters_applied": dict(self.filters_applied),
            "capabilities": dict(self.capabilities),
            "fingerprints": dict(self.fingerprints),
            "created_at": self.created_at,
        }


class SearchGateway:
    """Applies pre-retrieval ACL/license/time filters before ranking evidence-backed objects."""

    def __init__(
        self,
        repository: InMemoryEvidenceRepository,
        retriever: KeywordRetriever | None = None,
        full_text_retriever: FullTextRetriever | None = None,
        semantic_retriever: SemanticRetriever | None = None,
        hybrid_retriever: HybridRetriever | None = None,
        alpha_engine: StructuredAlphaEngine | None = None,
        index_store: JsonlSearchIndexStore | None = None,
        index_adapter: KeywordIndexAdapter | None = None,
        retrieval_backend: Any | None = None,
    ) -> None:
        self.repository = repository
        self.retriever = retriever or KeywordRetriever()
        self.full_text_retriever = full_text_retriever or FullTextRetriever()
        self.semantic_retriever = semantic_retriever or SemanticRetriever(embedding_backend=None)
        self.hybrid_retriever = hybrid_retriever or HybridRetriever(
            lexical_retriever=self.full_text_retriever,
            semantic_retriever=self.semantic_retriever,
        )
        self.alpha_engine = alpha_engine or StructuredAlphaEngine()
        self.index_store = index_store
        self.index_adapter = index_adapter or KeywordIndexAdapter(repository)
        self.retrieval_backend = retrieval_backend

    def get_capabilities(self) -> dict[str, Any]:
        """Report retrieval capability matrix honestly."""
        return {
            "keyword": {
                "available": self.retriever.is_available(),
                "ranker_version": self.retriever.ranker_version,
            },
            "full_text": {
                "available": self.full_text_retriever.is_available(),
                "ranker_version": self.full_text_retriever.ranker_version,
            },
            "semantic": {
                "available": self.semantic_retriever.is_available(),
                "ranker_version": self.semantic_retriever.ranker_version if self.semantic_retriever.is_available() else None,
                "model_name": self.semantic_retriever.model_name if self.semantic_retriever.is_available() else None,
            },
            "hybrid": {
                "available": self.hybrid_retriever.is_available(),
                "ranker_version": self.hybrid_retriever.ranker_version if self.hybrid_retriever.is_available() else None,
                "calibration_method": self.hybrid_retriever.calibration_method if self.hybrid_retriever.is_available() else None,
            },
            "structured_alpha": {
                "available": True,
                "engine_version": "structured_alpha.v1",
                "registered_datasets": list(self.alpha_engine.schemas.keys()),
            },
        }

    def search(self, request: SearchRequest, context: SearchAccessContext) -> GovernedSearchResponse:
        now = datetime.now(timezone.utc)
        created_at = _now_iso()
        capabilities = self.get_capabilities()

        # Route structured-alpha queries
        if request.retrieval_mode == "structured_alpha" or request.structured_alpha is not None:
            return self._execute_structured_alpha(request, context, now=now, created_at=created_at, capabilities=capabilities)

        # Pre-retrieval filter plan for document retrieval
        filters: SearchFilters = (
            request.filters
            if isinstance(request.filters, SearchFilters)
            else SearchFilters.from_dict(request.filters)
        )

        mode = request.retrieval_mode
        if self.retrieval_backend is not None and mode in ("keyword", "full_text", "semantic", "hybrid"):
            hits = self.retrieval_backend.search(
                query=request.query,
                context=context,
                filters=filters,
                top_k=request.top_k,
                mode=mode,
            )
            filters_applied = {
                **dict(request.filters_applied),
                "source_types": list(filters.source_types),
                "environment": context.environment,
                "access_scopes": list(context.access_scopes),
                "license_scopes": list(context.license_scopes),
                "pre_ranking_filter": "acl_license_workspace_environment",
                "available_time": "not_future",
            }
            if filters.event_time_gte:
                filters_applied["event_time_gte"] = filters.event_time_gte
            if filters.event_time_lte:
                filters_applied["event_time_lte"] = filters.event_time_lte
            if filters.available_time_lte:
                filters_applied["available_time_lte"] = filters.available_time_lte
            if filters.sensitivity:
                filters_applied["sensitivity"] = list(filters.sensitivity)
            if filters.capital_pool_scope:
                filters_applied["capital_pool_scope"] = list(filters.capital_pool_scope)
            if filters.asset_class:
                filters_applied["asset_class"] = list(filters.asset_class)
            if filters.strategy_id:
                filters_applied["strategy_id"] = filters.strategy_id

            query_fp = hashlib.sha256(f"{request.query}:{mode}:{sorted(filters_applied.items())}".encode("utf-8")).hexdigest()[:16]
            ranker_ver = hits[0].ranker_version if hits else f"postgres-{mode}-v1"

            results: list[RetrievalResult] = []
            for hit in hits:
                ko = self.repository.get_knowledge_object(hit.id)
                evidence_item = self.repository.get_evidence_item(ko.evidence_item_id) if ko else None
                bundle = self.repository.get_bundle(ko.evidence_bundle_id) if ko else None

                bundle_id = bundle.evidence_bundle_id if bundle else (hit.evidence_bundle_id or f"bundle-{hit.id}")
                evidence_id = evidence_item.evidence_item_id if evidence_item else (hit.evidence_item_id or f"item-{hit.id}")
                citation = evidence_item.citation_label if evidence_item else (hit.citation_label or f"doc:{hit.id}")
                content_ref = evidence_item.content_ref if evidence_item else (hit.content_ref or f"/docs/{hit.id}")
                answer_text = ko.text if ko else hit.search_text

                results.append(
                    RetrievalResult(
                        result_id=hit.id,
                        request_id=request.request_id,
                        evidence_bundle_id=bundle_id,
                        matched_items=[
                            {
                                "knowledge_object_id": hit.id,
                                "source_id": ko.source_id if ko else f"src-{hit.id}",
                                "evidence_item_id": evidence_id,
                                "content_ref": content_ref,
                                "citation_label": citation,
                                "matched_terms": list(hit.matched_terms),
                            }
                        ],
                        answer_context=answer_text,
                        citations=[citation],
                        filters_applied=filters_applied,
                        rejected_items_count=0,
                        relevance_score=hit.score,
                        component_scores=dict(hit.component_scores),
                        ranker_version=hit.ranker_version,
                        created_at=created_at,
                    )
                )

            response = GovernedSearchResponse(
                request_id=request.request_id,
                trace_id=request.trace_id,
                retrieval_mode=mode,
                results=results,
                rejected_items_count=0,
                rejected_by_reason={},
                filters_applied=filters_applied,
                capabilities=capabilities,
                fingerprints={
                    "query_fingerprint": query_fp,
                    "ranker_fingerprint": ranker_ver,
                },
                created_at=created_at,
            )
            if self.index_store is not None:
                self.index_store.append_snapshot(SearchIndexSnapshot.from_response(response))
            return response

        filtered = []
        rejected = 0
        rejected_by_reason: dict[str, int] = {
            "source_type": 0,
            "environment": 0,
            "access_scope": 0,
            "license_scope": 0,
            "persona_scope": 0,
            "workspace_scope": 0,
            "role_scope": 0,
            "sensitivity": 0,
            "capital_pool": 0,
            "asset_class": 0,
            "strategy_id": 0,
            "event_time": 0,
            "available_time": 0,
            "missing_citation": 0,
        }

        for knowledge_object in self.repository.list_knowledge_objects():
            evidence_item = self.repository.get_evidence_item(knowledge_object.evidence_item_id)
            bundle = self.repository.get_bundle(knowledge_object.evidence_bundle_id)

            allowed, reason = context.permits(
                knowledge_object,
                evidence_item=evidence_item,
                bundle=bundle,
                filters=filters,
                now=now,
                require_citations=request.require_citations,
            )
            if not allowed:
                rejected += 1
                r_key = reason or "access_scope"
                rejected_by_reason[r_key] = rejected_by_reason.get(r_key, 0) + 1
                continue

            filtered.append(knowledge_object)

        # Restrict index documents strictly to already authorized candidate IDs
        index_documents = self.index_adapter.documents_for(filtered)

        # Select retriever based on retrieval_mode
        mode = request.retrieval_mode
        if mode == "keyword":
            matches = self.retriever.retrieve(request.query, index_documents, top_k=request.top_k)
        elif mode == "full_text":
            matches = self.full_text_retriever.retrieve(request.query, index_documents, top_k=request.top_k)
        elif mode == "semantic":
            matches = self.semantic_retriever.retrieve(request.query, index_documents, top_k=request.top_k)
        elif mode == "hybrid":
            matches = self.hybrid_retriever.retrieve(request.query, index_documents, top_k=request.top_k)
        else:
            raise SearchPolicyError(f"Unsupported retrieval_mode '{mode}'")

        results: list[RetrievalResult] = []
        filters_applied = {
            **dict(request.filters_applied),
            "source_types": list(filters.source_types),
            "environment": context.environment,
            "access_scopes": list(context.access_scopes),
            "license_scopes": list(context.license_scopes),
            "pre_ranking_filter": "acl_license_workspace_environment",
            "available_time": "not_future",
        }
        if filters.event_time_gte:
            filters_applied["event_time_gte"] = filters.event_time_gte
        if filters.event_time_lte:
            filters_applied["event_time_lte"] = filters.event_time_lte
        if filters.available_time_lte:
            filters_applied["available_time_lte"] = filters.available_time_lte
        if filters.sensitivity:
            filters_applied["sensitivity"] = list(filters.sensitivity)
        if filters.capital_pool_scope:
            filters_applied["capital_pool_scope"] = list(filters.capital_pool_scope)
        if filters.asset_class:
            filters_applied["asset_class"] = list(filters.asset_class)
        if filters.strategy_id:
            filters_applied["strategy_id"] = filters.strategy_id

        query_fp = hashlib.sha256(f"{request.query}:{mode}:{sorted(filters_applied.items())}".encode("utf-8")).hexdigest()[:16]
        ranker_ver = matches[0].ranker_version if matches else f"{mode}-v1"

        for match in matches:
            knowledge_object = match.knowledge_object
            evidence_item = self.repository.get_evidence_item(knowledge_object.evidence_item_id)
            bundle = self.repository.get_bundle(knowledge_object.evidence_bundle_id)
            if bundle is None or evidence_item is None:
                raise SearchPolicyError("search index references missing governed evidence")

            results.append(
                RetrievalResult(
                    result_id=knowledge_object.knowledge_object_id,
                    request_id=request.request_id,
                    evidence_bundle_id=bundle.evidence_bundle_id,
                    matched_items=[
                        {
                            "knowledge_object_id": knowledge_object.knowledge_object_id,
                            "source_id": knowledge_object.source_id,
                            "evidence_item_id": evidence_item.evidence_item_id,
                            "content_ref": evidence_item.content_ref,
                            "citation_label": evidence_item.citation_label,
                            "matched_terms": list(match.matched_terms),
                        }
                    ],
                    answer_context=knowledge_object.text,
                    citations=[evidence_item.citation_label],
                    filters_applied=filters_applied,
                    rejected_items_count=rejected,
                    relevance_score=match.score,
                    component_scores=dict(match.component_scores),
                    ranker_version=match.ranker_version,
                    created_at=created_at,
                )
            )

        response = GovernedSearchResponse(
            request_id=request.request_id,
            trace_id=request.trace_id,
            retrieval_mode=mode,
            results=results,
            rejected_items_count=rejected,
            rejected_by_reason={k: v for k, v in rejected_by_reason.items() if v > 0},
            filters_applied=filters_applied,
            capabilities=capabilities,
            fingerprints={
                "query_fingerprint": query_fp,
                "ranker_fingerprint": ranker_ver,
            },
            created_at=created_at,
        )

        if self.index_store is not None:
            self.index_store.append_snapshot(SearchIndexSnapshot.from_response(response))

        return response

    def _execute_structured_alpha(
        self,
        request: SearchRequest,
        context: SearchAccessContext,
        now: datetime,
        created_at: str,
        capabilities: dict[str, Any],
    ) -> GovernedSearchResponse:
        alpha_payload = request.structured_alpha or {}
        alpha_query = StructuredAlphaQuery.from_dict(alpha_payload)
        snapshot = self.alpha_engine.execute(alpha_query, context)

        filters_applied = {
            **dict(request.filters_applied),
            "schema_version": request.schema_version,
            "retrieval_mode": "structured_alpha",
            "dataset_ref": alpha_query.dataset_ref,
            "universe": list(alpha_query.universe),
            "as_of": snapshot.cutoff,
            "license_scope": snapshot.license_scope,
        }

        results: list[RetrievalResult] = []
        for idx, rec in enumerate(snapshot.matched_records):
            entity_id = rec.get("entity_id") or f"entity-{idx}"
            results.append(
                RetrievalResult(
                    result_id=f"alpha:{alpha_query.dataset_ref}:{entity_id}",
                    request_id=request.request_id,
                    evidence_bundle_id=f"bundle-alpha-{alpha_query.dataset_ref}",
                    matched_items=[
                        {
                            "entity_id": entity_id,
                            "dataset_ref": alpha_query.dataset_ref,
                            "values": rec.get("values", {}),
                            "event_time": rec.get("event_time"),
                            "available_time": rec.get("available_time"),
                            "ranker_version": snapshot.ranker_fingerprint,
                        }
                    ],
                    answer_context=json.dumps(rec.get("values", {})),
                    citations=snapshot.citations,
                    filters_applied=filters_applied,
                    rejected_items_count=0,
                    relevance_score=round(1.0 - (idx * 0.01), 3),
                    component_scores={"rank_index": idx + 1},
                    ranker_version=snapshot.ranker_fingerprint,
                    created_at=created_at,
                )
            )

        response = GovernedSearchResponse(
            request_id=request.request_id,
            trace_id=request.trace_id,
            retrieval_mode="structured_alpha",
            results=results,
            rejected_items_count=0,
            rejected_by_reason={},
            filters_applied=filters_applied,
            capabilities=capabilities,
            fingerprints={
                "query_fingerprint": snapshot.query_fingerprint,
                "dataset_fingerprint": snapshot.dataset_fingerprint,
                "ranker_fingerprint": snapshot.ranker_fingerprint,
                "quota_receipt": snapshot.quota_receipt,
                "cost_receipt": snapshot.cost_receipt,
            },
            created_at=created_at,
        )

        if self.index_store is not None:
            self.index_store.append_snapshot(SearchIndexSnapshot.from_response(response))

        return response
