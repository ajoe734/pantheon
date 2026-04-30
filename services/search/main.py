"""Deployable HTTP wrapper for governed search."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.foundation.health import register_fastapi_health_routes
from services.knowledge.evidence import (
    EvidenceBundle,
    EvidenceItem,
    InMemoryEvidenceRepository,
    KnowledgeObject,
)
from services.knowledge.evidence.models import EvidenceValidationError
from services.source_ingestion.connectors import SourceRecord

from .filters import SearchAccessContext, SearchPolicyError, SearchRequest
from .gateway import SearchGateway
from .index_adapter import KeywordIndexAdapter
from .index_store import JsonlSearchIndexStore
from .pg_store import build_search_evidence_repository, build_search_index_store


def _resolve_data_dir() -> Path:
    data_dir = Path(os.getenv("SEARCH_DATA_DIR", "/tmp/pantheon/search"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


DATA_DIR = _resolve_data_dir()
INDEX_STORE_PATH = Path(os.getenv("SEARCH_INDEX_STORE_PATH", str(DATA_DIR / "search-index.jsonl")))
MATERIALIZE_STORE_PATH = Path(os.getenv("SEARCH_MATERIALIZE_STORE_PATH", str(DATA_DIR / "search-materialize.jsonl")))
EVIDENCE_STORE_PATH = Path(
    os.getenv(
        "SEARCH_EVIDENCE_STORE_PATH",
        os.getenv("SOURCE_INGEST_EVIDENCE_STORE_PATH", str(DATA_DIR / "source_evidence.jsonl")),
    )
)


class JsonlMaterializedIndexStore:
    """Append/replay store for materialized search index state snapshots."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._last: dict[str, Any] | None = None
        self.reload()

    def reload(self) -> None:
        self._last = None
        if not self.path.exists():
            return
        last_entry: dict[str, Any] | None = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped:
                try:
                    last_entry = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
        self._last = last_entry

    def record_materialize(self, state: dict[str, Any]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "schema_version": "search_materialize_store.v1",
            "record_type": "materialized_index",
            "payload": state,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
        self._last = entry
        return state

    def get_last(self) -> dict[str, Any] | None:
        if self._last is None:
            return None
        return self._last.get("payload")


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
    allow_request_documents_compat: bool = False
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


def _source_watermarks(repository: InMemoryEvidenceRepository) -> dict[str, str | None]:
    watermarks: dict[str, str | None] = {}
    for source in repository.list_source_records():
        source_type = source.source_type.value if hasattr(source.source_type, "value") else str(source.source_type)
        created_at = str(source.created_at or "")
        if source_type not in watermarks or created_at > str(watermarks[source_type] or ""):
            watermarks[source_type] = created_at or None
    return watermarks


def create_app(
    index_store_path: Path | None = None,
    evidence_store_path: Path | None = None,
    materialize_store_path: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Pantheon Search Service", version="0.1.0")
    store = build_search_index_store(index_store_path or INDEX_STORE_PATH)
    durable_repository = build_search_evidence_repository(evidence_store_path or EVIDENCE_STORE_PATH)
    materialize_store = JsonlMaterializedIndexStore(materialize_store_path or MATERIALIZE_STORE_PATH)
    register_fastapi_health_routes(
        app,
        "pantheon-search",
        metrics=lambda: {
            "snapshot_count": len(store.list_snapshots()),
            "indexed_object_count": len(durable_repository.list_knowledge_objects()),
        },
        details=lambda: {
            "index_store_path": str(store.path),
            "evidence_store_path": str(durable_repository.path),
        },
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        store.reload()
        durable_repository.reload()
        return {
            "status": "ok",
            "service": "pantheon-search",
            "index_store_path": str(store.path),
            "evidence_store_path": str(durable_repository.path),
            "snapshot_count": len(store.list_snapshots()),
            "indexed_object_count": len(durable_repository.list_knowledge_objects()),
        }

    @app.post("/api/search/index/reload")
    def reload_index() -> dict[str, Any]:
        try:
            durable_repository.reload()
            adapter = KeywordIndexAdapter(
                durable_repository,
                source_watermarks=_source_watermarks(durable_repository),
                adapter_state="durable",
            )
            return {
                "index": adapter.snapshot().to_dict(),
                "evidence_store_path": str(durable_repository.path),
                "indexed_object_count": len(durable_repository.list_knowledge_objects()),
            }
        except EvidenceValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/search/index/status")
    def index_status() -> dict[str, Any]:
        try:
            durable_repository.reload()
            adapter = KeywordIndexAdapter(
                durable_repository,
                source_watermarks=_source_watermarks(durable_repository),
                adapter_state="durable",
            )
            return {
                "index": adapter.snapshot().to_dict(),
                "evidence_store_path": str(durable_repository.path),
                "indexed_object_count": len(durable_repository.list_knowledge_objects()),
            }
        except EvidenceValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _query_search(body: SearchQueryBody, *, allow_request_documents_compat: bool = False) -> dict[str, Any]:
        try:
            if body.documents:
                if not (allow_request_documents_compat or body.allow_request_documents_compat):
                    raise ValueError(
                        "request documents are compatibility-only; use "
                        "allow_request_documents_compat=true or /api/search/query/request-documents-compat"
                    )
                repository = _repository_from_documents(body.documents)
                adapter_state = "request_documents_compat"
            else:
                durable_repository.reload()
                repository = durable_repository
                adapter_state = "durable"
            index_adapter = KeywordIndexAdapter(
                repository,
                source_watermarks=_source_watermarks(repository),
                adapter_state=adapter_state,
            )
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

    @app.post("/api/search/query")
    def query_search(body: SearchQueryBody) -> dict[str, Any]:
        return _query_search(body)

    @app.post("/api/search/query/request-documents-compat")
    def query_search_request_documents_compat(body: SearchQueryBody) -> dict[str, Any]:
        return _query_search(body, allow_request_documents_compat=True)

    @app.get("/api/search/snapshots/{request_id}")
    def get_snapshot(request_id: str) -> dict[str, Any]:
        store.reload()
        snapshot = store.get_snapshot(request_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="search snapshot not found")
        return {"snapshot": snapshot.to_dict()}

    @app.post("/api/search/index/materialize")
    def materialize_index() -> dict[str, Any]:
        try:
            durable_repository.reload()
            adapter = KeywordIndexAdapter(
                durable_repository,
                source_watermarks=_source_watermarks(durable_repository),
                adapter_state="materialized",
            )
            state: dict[str, Any] = {
                "materialized_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "indexed_object_count": len(durable_repository.list_knowledge_objects()),
                "index": adapter.snapshot().to_dict(),
                "evidence_store_path": str(durable_repository.path),
            }
            materialize_store.record_materialize(state)
            return state
        except EvidenceValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/search/index/materialize")
    def get_materialized_index() -> dict[str, Any]:
        materialize_store.reload()
        state = materialize_store.get_last()
        if state is None:
            raise HTTPException(status_code=404, detail="no materialized index found")
        return state

    return app


app = create_app()
