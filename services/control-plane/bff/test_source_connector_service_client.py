from __future__ import annotations

import os
import sys


from services.control_plane.bff.ports import DefaultResearchKnowledgeSourcePort


def test_bff_reads_source_connector_registry_through_typed_source_port() -> None:
    calls = []

    def fake_get(base_url, path):
        calls.append((base_url, path))
        return True, {
            "schema_version": "source_connector_registry.v1",
            "connectors": [{"connector_id": "conn-openalex-api", "provider": "OpenAlex"}],
            "provider_examples": [{"connector": {"connector_id": "example-openalex-feed"}}],
            "policy_registry": {"schema_version": "source_crawler_indexer_policy_registry.v1"},
            "financial_data_source_catalog": {"summary": {"data_source_count": 6}},
            "active_universe_policy": {"summary": {"rule_count": 9}},
        }

    port = DefaultResearchKnowledgeSourcePort(
        source_ingest_service_url="http://source-ingest:8097", http_get_fn=fake_get
    )
    registry = port.get_source_connector_registry()

    assert calls == [("http://source-ingest:8097", "/api/source-ingest/registry")]
    assert registry["source"] == "service_client"
    assert registry["connectors"][0]["connector_id"] == "conn-openalex-api"
    assert registry["financial_data_source_catalog"]["summary"]["data_source_count"] == 6
