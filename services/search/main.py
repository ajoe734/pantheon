"""Deployable HTTP wrapper for governed search."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.knowledge.evidence import EvidenceBundle, EvidenceItem, InMemoryEvidenceRepository, KnowledgeObject
from services.knowledge.evidence.models import EvidenceValidationError
from services.source_ingestion.connectors import SourceRecord

from .filters import SearchAccessContext, SearchPolicyError, SearchRequest
from .gateway import SearchGateway
from .index_adapter import KeywordIndexAdapter
from .index_store import JsonlSearchIndexStore


def _resolve_data_dir() -> Path:
    data_dir = Path(os.getenv("SEARCH_DATA_DIR", "/tmp/pantheon/search"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


DATA_DIR = _resolve_data_dir()
INDEX_STORE_PATH = Path(os.getenv("SEARCH_INDEX_STORE_PATH", str(DATA_DIR / "search-index.jsonl")))


class SearchDocumentBody(BaseModel):
    result_id: str
    match_type: str = "document"
    title: str
    excerpt: str = ""
    search_text: str = ""
    source_id: str | None = None
    source_type: str = "internal_note"
    content_ref: str | None = None
    citation_label: str | None = None
    evidence_bundle_id: str | None = None
    evidence_item_id: str | None = None
    linked_ticket_id: str | None = None
    links: dict[str, Any] = Field(default_factory=dict)
    relevance_score: float = 0.0
    confidence: float = 1.0
    license_scope: str = "internal"
    access_scope: list[str] = Field(default_factory=lambda: ["operator", "research"])
    environment_scope: list[str] = Field(default_factory=lambda: ["paper"])
    keywords: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""
    updated_at: str | None = None
    created_at: str | None = None


class AccessContextBody(BaseModel):
    persona_id: str | None = None
    workspace_id: str | None = None
    environment: str = "paper"
    access_scopes: list[str] = Field(default_factory=lambda: ["operator", "research"])
    license_scopes: list[str] = Field(default_factory=lambda: ["internal"])

    def to_domain(self) -> SearchAccessContext:
        return SearchAccessContext(
            persona_id=self.persona_id,
            workspace_id=self.workspace_id,
            environment=self.environment,
            access_scopes=self.access_scopes,
            license_scopes=self.license_scopes,
        )


class SearchQueryBody(BaseModel):
    query: str
    documents: list[SearchDocumentBody] = Field(default_factory=list)
    request_id: str = "search-service-query"
    trace_id: str = "trace-search-service-query"
    persona_id: str | None = None
    workspace_id: str | None = None
    source_types: list[str] = Field(default_factory=lambda: ["internal_note"])
    environment: str = "paper"
    top_k: int = 10
    require_citations: bool = True
    filters_applied: dict[str, Any] = Field(default_factory=dict)
    access_context: AccessContextBody = Field(default_factory=AccessContextBody)


def _result_detail(document: SearchDocumentBody) -> str:
    if document.content_ref:
        return document.content_ref
    if document.links.get("result_detail"):
        return str(document.links["result_detail"])
    if document.match_type == "ticket":
        return f"/research/tickets/{document.result_id}"
    return f"/research/{document.match_type}s/{document.result_id}"


def _repository_from_documents(documents: list[SearchDocumentBody]) -> InMemoryEvidenceRepository:
    repository = InMemoryEvidenceRepository()
    for document in documents:
        result_id = document.result_id.strip()
        if not result_id:
            continue
        match_type = document.match_type.strip().lower() or "document"
        result_detail = _result_detail(document)
        source_id = document.source_id or f"src-search-{result_id}"
        evidence_item_id = document.evidence_item_id or f"evi-search-{result_id}"
        evidence_bundle_id = document.evidence_bundle_id or f"evbundle-search-{result_id}"
        citation_label = document.citation_label or f"{match_type}:{result_id}"
        metadata = {
            **document.metadata,
            "result_id": result_id,
            "match_type": match_type,
            "linked_ticket_id": document.linked_ticket_id,
            "result_detail": result_detail,
            "search_text": document.search_text,
            "relevance_score": document.relevance_score,
            "updated_at": document.updated_at,
            "created_at": document.created_at,
            "links": document.links,
        }
        source = SourceRecord(
            source_id=source_id,
            connector_id="pantheon-search-service",
            source_type=document.source_type,
            title=document.title,
            content_ref=result_detail,
            metadata={
                "provider": "pantheon-search-service",
                "raw_uri": result_detail,
                "license_scope": document.license_scope,
                "access_scope": document.access_scope,
                "match_type": match_type,
            },
            trace_id=document.trace_id or f"trace-search-{result_id}",
        )
        item = EvidenceItem(
            evidence_item_id=evidence_item_id,
            source_id=source.source_id,
            item_type="text_chunk",
            content_ref=f"{result_detail}#search-index",
            citation_label=citation_label,
            body=document.excerpt or document.search_text or document.title,
            confidence=document.confidence,
            access_scope=document.access_scope,
            trace_refs=[source.trace_id],
            metadata={"linked_ticket_id": document.linked_ticket_id},
        )
        bundle = EvidenceBundle(
            evidence_bundle_id=evidence_bundle_id,
            source_ids=[source.source_id],
            evidence_item_ids=[item.evidence_item_id],
            summary=document.excerpt or document.title,
            citation_refs=[citation_label],
            confidence=document.confidence,
            license_scope=document.license_scope,
            access_scope=document.access_scope,
            created_by="pantheon-search-service",
            trace_refs=[source.trace_id],
            metadata={
                "result_id": result_id,
                "match_type": match_type,
                "linked_ticket_id": document.linked_ticket_id,
                "result_detail": result_detail,
            },
        )
        knowledge_object = KnowledgeObject(
            knowledge_object_id=result_id,
            source_id=source.source_id,
            evidence_item_id=item.evidence_item_id,
            evidence_bundle_id=bundle.evidence_bundle_id,
            title=document.title,
            text=document.excerpt or document.search_text or document.title,
            source_type=document.source_type,
            license_scope=document.license_scope,
            access_scope=document.access_scope,
            environment_scope=document.environment_scope,
            keywords=document.keywords or [match_type, str(document.linked_ticket_id or "")],
            metadata=metadata,
        )
        repository.add_source_record(source)
        repository.add_evidence_item(item)
        repository.add_bundle(bundle)
        repository.add_knowledge_object(knowledge_object)
    return repository


def create_app(index_store_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="Pantheon Search Service", version="0.1.0")
    store = JsonlSearchIndexStore(index_store_path or INDEX_STORE_PATH)

    @app.get("/health")
    def health() -> dict[str, Any]:
        store.reload()
        return {
            "status": "ok",
            "service": "pantheon-search",
            "index_store_path": str(store.path),
            "snapshot_count": len(store.list_snapshots()),
        }

    @app.post("/api/search/query")
    def query_search(body: SearchQueryBody) -> dict[str, Any]:
        try:
            repository = _repository_from_documents(body.documents)
            index_adapter = KeywordIndexAdapter(repository)
            request = SearchRequest(
                request_id=body.request_id,
                query=body.query,
                persona_id=body.persona_id or body.access_context.persona_id,
                workspace_id=body.workspace_id or body.access_context.workspace_id,
                source_types=body.source_types,
                environment=body.environment,
                top_k=body.top_k,
                require_citations=body.require_citations,
                trace_id=body.trace_id,
                filters_applied=body.filters_applied,
            )
            context = body.access_context.to_domain()
            context.require_persona_workspace()
            response = SearchGateway(repository, index_store=store, index_adapter=index_adapter).search(request, context)
            snapshot = store.get_snapshot(response.request_id)
            return {
                **response.to_dict(),
                "index_adapter": index_adapter.snapshot().to_dict(),
                "index_snapshot": snapshot.to_dict() if snapshot else None,
            }
        except (EvidenceValidationError, SearchPolicyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/search/snapshots/{request_id}")
    def get_snapshot(request_id: str) -> dict[str, Any]:
        store.reload()
        snapshot = store.get_snapshot(request_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="search snapshot not found")
        return {"snapshot": snapshot.to_dict()}

    return app


app = create_app()
