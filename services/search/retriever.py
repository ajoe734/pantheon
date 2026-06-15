"""Deterministic keyword retriever used behind the governed search gateway."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List

from services.knowledge.evidence.models import KnowledgeObject
from services.search.index_adapter import SearchIndexDocument


@dataclass(frozen=True)
class KeywordMatch:
    knowledge_object: KnowledgeObject
    score: float
    matched_terms: tuple[str, ...]
    updated_at: datetime | None


class KeywordRetriever:
    """Simple deterministic ranker.

    The BFF already publishes a stable relevance score for existing read-model
    documents; this ranker preserves that signal while requiring term matches
    against governed knowledge objects.
    """

    def retrieve(self, query: str, documents: Iterable[SearchIndexDocument], *, top_k: int) -> List[KeywordMatch]:
        query_terms = tuple(token for token in str(query).strip().lower().split() if token)
        matches: list[KeywordMatch] = []
        for document in documents:
            knowledge_object = document.knowledge_object
            search_text = document.search_text.lower()
            matched = tuple(token for token in query_terms if token in search_text)
            if not matched:
                continue

            title_text = knowledge_object.title.lower()
            title_hits = sum(1 for token in query_terms if token in title_text)
            base_score = document.relevance_score
            score = round(
                min(0.999, max(base_score, base_score + len(matched) * 0.01 + title_hits * 0.015)),
                3,
            )
            matches.append(
                KeywordMatch(
                    knowledge_object=knowledge_object,
                    score=score,
                    matched_terms=matched,
                    updated_at=document.indexed_at,
                )
            )

        # Normalize the datetime tie-breaker to naive: updated_at is parsed from
        # the index (aware for "...Z", naive otherwise), and mixing it with the
        # naive datetime.min floor would raise TypeError when two matches tie on
        # score. Strip tzinfo so the key is uniform.
        matches.sort(
            key=lambda item: (
                item.score,
                (item.updated_at or datetime.min).replace(tzinfo=None),
            ),
            reverse=True,
        )
        return matches[:top_k]
