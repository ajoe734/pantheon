"""Unified Search backend integration for Institutional and Persona Memory.

Provides:
- Projection from canonical memory entries to search index records.
- Candidate retrieval delegation to the governed Search backend.
- Owner hydration, authorization, and active/expiry/supersession revalidation.
- Unification of semantic retrieval across memory stores without altering canonical write ownership.
"""

from __future__ import annotations

import os
import json
import logging
import urllib.request
from types import SimpleNamespace
from uuid import uuid4
from typing import Any, Iterable, List, Optional

from services.search.filters import SearchAccessContext, SearchFilters
from services.search.pg_retrieval import PostgresRetrievalBackend, RetrievalIndexRecord

from .usefulness import usefulness_score

from .institutional_memory_store import (
    InstitutionalMemoryEntry,
    InstitutionalMemoryStore,
    RetrievalHit,
    _parse_utc_timestamp,
)
from .persona_memory_store import (
    PersonaMemoryEntry,
    PersonaMemoryStore,
    PersonaRetrievalHit,
)

_GLOBAL_SEARCH_BACKEND: Any = None
_LOG = logging.getLogger(__name__)


class SearchServiceClient:
    """Memory never opens the retrieval database or runs an embedding model."""

    def __init__(self, url: str, token: str = "") -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.source_event_id = None
        self.last_mode = "hybrid"
        self.last_reason = None

    def _post(self, path: str, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        from services.service_token_file import configured_service_token
        token = self.token or configured_service_token("PANTHEON_SEARCH_SERVICE_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.url + path, data=json.dumps(payload).encode(),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)

    def project(self, entry: Any) -> dict:
        kind = "institutional_memory" if isinstance(entry, InstitutionalMemoryEntry) else "persona_memory"
        result = self._post("/api/search/index/refresh", {
            "memory_entries": [{"record_kind": kind, "entry": entry.to_dict()}],
        })
        if result.get("projected") != 1:
            raise ValueError("search_projection_acknowledgment_missing")
        return result

    def search(self, *, query, context, filters, top_k, mode, record_kind):
        result = self._post("/api/search/query", {
            "query": query, "memory_record_kind": record_kind,
            "request_id": str(uuid4()), "source_event_id": self.source_event_id,
            "top_k": top_k, "retrieval_mode": mode,
            "role_refs": ["system"],
            "filters": {"source_types": list(filters.source_types)},
            "access_context": {
                "tenant_id": context.tenant_id,
                "persona_id": context.persona_id or "memory-svc",
                "workspace_id": context.workspace_id or "memory",
                "role_refs": ["system"], "environment": "paper",
            },
        })
        if result.get("retrieval_mode") not in {"hybrid", "lexical_only"}:
            raise ValueError("search_retrieval_mode_invalid")
        self.last_mode = result["retrieval_mode"]
        self.last_reason = result.get("fallback_reason")
        return [SimpleNamespace(**hit) for hit in result["candidates"]]


def get_search_retrieval_backend() -> Any:
    if _GLOBAL_SEARCH_BACKEND is not None:
        return _GLOBAL_SEARCH_BACKEND
    url = os.getenv("PANTHEON_SEARCH_API_URL", "").strip()
    if not url:
        return None
    return SearchServiceClient(url)


def set_search_retrieval_backend(backend: Any) -> None:
    global _GLOBAL_SEARCH_BACKEND
    _GLOBAL_SEARCH_BACKEND = backend


def sync_memory_projection(entry: Any) -> dict:
    """Canonical writes survive index outages; failures are explicit and replayable."""
    entry_id = getattr(entry, "entry_id", None) or entry.memory_id
    try:
        backend = get_search_retrieval_backend()
        if backend is None:
            raise RuntimeError("search_service_unconfigured")
        backend.project(entry)
        return {"status": "projected", "entry_id": entry_id}
    except Exception as exc:
        reason = "search_service_unconfigured" if str(exc) == "search_service_unconfigured" else "search_projection_failed"
        _LOG.warning("memory_projection_failed entry_id=%s source_event_id=%s reason=%s error_type=%s",
                     entry_id, entry.source_event_id, reason, type(exc).__name__)
        return {"status": "failed", "entry_id": entry_id, "reason": reason}


def backfill_memory_index(institutional_store, persona_store) -> dict:
    """Operator CLI repair/rebuild; upserts include inactive entries as tombstones."""
    report = {"processed": 0, "projected": 0, "tombstones": 0, "failures": []}
    for store in (institutional_store, persona_store):
        store.archive_expired()
        for entry in store.list(active_only=False):
            report["processed"] += 1
            result = sync_memory_projection(entry)
            if result["status"] == "failed":
                report["failures"].append(result)
            else:
                report["projected"] += 1
                report["tombstones"] += int(not entry.is_active or entry.is_expired())
    return report


def _fallback(diagnostics, reason):
    diagnostics.update(retrieval_mode="keyword_fallback", fallback_reason=reason)
    _LOG.warning("memory_retrieval_fallback reason=%s", reason)


def project_institutional_entry(entry: InstitutionalMemoryEntry) -> RetrievalIndexRecord:
    headline = entry.content.get("headline", "")
    body = entry.content.get("body", "")
    tags = " ".join(entry.content.get("tags", []) or [])
    personas = " ".join(entry.contributing_persona_ids or [])
    search_text = f"{headline} {body} {tags} {personas}".strip()

    access_scope = ["public"] if entry.scope == "system_wide" else ["operator", "research"]
    return RetrievalIndexRecord(
        id=entry.entry_id,
        record_kind="institutional_memory",
        tenant_id=entry.tenant_id or "default",
        persona_id=None,
        workspace_id=None,
        environment_scope=["paper"],
        access_scope=access_scope,
        license_scope="internal",
        role_scope=[],
        sensitivity="internal",
        capital_pool_scope=[],
        source_type=entry.knowledge_type,
        asset_class=[],
        strategy_id=None,
        title=headline,
        search_text=search_text,
        content_ref=f"/memory/institutional/{entry.entry_id}",
        citation_label=f"institutional:{entry.entry_id}",
        evidence_bundle_id=f"bundle-mem-{entry.source_event_id}",
        evidence_item_id=f"item-mem-{entry.entry_id}",
        event_time=entry.written_at,
        available_time=entry.written_at,
        relevance_score=usefulness_score(entry),
        metadata={
            "entry_id": entry.entry_id,
            "tenant_id": entry.tenant_id,
            "knowledge_type": entry.knowledge_type,
            "source_event_type": entry.source_event_type,
            "source_event_id": entry.source_event_id,
            "scope": entry.scope,
            "scope_filter": entry.scope_filter,
            "write_authority": entry.write_authority,
            "superseded_by": entry.superseded_by,
            "expires_at": entry.expires_at,
            "archived_at": entry.archived_at,
        },
        version=1,
        is_active=entry.is_active and not entry.is_expired(),
    )


def project_persona_entry(entry: PersonaMemoryEntry) -> RetrievalIndexRecord:
    summary = entry.content.get("summary", "")
    tags = " ".join(entry.content.get("tags", []) or [])
    search_text = f"{summary} {tags} {entry.source_event_type} {entry.source_event_id}".strip()

    return RetrievalIndexRecord(
        id=entry.memory_id,
        record_kind="persona_memory",
        tenant_id=entry.tenant_id or "default",
        persona_id=entry.persona_id,
        workspace_id=None,
        environment_scope=["paper"],
        access_scope=["operator", "research"],
        license_scope="internal",
        role_scope=[],
        sensitivity="internal",
        capital_pool_scope=[],
        source_type=entry.memory_type,
        asset_class=[],
        strategy_id=None,
        title=summary[:120],
        search_text=search_text,
        content_ref=f"/memory/persona/{entry.memory_id}",
        citation_label=f"persona:{entry.persona_id}:{entry.memory_id}",
        evidence_bundle_id=f"bundle-persona-{entry.source_event_id}",
        evidence_item_id=f"item-persona-{entry.memory_id}",
        event_time=entry.written_at,
        available_time=entry.written_at,
        relevance_score=usefulness_score(entry),
        metadata={
            "memory_id": entry.memory_id,
            "tenant_id": entry.tenant_id,
            "persona_id": entry.persona_id,
            "memory_type": entry.memory_type,
            "relevance_scope": entry.relevance_scope,
            "source_event_type": entry.source_event_type,
            "source_event_id": entry.source_event_id,
            "write_authority": entry.write_authority,
            "superseded_by": entry.superseded_by,
            "expires_at": entry.expires_at,
            "archived_at": entry.archived_at,
        },
        version=1,
        is_active=entry.is_active and not entry.is_expired(),
    )


def retrieve_institutional_with_backend(
    store: InstitutionalMemoryStore,
    backend: Optional[PostgresRetrievalBackend] = None,
    *,
    query: str = "",
    tenant_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    knowledge_type: Optional[str] = None,
    scope: Optional[str] = None,
    scope_filter: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    limit: int = 10,
    diagnostics: Optional[dict] = None,
    review_required: bool = False,
) -> List[RetrievalHit]:
    if limit <= 0:
        return []

    diagnostics = diagnostics if diagnostics is not None else {}
    store.archive_expired()
    active_backend = backend or get_search_retrieval_backend()
    candidate_hits = None
    if active_backend is not None and query.strip():
        try:
            candidate_hits = active_backend.search(
                query=query,
                context=SearchAccessContext(tenant_id=tenant_id or "default", persona_id=persona_id,
                                            workspace_id=workspace_id, environment="paper",
                                            access_scopes=["public", "operator", "research"]),
                filters=SearchFilters(source_types=(knowledge_type,) if knowledge_type else ()),
                top_k=max(limit * 3, 20), mode="hybrid", record_kind="institutional_memory",
            )
            diagnostics.update(retrieval_mode=getattr(active_backend, "last_mode", "hybrid"),
                               fallback_reason=getattr(active_backend, "last_reason", None))
        except Exception:
            _fallback(diagnostics, "search_service_unavailable")
    else:
        _fallback(diagnostics, "empty_query" if not query.strip() else "search_service_unconfigured")
    if candidate_hits is None:
        candidate_hits = [SimpleNamespace(id=hit.entry.entry_id, score=(hit.relevance_score - usefulness_score(hit.entry)) / 100)
                          for hit in store.retrieve(query=query, knowledge_type=knowledge_type, scope=scope,
                                                    scope_filter=scope_filter, tags=tags, limit=len(store.list()))]

    hits: List[RetrievalHit] = []
    normalized_tags = {tag.strip().lower() for tag in (tags or []) if str(tag).strip()}
    seen_ids = set()

    for cand in candidate_hits:
        if cand.id in seen_ids:
            continue
        seen_ids.add(cand.id)

        entry = store.get(cand.id)
        if entry is None or not entry.is_active or entry.is_expired():
            continue
        if review_required and not entry.memory_review:
            continue
        if tenant_id is not None and (entry.tenant_id or "default") != tenant_id:
            continue

        if scope and entry.scope != scope:
            continue
        if scope_filter and entry.scope_filter != scope_filter:
            continue
        if knowledge_type and entry.knowledge_type != knowledge_type:
            continue

        if normalized_tags:
            entry_tags = {tag.strip().lower() for tag in (entry.content.get("tags", []) or []) if str(tag).strip()}
            if normalized_tags.isdisjoint(entry_tags):
                continue

        # Combine backend relevance with bounded canonical usefulness
        final_score = round(cand.score * 100.0 + usefulness_score(entry), 4)
        hits.append(RetrievalHit(entry=entry, relevance_score=final_score))

    hits.sort(
        key=lambda hit: (
            hit.relevance_score,
            usefulness_score(hit.entry),
            _parse_utc_timestamp(hit.entry.written_at),
        ),
        reverse=True,
    )
    return hits[:limit]


def retrieve_persona_with_backend(
    store: PersonaMemoryStore,
    persona_id: str,
    backend: Optional[PostgresRetrievalBackend] = None,
    *,
    query: str = "",
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    memory_type: Optional[str] = None,
    relevance_scope: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    limit: int = 10,
    diagnostics: Optional[dict] = None,
    review_required: bool = False,
) -> List[PersonaRetrievalHit]:
    if limit <= 0:
        return []
    if not persona_id:
        return []

    diagnostics = diagnostics if diagnostics is not None else {}
    store.archive_expired()
    active_backend = backend or get_search_retrieval_backend()
    candidate_hits = None
    if active_backend is not None and query.strip():
        try:
            candidate_hits = active_backend.search(
                query=query,
                context=SearchAccessContext(tenant_id=tenant_id or "default", persona_id=persona_id,
                                            workspace_id=workspace_id, environment="paper",
                                            access_scopes=["operator", "research"]),
                filters=SearchFilters(source_types=(memory_type,) if memory_type else ()),
                top_k=max(limit * 3, 20), mode="hybrid", record_kind="persona_memory",
            )
            diagnostics.update(retrieval_mode=getattr(active_backend, "last_mode", "hybrid"),
                               fallback_reason=getattr(active_backend, "last_reason", None))
        except Exception:
            _fallback(diagnostics, "search_service_unavailable")
    else:
        _fallback(diagnostics, "empty_query" if not query.strip() else "search_service_unconfigured")
    if candidate_hits is None:
        candidate_hits = [SimpleNamespace(id=hit.entry.memory_id, score=(hit.relevance_score - usefulness_score(hit.entry)) / 100)
                          for hit in store.retrieve(persona_id=persona_id, query=query, memory_type=memory_type,
                                                    relevance_scope=relevance_scope, tags=tags, limit=len(store.list()))]

    hits: List[PersonaRetrievalHit] = []
    normalized_tags = {tag.strip().lower() for tag in (tags or []) if str(tag).strip()}
    seen_ids = set()

    for cand in candidate_hits:
        if cand.id in seen_ids:
            continue
        seen_ids.add(cand.id)

        entry = store.get(cand.id)
        if entry is None or not entry.is_active or (callable(getattr(entry, "is_expired", None)) and entry.is_expired()):
            continue
        if review_required and not entry.memory_review:
            continue
        if tenant_id is not None and (entry.tenant_id or "default") != tenant_id:
            continue
        if entry.persona_id != persona_id:
            # Revalidation guard against cross-persona leakage
            continue
        if memory_type and entry.memory_type != memory_type:
            continue
        if relevance_scope and entry.relevance_scope != relevance_scope:
            continue

        if normalized_tags:
            entry_tags = {tag.strip().lower() for tag in (entry.content.get("tags", []) or []) if str(tag).strip()}
            if normalized_tags.isdisjoint(entry_tags):
                continue

        final_score = round(cand.score * 100.0 + usefulness_score(entry), 4)
        hits.append(PersonaRetrievalHit(entry=entry, relevance_score=final_score))

    hits.sort(
        key=lambda hit: (
            hit.relevance_score,
            usefulness_score(hit.entry),
            _parse_utc_timestamp(hit.entry.written_at),
        ),
        reverse=True,
    )
    return hits[:limit]


if __name__ == "__main__":
    from .main import _store, _persona_store
    report = backfill_memory_index(_store(), _persona_store())
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(1 if report["failures"] else 0)
