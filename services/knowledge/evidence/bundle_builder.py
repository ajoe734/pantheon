"""Evidence bundle construction helpers."""

from __future__ import annotations

from typing import Iterable, Sequence
from uuid import uuid4

from services.source_ingestion.connectors.base import SourceRecord

from .models import EvidenceBundle, EvidenceItem, EvidenceValidationError, KnowledgeObject
from .repository import InMemoryEvidenceRepository


class EvidenceBundleBuilder:
    """Builds evidence bundles from governed source records and items."""

    def __init__(self, repository: InMemoryEvidenceRepository) -> None:
        self.repository = repository

    def build_bundle(
        self,
        *,
        source_records: Sequence[SourceRecord],
        evidence_items: Sequence[EvidenceItem],
        summary: str,
        created_by: str,
        evidence_bundle_id: str | None = None,
        confidence: float | None = None,
        license_scope: str | None = None,
        metadata: dict | None = None,
    ) -> EvidenceBundle:
        if not source_records:
            raise EvidenceValidationError("Cannot build EvidenceBundle without source records")
        if not evidence_items:
            raise EvidenceValidationError("Cannot build EvidenceBundle without evidence items")
        rejected = [source.source_id for source in source_records if source.is_rejected]
        if rejected:
            raise EvidenceValidationError(f"Rejected sources cannot be bundled: {rejected}")

        source_ids = {source.source_id for source in source_records}
        for source in source_records:
            self.repository.add_source_record(source)
        for item in evidence_items:
            if item.source_id not in source_ids:
                raise EvidenceValidationError(
                    f"Evidence item {item.evidence_item_id} references source outside bundle: {item.source_id}"
                )
            self.repository.add_evidence_item(item)

        trace_refs: list[str] = []
        access_scope: set[str] = set()
        source_license = license_scope
        for source in source_records:
            if source.trace_id and source.trace_id not in trace_refs:
                trace_refs.append(source.trace_id)
            source_access = source.metadata.get("access_scope") if isinstance(source.metadata, dict) else None
            if isinstance(source_access, list):
                access_scope.update(str(value) for value in source_access if str(value).strip())
            if source_license is None and isinstance(source.metadata, dict):
                source_license = str(source.metadata.get("license_scope") or "").strip() or None
        for item in evidence_items:
            access_scope.update(item.access_scope)
            for trace_ref in item.trace_refs:
                if trace_ref not in trace_refs:
                    trace_refs.append(trace_ref)

        item_confidences = [item.confidence for item in evidence_items]
        effective_confidence = confidence if confidence is not None else min(item_confidences)
        bundle = EvidenceBundle(
            evidence_bundle_id=evidence_bundle_id or f"evbundle-{uuid4().hex[:12]}",
            source_ids=[source.source_id for source in source_records],
            evidence_item_ids=[item.evidence_item_id for item in evidence_items],
            summary=summary,
            citation_refs=[item.citation_label for item in evidence_items],
            confidence=effective_confidence,
            license_scope=source_license or "internal",
            access_scope=sorted(access_scope) or ["public"],
            created_by=created_by,
            trace_refs=trace_refs,
            metadata=metadata or {},
        )
        return self.repository.add_bundle(bundle)

    def build_knowledge_object(
        self,
        *,
        knowledge_object_id: str,
        source_record: SourceRecord,
        evidence_item: EvidenceItem,
        evidence_bundle: EvidenceBundle,
        title: str,
        text: str,
        source_type: str | None = None,
        keywords: Iterable[str] = (),
        metadata: dict | None = None,
    ) -> KnowledgeObject:
        knowledge_object = KnowledgeObject(
            knowledge_object_id=knowledge_object_id,
            source_id=source_record.source_id,
            evidence_item_id=evidence_item.evidence_item_id,
            evidence_bundle_id=evidence_bundle.evidence_bundle_id,
            title=title,
            text=text,
            source_type=source_type or source_record.source_type.value,
            license_scope=evidence_bundle.license_scope,
            access_scope=evidence_bundle.access_scope,
            keywords=list(keywords),
            metadata=metadata or {},
        )
        return self.repository.add_knowledge_object(knowledge_object)
