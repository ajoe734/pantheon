"""Governed search gateway for evidence-backed retrieval and structured alpha queries."""

from .filters import (
    RETRIEVAL_MODES,
    SearchAccessContext,
    SearchCapabilityUnavailableError,
    SearchFilters,
    SearchPolicyError,
    SearchRequest,
)
from .gateway import GovernedSearchResponse, RetrievalResult, SearchGateway
from .hybrid_retriever import HybridRetriever
from .index_adapter import KeywordIndexAdapter, SearchIndexAdapterSnapshot, SearchIndexDocument
from .index_store import JsonlSearchIndexStore, SearchIndexSnapshot
from .retriever import (
    FullTextRetriever,
    KeywordMatch,
    KeywordRetriever,
    MockVectorEmbeddingBackend,
    SemanticRetriever,
    VectorEmbeddingBackend,
)
from .structured_alpha import (
    AlphaDatasetSchema,
    AlphaFieldDef,
    AlphaQueryResultSnapshot,
    AlphaRecord,
    AlphaSortSpec,
    StructuredAlphaEngine,
    StructuredAlphaQuery,
)

__all__ = [
    "AlphaDatasetSchema",
    "AlphaFieldDef",
    "AlphaQueryResultSnapshot",
    "AlphaRecord",
    "AlphaSortSpec",
    "FullTextRetriever",
    "GovernedSearchResponse",
    "HybridRetriever",
    "JsonlSearchIndexStore",
    "KeywordIndexAdapter",
    "KeywordMatch",
    "KeywordRetriever",
    "MockVectorEmbeddingBackend",
    "RETRIEVAL_MODES",
    "RetrievalResult",
    "SearchAccessContext",
    "SearchCapabilityUnavailableError",
    "SearchFilters",
    "SearchGateway",
    "SearchIndexAdapterSnapshot",
    "SearchIndexDocument",
    "SearchIndexSnapshot",
    "SearchPolicyError",
    "SearchRequest",
    "SemanticRetriever",
    "StructuredAlphaEngine",
    "StructuredAlphaQuery",
    "VectorEmbeddingBackend",
]
