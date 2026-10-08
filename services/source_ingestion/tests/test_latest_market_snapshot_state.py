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
    """When snapshot lacks market and binding/artifact lack market, fails closed with market_input_missing naming the required market field."""
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
    assert exc_info.value.code == "market_input_missing"
    assert "market" in str(exc_info.value)


def test_stored_marketless_spy_snapshot_becomes_market_bearing_and_admits_producer(tmp_path: Path) -> None:
    """Acceptance 4: stored marketless SPY snapshot becomes legitimately market-bearing ('US')

    derived from registered Source connector authority before final producer health gate,
    without silent US or lexical guesses.
    """
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    snapshot_path = tmp_path / "latest_market_snapshots.jsonl"
    store = LatestMarketSnapshotStore(snapshot_path)

    # 1. Pre-seed marketless snapshot matching historical VM state (e.g. mss-008c3b5fa7f0563691f3be83)
    p_old1 = MarketSnapshotPoint(
        event_time="2026-10-05T00:00:00Z",
        close=514.0,
        source_id="src-old-1",
        connector_id="conn-legacy",
        content_ref="ref-1",
        ingest_run_id="run-old-1",
        market=None,
    )
    p_old2 = MarketSnapshotPoint(
        event_time=now_iso,
        close=515.0,
        source_id="src-old-2",
        connector_id="conn-legacy",
        content_ref="ref-2",
        ingest_run_id="run-old-2",
        market=None,
    )
    initial_snapshot = LatestMarketSnapshot(
        symbol="SPY",
        points=[p_old1, p_old2],
        observed_at=now_iso,
        market=None,
    )
    assert initial_snapshot.market is None

    # Write initial snapshot to store
    state = initial_snapshot.to_dict()
    state_json = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    envelope = {
        "state": state,
        "checksum_algorithm": "sha256",
        "checksum": hashlib.sha256(state_json.encode("utf-8")).hexdigest(),
    }
    with snapshot_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    store.reload()

    read_back = store.get("SPY")
    assert read_back is not None
    assert read_back.market is None

    # 2. Producer rejects marketless snapshot fail-closed
    reg = load_strategy_artifact_registration(BUILTIN_STRATEGY_ARTIFACT_PATHS[0])
    legacy_artifact = copy.deepcopy(reg["strategy_artifact"])
    legacy_artifact["artifact_id"] = "artifact-dev-paper-sp-1"
    legacy_artifact["strategy_id"] = "dev_paper_strategy"
    legacy_artifact["parameters"]["symbols"] = ["SPY"]
    legacy_artifact["lineage"]["source_dataset_refs"] = ["source-ingest:dev-paper-simulation"]

    object_store, checksum = _projection(legacy_artifact)

    binding = {
        "binding_id": "rb-dev-paper-1",
        "runtime_id": "runtime-rb-1",
        "capital_pool_id": "pool-rb-1",
        "plan_id": "plan-rb-1",
        "persona_capital_binding_id": "pcb-rb-1",
        "status": "active",
        "deployment_mode": "paper",
        "strategy_id": "dev_paper_strategy",
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
        "market_input": read_back.to_public_dict(),
    }
    strategy = CurrentArtifactStrategy()
    with pytest.raises(SignalDecisionUnavailable) as exc_info:
        strategy(binding, now_iso)
    assert exc_info.value.code == "market_input_missing"
    assert "market" in str(exc_info.value)

    producer = PaperSignalProducer(
        store_for=lambda _: InMemoryPendingSignalStore(),
        strategy=strategy,
    )
    assert producer.produce(binding, now_iso) == 0
    assert "market_input_missing" in producer._degraded_by_binding[binding["binding_id"]]
    assert "market" in producer._degraded_by_binding[binding["binding_id"]]

    # 3. Simulate Source connector authority refresh using DevPaperUsEquitySimulationAdapter
    connector = _DummyConnector(
        metadata={
            "dev_only": True,
            "is_real": False,
            "market": "US",
            "provenance": "simulation",
            "symbols": ["SPY"],
        }
    )
    adapter = DevPaperUsEquitySimulationAdapter(connector_metadata=connector.metadata)
    records = adapter.records_from_now(symbols=["SPY"])
    assert len(records) >= 1
    for record in records:
        assert record.metadata.get("market") == "US"

    # Append authoritative records into store
    update_res = store.append_normalized_records(
        records,
        ingest_run_id="run-fresh-sim",
        observed_at=now_iso,
        connector=connector,
    )
    assert update_res["updated_snapshot_count"] == 1

    # 4. Prove snapshot is now legitimately market-bearing ('US') without lexical guess
    updated_snap = store.get("SPY")
    assert updated_snap is not None
    assert updated_snap.market == "US"
    assert len(updated_snap.closes) >= 2

    # 5. Producer successfully evaluates and admits the snapshot
    pending_store = InMemoryPendingSignalStore()
    active_producer = PaperSignalProducer(
        store_for=lambda _: pending_store,
        strategy=CurrentArtifactStrategy(),
    )
    binding["market_input"] = updated_snap.to_public_dict()
    signals = active_producer.produce(binding, now_iso)
    assert signals == 1
    assert len(pending_store._pending) == 1
    assert pending_store._pending[0]["symbol"] == "SPY.US"
