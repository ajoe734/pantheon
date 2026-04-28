from __future__ import annotations

import pytest

from services.knowledge.evidence import EvidenceBundleBuilder, EvidenceItem, InMemoryEvidenceRepository, JsonlEvidenceRepository
from services.knowledge.evidence.models import EvidenceValidationError
from services.source_ingestion.connectors import SourceRecord


def _source(status: str = "normalized") -> SourceRecord:
    return SourceRecord(
        source_id="src-note-001",
        connector_id="conn-notes",
        source_type="internal_note",
        title="Momentum volatility note",
        content_ref="note://pantheon/note-001",
        status=status,
        metadata={
            "license_scope": "internal",
            "access_scope": ["research", "operator"],
            "raw_uri": "note://pantheon/note-001",
        },
        trace_id="trace-source-001",
    )


def _item() -> EvidenceItem:
    return EvidenceItem(
        evidence_item_id="evi-note-001",
        source_id="src-note-001",
        item_type="text_chunk",
        content_ref="note://pantheon/note-001#chunk-1",
        citation_label="note-001#chunk-1",
        body="Momentum factor decay accelerates during high-volatility windows.",
        confidence=0.87,
        access_scope=["research"],
        trace_refs=["trace-evidence-001"],
    )


def test_bundle_builder_persists_citation_refs_and_trace_refs() -> None:
    repository = InMemoryEvidenceRepository()
    builder = EvidenceBundleBuilder(repository)

    bundle = builder.build_bundle(
        source_records=[_source()],
        evidence_items=[_item()],
        summary="Evidence for momentum decay during volatility clusters.",
        created_by="Codex",
        evidence_bundle_id="evbundle-note-001",
    )

    assert bundle.evidence_bundle_id == "evbundle-note-001"
    assert bundle.citation_refs == ("note-001#chunk-1",)
    assert bundle.confidence == 0.87
    assert bundle.trace_refs == ("trace-source-001", "trace-evidence-001")
    assert repository.get_bundle("evbundle-note-001") == bundle


def test_rejected_source_cannot_be_used_in_bundle() -> None:
    repository = InMemoryEvidenceRepository()
    builder = EvidenceBundleBuilder(repository)

    with pytest.raises(EvidenceValidationError, match="Rejected sources"):
        builder.build_bundle(
            source_records=[_source(status="rejected")],
            evidence_items=[_item()],
            summary="Rejected material should not become governed evidence.",
            created_by="Codex",
        )


def test_knowledge_object_links_back_to_evidence_bundle() -> None:
    repository = InMemoryEvidenceRepository()
    builder = EvidenceBundleBuilder(repository)
    source = _source()
    item = _item()
    bundle = builder.build_bundle(
        source_records=[source],
        evidence_items=[item],
        summary="Evidence for momentum decay during volatility clusters.",
        created_by="Codex",
        evidence_bundle_id="evbundle-note-001",
    )

    knowledge_object = builder.build_knowledge_object(
        knowledge_object_id="ko-note-001",
        source_record=source,
        evidence_item=item,
        evidence_bundle=bundle,
        title="Momentum volatility note",
        text=item.body,
        keywords=["momentum", "volatility"],
    )

    assert knowledge_object.evidence_bundle_id == "evbundle-note-001"
    assert repository.get_knowledge_object("ko-note-001") == knowledge_object


def test_jsonl_repository_replays_source_evidence_and_knowledge_refs(tmp_path) -> None:
    repository = JsonlEvidenceRepository(tmp_path / "source-evidence.jsonl")
    builder = EvidenceBundleBuilder(repository)
    source = _source()
    item = _item()
    bundle = builder.build_bundle(
        source_records=[source],
        evidence_items=[item],
        summary="Evidence for momentum decay during volatility clusters.",
        created_by="Codex",
        evidence_bundle_id="evbundle-note-001",
    )
    builder.build_knowledge_object(
        knowledge_object_id="ko-note-001",
        source_record=source,
        evidence_item=item,
        evidence_bundle=bundle,
        title="Momentum volatility note",
        text=item.body,
        keywords=["momentum", "volatility"],
    )

    replayed = JsonlEvidenceRepository(tmp_path / "source-evidence.jsonl")

    assert replayed.get_source_record("src-note-001").to_dict() == source.to_dict()
    assert replayed.get_evidence_item("evi-note-001").to_dict() == item.to_dict()
    assert replayed.get_bundle("evbundle-note-001").to_dict() == bundle.to_dict()
    assert replayed.get_knowledge_object("ko-note-001").evidence_bundle_id == "evbundle-note-001"
