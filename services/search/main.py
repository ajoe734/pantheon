"""Deployable HTTP wrapper for governed search and structured alpha queries."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Response
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
from services.source_search_posture import require_source_search_posture

from .filters import (
    SearchAccessContext,
    SearchCapabilityUnavailableError,
    SearchFilters,
    SearchPolicyError,
    SearchRequest,
)
from .gateway import SearchGateway
from .hybrid_retriever import HybridRetriever
from .index_adapter import KeywordIndexAdapter
from .index_pipeline import IncrementalIndexPipeline, JsonlIndexPipelineStore
from .index_store import JsonlSearchIndexStore
from .pg_store import build_search_evidence_repository, build_search_index_store
from .retriever import (
    FullTextRetriever,
    KeywordRetriever,
    MockVectorEmbeddingBackend,
    SemanticRetriever,
)
from .structured_alpha import StructuredAlphaEngine, StructuredAlphaQuery


def _resolve_data_dir() -> Path:
    data_dir = Path(os.getenv("SEARCH_DATA_DIR", "/tmp/pantheon/search"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


DATA_DIR = _resolve_data_dir()
INDEX_STORE_PATH = Path(os.getenv("SEARCH_INDEX_STORE_PATH", str(DATA_DIR / "search-index.jsonl")))
MATERIALIZE_STORE_PATH = Path(os.getenv("SEARCH_MATERIALIZE_STORE_PATH", str(DATA_DIR / "search-materialize.jsonl")))
PIPELINE_STORE_PATH = Path(os.getenv("SEARCH_PIPELINE_STORE_PATH", str(DATA_DIR / "search-pipeline.jsonl")))
EVIDENCE_STORE_PATH = Path(
    os.getenv(
        "SEARCH_EVIDENCE_STORE_PATH",
        os.getenv("SOURCE_INGEST_EVIDENCE_STORE_PATH", str(DATA_DIR / "source_evidence.jsonl")),
    )
)
FRESHNESS_SLA_SECONDS = max(1, int(os.getenv("SEARCH_FRESHNESS_SLA_SECONDS", "3600")))
PIPELINE_RETENTION_RUNS = max(1, int(os.getenv("SEARCH_PIPELINE_RETENTION_RUNS", "500")))
DURABLE_INDEX_ONLY = os.getenv("SEARCH_DURABLE_INDEX_ONLY", "false").strip().lower() in ("1", "true", "yes")
VECTOR_EMBEDDING_ENABLED = os.getenv("SEARCH_VECTOR_EMBEDDING_ENABLED", "false").strip().lower() in ("1", "true", "yes")
PRODUCTION_POSTURE = require_source_search_posture("search")


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
    actor_ref: str | None = None
    persona_id: str | None = None
    workspace_id: str | None = None
    role_refs: list[str] = Field(default_factory=lambda: ["researcher"])
    environment: str = "paper"
    access_scopes: list[str] = Field(default_factory=lambda: ["operator", "research", "public"])
    license_scopes: list[str] = Field(default_factory=lambda: ["internal", "open"])
    sensitivity_scopes: list[str] = Field(default_factory=lambda: ["public", "internal"])
    capital_pool_scopes: list[str] = Field(default_factory=list)
    entitlements: list[str] = Field(default_factory=list)
    as_of: str | None = None

    def to_domain(self) -> SearchAccessContext:
        return SearchAccessContext(
            actor_ref=self.actor_ref,
            persona_id=self.persona_id,
            workspace_id=self.workspace_id,
            role_refs=self.role_refs,
            environment=self.environment,
            access_scopes=self.access_scopes,
            license_scopes=self.license_scopes,
            sensitivity_scopes=self.sensitivity_scopes,
            capital_pool_scopes=self.capital_pool_scopes,
            entitlements=self.entitlements,
            as_of=self.as_of,
        )


class SearchQueryBody(BaseModel):
    query: str = ""
    schema_version: str = "governed_search_request.v2"
    retrieval_mode: str = "keyword"
    actor_ref: str | None = None
    role_refs: list[str] = Field(default_factory=lambda: ["researcher"])
    purpose: str = "research"
    documents: list[SearchDocumentBody] = Field(default_factory=list)
    allow_request_documents_compat: bool = False
    request_id: str = "search-service-query"
    trace_id: str = "trace-search-service-query"
    persona_id: str | None = None
    workspace_id: str | None = None
    source_types: list[str] = Field(default_factory=lambda: ["internal_note"])
    environment: str = "paper"
    time_window: dict[str, Any] | None = None
    filters: dict[str, Any] | None = None
    structured_alpha: dict[str, Any] | None = None
    top_k: int = 10
    require_citations: bool = True
    filters_applied: dict[str, Any] = Field(default_factory=dict)
    access_context: AccessContextBody = Field(default_factory=AccessContextBody)


class StructuredAlphaQueryBody(BaseModel):
    schema_version: str = "alpha_rule_query.v1"
    dataset_ref: str
    universe: list[str] = Field(default_factory=lambda: ["US_EQUITY"])
    as_of: str | None = None
    rule: dict[str, Any]
    sort: list[dict[str, Any]] = Field(default_factory=list)
    limit: int = 50
    request_id: str = "alpha-service-query"
    trace_id: str = "trace-alpha-query"
    access_context: AccessContextBody = Field(default_factory=AccessContextBody)


class IndexRefreshBody(BaseModel):
    triggered_by: str = "manual"
    trigger_ref: str | None = None
    force_full: bool = False


class SourceCompletionBody(BaseModel):
    ingest_run_id: str
    connector_id: str | None = None
    source_type: str | None = None
    trace_id: str | None = None
    normalized_count: int = 0
    source_ids: list[str] = Field(default_factory=list)
    evidence_bundle_id: str | None = None
    knowledge_object_ids: list[str] = Field(default_factory=list)
    force_full: bool = False
    materialize: bool = True


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
    pipeline_store_path: Path | None = None,
    freshness_sla_seconds: int | None = None,
    pipeline_retention_runs: int | None = None,
    durable_index_only: bool | None = None,
    vector_embedding_backend: Any | None = None,
    alpha_engine: StructuredAlphaEngine | None = None,
) -> FastAPI:
    app = FastAPI(title="Pantheon Search Service", version="0.2.0")
    store = build_search_index_store(index_store_path or INDEX_STORE_PATH)
    durable_repository = build_search_evidence_repository(evidence_store_path or EVIDENCE_STORE_PATH)
    materialize_store = JsonlMaterializedIndexStore(materialize_store_path or MATERIALIZE_STORE_PATH)
    durable_only = durable_index_only if durable_index_only is not None else DURABLE_INDEX_ONLY
    retention_runs = pipeline_retention_runs if pipeline_retention_runs is not None else PIPELINE_RETENTION_RUNS
    pipeline_store = JsonlIndexPipelineStore(pipeline_store_path or PIPELINE_STORE_PATH, max_retention=retention_runs)
    sla_seconds = freshness_sla_seconds if freshness_sla_seconds is not None else FRESHNESS_SLA_SECONDS
    pipeline = IncrementalIndexPipeline(durable_repository, pipeline_store, freshness_sla_seconds=sla_seconds)

    # Initialize retrievers
    kw_retriever = KeywordRetriever()
    ft_retriever = FullTextRetriever()
    embedding_backend = vector_embedding_backend
    if embedding_backend is None and VECTOR_EMBEDDING_ENABLED:
        embedding_backend = MockVectorEmbeddingBackend()
    sem_retriever = SemanticRetriever(embedding_backend=embedding_backend)
    hyb_retriever = HybridRetriever(lexical_retriever=ft_retriever, semantic_retriever=sem_retriever)
    alpha_eng = alpha_engine or StructuredAlphaEngine()

    def _build_gateway(repository: InMemoryEvidenceRepository, adapter: KeywordIndexAdapter | None = None) -> SearchGateway:
        return SearchGateway(
            repository=repository,
            retriever=kw_retriever,
            full_text_retriever=ft_retriever,
            semantic_retriever=sem_retriever,
            hybrid_retriever=hyb_retriever,
            alpha_engine=alpha_eng,
            index_store=store,
            index_adapter=adapter,
        )

    def _materialize_index_state(
        *,
        triggered_by: str = "manual_materialize",
        trigger_ref: str | None = None,
        source_completion: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        durable_repository.reload()
        adapter = KeywordIndexAdapter(
            durable_repository,
            source_watermarks=_source_watermarks(durable_repository),
            adapter_state="materialized",
        )
        state: dict[str, Any] = {
            "materialized_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "triggered_by": triggered_by,
            "trigger_ref": trigger_ref,
            "indexed_object_count": len(durable_repository.list_knowledge_objects()),
            "index": adapter.snapshot().to_dict(),
            "evidence_store_path": str(durable_repository.path),
        }
        if source_completion is not None:
            state["source_completion"] = dict(source_completion)
        materialize_store.record_materialize(state)
        return state

    def _source_completion_truth(ingest_run_id: str) -> dict[str, Any]:
        pipeline_store.reload()
        materialize_store.reload()
        runs = [
            run
            for run in pipeline_store.list_runs(limit=pipeline_store.max_retention)
            if run.trigger_ref == ingest_run_id
        ]
        latest = runs[-1] if runs else None
        materialized = materialize_store.get_last()
        materialized_trigger_ref = materialized.get("trigger_ref") if materialized else None
        freshness = pipeline.freshness_status()
        index_refreshed = latest is not None
        materialized_matches_completion = bool(materialized and materialized_trigger_ref == ingest_run_id)
        return {
            "schema_version": "source_search_completion_truth.v1",
            "ingest_run_id": ingest_run_id,
            "status": "refreshed" if index_refreshed else "missing_refresh",
            "index_refreshed": index_refreshed,
            "pipeline_run_id": latest.pipeline_run_id if latest else None,
            "pipeline_triggered_by": latest.triggered_by if latest else None,
            "pipeline_indexed_at": latest.indexed_at if latest else None,
            "pipeline_indexed_count": latest.indexed_count if latest else None,
            "freshness_status": freshness.get("status"),
            "freshness_within_sla": bool(freshness.get("within_sla")),
            "materialized": bool(materialized),
            "materialized_at": materialized.get("materialized_at") if materialized else None,
            "materialized_trigger_ref": materialized_trigger_ref,
            "materialized_matches_completion": materialized_matches_completion,
        }

    register_fastapi_health_routes(
        app,
        "pantheon-search",
        dependencies=lambda: {
            "source_search_posture": PRODUCTION_POSTURE.to_dict(),
        },
        metrics=lambda: {
            "snapshot_count": len(store.list_snapshots()),
            "indexed_object_count": len(durable_repository.list_knowledge_objects()),
            "pipeline_run_count": pipeline_store.count_runs(),
            "posture_alert_count": PRODUCTION_POSTURE.alert_count(),
        },
        details=lambda: {
            "index_store_path": str(store.path),
            "evidence_store_path": str(durable_repository.path),
            "pipeline_store_path": str(pipeline_store.path),
            "pipeline_retention_runs": pipeline_store.max_retention,
            "freshness_sla_seconds": sla_seconds,
            "source_search_posture": PRODUCTION_POSTURE.to_dict(),
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
            "pipeline_run_count": pipeline_store.count_runs(),
            "pipeline_retention_runs": pipeline_store.max_retention,
            "freshness_sla_seconds": sla_seconds,
            "durable_index_only": durable_only,
            "capabilities": _build_gateway(durable_repository).get_capabilities(),
            "source_search_posture": PRODUCTION_POSTURE.to_dict(),
            "posture_alert_count": PRODUCTION_POSTURE.alert_count(),
        }

    @app.get("/api/search/capabilities")
    def search_capabilities() -> dict[str, Any]:
        """Report retrieval mode capabilities honestly."""
        return _build_gateway(durable_repository).get_capabilities()

    @app.post("/api/search/index/refresh")
    def refresh_index(body: IndexRefreshBody | None = Body(default=None)) -> dict[str, Any]:
        """Trigger incremental (or full) index refresh from current evidence repository."""
        try:
            triggered_by = (body.triggered_by if body else "manual").strip() or "manual"
            trigger_ref = body.trigger_ref if body else None
            force_full = body.force_full if body else False
            snapshot = pipeline.run(
                triggered_by=triggered_by,
                trigger_ref=trigger_ref,
                force_full=force_full,
            )
            return {"pipeline_snapshot": snapshot.to_dict()}
        except EvidenceValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/search/index/freshness")
    def index_freshness() -> dict[str, Any]:
        """Query index freshness SLA status."""
        pipeline_store.reload()
        return pipeline.freshness_status()

    @app.get("/api/search/index/pipeline-runs")
    def list_pipeline_runs(limit: int = 50) -> dict[str, Any]:
        """List recent pipeline run snapshots."""
        pipeline_store.reload()
        runs = pipeline_store.list_runs(limit=min(max(limit, 1), 200))
        return {
            "runs": [r.to_dict() for r in reversed(runs)],
            "total": pipeline_store.count_runs(),
            "retention_runs": pipeline_store.max_retention,
        }

    @app.post("/api/search/index/source-completions")
    def record_source_completion(body: SourceCompletionBody) -> dict[str, Any]:
        """Record source ingest completion and reconcile search freshness/materialization truth."""
        ingest_run_id = body.ingest_run_id.strip()
        if not ingest_run_id:
            raise HTTPException(status_code=400, detail="ingest_run_id is required")
        completion = {
            "schema_version": "source_ingest_completion.v1",
            "ingest_run_id": ingest_run_id,
            "connector_id": body.connector_id,
            "source_type": body.source_type,
            "trace_id": body.trace_id,
            "normalized_count": body.normalized_count,
            "source_ids": list(body.source_ids),
            "evidence_bundle_id": body.evidence_bundle_id,
            "knowledge_object_ids": list(body.knowledge_object_ids),
        }
        try:
            snapshot = pipeline.run(
                triggered_by="ingest_completion",
                trigger_ref=ingest_run_id,
                force_full=body.force_full,
            )
            pipeline_store.reload()
            freshness = pipeline.freshness_status()
            materialized = (
                _materialize_index_state(
                    triggered_by="ingest_completion",
                    trigger_ref=ingest_run_id,
                    source_completion=completion,
                )
                if body.materialize
                else None
            )
            return {
                "schema_version": "source_search_completion_refresh.v1",
                "completion": completion,
                "pipeline_snapshot": snapshot.to_dict(),
                "freshness": freshness,
                "materialized_index": materialized,
                "truth": _source_completion_truth(ingest_run_id),
            }
        except EvidenceValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/search/index/source-completions/{ingest_run_id}")
    def get_source_completion_truth(ingest_run_id: str) -> dict[str, Any]:
        """Replay source-to-search refresh truth for a completed ingest run."""
        ingest_run_id = ingest_run_id.strip()
        if not ingest_run_id:
            raise HTTPException(status_code=400, detail="ingest_run_id is required")
        return {"truth": _source_completion_truth(ingest_run_id)}

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
                if durable_only:
                    raise ValueError(
                        "request documents are not allowed in durable-index-only mode; "
                        "staging and production paths must use the durable evidence index"
                    )
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
                schema_version=body.schema_version,
                request_id=body.request_id,
                query=body.query,
                retrieval_mode=body.retrieval_mode,
                actor_ref=body.actor_ref or body.access_context.actor_ref,
                persona_id=body.persona_id or body.access_context.persona_id,
                workspace_id=body.workspace_id or body.access_context.workspace_id,
                role_refs=body.role_refs or body.access_context.role_refs,
                source_types=body.source_types,
                environment=body.environment or body.access_context.environment,
                purpose=body.purpose,
                time_window=body.time_window,
                filters=body.filters,
                structured_alpha=body.structured_alpha,
                top_k=body.top_k,
                require_citations=body.require_citations,
                trace_id=body.trace_id,
                filters_applied=body.filters_applied,
            )
            context = body.access_context.to_domain()
            context.require_persona_workspace()

            gateway = _build_gateway(repository, adapter=index_adapter)
            response = gateway.search(request, context)
            snapshot = store.get_snapshot(response.request_id)
            return {
                **response.to_dict(),
                "index_adapter": index_adapter.snapshot().to_dict(),
                "index_snapshot": snapshot.to_dict() if snapshot else None,
            }
        except SearchCapabilityUnavailableError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except (EvidenceValidationError, SearchPolicyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/search/query")
    def query_search(body: SearchQueryBody) -> dict[str, Any]:
        return _query_search(body)

    @app.post("/api/search/v2/query")
    def query_search_v2(body: SearchQueryBody) -> dict[str, Any]:
        return _query_search(body)

    @app.post("/api/search/v2/alpha-query")
    def query_structured_alpha(body: StructuredAlphaQueryBody) -> dict[str, Any]:
        """Execute a constrained structured alpha rule query."""
        try:
            alpha_query = StructuredAlphaQuery(
                schema_version=body.schema_version,
                dataset_ref=body.dataset_ref,
                universe=body.universe,
                as_of=body.as_of,
                rule=body.rule,
                sort=body.sort,
                limit=body.limit,
            )
            context = body.access_context.to_domain()
            context.require_persona_workspace()

            request = SearchRequest(
                schema_version="governed_search_request.v2",
                request_id=body.request_id,
                query="",
                retrieval_mode="structured_alpha",
                actor_ref=context.actor_ref,
                persona_id=context.persona_id,
                workspace_id=context.workspace_id,
                role_refs=context.role_refs,
                environment=context.environment,
                structured_alpha=alpha_query.to_dict(),
                top_k=body.limit,
                trace_id=body.trace_id,
            )
            gateway = _build_gateway(durable_repository)
            response = gateway.search(request, context)
            snapshot = store.get_snapshot(response.request_id)
            return {
                **response.to_dict(),
                "index_snapshot": snapshot.to_dict() if snapshot else None,
            }
        except SearchCapabilityUnavailableError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except (EvidenceValidationError, SearchPolicyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/search/query/request-documents-compat")
    def query_search_request_documents_compat(body: SearchQueryBody, response: Response) -> dict[str, Any]:
        response.headers["Deprecation"] = "true"
        response.headers["X-Search-Path"] = "request_documents_compat"
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
            return _materialize_index_state()
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
