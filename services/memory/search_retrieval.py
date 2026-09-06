"""Unified Search backend integration for Institutional and Persona Memory.

Provides:
- Projection from canonical memory entries to search index records.
- Candidate retrieval delegation to the governed Search backend.
- Owner hydration, authorization, and active/expiry/supersession revalidation.
- Unification of semantic retrieval across memory stores without altering canonical write ownership.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, List, Optional

from services.search.filters import SearchAccessContext, SearchFilters
from services.search.pg_retrieval import PostgresRetrievalBackend, RetrievalIndexRecord

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

_GLOBAL_SEARCH_BACKEND: Optional[PostgresRetrievalBackend] = None


def get_search_retrieval_backend() -> Optional[PostgresRetrievalBackend]:
    global _GLOBAL_SEARCH_BACKEND
    if _GLOBAL_SEARCH_BACKEND is not None:
        return _GLOBAL_SEARCH_BACKEND
    dsn = os.getenv("PANTHEON_SEARCH_POSTGRES_DSN")
    if dsn:
        try:
            backend = PostgresRetrievalBackend(dsn=dsn)
            health = backend.check_health()
            if health.get("status") == "ok":
                _GLOBAL_SEARCH_BACKEND = backend
                return _GLOBAL_SEARCH_BACKEND
        except Exception:
            pass
    return None


def set_search_retrieval_backend(backend: Optional[PostgresRetrievalBackend]) -> None:
    global _GLOBAL_SEARCH_BACKEND
    _GLOBAL_SEARCH_BACKEND = backend


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
        tenant_id="default",
        persona_id=None,
        workspace_id=None,
        environment_scope=["paper", "live"],
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
        relevance_score=float(entry.reuse_count),
        metadata={
            "entry_id": entry.entry_id,
            "knowledge_type": entry.knowledge_type,
            "source_event_type": entry.source_event_type,
            "source_event_id": entry.source_event_id,
            "scope": entry.scope,
            "scope_filter": entry.scope_filter,
            "write_authority": entry.write_authority,
            "superseded_by": entry.superseded_by,
        },
        version=1,
        is_active=entry.is_active,
    )


def project_persona_entry(entry: PersonaMemoryEntry) -> RetrievalIndexRecord:
    summary = entry.content.get("summary", "")
    tags = " ".join(entry.content.get("tags", []) or [])
    search_text = f"{summary} {tags} {entry.source_event_type} {entry.source_event_id}".strip()

    return RetrievalIndexRecord(
        id=entry.memory_id,
        record_kind="persona_memory",
        tenant_id="default",
        persona_id=entry.persona_id,
        workspace_id=None,
        environment_scope=["paper", "live"],
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
        relevance_score=float(entry.reuse_count),
        metadata={
            "memory_id": entry.memory_id,
            "persona_id": entry.persona_id,
            "memory_type": entry.memory_type,
            "relevance_scope": entry.relevance_scope,
            "source_event_type": entry.source_event_type,
            "source_event_id": entry.source_event_id,
            "write_authority": entry.write_authority,
            "superseded_by": entry.superseded_by,
        },
        version=1,
        is_active=entry.is_active,
    )


def retrieve_institutional_with_backend(
    store: InstitutionalMemoryStore,
    backend: Optional[PostgresRetrievalBackend] = None,
    *,
    query: str = "",
    knowledge_type: Optional[str] = None,
    scope: Optional[str] = None,
    scope_filter: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    limit: int = 10,
) -> List[RetrievalHit]:
    if limit <= 0:
        return []

    active_backend = backend or get_search_retrieval_backend()
    if active_backend is None or not query.strip():
        # Fallback to local store implementation when backend unavailable or empty query
        return store.retrieve(
            query=query,
            knowledge_type=knowledge_type,
            scope=scope,
            scope_filter=scope_filter,
            tags=tags,
            limit=limit,
        )

    context = SearchAccessContext(
        persona_id=None,
        workspace_id=None,
        environment="paper",
        access_scopes=["public", "operator", "research"],
    )
    filters = SearchFilters(
        source_types=(knowledge_type,) if knowledge_type else (),
    )

    candidate_hits = active_backend.search(
        query=query,
        context=context,
        filters=filters,
        top_k=max(limit * 3, 20),
        mode="hybrid",
        record_kind="institutional_memory",
    )

    hits: List[RetrievalHit] = []
    normalized_tags = {tag.strip().lower() for tag in (tags or []) if str(tag).strip()}
    seen_ids = set()

    for cand in candidate_hits:
        if cand.id in seen_ids:
            continue
        seen_ids.add(cand.id)

        entry = store.get(cand.id)
        if entry is None or not entry.is_active:
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

        # Combine backend score with canonical reuse count
        final_score = round(cand.score * 100.0 + float(entry.reuse_count), 4)
        hits.append(RetrievalHit(entry=entry, relevance_score=final_score))
        if len(hits) >= limit:
            break

    # If backend returned fewer than limit (e.g. out of vocabulary or fresh writes), augment from store
    if len(hits) < limit:
        store_hits = store.retrieve(
            query=query,
            knowledge_type=knowledge_type,
            scope=scope,
            scope_filter=scope_filter,
            tags=tags,
            limit=limit,
        )
        for sh in store_hits:
            if sh.entry.entry_id not in seen_ids:
                hits.append(sh)
                seen_ids.add(sh.entry.entry_id)
                if len(hits) >= limit:
                    break

    hits.sort(
        key=lambda hit: (
            hit.relevance_score,
            hit.entry.reuse_count,
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
    memory_type: Optional[str] = None,
    relevance_scope: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    limit: int = 10,
) -> List[PersonaRetrievalHit]:
    if limit <= 0:
        return []
    if not persona_id:
        return []

    active_backend = backend or get_search_retrieval_backend()
    if active_backend is None or not query.strip():
        # Fallback to local store implementation when backend unavailable or empty query
        return store.retrieve(
            persona_id=persona_id,
            query=query,
            memory_type=memory_type,
            relevance_scope=relevance_scope,
            tags=tags,
            limit=limit,
        )

    context = SearchAccessContext(
        persona_id=persona_id,
        workspace_id="workspace-default",
        environment="paper",
        access_scopes=["operator", "research"],
    )
    filters = SearchFilters(
        source_types=(memory_type,) if memory_type else (),
    )

    candidate_hits = active_backend.search(
        query=query,
        context=context,
        filters=filters,
        top_k=max(limit * 3, 20),
        mode="hybrid",
        record_kind="persona_memory",
    )

    hits: List[PersonaRetrievalHit] = []
    normalized_tags = {tag.strip().lower() for tag in (tags or []) if str(tag).strip()}
    seen_ids = set()

    for cand in candidate_hits:
        if cand.id in seen_ids:
            continue
        seen_ids.add(cand.id)

        entry = store.get(cand.id)
        if entry is None or not entry.is_active:
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

        final_score = round(cand.score * 100.0 + float(entry.reuse_count), 4)
        hits.append(PersonaRetrievalHit(entry=entry, relevance_score=final_score))
        if len(hits) >= limit:
            break

    if len(hits) < limit:
        store_hits = store.retrieve(
            persona_id=persona_id,
            query=query,
            memory_type=memory_type,
            relevance_scope=relevance_scope,
            tags=tags,
            limit=limit,
        )
        for sh in store_hits:
            if sh.entry.memory_id not in seen_ids:
                hits.append(sh)
                seen_ids.add(sh.entry.memory_id)
                if len(hits) >= limit:
                    break

    hits.sort(
        key=lambda hit: (
            hit.relevance_score,
            hit.entry.reuse_count,
            _parse_utc_timestamp(hit.entry.written_at),
        ),
        reverse=True,
    )
    return hits[:limit]
