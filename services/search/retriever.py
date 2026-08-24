"""Deterministic lexical, full-text, and semantic retrievers for governed search."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, List, Mapping, Sequence

from services.knowledge.evidence.models import KnowledgeObject
from services.search.filters import SearchCapabilityUnavailableError
from services.search.index_adapter import SearchIndexDocument


@dataclass(frozen=True)
class KeywordMatch:
    knowledge_object: KnowledgeObject
    score: float
    matched_terms: tuple[str, ...]
    updated_at: datetime | None
    component_scores: dict[str, float] = field(default_factory=dict)
    ranker_version: str = "keyword-v1"


class KeywordRetriever:
    """Deterministic keyword ranker.

    Preserves baseline relevance score while requiring term matches against
    governed knowledge objects.
    """

    def __init__(self, ranker_version: str = "keyword-v1") -> None:
        self.ranker_version = ranker_version

    def is_available(self) -> bool:
        return True

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
                    component_scores={"keyword_score": score},
                    ranker_version=self.ranker_version,
                )
            )

        matches.sort(
            key=lambda item: (
                item.score,
                (item.updated_at or datetime.min).replace(tzinfo=None),
            ),
            reverse=True,
        )
        return matches[:top_k]


class FullTextRetriever:
    """Postgres-compatible full-text / BM25 lexical ranker."""

    def __init__(self, ranker_version: str = "fulltext-v1", k1: float = 1.2, b: float = 0.75) -> None:
        self.ranker_version = ranker_version
        self.k1 = k1
        self.b = b

    def is_available(self) -> bool:
        return True

    def _tokenize(self, text: str) -> list[str]:
        return [tok for tok in re.findall(r"\w+", text.lower()) if len(tok) > 1]

    def retrieve(self, query: str, documents: Iterable[SearchIndexDocument], *, top_k: int) -> List[KeywordMatch]:
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        doc_list = list(documents)
        if not doc_list:
            return []

        doc_token_counts = []
        doc_lengths = []
        df: dict[str, int] = {token: 0 for token in query_tokens}

        for doc in doc_list:
            tokens = self._tokenize(doc.search_text)
            doc_lengths.append(len(tokens))
            counts: dict[str, int] = {}
            for t in tokens:
                counts[t] = counts.get(t, 0) + 1
            doc_token_counts.append(counts)
            for token in query_tokens:
                if counts.get(token, 0) > 0:
                    df[token] += 1

        n_docs = len(doc_list)
        avg_doc_len = (sum(doc_lengths) / n_docs) if n_docs > 0 else 1.0

        matches: list[KeywordMatch] = []
        for idx, doc in enumerate(doc_list):
            counts = doc_token_counts[idx]
            matched = tuple(token for token in query_tokens if counts.get(token, 0) > 0)
            if not matched:
                continue

            doc_len = doc_lengths[idx]
            bm25_score = 0.0
            for token in matched:
                freq = counts.get(token, 0)
                doc_freq = df.get(token, 0)
                idf = math.log(1.0 + (n_docs - doc_freq + 0.5) / (doc_freq + 0.5))
                numerator = freq * (self.k1 + 1.0)
                denominator = freq + self.k1 * (1.0 - self.b + self.b * (doc_len / (avg_doc_len or 1.0)))
                bm25_score += max(0.0, idf * (numerator / (denominator or 1.0)))

            title_tokens = self._tokenize(doc.knowledge_object.title)
            title_hits = sum(1 for token in query_tokens if token in title_tokens)
            base_score = doc.relevance_score
            normalized_score = round(
                min(0.999, max(0.01, base_score * 0.5 + min(0.5, bm25_score * 0.1) + title_hits * 0.05)),
                3,
            )
            matches.append(
                KeywordMatch(
                    knowledge_object=doc.knowledge_object,
                    score=normalized_score,
                    matched_terms=matched,
                    updated_at=doc.indexed_at,
                    component_scores={"full_text_score": normalized_score, "bm25_raw": round(bm25_score, 4)},
                    ranker_version=self.ranker_version,
                )
            )

        matches.sort(
            key=lambda item: (
                item.score,
                (item.updated_at or datetime.min).replace(tzinfo=None),
            ),
            reverse=True,
        )
        return matches[:top_k]


class VectorEmbeddingBackend:
    """Interface for semantic embedding vector backends."""

    def is_ready(self) -> bool:
        return True

    def embed_text(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]


class MockVectorEmbeddingBackend(VectorEmbeddingBackend):
    """Deterministic hash-based embedding backend for local execution and unit testing."""

    def __init__(self, dimension: int = 32) -> None:
        self.dimension = dimension

    def is_ready(self) -> bool:
        return True

    def embed_text(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        tokens = [tok for tok in re.findall(r"\w+", text.lower()) if tok]
        if not tokens:
            return vec
        for tok in tokens:
            h = hash(tok)
            for i in range(self.dimension):
                vec[i] += ((h >> (i % 16)) & 0xFF) / 255.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [round(x / norm, 4) for x in vec]


def _cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


class SemanticRetriever:
    """Vector-based semantic retriever."""

    def __init__(
        self,
        embedding_backend: VectorEmbeddingBackend | None = None,
        model_name: str = "pantheon-vector-embedding",
        model_version: str = "1.0.0",
    ) -> None:
        self.embedding_backend = embedding_backend
        self.model_name = model_name
        self.model_version = model_version
        self.ranker_version = f"semantic-{model_name}:{model_version}"

    def is_available(self) -> bool:
        return self.embedding_backend is not None and self.embedding_backend.is_ready()

    def retrieve(self, query: str, documents: Iterable[SearchIndexDocument], *, top_k: int) -> List[KeywordMatch]:
        if not self.is_available():
            raise SearchCapabilityUnavailableError(
                "Semantic retrieval mode is unavailable: vector embedding model or backend not configured"
            )
        assert self.embedding_backend is not None

        doc_list = list(documents)
        if not doc_list:
            return []

        query_vec = self.embedding_backend.embed_text(query)
        doc_texts = [d.search_text for d in doc_list]
        doc_vecs = self.embedding_backend.embed_batch(doc_texts)

        query_terms = tuple(token for token in str(query).strip().lower().split() if token)
        matches: list[KeywordMatch] = []
        for idx, doc in enumerate(doc_list):
            sim = _cosine_similarity(query_vec, doc_vecs[idx])
            sim_score = round(max(0.0, (sim + 1.0) / 2.0), 4)
            if sim_score < 0.1:
                continue

            search_lower = doc.search_text.lower()
            matched = tuple(token for token in query_terms if token in search_lower)
            matches.append(
                KeywordMatch(
                    knowledge_object=doc.knowledge_object,
                    score=sim_score,
                    matched_terms=matched,
                    updated_at=doc.indexed_at,
                    component_scores={"semantic_score": sim_score},
                    ranker_version=self.ranker_version,
                )
            )

        matches.sort(
            key=lambda item: (
                item.score,
                (item.updated_at or datetime.min).replace(tzinfo=None),
            ),
            reverse=True,
        )
        return matches[:top_k]
