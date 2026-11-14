"""Memory retrieval facade over the Hindsight index (H6).

Hindsight recall proposes canonical ids; the canonical stores are the only
source of entries.  Every id is hydrated and authorised from the owner store
(tenant, persona, scope, activity, expiry, filters) before it is returned, and
``usefulness_score`` is the final re-rank.  Keyword ranking is used only as an
explicit fallback whose reason is reported to the caller.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from types import SimpleNamespace
from typing import Any, Iterable, List, Optional
from uuid import uuid4

from . import hindsight_index as hs
from .institutional_memory_store import (
    InstitutionalMemoryStore,
    RetrievalHit,
    _parse_utc_timestamp,
)
from .persona_memory_store import (
    PersonaMemoryStore,
    PersonaRetrievalHit,
)
from .usefulness import usefulness_score

_LOG = logging.getLogger(__name__)
_GLOBAL_SEARCH_BACKEND: Any = None


class SearchServiceClient:
    """Search-service client for non-memory record kinds (e.g. negative memory).

    Memory is no longer projected into the search index (H7); this client only
    serves callers that search other record kinds through the governed gateway.
    """

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
    return SearchServiceClient(url) if url else None


def set_search_retrieval_backend(backend: Any) -> None:
    global _GLOBAL_SEARCH_BACKEND
    _GLOBAL_SEARCH_BACKEND = backend


MODE_HINDSIGHT = "hindsight"
MODE_FALLBACK = "keyword_fallback"


def _fallback(diagnostics: dict, reason: str) -> None:
    diagnostics.update(retrieval_mode=MODE_FALLBACK, fallback_reason=reason)
    _LOG.warning("memory_retrieval_fallback reason=%s", reason)


def _candidates(client: Any, bank: str, *, query: str, groups: List[dict], limit: int,
                diagnostics: dict) -> Optional[List[Any]]:
    """Hindsight candidates, or ``None`` after recording the fallback reason."""
    if not query.strip():
        _fallback(diagnostics, "empty_query")
        return None
    if client is None:
        _fallback(diagnostics, "hindsight_unconfigured")
        return None
    try:
        recalled = hs.recall_canonical_ids(client, bank, query=query, groups=groups,
                                           limit=max(limit * 3, 20))
    except hs.HindsightError as exc:
        _fallback(diagnostics, exc.reason)
        return None
    diagnostics.update(retrieval_mode=MODE_HINDSIGHT, fallback_reason=None)
    return [SimpleNamespace(id=cid, score=score) for cid, score in recalled]


def _tag_set(entry: Any) -> set:
    return {str(t).strip().lower() for t in (entry.content.get("tags", []) or []) if str(t).strip()}


def _sorted(hits: list) -> list:
    hits.sort(key=lambda h: (h.relevance_score, usefulness_score(h.entry),
                             _parse_utc_timestamp(h.entry.written_at)), reverse=True)
    return hits


def retrieve_institutional(
    store: InstitutionalMemoryStore,
    *,
    client: Any = None,
    query: str = "",
    tenant_id: Optional[str] = None,
    knowledge_type: Optional[str] = None,
    scope: Optional[str] = None,
    scope_filter: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    subject_anchors: Iterable[str] = (),
    limit: int = 10,
    diagnostics: Optional[dict] = None,
    review_required: bool = False,
) -> List[RetrievalHit]:
    if limit <= 0:
        return []
    diagnostics = diagnostics if diagnostics is not None else {}
    store.archive_expired()
    groups = hs.tag_groups(subject_anchors=subject_anchors,
                           type_tag=f"type:{knowledge_type}" if knowledge_type else None)
    candidates = _candidates(client, hs.facts_bank(tenant_id), query=query, groups=groups,
                             limit=limit, diagnostics=diagnostics)
    if candidates is None:
        candidates = [SimpleNamespace(id=h.entry.entry_id, score=(h.relevance_score - usefulness_score(h.entry)) / 100)
                      for h in store.retrieve(query=query, knowledge_type=knowledge_type, scope=scope,
                                              scope_filter=scope_filter, tags=tags, limit=len(store.list()))]
    anchors = {hs.parse_subject_anchor(a) for a in subject_anchors} - {None}
    wanted_tags = {str(t).strip().lower() for t in (tags or []) if str(t).strip()}
    hits: List[RetrievalHit] = []
    seen: set = set()
    for cand in candidates:
        if cand.id in seen:
            continue
        seen.add(cand.id)
        entry = store.get(cand.id)
        if entry is None or not entry.is_active or entry.is_expired():
            continue
        if review_required and not entry.memory_review:
            continue
        if tenant_id is not None and (entry.tenant_id or "default") != tenant_id:
            continue
        if (scope and entry.scope != scope) or (scope_filter and entry.scope_filter != scope_filter):
            continue
        if knowledge_type and entry.knowledge_type != knowledge_type:
            continue
        if wanted_tags and wanted_tags.isdisjoint(_tag_set(entry)):
            continue
        if anchors and anchors.isdisjoint(hs.anchors_for(entry)):
            continue
        hits.append(RetrievalHit(entry=entry, relevance_score=round(cand.score * 100.0 + usefulness_score(entry), 4)))
    return _sorted(hits)[:limit]


def retrieve_persona(
    store: PersonaMemoryStore,
    persona_id: str,
    *,
    client: Any = None,
    query: str = "",
    tenant_id: Optional[str] = None,
    memory_type: Optional[str] = None,
    relevance_scope: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    subject_anchors: Iterable[str] = (),
    limit: int = 10,
    diagnostics: Optional[dict] = None,
    review_required: bool = False,
) -> List[PersonaRetrievalHit]:
    if limit <= 0 or not persona_id:
        return []
    diagnostics = diagnostics if diagnostics is not None else {}
    store.archive_expired()
    committee = relevance_scope == "persona_and_committee"
    groups = hs.tag_groups(subject_anchors=subject_anchors,
                           scope_tags=["scope:committee"] if committee else (),
                           type_tag=f"type:{memory_type}" if memory_type else None)
    candidates = _candidates(client, hs.persona_bank(tenant_id, persona_id), query=query, groups=groups,
                             limit=limit, diagnostics=diagnostics)
    if candidates is None:
        candidates = [SimpleNamespace(id=h.entry.memory_id, score=(h.relevance_score - usefulness_score(h.entry)) / 100)
                      for h in store.retrieve(persona_id=persona_id, query=query, memory_type=memory_type,
                                              relevance_scope=relevance_scope, tags=tags, limit=len(store.list()))]
    anchors = {hs.parse_subject_anchor(a) for a in subject_anchors} - {None}
    wanted_tags = {str(t).strip().lower() for t in (tags or []) if str(t).strip()}
    hits: List[PersonaRetrievalHit] = []
    seen: set = set()
    for cand in candidates:
        if cand.id in seen:
            continue
        seen.add(cand.id)
        entry = store.get(cand.id)
        if entry is None or not entry.is_active or entry.is_expired():
            continue
        if review_required and not entry.memory_review:
            continue
        if tenant_id is not None and (entry.tenant_id or "default") != tenant_id:
            continue
        if entry.persona_id != persona_id:
            continue  # never cross personas, whatever the index returned
        if memory_type and entry.memory_type != memory_type:
            continue
        if relevance_scope and entry.relevance_scope != relevance_scope:
            continue
        if wanted_tags and wanted_tags.isdisjoint(_tag_set(entry)):
            continue
        if anchors and anchors.isdisjoint(hs.anchors_for(entry)):
            continue
        hits.append(PersonaRetrievalHit(entry=entry, relevance_score=round(cand.score * 100.0 + usefulness_score(entry), 4)))
    return _sorted(hits)[:limit]
