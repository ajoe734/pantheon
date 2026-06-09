"""Tests for the registry split layer (DATASTRAT-REG-002).

Acceptance criteria:
1. Can add/list/get lifecycle for data sources.
2. Can add/list/get lifecycle for strategy seed sources.
3. Data source and strategy seed source CANNOT be confused — the
   ``source_kind`` discriminator is enforced by both from_dict and
   the registry add operation.
4. Disabled/retired sources are not ingestable.
5. JSONL persistence round-trip works for both registries.
6. Existing SourceConnector projects into exactly one product registry role
   unless explicitly configured twice (connector_projection module).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from services.source_ingestion.registry import (
    ConnectorProjectionError,
    DataSourceEntry,
    DataSourceLifecycleState,
    DataSourceRegistry,
    DataSourceRegistryError,
    RegistryRole,
    StrategySeedSourceEntry,
    StrategySeedSourceLifecycleState,
    StrategySeedSourceRegistry,
    StrategySeedSourceRegistryError,
    natural_role,
    project_to_data_source,
    project_to_strategy_seed_source,
)
from services.source_ingestion.connectors.base import SourceConnector, SourceType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_DATASET = [{"dataset_id": "tw_price_daily", "dataset_class": "market_daily"}]


def _data_source(**overrides) -> DataSourceEntry:
    defaults = dict(
        data_source_id="ds-finmind-tw-price",
        provider="FinMind",
        source_class="market_daily",
        datasets=MINIMAL_DATASET,
        license_scope="commercial",
        allowed_use=["research_data", "backtest_data"],
        update_frequency="daily",
        lifecycle_state="candidate",
    )
    defaults.update(overrides)
    return DataSourceEntry(**defaults)


def _seed_source(**overrides) -> StrategySeedSourceEntry:
    defaults = dict(
        strategy_seed_source_id="sss-openalex",
        provider="OpenAlex",
        source_class="paper",
        source_scope="public_metadata",
        license_scope="open",
        allowed_use=["research_discovery", "strategy_seed_generation"],
        lifecycle_state="candidate",
    )
    defaults.update(overrides)
    return StrategySeedSourceEntry(**defaults)


# ---------------------------------------------------------------------------
# DataSourceEntry model tests
# ---------------------------------------------------------------------------

class TestDataSourceEntry:
    def test_source_kind_discriminator_is_always_data_source(self) -> None:
        entry = _data_source()
        assert entry.source_kind == "data_source"

    def test_source_kind_cannot_be_overridden_via_kwargs(self) -> None:
        """source_kind is not an __init__ parameter; passing it should be rejected."""
        with pytest.raises(TypeError):
            DataSourceEntry(
                data_source_id="x",
                provider="x",
                source_class="market_daily",
                datasets=MINIMAL_DATASET,
                license_scope="open",
                allowed_use=["research_data"],
                update_frequency="daily",
                source_kind="strategy_seed_source",  # forbidden extra kwarg
            )

    def test_requires_data_source_id(self) -> None:
        with pytest.raises(DataSourceRegistryError, match="data_source_id is required"):
            _data_source(data_source_id="")

    def test_requires_provider(self) -> None:
        with pytest.raises(DataSourceRegistryError, match="provider is required"):
            _data_source(provider="")

    def test_requires_non_empty_datasets(self) -> None:
        with pytest.raises(DataSourceRegistryError, match="datasets must contain"):
            _data_source(datasets=[])

    def test_rejects_invalid_source_class(self) -> None:
        with pytest.raises(DataSourceRegistryError, match="source_class must be one of"):
            _data_source(source_class="paper")  # paper belongs to seed registry

    def test_rejects_invalid_allowed_use(self) -> None:
        with pytest.raises(DataSourceRegistryError, match="allowed_use value"):
            _data_source(allowed_use=["research_discovery"])  # seed registry use value

    def test_lifecycle_is_not_ingestable_when_disabled(self) -> None:
        entry = _data_source(lifecycle_state="disabled")
        assert not entry.is_ingestable

    def test_lifecycle_is_not_ingestable_when_retired(self) -> None:
        entry = _data_source(lifecycle_state="retired")
        assert not entry.is_ingestable

    def test_lifecycle_is_ingestable_when_enabled(self) -> None:
        entry = _data_source(lifecycle_state="enabled")
        assert entry.is_ingestable

    def test_lifecycle_is_ingestable_when_degraded(self) -> None:
        entry = _data_source(lifecycle_state="degraded")
        assert entry.is_ingestable

    def test_with_lifecycle_returns_new_entry(self) -> None:
        entry = _data_source(lifecycle_state="candidate")
        updated = entry.with_lifecycle("enabled")
        assert updated.lifecycle_state == DataSourceLifecycleState.ENABLED
        assert entry.lifecycle_state == DataSourceLifecycleState.CANDIDATE
        assert updated.data_source_id == entry.data_source_id

    def test_to_dict_round_trip(self) -> None:
        entry = _data_source()
        d = entry.to_dict()
        assert d["schema_version"] == "data_source_registry_entry.v1"
        assert d["source_kind"] == "data_source"
        recovered = DataSourceEntry.from_dict(d)
        assert recovered.data_source_id == entry.data_source_id
        assert recovered.source_kind == "data_source"

    def test_from_dict_rejects_strategy_seed_source_kind(self) -> None:
        d = _data_source().to_dict()
        d["source_kind"] = "strategy_seed_source"
        with pytest.raises(DataSourceRegistryError, match="source_kind must be 'data_source'"):
            DataSourceEntry.from_dict(d)


# ---------------------------------------------------------------------------
# StrategySeedSourceEntry model tests
# ---------------------------------------------------------------------------

class TestStrategySeedSourceEntry:
    def test_source_kind_discriminator_is_always_strategy_seed_source(self) -> None:
        entry = _seed_source()
        assert entry.source_kind == "strategy_seed_source"

    def test_source_kind_cannot_be_overridden_via_kwargs(self) -> None:
        with pytest.raises(TypeError):
            StrategySeedSourceEntry(
                strategy_seed_source_id="x",
                provider="x",
                source_class="paper",
                source_scope="public_metadata",
                license_scope="open",
                allowed_use=["research_discovery"],
                source_kind="data_source",  # forbidden extra kwarg
            )

    def test_requires_strategy_seed_source_id(self) -> None:
        with pytest.raises(StrategySeedSourceRegistryError, match="strategy_seed_source_id is required"):
            _seed_source(strategy_seed_source_id="")

    def test_rejects_invalid_source_class(self) -> None:
        with pytest.raises(StrategySeedSourceRegistryError, match="source_class must be one of"):
            _seed_source(source_class="market_daily")  # data registry class

    def test_rejects_invalid_allowed_use(self) -> None:
        with pytest.raises(StrategySeedSourceRegistryError, match="allowed_use value"):
            _seed_source(allowed_use=["research_data"])  # data registry use value

    def test_rejects_execution_route_in_metadata(self) -> None:
        with pytest.raises(StrategySeedSourceRegistryError, match="execution_route"):
            _seed_source(metadata={"execution_route": "live"})

    def test_rejects_research_only_false_in_metadata(self) -> None:
        with pytest.raises(StrategySeedSourceRegistryError, match="research_only must be true"):
            _seed_source(metadata={"research_only": False})

    def test_accepts_research_only_true_in_metadata(self) -> None:
        entry = _seed_source(metadata={"research_only": True})
        assert entry.metadata.get("research_only") is True

    def test_lifecycle_is_not_ingestable_when_disabled(self) -> None:
        entry = _seed_source(lifecycle_state="disabled")
        assert not entry.is_ingestable

    def test_lifecycle_is_ingestable_when_enabled(self) -> None:
        entry = _seed_source(lifecycle_state="enabled")
        assert entry.is_ingestable

    def test_with_lifecycle_returns_new_entry(self) -> None:
        entry = _seed_source(lifecycle_state="candidate")
        updated = entry.with_lifecycle("enabled")
        assert updated.lifecycle_state == StrategySeedSourceLifecycleState.ENABLED
        assert entry.lifecycle_state == StrategySeedSourceLifecycleState.CANDIDATE

    def test_to_dict_round_trip(self) -> None:
        entry = _seed_source()
        d = entry.to_dict()
        assert d["schema_version"] == "strategy_seed_source_registry_entry.v1"
        assert d["source_kind"] == "strategy_seed_source"
        recovered = StrategySeedSourceEntry.from_dict(d)
        assert recovered.strategy_seed_source_id == entry.strategy_seed_source_id
        assert recovered.source_kind == "strategy_seed_source"

    def test_from_dict_rejects_data_source_kind(self) -> None:
        d = _seed_source().to_dict()
        d["source_kind"] = "data_source"
        with pytest.raises(StrategySeedSourceRegistryError, match="source_kind must be 'strategy_seed_source'"):
            StrategySeedSourceEntry.from_dict(d)


# ---------------------------------------------------------------------------
# Cross-registry confusion tests (the core semantic boundary)
# ---------------------------------------------------------------------------

class TestRegistrySplitBoundary:
    """Prove that data sources and strategy seed sources are not interchangeable."""

    def test_data_source_dict_cannot_load_into_seed_registry(self) -> None:
        ds_dict = _data_source().to_dict()
        with pytest.raises(StrategySeedSourceRegistryError, match="source_kind must be 'strategy_seed_source'"):
            StrategySeedSourceEntry.from_dict(ds_dict)

    def test_seed_source_dict_cannot_load_into_data_registry(self) -> None:
        sss_dict = _seed_source().to_dict()
        with pytest.raises(DataSourceRegistryError, match="source_kind must be 'data_source'"):
            DataSourceEntry.from_dict(sss_dict)

    def test_data_source_allowed_use_is_rejected_by_seed_registry(self) -> None:
        """allowed_use values from the data registry are rejected by the seed registry."""
        with pytest.raises(StrategySeedSourceRegistryError, match="allowed_use value"):
            _seed_source(allowed_use=["research_data"])

    def test_seed_source_allowed_use_is_rejected_by_data_registry(self) -> None:
        """allowed_use values from the seed registry are rejected by the data registry."""
        with pytest.raises(DataSourceRegistryError, match="allowed_use value"):
            _data_source(allowed_use=["research_discovery"])

    def test_data_source_class_is_rejected_by_seed_registry(self) -> None:
        """source_class values from the data registry are rejected by the seed registry."""
        for cls_val in ["market_daily", "filing_event", "macro", "taiwan_chip"]:
            with pytest.raises(StrategySeedSourceRegistryError, match="source_class must be one of"):
                _seed_source(source_class=cls_val)

    def test_seed_source_class_is_rejected_by_data_registry(self) -> None:
        """source_class values from the seed registry are rejected by the data registry."""
        for cls_val in ["paper", "repo", "internal_note", "alpha_db"]:
            with pytest.raises(DataSourceRegistryError, match="source_class must be one of"):
                _data_source(source_class=cls_val)


# ---------------------------------------------------------------------------
# DataSourceRegistry CRUD tests
# ---------------------------------------------------------------------------

class TestDataSourceRegistry:
    def test_add_and_get(self) -> None:
        reg = DataSourceRegistry()
        entry = _data_source()
        reg.add(entry)
        fetched = reg.get(entry.data_source_id)
        assert fetched is not None
        assert fetched.data_source_id == entry.data_source_id

    def test_add_duplicate_raises(self) -> None:
        reg = DataSourceRegistry()
        entry = _data_source()
        reg.add(entry)
        with pytest.raises(DataSourceRegistryError, match="already registered"):
            reg.add(entry)

    def test_upsert_overwrites(self) -> None:
        reg = DataSourceRegistry()
        entry = _data_source(lifecycle_state="candidate")
        reg.add(entry)
        updated = entry.with_lifecycle("enabled")
        reg.upsert(updated)
        fetched = reg.get(entry.data_source_id)
        assert fetched.lifecycle_state == DataSourceLifecycleState.ENABLED

    def test_list_returns_all_entries(self) -> None:
        reg = DataSourceRegistry()
        reg.add(_data_source(data_source_id="ds-a", provider="A", update_frequency="daily"))
        reg.add(_data_source(data_source_id="ds-b", provider="B", update_frequency="weekly"))
        entries = reg.list()
        assert len(entries) == 2

    def test_set_lifecycle(self) -> None:
        reg = DataSourceRegistry()
        reg.add(_data_source())
        updated = reg.set_lifecycle("ds-finmind-tw-price", "enabled")
        assert updated.lifecycle_state == DataSourceLifecycleState.ENABLED
        assert reg.get("ds-finmind-tw-price").lifecycle_state == DataSourceLifecycleState.ENABLED

    def test_set_lifecycle_unknown_id_raises(self) -> None:
        reg = DataSourceRegistry()
        with pytest.raises(DataSourceRegistryError, match="Unknown data source"):
            reg.set_lifecycle("nonexistent", "enabled")

    def test_disabled_source_is_not_ingestable(self) -> None:
        reg = DataSourceRegistry()
        reg.add(_data_source())
        reg.set_lifecycle("ds-finmind-tw-price", "disabled")
        entry = reg.get("ds-finmind-tw-price")
        assert not entry.is_ingestable

    def test_jsonl_persistence_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data_sources.jsonl"
            reg1 = DataSourceRegistry.from_jsonl(path)
            reg1.add(_data_source(data_source_id="ds-stooq", provider="Stooq", update_frequency="daily"))
            reg1.add(_data_source(data_source_id="ds-fred", provider="FRED",
                                  source_class="macro", update_frequency="weekly"))

            reg2 = DataSourceRegistry.from_jsonl(path)
            assert len(reg2.list()) == 2
            assert reg2.get("ds-stooq") is not None
            stooq = reg2.get("ds-stooq")
            assert stooq.source_kind == "data_source"

    def test_jsonl_lifecycle_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ds.jsonl"
            reg1 = DataSourceRegistry.from_jsonl(path)
            reg1.add(_data_source())
            reg1.set_lifecycle("ds-finmind-tw-price", "enabled")

            reg2 = DataSourceRegistry.from_jsonl(path)
            entry = reg2.get("ds-finmind-tw-price")
            assert entry.lifecycle_state == DataSourceLifecycleState.ENABLED


# ---------------------------------------------------------------------------
# StrategySeedSourceRegistry CRUD tests
# ---------------------------------------------------------------------------

class TestStrategySeedSourceRegistry:
    def test_add_and_get(self) -> None:
        reg = StrategySeedSourceRegistry()
        entry = _seed_source()
        reg.add(entry)
        fetched = reg.get(entry.strategy_seed_source_id)
        assert fetched is not None
        assert fetched.strategy_seed_source_id == entry.strategy_seed_source_id

    def test_add_duplicate_raises(self) -> None:
        reg = StrategySeedSourceRegistry()
        entry = _seed_source()
        reg.add(entry)
        with pytest.raises(StrategySeedSourceRegistryError, match="already registered"):
            reg.add(entry)

    def test_upsert_overwrites(self) -> None:
        reg = StrategySeedSourceRegistry()
        entry = _seed_source(lifecycle_state="candidate")
        reg.add(entry)
        updated = entry.with_lifecycle("enabled")
        reg.upsert(updated)
        fetched = reg.get(entry.strategy_seed_source_id)
        assert fetched.lifecycle_state == StrategySeedSourceLifecycleState.ENABLED

    def test_list_returns_all_entries(self) -> None:
        reg = StrategySeedSourceRegistry()
        reg.add(_seed_source(strategy_seed_source_id="sss-a", provider="A"))
        reg.add(_seed_source(strategy_seed_source_id="sss-b", provider="B",
                             source_class="repo", source_scope="allowlisted_repo"))
        assert len(reg.list()) == 2

    def test_set_lifecycle(self) -> None:
        reg = StrategySeedSourceRegistry()
        reg.add(_seed_source())
        updated = reg.set_lifecycle("sss-openalex", "enabled")
        assert updated.lifecycle_state == StrategySeedSourceLifecycleState.ENABLED

    def test_disabled_source_is_not_ingestable(self) -> None:
        reg = StrategySeedSourceRegistry()
        reg.add(_seed_source())
        reg.set_lifecycle("sss-openalex", "disabled")
        assert not reg.get("sss-openalex").is_ingestable

    def test_retired_source_is_not_ingestable(self) -> None:
        reg = StrategySeedSourceRegistry()
        reg.add(_seed_source(lifecycle_state="enabled"))
        reg.set_lifecycle("sss-openalex", "retired")
        assert not reg.get("sss-openalex").is_ingestable

    def test_jsonl_persistence_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "seed_sources.jsonl"
            reg1 = StrategySeedSourceRegistry.from_jsonl(path)
            reg1.add(_seed_source(strategy_seed_source_id="sss-arxiv", provider="arXiv"))
            reg1.add(_seed_source(strategy_seed_source_id="sss-github",
                                  provider="GitHub",
                                  source_class="repo",
                                  source_scope="allowlisted_repo"))

            reg2 = StrategySeedSourceRegistry.from_jsonl(path)
            assert len(reg2.list()) == 2
            github = reg2.get("sss-github")
            assert github is not None
            assert github.source_kind == "strategy_seed_source"

    def test_jsonl_lifecycle_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sss.jsonl"
            reg1 = StrategySeedSourceRegistry.from_jsonl(path)
            reg1.add(_seed_source())
            reg1.set_lifecycle("sss-openalex", "enabled")

            reg2 = StrategySeedSourceRegistry.from_jsonl(path)
            entry = reg2.get("sss-openalex")
            assert entry.lifecycle_state == StrategySeedSourceLifecycleState.ENABLED


# ---------------------------------------------------------------------------
# Connector projection tests (acceptance criterion 3)
# ---------------------------------------------------------------------------

def _market_connector(**overrides) -> SourceConnector:
    defaults = dict(
        connector_id="conn-finmind-market",
        source_type="market",
        provider="FinMind",
        license_scope="commercial",
    )
    defaults.update(overrides)
    return SourceConnector(**defaults)


def _paper_connector(**overrides) -> SourceConnector:
    defaults = dict(
        connector_id="conn-openalex-paper",
        source_type="paper",
        provider="OpenAlex",
        license_scope="open",
    )
    defaults.update(overrides)
    return SourceConnector(**defaults)


_MINIMAL_DATASETS = [{"dataset_id": "tw_price_daily", "dataset_class": "market_daily"}]


class TestConnectorProjection:
    """SourceConnector projects into exactly one product registry role."""

    def test_market_connector_natural_role_is_data_source(self) -> None:
        conn = _market_connector()
        assert natural_role(conn) == RegistryRole.DATA_SOURCE

    def test_paper_connector_natural_role_is_strategy_seed_source(self) -> None:
        conn = _paper_connector()
        assert natural_role(conn) == RegistryRole.STRATEGY_SEED_SOURCE

    def test_all_data_source_types_have_data_source_role(self) -> None:
        data_types = ["market", "filing", "news", "social", "macro"]
        for st in data_types:
            conn = _market_connector(connector_id=f"conn-{st}", source_type=st, provider="P")
            assert natural_role(conn) == RegistryRole.DATA_SOURCE, f"{st} should map to data_source"

    def test_all_seed_source_types_have_strategy_seed_role(self) -> None:
        seed_types = ["paper", "repo", "internal_note", "telemetry", "alpha_db"]
        for st in seed_types:
            conn = _paper_connector(connector_id=f"conn-{st}", source_type=st, provider="P")
            assert natural_role(conn) == RegistryRole.STRATEGY_SEED_SOURCE, f"{st} should map to strategy_seed_source"

    def test_project_market_connector_to_data_source(self) -> None:
        conn = _market_connector()
        entry = project_to_data_source(
            conn,
            data_source_id="ds-finmind-market",
            datasets=_MINIMAL_DATASETS,
            allowed_use=["research_data", "backtest_data"],
            update_frequency="daily",
        )
        assert entry.source_kind == "data_source"
        assert entry.connector_id == conn.connector_id
        assert entry.provider == conn.provider

    def test_project_paper_connector_to_strategy_seed_source(self) -> None:
        conn = _paper_connector()
        entry = project_to_strategy_seed_source(
            conn,
            strategy_seed_source_id="sss-openalex-proj",
            source_scope="public_metadata",
            allowed_use=["research_discovery", "strategy_seed_generation"],
        )
        assert entry.source_kind == "strategy_seed_source"
        assert entry.connector_id == conn.connector_id
        assert entry.provider == conn.provider

    def test_market_connector_cannot_project_to_strategy_seed_source(self) -> None:
        conn = _market_connector()
        with pytest.raises(ConnectorProjectionError, match="natural role"):
            project_to_strategy_seed_source(
                conn,
                strategy_seed_source_id="sss-wrong",
                source_scope="public_metadata",
                allowed_use=["research_discovery"],
            )

    def test_paper_connector_cannot_project_to_data_source(self) -> None:
        conn = _paper_connector()
        with pytest.raises(ConnectorProjectionError, match="natural role"):
            project_to_data_source(
                conn,
                data_source_id="ds-wrong",
                datasets=_MINIMAL_DATASETS,
                allowed_use=["research_data"],
                update_frequency="daily",
            )

    def test_force_role_requires_non_empty_note(self) -> None:
        conn = _market_connector()
        with pytest.raises(ConnectorProjectionError, match="force_role_note"):
            project_to_strategy_seed_source(
                conn,
                strategy_seed_source_id="sss-dual",
                source_scope="internal",
                allowed_use=["internal_research_only"],
                force_role=True,
                force_role_note="",
            )

    def test_force_role_with_note_allows_cross_projection(self) -> None:
        """Dual-role registration must be explicit with an audit note."""
        conn = _market_connector()
        entry = project_to_strategy_seed_source(
            conn,
            strategy_seed_source_id="sss-tej-dual",
            source_scope="restricted_vendor",
            allowed_use=["internal_research_only"],
            force_role=True,
            force_role_note="TEJ also provides strategy factor ideas — separate entry for seed role.",
        )
        assert entry.source_kind == "strategy_seed_source"
        assert entry.connector_id == conn.connector_id

    def test_connector_id_is_preserved_in_data_source_entry(self) -> None:
        conn = _market_connector(connector_id="conn-unique-market-id")
        entry = project_to_data_source(
            conn,
            data_source_id="ds-market-x",
            datasets=_MINIMAL_DATASETS,
            allowed_use=["research_data"],
            update_frequency="daily",
        )
        assert entry.connector_id == "conn-unique-market-id"

    def test_connector_id_is_preserved_in_seed_source_entry(self) -> None:
        conn = _paper_connector(connector_id="conn-unique-paper-id")
        entry = project_to_strategy_seed_source(
            conn,
            strategy_seed_source_id="sss-paper-x",
            source_scope="public_metadata",
            allowed_use=["research_discovery"],
        )
        assert entry.connector_id == "conn-unique-paper-id"
