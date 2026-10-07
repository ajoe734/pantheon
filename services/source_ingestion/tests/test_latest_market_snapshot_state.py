"""Tests for Source Ingest LatestMarketSnapshot state and market context propagation."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from services.execution.artifact_loader import ArtifactLoader
from services.registry.strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS,
    load_strategy_artifact_registration,
)
from services.execution.lean_runtime.paper_signal_producer import (
    CurrentArtifactStrategy,
    PaperSignalProducer,
    SignalDecisionUnavailable,
)
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.connectors.dev_paper_simulation import (
    DevPaperUsEquitySimulationAdapter,
)
from services.source_ingestion.market_snapshot import (
    LatestMarketSnapshot,
    LatestMarketSnapshotStore,
    MarketSnapshotPoint,
    MarketSnapshotStateError,
)


@dataclass
class _DummyConnector:
    metadata: dict[str, Any]


def _make_point(
    event_time: str = "2026-10-07T00:00:00Z",
    close: float = 520.0,
    market: str | None = None,
    source_id: str = "src-1",
) -> MarketSnapshotPoint:
    return MarketSnapshotPoint(
        event_time=event_time,
        close=close,
        source_id=source_id,
        connector_id="conn-1",
        content_ref="ref-1",
        ingest_run_id="run-1",
        market=market,
    )


def test_market_snapshot_point_market_persistence() -> None:
    point = _make_point(market="US")
    assert point.market == "US"
    data = point.to_dict()
    assert data["market"] == "US"
    loaded = MarketSnapshotPoint.from_dict(data)
    assert loaded.market == "US"
    assert loaded == point


def test_market_snapshot_point_market_optional() -> None:
    point = _make_point(market=None)
    assert point.market is None
    data = point.to_dict()
    assert "market" not in data
    loaded = MarketSnapshotPoint.from_dict(data)
    assert loaded.market is None
    assert loaded == point


def test_latest_market_snapshot_market_propagation() -> None:
    p1 = _make_point(event_time="2026-10-06T00:00:00Z", close=519.0, market="US", source_id="src-1")
    p2 = _make_point(event_time="2026-10-07T00:00:00Z", close=520.0, market="US", source_id="src-2")
    snapshot = LatestMarketSnapshot(
        symbol="SPY",
        points=(p1, p2),
        observed_at="2026-10-07T00:05:00Z",
    )
    assert snapshot.market == "US"
    public = snapshot.to_public_dict()
    assert public["market"] == "US"
    assert public["symbol"] == "SPY"

    # Verify serialization roundtrip
    stored = snapshot.to_dict()
    assert stored["market"] == "US"
    loaded = LatestMarketSnapshot.from_dict(stored)
    assert loaded.market == "US"
    assert loaded.snapshot_id == snapshot.snapshot_id


def test_latest_market_snapshot_contradictory_points_market_rejected() -> None:
    p1 = _make_point(event_time="2026-10-06T00:00:00Z", close=519.0, market="US", source_id="src-1")
    p2 = _make_point(event_time="2026-10-07T00:00:00Z", close=520.0, market="TW", source_id="src-2")
    with pytest.raises(MarketSnapshotStateError, match="contradictory markets"):
        LatestMarketSnapshot(
            symbol="SPY",
            points=(p1, p2),
            observed_at="2026-10-07T00:05:00Z",
        )


def test_latest_market_snapshot_contradiction_with_intrinsic_symbol() -> None:
    p1 = _make_point(event_time="2026-10-07T00:00:00Z", close=520.0, market="US")
    with pytest.raises(MarketSnapshotStateError, match="contradicts explicit market"):
        LatestMarketSnapshot(
            symbol="2330.TW",
            points=(p1,),
            observed_at="2026-10-07T00:05:00Z",
        )


def test_latest_market_snapshot_explicit_contradicts_point_market() -> None:
    p1 = _make_point(event_time="2026-10-07T00:00:00Z", close=520.0, market="US")
    with pytest.raises(MarketSnapshotStateError, match="contradicts points market"):
        LatestMarketSnapshot(
            symbol="SPY",
            points=(p1,),
            observed_at="2026-10-07T00:05:00Z",
            market="TW",
        )


def test_store_append_normalized_records_extracts_market_from_normalized_row(tmp_path: Path) -> None:
    store = LatestMarketSnapshotStore(tmp_path / "snapshots.jsonl")
    records = [
        SourceRecord(
            source_id="src-1",
            connector_id="conn-1",
            source_type="market",
            title="SPY Close",
            content_ref="ref-1",
            metadata={
                "normalized_row": {
                    "symbol": "SPY",
                    "close": 520.0,
                    "event_time": "2026-10-07T00:00:00Z",
                    "market": "US",
                },
            },
        ),
        SourceRecord(
            source_id="src-2",
            connector_id="conn-1",
            source_type="market",
            title="SPY Close prior",
            content_ref="ref-2",
            metadata={
                "normalized_row": {
                    "symbol": "SPY",
                    "close": 519.0,
                    "event_time": "2026-10-06T00:00:00Z",
                    "market": "US",
                },
            },
        ),
    ]
    batch = store.append_normalized_records(records, ingest_run_id="run-1")
    assert batch["updated_snapshot_count"] == 1
    snapshot = store.get("SPY")
    assert snapshot is not None
    assert snapshot.market == "US"
    assert snapshot.to_public_dict()["market"] == "US"


def test_store_append_normalized_records_extracts_market_from_metadata(tmp_path: Path) -> None:
    store = LatestMarketSnapshotStore(tmp_path / "snapshots.jsonl")
    records = [
        SourceRecord(
            source_id="src-1",
            connector_id="conn-1",
            source_type="market",
            title="SPY Close",
            content_ref="ref-1",
            metadata={
                "symbol": "SPY",
                "close": 520.0,
                "event_time": "2026-10-07T00:00:00Z",
                "market": "US",
            },
        ),
        SourceRecord(
            source_id="src-2",
            connector_id="conn-1",
            source_type="market",
            title="SPY Close prior",
            content_ref="ref-2",
            metadata={
                "symbol": "SPY",
                "close": 519.0,
                "event_time": "2026-10-06T00:00:00Z",
                "market": "US",
            },
        ),
    ]
    batch = store.append_normalized_records(records, ingest_run_id="run-1")
    assert batch["updated_snapshot_count"] == 1
    snapshot = store.get("SPY")
    assert snapshot is not None
    assert snapshot.market == "US"


def test_store_append_normalized_records_uses_connector_fallback(tmp_path: Path) -> None:
    store = LatestMarketSnapshotStore(tmp_path / "snapshots.jsonl")
    records = [
        SourceRecord(
            source_id="src-1",
            connector_id="conn-1",
            source_type="market",
            title="SPY Close",
            content_ref="ref-1",
            metadata={
                "symbol": "SPY",
                "close": 520.0,
                "event_time": "2026-10-07T00:00:00Z",
            },
        ),
        SourceRecord(
            source_id="src-2",
            connector_id="conn-1",
            source_type="market",
            title="SPY Close prior",
            content_ref="ref-2",
            metadata={
                "symbol": "SPY",
                "close": 519.0,
                "event_time": "2026-10-06T00:00:00Z",
            },
        ),
    ]
    connector = _DummyConnector(metadata={"market": "US"})
    batch = store.append_normalized_records(records, ingest_run_id="run-1", connector=connector)
    assert batch["updated_snapshot_count"] == 1
    snapshot = store.get("SPY")
    assert snapshot is not None
    assert snapshot.market == "US"


def test_store_rejects_contradictory_record_market(tmp_path: Path) -> None:
    store = LatestMarketSnapshotStore(tmp_path / "snapshots.jsonl")
    records = [
        SourceRecord(
            source_id="src-1",
            connector_id="conn-1",
            source_type="market",
            title="SPY Close",
            content_ref="ref-1",
            metadata={
                "market": "US",
                "normalized_row": {
                    "symbol": "SPY",
                    "close": 520.0,
                    "event_time": "2026-10-07T00:00:00Z",
                    "market": "TW",  # Contradicts metadata
                },
            },
        ),
    ]
    batch = store.append_normalized_records(records, ingest_run_id="run-1")
    assert batch["accepted_record_count"] == 0
    assert store.get("SPY") is None


def test_legacy_record_without_market_stays_none(tmp_path: Path) -> None:
    store = LatestMarketSnapshotStore(tmp_path / "snapshots.jsonl")
    records = [
        SourceRecord(
            source_id="src-1",
            connector_id="conn-1",
            source_type="market",
            title="SPY Close",
            content_ref="ref-1",
            metadata={
                "symbol": "SPY",
                "close": 520.0,
                "event_time": "2026-10-07T00:00:00Z",
            },
        ),
        SourceRecord(
            source_id="src-2",
            connector_id="conn-1",
            source_type="market",
            title="SPY Close prior",
            content_ref="ref-2",
            metadata={
                "symbol": "SPY",
                "close": 519.0,
                "event_time": "2026-10-06T00:00:00Z",
            },
        ),
    ]
    batch = store.append_normalized_records(records, ingest_run_id="run-1")
    assert batch["updated_snapshot_count"] == 1
    snapshot = store.get("SPY")
    assert snapshot is not None
    assert snapshot.market is None
    assert "market" not in snapshot.to_public_dict()


def test_dev_paper_simulation_adapter_emits_market_in_row_and_metadata() -> None:
    adapter = DevPaperUsEquitySimulationAdapter()
    records = adapter.records_from_now(symbols=["SPY"])
    assert len(records) >= 1
    for rec in records:
        assert rec.metadata.get("market") == "US"
        assert rec.metadata["normalized_row"].get("market") == "US"
        assert rec.metadata["normalized_row"]["symbol"] == "SPY"
        assert rec.metadata["is_real"] is False
        assert rec.metadata["provenance"] == "simulation"


def test_dev_paper_simulation_adapter_fails_closed_without_metadata_market() -> None:
    """AC3 requirement: connector fails closed without market when connector_metadata lacks market (no guessing 'US')."""
    adapter = DevPaperUsEquitySimulationAdapter(connector_metadata={})
    records = adapter.records_from_now(symbols=["SPY"])
    assert len(records) >= 1
    for rec in records:
        assert rec.metadata.get("market") is None
        assert "market" not in rec.metadata["normalized_row"]


def _projection(artifact: dict[str, Any]) -> tuple[dict[str, Any], str]:
    payload = json.dumps(
        artifact,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checksum = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    metadata = {
        "registry_id": artifact["artifact_id"],
        "strategy_id": artifact["strategy_id"],
        "version": artifact["version"],
        "artifact_type": "execution_bundle",
        "artifact_state": "approved",
        "deployment_stage": "paper",
        "promotion_state": "paper",
        "lineage": dict(artifact["lineage"]),
        "created_at": "2026-10-07T00:00:00Z",
        "checksum": checksum,
    }
    projection = ArtifactLoader.build_projection(
        artifact["strategy_id"], artifact["version"]
    )
    return {
        projection.metadata_key: metadata,
        projection.artifact_key: payload,
    }, checksum


def test_paper_signal_producer_admit_snapshot_with_market_us() -> None:
    """A legacy binding without market context successfully admits a Source snapshot declaring market 'US'."""
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    reg = load_strategy_artifact_registration(BUILTIN_STRATEGY_ARTIFACT_PATHS[0])
    legacy_artifact = copy.deepcopy(reg["strategy_artifact"])
    legacy_artifact["artifact_id"] = "artifact-legacy-paper-1"
    legacy_artifact["strategy_id"] = "legacy_strategy"
    legacy_artifact["parameters"]["symbols"] = ["SPY"]
    legacy_artifact["lineage"]["source_dataset_refs"] = ["source-ingest:dev-paper-simulation"]

    object_store, checksum = _projection(legacy_artifact)

    p1 = MarketSnapshotPoint(
        event_time="2026-10-06T00:00:00Z",
        close=518.0,
        source_id="src",
        connector_id="conn",
        content_ref="ref",
        ingest_run_id="run",
        market="US",
    )
    p2 = MarketSnapshotPoint(
        event_time=now_iso,
        close=520.0,
        source_id="src",
        connector_id="conn",
        content_ref="ref",
        ingest_run_id="run",
        market="US",
    )
    snap = LatestMarketSnapshot(
        symbol="SPY",
        points=[p1, p2],
        market="US",
        observed_at=now_iso,
    )

    binding = {
        "binding_id": "rb-legacy-1",
        "runtime_id": "runtime-rb-legacy-1",
        "capital_pool_id": "pool-rb-legacy-1",
        "plan_id": "plan-rb-legacy-1",
        "persona_capital_binding_id": "pcb-rb-legacy-1",
        "status": "active",
        "deployment_mode": "paper",
        "strategy_id": "legacy_strategy",
        "artifact_id": legacy_artifact["artifact_id"],
        "artifact_version": legacy_artifact["version"],
        "artifact_checksum": checksum,
        "object_store": object_store,
        "symbol": "SPY",
        "market_data_policy": {
            "owner": "source-ingest",
            "contract": "latest_stored_normalized",
            "max_age_seconds": 86400,
            "minimum_closes": 2,
        },
        "strategy_artifact": legacy_artifact,
        "market_input": snap.to_public_dict(),
    }

    store = InMemoryPendingSignalStore()
    producer = PaperSignalProducer(
        store_for=lambda _: store,
        strategy=CurrentArtifactStrategy(),
    )

    signals_produced = producer.produce(binding, now_iso)
    assert signals_produced == 1
    assert len(store._pending) == 1
    signal = store._pending[0]
    assert signal["symbol"] == "SPY.US"
    assert signal["metadata"]["raw_symbol"] == "SPY"
    assert signal["binding_id"] == "rb-legacy-1"


def test_paper_signal_producer_rejects_missing_market_context_fail_closed() -> None:
    """When snapshot lacks market and binding/artifact lack market, fails closed with market_context_missing."""
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    reg = load_strategy_artifact_registration(BUILTIN_STRATEGY_ARTIFACT_PATHS[0])
    legacy_artifact = copy.deepcopy(reg["strategy_artifact"])
    legacy_artifact["artifact_id"] = "artifact-legacy-paper-2"
    legacy_artifact["strategy_id"] = "legacy_strategy_2"
    legacy_artifact["parameters"]["symbols"] = ["SPY"]
    legacy_artifact["lineage"]["source_dataset_refs"] = ["source-ingest:dev-paper-simulation"]

    object_store, checksum = _projection(legacy_artifact)

    p1 = MarketSnapshotPoint(
        event_time="2026-10-06T00:00:00Z",
        close=518.0,
        source_id="src",
        connector_id="conn",
        content_ref="ref",
        ingest_run_id="run",
        market=None,
    )
    p2 = MarketSnapshotPoint(
        event_time=now_iso,
        close=520.0,
        source_id="src",
        connector_id="conn",
        content_ref="ref",
        ingest_run_id="run",
        market=None,
    )
    snap = LatestMarketSnapshot(
        symbol="SPY",
        points=[p1, p2],
        market=None,
        observed_at=now_iso,
    )

    binding = {
        "binding_id": "rb-legacy-2",
        "runtime_id": "runtime-rb-legacy-2",
        "capital_pool_id": "pool-rb-legacy-2",
        "plan_id": "plan-rb-legacy-2",
        "persona_capital_binding_id": "pcb-rb-legacy-2",
        "status": "active",
        "deployment_mode": "paper",
        "strategy_id": "legacy_strategy_2",
        "artifact_id": legacy_artifact["artifact_id"],
        "artifact_version": legacy_artifact["version"],
        "artifact_checksum": checksum,
        "object_store": object_store,
        "symbol": "SPY",
        "market_data_policy": {
            "owner": "source-ingest",
            "contract": "latest_stored_normalized",
            "max_age_seconds": 86400,
            "minimum_closes": 2,
        },
        "strategy_artifact": legacy_artifact,
        "market_input": snap.to_public_dict(),
    }

    strategy = CurrentArtifactStrategy()
    with pytest.raises(SignalDecisionUnavailable) as exc_info:
        strategy(binding, now_iso)
    assert exc_info.value.code == "market_context_missing"
