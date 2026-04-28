"""Governed search gateway for SD-03 evidence-backed retrieval."""

from .filters import SearchAccessContext, SearchRequest
from .gateway import GovernedSearchResponse, RetrievalResult, SearchGateway
from .index_adapter import KeywordIndexAdapter, SearchIndexAdapterSnapshot, SearchIndexDocument
from .index_store import JsonlSearchIndexStore, SearchIndexSnapshot

__all__ = [
    "GovernedSearchResponse",
    "JsonlSearchIndexStore",
    "KeywordIndexAdapter",
    "RetrievalResult",
    "SearchAccessContext",
    "SearchGateway",
    "SearchIndexAdapterSnapshot",
    "SearchIndexDocument",
    "SearchIndexSnapshot",
    "SearchRequest",
]
