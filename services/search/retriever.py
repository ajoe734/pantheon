"""Deterministic keyword retriever used behind the governed search gateway."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List

from services.knowledge.evidence.models import KnowledgeObject


@dataclass(frozen=True)
class KeywordMatch:
    knowledge_object: KnowledgeObject
    score: float
    matched_terms: tuple[str, ...]
    updated_at: datetime | None


def _parse_time(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class KeywordRetriever:
    """Simple deterministic ranker.

    The BFF already publishes a stable relevance score for existing read-model
    documents; this ranker preserves that signal while requiring term matches
    against governed knowledge objects.
    """

    def retrieve(self, query: str, knowledge_objects: Iterable[KnowledgeObject], *, top_k: int) -> List[KeywordMatch]:
        query_terms = tuple(token for token in str(query).strip().lower().split() if token)
        matches: list[KeywordMatch] = []
        for knowledge_object in knowledge_objects:
            metadata = knowledge_object.metadata
            search_text = " ".join(
                [
                    knowledge_object.title,
                    knowledge_object.text,
                    str(metadata.get("search_text") or ""),
                    " ".join(knowledge_object.keywords),
                ]
            ).lower()
            matched = tuple(token for token in query_terms if token in search_text)
            if not matched:
                continue

            title_text = knowledge_object.title.lower()
            title_hits = sum(1 for token in query_terms if token in title_text)
            base_score = float(metadata.get("relevance_score") or 0.0)
            score = round(
                min(0.999, max(base_score, base_score + len(matched) * 0.01 + title_hits * 0.015)),
                3,
            )
            updated_at = _parse_time(metadata.get("updated_at") or metadata.get("indexed_at") or metadata.get("created_at"))
            matches.append(
                KeywordMatch(
                    knowledge_object=knowledge_object,
                    score=score,
                    matched_terms=matched,
                    updated_at=updated_at,
                )
            )

        matches.sort(key=lambda item: (item.score, item.updated_at or datetime.min), reverse=True)
        return matches[:top_k]
