"""Evidence repository interfaces and durable JSONL implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from services.source_ingestion.connectors.base import SourceRecord

from .models import EvidenceBundle, EvidenceItem, EvidenceValidationError, KnowledgeObject


class InMemoryEvidenceRepository:
    """Small deterministic repository used by tests, BFF projections, and stubs."""

    def __init__(self) -> None:
        self._source_records: Dict[str, SourceRecord] = {}
        self._evidence_items: Dict[str, EvidenceItem] = {}
        self._bundles: Dict[str, EvidenceBundle] = {}
        self._knowledge_objects: Dict[str, KnowledgeObject] = {}

    def reload(self) -> None:
        """No-op for in-memory store; subclasses override to re-read from durable storage."""

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

    def list_source_records(self) -> List[SourceRecord]:
        return list(self._source_records.values())

    def list_evidence_items(self) -> List[EvidenceItem]:
        return list(self._evidence_items.values())


class JsonlEvidenceRepository(InMemoryEvidenceRepository):
    """Service-owned append/replay store for governed source evidence records.

    The JSONL log stores one validated upsert per line. Replay uses the same
    repository invariants as normal writes, so missing source/item/bundle refs
    fail fast instead of producing a partial search index.
    """

    _RECORD_LOADERS = {
        "source_record": SourceRecord.from_dict,
        "evidence_item": EvidenceItem.from_dict,
        "evidence_bundle": EvidenceBundle.from_dict,
        "knowledge_object": KnowledgeObject.from_dict,
    }

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._replaying = False
        super().__init__()
        self.reload()

    def reload(self) -> None:
        self._source_records.clear()
        self._evidence_items.clear()
        self._bundles.clear()
        self._knowledge_objects.clear()
        if not self.path.exists():
            return

        self._replaying = True
        try:
            for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise EvidenceValidationError(
                        f"Invalid source evidence JSONL at {self.path}:{line_no}: {exc.msg}"
                    ) from exc
                self._apply_record(record)
        finally:
            self._replaying = False

    def add_source_record(self, source: SourceRecord) -> SourceRecord:
        stored = super().add_source_record(source)
        self._append("source_record", stored.source_id, stored.to_dict())
        return stored

    def add_evidence_item(self, item: EvidenceItem) -> EvidenceItem:
        stored = super().add_evidence_item(item)
        self._append("evidence_item", stored.evidence_item_id, stored.to_dict())
        return stored

    def add_bundle(self, bundle: EvidenceBundle) -> EvidenceBundle:
        stored = super().add_bundle(bundle)
        self._append("evidence_bundle", stored.evidence_bundle_id, stored.to_dict())
        return stored

    def add_knowledge_object(self, knowledge_object: KnowledgeObject) -> KnowledgeObject:
        stored = super().add_knowledge_object(knowledge_object)
        self._append("knowledge_object", stored.knowledge_object_id, stored.to_dict())
        return stored

    def _apply_record(self, record: Mapping[str, Any]) -> None:
        record_type = str(record.get("record_type") or "")
        payload = record.get("payload")
        if record_type not in self._RECORD_LOADERS or not isinstance(payload, Mapping):
            raise EvidenceValidationError(f"Unsupported source evidence record: {record_type or '<missing>'}")
        obj = self._RECORD_LOADERS[record_type](payload)
        if record_type == "source_record":
            self.add_source_record(obj)
        elif record_type == "evidence_item":
            self.add_evidence_item(obj)
        elif record_type == "evidence_bundle":
            self.add_bundle(obj)
        elif record_type == "knowledge_object":
            self.add_knowledge_object(obj)

    def _append(self, record_type: str, record_id: str, payload: Mapping[str, Any]) -> None:
        if self._replaying:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "schema_version": "source_evidence_record.v1",
            "record_type": record_type,
            "record_id": record_id,
            "payload": dict(payload),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
