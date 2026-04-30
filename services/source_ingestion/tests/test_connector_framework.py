from __future__ import annotations

import pytest

from services.source_ingestion.configured import JsonlConfiguredConnectorStore
from services.source_ingestion.connectors import (
    AuthType,
    SourceConnector,
    SourceEvidenceError,
    example_provider_catalog,
)


def test_connector_framework_policy_contract_requires_secret_refs() -> None:
    connector = SourceConnector(
        connector_id="conn-openalex-api",
        source_type="paper",
        provider="OpenAlex",
        license_scope="open",
        auth_type=AuthType.API_KEY,
        secret_ref_id="env://OPENALEX_API_KEY",
        rate_limit_policy={
            "requests_per_minute": 60,
            "burst": 10,
            "retry_after_seconds": 60,
            "policy_ref": "source-ingest://policy/openalex",
        },
        license_policy={
            "license_scope": "open",
            "allowed_use": ["research", "search_index"],
            "attribution_required": True,
            "redistribution_allowed": False,
            "policy_ref": "source-ingest://license/openalex",
        },
        source_metadata={
            "display_name": "OpenAlex works",
            "homepage_url": "https://openalex.org",
            "tags": ["paper", "external_feed"],
        },
    )

    policy = connector.policy_summary()
    assert policy["auth"]["auth_type"] == "api_key"
    assert policy["auth"]["secret_ref"]["secret_ref_id"] == "env://OPENALEX_API_KEY"
    assert policy["rate_limit"]["requests_per_minute"] == 60
    assert policy["license"]["allowed_use"] == ["research", "search_index"]
    assert policy["source_metadata"]["display_name"] == "OpenAlex works"

    with pytest.raises(SourceEvidenceError, match="requires secret_ref_id"):
        SourceConnector(
            connector_id="conn-missing-secret",
            source_type="paper",
            provider="OpenAlex",
            license_scope="open",
            auth_type=AuthType.API_KEY,
        )

    with pytest.raises(SourceEvidenceError, match="inline secret"):
        SourceConnector(
            connector_id="conn-inline-secret",
            source_type="paper",
            provider="Bad Provider",
            license_scope="internal",
            metadata={"api_key": "raw-key-value"},
        )


def test_provider_examples_share_configured_fetch_contract(tmp_path) -> None:
    store = JsonlConfiguredConnectorStore(tmp_path / "connector_config.jsonl")
    providers = example_provider_catalog()

    for provider in providers:
        connector = provider.connector()
        config = store.upsert_config(connector, provider.fetch_config())
        assert config.connector.connector_id == connector.connector_id
        assert config.fetch["mode"] in {"static_records", "external_feed"}

    replayed = JsonlConfiguredConnectorStore(tmp_path / "connector_config.jsonl")
    assert {config.connector.connector_id for config in replayed.list_configs()} == {
        provider.connector().connector_id for provider in providers
    }


def test_fetch_config_validation_rejects_unsafe_urls_secrets_and_overlarge_payloads(tmp_path) -> None:
    store = JsonlConfiguredConnectorStore(tmp_path / "connector_config.jsonl")
    connector = SourceConnector(
        connector_id="conn-external-feed",
        source_type="internal_note",
        provider="External Feed",
        license_scope="internal",
    )

    with pytest.raises(SourceEvidenceError, match="inline credentials"):
        store.upsert_config(
            connector,
            {
                "mode": "external_feed",
                "url": "https://user:pass@example.test/feed.json",
                "allowed_url_prefixes": ["https://user:pass@example.test/"],
            },
        )

    with pytest.raises(SourceEvidenceError, match="inline secret"):
        store.upsert_config(
            connector,
            {
                "mode": "external_feed",
                "url": "https://feeds.example.test/feed.json",
                "allowed_url_prefixes": ["https://feeds.example.test/"],
                "headers": {"Authorization": "Bearer raw-token"},
            },
        )

    with pytest.raises(SourceEvidenceError, match="max_bytes"):
        store.upsert_config(
            connector,
            {
                "mode": "external_feed",
                "url": "https://feeds.example.test/feed.json",
                "allowed_url_prefixes": ["https://feeds.example.test/"],
                "max_bytes": 10_000_001,
            },
        )
