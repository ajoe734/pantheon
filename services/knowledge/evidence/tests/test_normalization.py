from __future__ import annotations

from services.knowledge.evidence import EvidenceItem
from services.knowledge.evidence.normalization import (
    normalize_doi,
    normalize_repo_url,
    normalize_source_evidence,
    normalize_source_record,
    normalize_url,
)
from services.source_ingestion.connectors import SourceRecord


def test_url_doi_and_repo_normalizers_produce_canonical_refs() -> None:
    assert normalize_url("HTTPS://Example.COM:443/paper/?b=2&a=1#section") == "https://example.com/paper?a=1&b=2"
    assert normalize_doi("https://doi.org/10.1234/ABC.Def.") == "10.1234/abc.def"
    assert normalize_repo_url("https://github.com/OpenAI/Example.git/tree/main") == "https://github.com/openai/example"


def test_source_record_normalization_adds_hash_scope_and_dedupe_metadata() -> None:
    source = SourceRecord(
        source_id="src-paper-raw",
        connector_id="conn-openalex",
        source_type="paper",
        title="A Paper",
        content_ref="https://doi.org/10.5555/Example.Paper",
        metadata={
            "body": "Canonical source evidence content.",
            "access_scope": "research",
        },
    )

    normalized = normalize_source_record(source, connector_license_scope="open")

    assert normalized.content_ref == "https://doi.org/10.5555/Example.Paper"
    assert normalized.metadata["canonical_url"] == "https://doi.org/10.5555/Example.Paper"
    assert normalized.metadata["canonical_doi"] == "10.5555/example.paper"
    assert normalized.metadata["content_hash"].startswith("sha256:")
    assert normalized.metadata["source_dedupe_key"] == "doi:10.5555/example.paper"
    assert normalized.metadata["license_scope"] == "open"
    assert normalized.metadata["access_scope"] == ["research"]


def test_source_evidence_normalization_assigns_deterministic_evidence_owner() -> None:
    source = SourceRecord(
        source_id="src-repo-raw",
        connector_id="conn-github",
        source_type="repo",
        title="Repo",
        content_ref="https://github.com/Pantheon/Strategy.git",
        metadata={"license_scope": "internal", "body": "Repo evidence body."},
    )
    item = EvidenceItem(
        evidence_item_id="raw-evidence-id",
        source_id="src-repo-raw",
        item_type="repo_readme",
        content_ref="https://github.com/Pantheon/Strategy/blob/main/README.md",
        citation_label="Pantheon/Strategy README",
        body="Repo evidence body.",
    )

    first = normalize_source_evidence(source_record=source, evidence_item=item)
    second = normalize_source_evidence(source_record=source, evidence_item=item)

    assert first.source_dedupe_key == "repo:https://github.com/pantheon/strategy"
    assert first.evidence_owner_id == second.evidence_owner_id
    assert first.evidence_item.evidence_item_id.startswith("evi-")
    assert first.evidence_item.metadata["source_owner_id"] == "src-repo-raw"
