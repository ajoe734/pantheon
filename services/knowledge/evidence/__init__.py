"""Evidence bundle and knowledge-object models for SD-03."""

from .bundle_builder import EvidenceBundleBuilder
from .models import DocumentChunk, EvidenceBundle, EvidenceItem, KnowledgeObject
from .normalization import (
    NormalizedEvidenceOwnership,
    normalize_doi,
    normalize_repo_url,
    normalize_source_evidence,
    normalize_source_record,
    normalize_url,
)
from .repository import InMemoryEvidenceRepository, JsonlEvidenceRepository

__all__ = [
    "DocumentChunk",
    "EvidenceBundle",
    "EvidenceBundleBuilder",
    "EvidenceItem",
    "InMemoryEvidenceRepository",
    "JsonlEvidenceRepository",
    "KnowledgeObject",
    "NormalizedEvidenceOwnership",
    "normalize_doi",
    "normalize_repo_url",
    "normalize_source_evidence",
    "normalize_source_record",
    "normalize_url",
]
