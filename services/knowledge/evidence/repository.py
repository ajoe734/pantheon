"""Evidence repository interfaces and in-memory implementation."""

from __future__ import annotations

from typing import Dict, Iterable, List

from services.source_ingestion.connectors.base import SourceRecord

from .models import EvidenceBundle, EvidenceItem, EvidenceValidationError, KnowledgeObject


class InMemoryEvidenceRepository:
    """Small deterministic repository used by tests, BFF projections, and stubs."""

    def __init__(self) -> None:
        self._source_records: Dict[str, SourceRecord] = {}
        self._evidence_items: Dict[str, EvidenceItem] = {}
        self._bundles: Dict[str, EvidenceBundle] = {}
        self._knowledge_objects: Dict[str, KnowledgeObject] = {}

    def add_source_record(self, source: SourceRecord) -> SourceRecord:
        self._source_records[source.source_id] = source
        return source

    def add_evidence_item(self, item: EvidenceItem) -> EvidenceItem:
        if item.source_id not in self._source_records:
            raise EvidenceValidationError(f"EvidenceItem references unknown source_id: {item.source_id}")
        self._evidence_items[item.evidence_item_id] = item
        return item

    def add_bundle(self, bundle: EvidenceBundle) -> EvidenceBundle:
        missing_sources = [source_id for source_id in bundle.source_ids if source_id not in self._source_records]
        if missing_sources:
            raise EvidenceValidationError(f"EvidenceBundle references unknown source_ids: {missing_sources}")
        missing_items = [item_id for item_id in bundle.evidence_item_ids if item_id not in self._evidence_items]
        if missing_items:
            raise EvidenceValidationError(f"EvidenceBundle references unknown evidence_item_ids: {missing_items}")
        self._bundles[bundle.evidence_bundle_id] = bundle
        return bundle

    def add_knowledge_object(self, knowledge_object: KnowledgeObject) -> KnowledgeObject:
        if knowledge_object.evidence_bundle_id not in self._bundles:
            raise EvidenceValidationError(
                f"KnowledgeObject references unknown evidence_bundle_id: {knowledge_object.evidence_bundle_id}"
            )
        if knowledge_object.evidence_item_id not in self._evidence_items:
            raise EvidenceValidationError(
                f"KnowledgeObject references unknown evidence_item_id: {knowledge_object.evidence_item_id}"
            )
        self._knowledge_objects[knowledge_object.knowledge_object_id] = knowledge_object
        return knowledge_object

    def get_source_record(self, source_id: str) -> SourceRecord | None:
        return self._source_records.get(source_id)

    def get_evidence_item(self, evidence_item_id: str) -> EvidenceItem | None:
        return self._evidence_items.get(evidence_item_id)

    def get_bundle(self, evidence_bundle_id: str) -> EvidenceBundle | None:
        return self._bundles.get(evidence_bundle_id)

    def get_knowledge_object(self, knowledge_object_id: str) -> KnowledgeObject | None:
        return self._knowledge_objects.get(knowledge_object_id)

    def list_knowledge_objects(self) -> List[KnowledgeObject]:
        return list(self._knowledge_objects.values())

    def list_bundles(self) -> List[EvidenceBundle]:
        return list(self._bundles.values())

    def add_knowledge_objects(self, objects: Iterable[KnowledgeObject]) -> None:
        for knowledge_object in objects:
            self.add_knowledge_object(knowledge_object)
