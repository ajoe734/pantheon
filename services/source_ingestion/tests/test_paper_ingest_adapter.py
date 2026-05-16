from __future__ import annotations

from pathlib import Path

import pytest

from services.source_ingestion.configured import JsonlConfiguredConnectorStore
from services.source_ingestion.connectors import OpenAlexPaperIngestAdapter, SourceEvidenceError


def test_openalex_paper_adapter_exposes_bounded_connector_contract(tmp_path: Path) -> None:
    adapter = OpenAlexPaperIngestAdapter(secret_ref_id="env://OPENALEX_API_KEY")

    connector = adapter.connector()
    fetch = adapter.fetch_config()

    assert connector.connector_id == "conn-openalex-papers"
    assert connector.source_type.value == "paper"
    assert connector.auth_policy.secret_ref is not None
    assert connector.auth_policy.secret_ref.secret_ref_id == "env://OPENALEX_API_KEY"
    assert connector.license_policy.allowed_use == ("research", "search_index", "strategy_seed")
    assert connector.metadata["adapter_type"] == "paper_ingest_skeleton"
    assert connector.metadata["direct_execution_allowed"] is False
    assert fetch["mode"] == "external_feed"
    assert fetch["url"] == "https://api.openalex.org/works"
    assert fetch["allowed_url_prefixes"] == ["https://api.openalex.org/"]
    assert fetch["max_records"] == 25

    store = JsonlConfiguredConnectorStore(tmp_path / "connector_config.jsonl")
    config = store.upsert_config(connector, fetch)

    assert config.fetch["url"] == "https://api.openalex.org/works"
    assert config.fetch["allowed_url_prefixes"] == ["https://api.openalex.org/"]


def test_openalex_paper_adapter_normalizes_openalex_results_to_source_records() -> None:
    adapter = OpenAlexPaperIngestAdapter(connector_id="conn-openalex-test")
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W1234567890",
                "title": "A Testable Alpha Paper",
                "publication_date": "2024-01-15",
                "doi": "https://doi.org/10.1234/Example.Paper",
                "abstract_inverted_index": {
                    "Alpha": [0],
                    "signals": [1],
                    "require": [2],
                    "replication": [3],
                },
                "authorships": [
                    {
                        "author": {
                            "display_name": "Ada Quant",
                            "orcid": "https://orcid.org/0000-0001-0000-0000",
                            "id": "https://openalex.org/A123",
                        }
                    }
                ],
                "primary_location": {"source": {"display_name": "Journal of Tests"}},
                "open_access": {"is_oa": True, "oa_status": "gold"},
                "cited_by_count": 42,
                "concepts": [{"display_name": "Algorithmic trading"}],
            }
        ]
    }

    records = adapter.source_records_from_openalex_response(
        payload,
        retrieved_at="2026-05-16T00:00:00Z",
        trace_id="trace-src-002",
    )

    assert len(records) == 1
    record = records[0]
    assert record.source_id == "src-paper-openalex-w1234567890"
    assert record.connector_id == "conn-openalex-test"
    assert record.source_type.value == "paper"
    assert record.content_ref == "https://doi.org/10.1234/example.paper"
    assert record.trace_id == "trace-src-002"
    assert record.metadata["openalex_work_id"] == "W1234567890"
    assert record.metadata["canonical_doi"] == "10.1234/example.paper"
    assert record.metadata["event_time"] == "2024-01-15T00:00:00Z"
    assert record.metadata["available_time"] == "2026-05-16T00:00:00Z"
    assert record.metadata["authors"][0]["name"] == "Ada Quant"
    assert record.metadata["venue"] == "Journal of Tests"
    assert record.metadata["body"] == "Alpha signals require replication"
    assert record.metadata["keywords"] == ["Algorithmic trading"]
    assert record.metadata["governance"]["direct_execution_allowed"] is False
    assert record.metadata["governance"]["openclaw_consumption"] == "governed_search_only"
    assert record.metadata["allowed_use"] == ["research", "search_index", "strategy_seed"]


def test_openalex_paper_adapter_rejects_unallowlisted_or_execution_bound_payloads() -> None:
    adapter = OpenAlexPaperIngestAdapter()

    with pytest.raises(SourceEvidenceError, match="hosted by openalex.org"):
        adapter.source_records_from_openalex_response(
            {
                "results": [
                    {
                        "id": "https://evil.example/W123",
                        "title": "Bad paper",
                        "publication_date": "2024-01-01",
                    }
                ]
            }
        )

    with pytest.raises(SourceEvidenceError, match="cannot target execution routes"):
        adapter.source_records_from_openalex_response(
            {
                "results": [
                    {
                        "id": "https://openalex.org/W123",
                        "title": "Routed paper",
                        "publication_date": "2024-01-01",
                        "metadata": {"routes": ["lean"]},
                    }
                ]
            }
        )
