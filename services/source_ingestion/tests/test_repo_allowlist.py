from __future__ import annotations

from pathlib import Path

import pytest

from services.source_ingestion.configured import ConfiguredConnectorFetcher, JsonlConfiguredConnectorStore
from services.source_ingestion.connectors import (
    RepoAllowlistEntry,
    RepoAllowlistProvider,
    SourceEvidenceError,
    example_provider_catalog,
)


def test_repo_allowlist_provider_emits_static_repo_source_records(tmp_path: Path) -> None:
    provider = RepoAllowlistProvider(
        connector_id="conn-github-allowlist",
        allowlist=(
            {
                "repo_full_name": "QuantConnect/Lean",
                "allowed_paths": ("Algorithm.Python/pantheon_algo", "Research"),
                "ref": "master",
                "purpose": "official LEAN research examples",
            },
        ),
    )
    connector = provider.connector()
    assert connector.source_type.value == "repo"
    assert connector.metadata["allowlist_enforced"] is True
    assert connector.metadata["direct_execution_allowed"] is False
    assert connector.license_policy.allowed_use == ("research", "search_index", "strategy_seed")

    store = JsonlConfiguredConnectorStore(tmp_path / "connector_config.jsonl")
    store.upsert_config(connector, provider.fetch_config())

    batch = ConfiguredConnectorFetcher(store).fetch_batch("conn-github-allowlist", watermark=None)

    assert batch.next_watermark is None
    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.source_type.value == "repo"
    assert record.content_ref == "https://github.com/QuantConnect/Lean"
    assert record.metadata["repo_full_name"] == "QuantConnect/Lean"
    assert record.metadata["allowed_paths"] == ["Algorithm.Python/pantheon_algo", "Research"]
    assert record.metadata["repo_allowlist_policy"] == "repo_allowlist_v1"
    assert record.metadata["allowlist_enforced"] is True
    assert record.metadata["license_scope"] == "oss"
    assert record.metadata["access_scope"] == ["research"]
    assert record.metadata["governance"]["canonical_sink"] == "SourceRecord/EvidenceBundle"
    assert record.metadata["governance"]["direct_execution_allowed"] is False
    assert record.metadata["governance"]["lean_consumption"] == "research_only_not_direct_action"
    assert "allowed paths: Algorithm.Python/pantheon_algo, Research" in record.metadata["body"]


def test_repo_allowlist_rejects_arbitrary_urls_traversal_and_duplicates() -> None:
    with pytest.raises(SourceEvidenceError, match="owner/repo"):
        RepoAllowlistEntry(
            repo_full_name="https://github.com/QuantConnect/Lean",
            allowed_paths=("Research",),
        )

    with pytest.raises(SourceEvidenceError, match="traversal"):
        RepoAllowlistEntry(
            repo_full_name="QuantConnect/Lean",
            allowed_paths=("../secrets",),
        )

    with pytest.raises(SourceEvidenceError, match="repository-relative"):
        RepoAllowlistEntry(
            repo_full_name="QuantConnect/Lean",
            allowed_paths=("/etc",),
        )

    with pytest.raises(SourceEvidenceError, match="duplicate repo"):
        RepoAllowlistProvider(
            connector_id="conn-duplicate",
            allowlist=(
                {"repo_full_name": "QuantConnect/Lean", "allowed_paths": ("Research",)},
                {"repo_full_name": "QuantConnect/Lean", "allowed_paths": ("Algorithm.Python",)},
            ),
        )


def test_repo_allowlist_provider_example_is_exposed_in_catalog() -> None:
    examples = [
        provider.connector().to_dict()
        for provider in example_provider_catalog()
        if provider.connector().source_type.value == "repo"
    ]

    assert examples
    example = examples[0]
    assert example["connector_id"] == "example-github-repo-allowlist"
    assert example["metadata"]["repo_allowlist_policy"] == "repo_allowlist_v1"
    assert example["metadata"]["allowlist_enforced"] is True
    assert example["metadata"]["allowed_repositories"][0]["repo_full_name"] == "QuantConnect/Lean"
