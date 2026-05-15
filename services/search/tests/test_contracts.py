from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft7Validator

from services.knowledge.evidence import EvidenceBundleBuilder, EvidenceItem, InMemoryEvidenceRepository
from services.search import SearchRequest
from services.source_ingestion.connectors import SourceConnector, SourceRecord


ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "docs" / "contracts"


def _errors(schema_name: str, payload: dict) -> list[str]:
    schema = json.loads((CONTRACTS / f"{schema_name}.schema.json").read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    return sorted(error.message for error in validator.iter_errors(payload))


def test_sd03_contract_schemas_accept_model_payloads() -> None:
    connector = SourceConnector(
        connector_id="conn-openalex",
        source_type="paper",
        provider="OpenAlex",
        license_scope="open",
    )
    repository = InMemoryEvidenceRepository()
    builder = EvidenceBundleBuilder(repository)
    source = SourceRecord(
        source_id="src-paper-001",
        connector_id=connector.connector_id,
        source_type="paper",
        title="Momentum decay paper",
        content_ref="https://openalex.org/W123",
        metadata={"license_scope": "open", "access_scope": ["research"]},
        trace_id="trace-src-paper",
    )
    item = EvidenceItem(
        evidence_item_id="evi-paper-001",
        source_id=source.source_id,
        item_type="text_chunk",
        content_ref="https://openalex.org/W123#abstract",
        citation_label="W123#abstract",
        body="Momentum signals decay under volatility clustering.",
        access_scope=["research"],
    )
    bundle = builder.build_bundle(
        source_records=[source],
        evidence_items=[item],
        summary="Paper evidence about volatility-driven momentum decay.",
        created_by="Codex",
        evidence_bundle_id="evbundle-paper-001",
    )
    knowledge_object = builder.build_knowledge_object(
        knowledge_object_id="ko-paper-001",
        source_record=source,
        evidence_item=item,
        evidence_bundle=bundle,
        title="Momentum decay paper",
        text=item.body,
        keywords=["momentum", "volatility"],
    )
    request = SearchRequest(
        request_id="search-contract-001",
        query="momentum volatility",
        persona_id="persona-alpha",
        workspace_id="workspace-research",
        source_types=["paper"],
        trace_id="trace-search-contract",
    )

    assert _errors("source_connector", connector.to_dict()) == []
    assert _errors("evidence_bundle", bundle.to_dict()) == []
    assert _errors("knowledge_object", knowledge_object.to_dict()) == []
    assert _errors("search_request", request.to_dict()) == []
