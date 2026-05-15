"""Index adapter boundary for governed keyword search inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from services.knowledge.evidence.models import KnowledgeObject
from services.knowledge.evidence.repository import InMemoryEvidenceRepository


def _parse_time(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class SearchIndexDocument:
    """Retriever-facing document built by the index adapter, not by the BFF."""

    knowledge_object: KnowledgeObject
    search_text: str
    relevance_score: float
    indexed_at: datetime | None
    source_watermark: str | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class SearchIndexAdapterSnapshot:
    adapter_id: str
    adapter_state: str
    indexed_source_types: tuple[str, ...]
    source_watermarks: Mapping[str, str | None]
    snapshot_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_state": self.adapter_state,
            "indexed_source_types": list(self.indexed_source_types),
            "source_watermarks": dict(self.source_watermarks),
            "snapshot_at": self.snapshot_at,
        }


class KeywordIndexAdapter:
    """Builds deterministic keyword-index documents from governed repository state."""

    def __init__(
        self,
        repository: InMemoryEvidenceRepository,
        *,
        adapter_id: str = "governed-keyword-index",
        source_watermarks: Mapping[str, str | None] | None = None,
        adapter_state: str = "fresh",
    ) -> None:
        self.repository = repository
        self.adapter_id = adapter_id
        self.source_watermarks = dict(source_watermarks or {})
        self.adapter_state = adapter_state

    def documents_for(self, knowledge_objects: Iterable[KnowledgeObject]) -> list[SearchIndexDocument]:
        documents: list[SearchIndexDocument] = []
        for knowledge_object in knowledge_objects:
            evidence_item = self.repository.get_evidence_item(knowledge_object.evidence_item_id)
            bundle = self.repository.get_bundle(knowledge_object.evidence_bundle_id)
            metadata = dict(knowledge_object.metadata)
            search_parts = [
                knowledge_object.title,
                knowledge_object.text,
                " ".join(knowledge_object.keywords),
                metadata.get("search_text"),
            ]
            if evidence_item is not None:
                search_parts.extend([evidence_item.body, evidence_item.citation_label])
            if bundle is not None:
                search_parts.extend([bundle.summary, " ".join(bundle.citation_refs)])
            updated_at = (
                metadata.get("updated_at")
                or metadata.get("indexed_at")
                or metadata.get("created_at")
                or knowledge_object.created_at
            )
            documents.append(
                SearchIndexDocument(
                    knowledge_object=knowledge_object,
                    search_text=" ".join(str(part or "") for part in search_parts),
                    relevance_score=float(metadata.get("relevance_score") or 0.0),
                    indexed_at=_parse_time(updated_at),
                    source_watermark=self.source_watermarks.get(knowledge_object.source_type),
                    metadata=metadata,
                )
            )
        return documents

    def snapshot(self) -> SearchIndexAdapterSnapshot:
        source_types = sorted({item.source_type for item in self.repository.list_knowledge_objects()})
        latest = None
        for document in self.documents_for(self.repository.list_knowledge_objects()):
            if document.indexed_at is not None and (latest is None or document.indexed_at > latest):
                latest = document.indexed_at
        return SearchIndexAdapterSnapshot(
            adapter_id=self.adapter_id,
            adapter_state=self.adapter_state,
            indexed_source_types=tuple(source_types),
            source_watermarks=self.source_watermarks,
            snapshot_at=latest.replace(microsecond=0).isoformat().replace("+00:00", "Z") if latest else None,
        )
