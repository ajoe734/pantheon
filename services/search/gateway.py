"""Governed evidence-backed search gateway."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from services.knowledge.evidence.repository import InMemoryEvidenceRepository

from .filters import SearchAccessContext, SearchPolicyError, SearchRequest
from .index_adapter import KeywordIndexAdapter
from .index_store import JsonlSearchIndexStore, SearchIndexSnapshot
from .retriever import KeywordRetriever


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _available_at_or_before_now(value: Any, now: datetime) -> bool:
    if value in (None, ""):
        return True
    if isinstance(value, datetime):
        available_at = value
    else:
        try:
            available_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return False
    if available_at.tzinfo is None:
        available_at = available_at.replace(tzinfo=timezone.utc)
    return available_at.astimezone(timezone.utc) <= now


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "results": [result.to_dict() for result in self.results],
            "rejected_items_count": self.rejected_items_count,
            "filters_applied": dict(self.filters_applied),
            "created_at": self.created_at,
        }


class SearchGateway:
    """Applies ACL/license/environment filters before ranking evidence-backed objects."""

    def __init__(
        self,
        repository: InMemoryEvidenceRepository,
        retriever: KeywordRetriever | None = None,
        index_store: JsonlSearchIndexStore | None = None,
        index_adapter: KeywordIndexAdapter | None = None,
    ) -> None:
        self.repository = repository
        self.retriever = retriever or KeywordRetriever()
        self.index_store = index_store
        self.index_adapter = index_adapter or KeywordIndexAdapter(repository)

    def search(self, request: SearchRequest, context: SearchAccessContext) -> GovernedSearchResponse:
        filtered = []
        rejected = 0
        requested_source_types = set(request.source_types)
        now = datetime.now(timezone.utc)

        for knowledge_object in self.repository.list_knowledge_objects():
            if requested_source_types and knowledge_object.source_type not in requested_source_types:
                rejected += 1
                continue
            allowed, _reason = context.permits(knowledge_object)
            if not allowed:
                rejected += 1
                continue
            if request.require_citations:
                bundle = self.repository.get_bundle(knowledge_object.evidence_bundle_id)
                if bundle is None or not bundle.citation_refs:
                    rejected += 1
                    continue
            evidence_item = self.repository.get_evidence_item(knowledge_object.evidence_item_id)
            if evidence_item is None or not _available_at_or_before_now(evidence_item.available_time, now):
                rejected += 1
                continue
            filtered.append(knowledge_object)

        index_documents = self.index_adapter.documents_for(filtered)
        matches = self.retriever.retrieve(request.query, index_documents, top_k=request.top_k)
        results: list[RetrievalResult] = []
        created_at = _now_iso()
        filters_applied = {
            **dict(request.filters_applied),
            "source_types": list(request.source_types),
            "environment": context.environment,
            "access_scopes": list(context.access_scopes),
            "license_scopes": list(context.license_scopes),
            "pre_ranking_filter": "acl_license_workspace_environment",
            "available_time": "not_future",
        }
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
                    created_at=created_at,
                )
            )

        response = GovernedSearchResponse(
            request_id=request.request_id,
            trace_id=request.trace_id,
            results=results,
            rejected_items_count=rejected,
            filters_applied=filters_applied,
            created_at=created_at,
        )
        if self.index_store is not None:
            self.index_store.append_snapshot(SearchIndexSnapshot.from_response(response))
        return response
