"""Governed hybrid retriever combining lexical and vector semantic retrieval via RRF."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Sequence

from services.knowledge.evidence.models import KnowledgeObject
from services.search.filters import SearchCapabilityUnavailableError
from services.search.index_adapter import SearchIndexDocument
from services.search.retriever import FullTextRetriever, KeywordMatch, KeywordRetriever, SemanticRetriever


class HybridRetriever:
    """Hybrid ranker fusing lexical and vector results via Reciprocal Rank Fusion (RRF)."""

    def __init__(
        self,
        lexical_retriever: KeywordRetriever | FullTextRetriever | None = None,
        semantic_retriever: SemanticRetriever | None = None,
        *,
        calibration_method: str = "reciprocal_rank_fusion_k60",
        rrf_k: int = 60,
        lexical_weight: float = 1.0,
        semantic_weight: float = 1.0,
        ranker_version: str = "rrf-v1",
    ) -> None:
        self.lexical_retriever = lexical_retriever or FullTextRetriever()
        self.semantic_retriever = semantic_retriever
        self.calibration_method = calibration_method
        self.rrf_k = rrf_k
        self.lexical_weight = lexical_weight
        self.semantic_weight = semantic_weight
        self.ranker_version = ranker_version

    def is_available(self) -> bool:
        return (
            self.lexical_retriever is not None
            and self.lexical_retriever.is_available()
            and self.semantic_retriever is not None
            and self.semantic_retriever.is_available()
        )

    def retrieve(
        self,
        query: str,
        documents: Iterable[SearchIndexDocument],
        *,
        top_k: int,
    ) -> List[KeywordMatch]:
        if not self.is_available():
            raise SearchCapabilityUnavailableError(
                "Hybrid retrieval mode is unavailable: semantic vector retriever is not available"
            )
        assert self.semantic_retriever is not None

        doc_list = list(documents)
        if not doc_list:
            return []

        # Retrieve lexical and semantic matches over the authorized documents
        lex_matches = self.lexical_retriever.retrieve(query, doc_list, top_k=len(doc_list))
        sem_matches = self.semantic_retriever.retrieve(query, doc_list, top_k=len(doc_list))

        doc_by_id = {d.knowledge_object.knowledge_object_id: d for d in doc_list}
        lex_ranks: dict[str, int] = {
            m.knowledge_object.knowledge_object_id: rank for rank, m in enumerate(lex_matches, start=1)
        }
        lex_scores: dict[str, float] = {
            m.knowledge_object.knowledge_object_id: m.score for m in lex_matches
        }
        lex_terms: dict[str, tuple[str, ...]] = {
            m.knowledge_object.knowledge_object_id: m.matched_terms for m in lex_matches
        }

        sem_ranks: dict[str, int] = {
            m.knowledge_object.knowledge_object_id: rank for rank, m in enumerate(sem_matches, start=1)
        }
        sem_scores: dict[str, float] = {
            m.knowledge_object.knowledge_object_id: m.score for m in sem_matches
        }

        all_candidate_ids = set(lex_ranks.keys()) | set(sem_ranks.keys())
        if not all_candidate_ids:
            return []

        matches: list[KeywordMatch] = []
        for obj_id in all_candidate_ids:
            doc = doc_by_id.get(obj_id)
            if doc is None:
                continue

            r_lex = lex_ranks.get(obj_id)
            r_sem = sem_ranks.get(obj_id)

            rrf_lex = (self.lexical_weight / (self.rrf_k + r_lex)) if r_lex is not None else 0.0
            rrf_sem = (self.semantic_weight / (self.rrf_k + r_sem)) if r_sem is not None else 0.0
            rrf_raw = rrf_lex + rrf_sem

            # Normalize RRF score to [0, 1) range
            max_possible_rrf = (self.lexical_weight + self.semantic_weight) / (self.rrf_k + 1)
            normalized_score = round(min(0.999, max(0.01, rrf_raw / max_possible_rrf)), 4)

            matched_terms = lex_terms.get(obj_id, ())
            component_scores = {
                "lexical_score": lex_scores.get(obj_id, 0.0),
                "semantic_score": sem_scores.get(obj_id, 0.0),
                "rrf_score": round(rrf_raw, 6),
                "lexical_rank": r_lex,
                "semantic_rank": r_sem,
            }

            matches.append(
                KeywordMatch(
                    knowledge_object=doc.knowledge_object,
                    score=normalized_score,
                    matched_terms=matched_terms,
                    updated_at=doc.indexed_at,
                    component_scores=component_scores,
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
