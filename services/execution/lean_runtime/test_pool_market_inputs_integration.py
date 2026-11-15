"""Multi-member envelope: real Source router + store over HTTP -> real producer -> paper runtime.

Nothing between Source and the paper ledger is faked: the Source side is the real
``create_ingest_operations_router`` over a real ``LatestMarketSnapshotStore``; the pool
is evaluated by the real ``CurrentArtifactStrategy`` and filled by ``PaperRuntimeService``.
"""
import json
import threading
import time
import urllib.request
from datetime import datetime
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI

from services.execution.lean_runtime import pool_market_inputs as pmi
from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy, PaperSignalProducer
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.test_current_artifact_signal import (
    _binding, _identity, _runtime_binding, _RuntimeManager, _Telemetry,
)
from services.execution.lean_runtime.test_portfolio_target_positions import (
    cash_candidates, multi_envelope, production_inputs,
)
from services.execution.lean_runtime.test_expression_inputs import _pool, _summary
from services.source_ingestion.requirement_state import (
    LatestMarketSnapshot, LatestMarketSnapshotStore, MarketSnapshotPoint, _checksum,
)
from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router

SYMBOLS = ["2330.TW", "2317.TW", "2454.TW"]
DAY = "2026-11-13"
NOW = DAY + "T06:00:00Z"


def _snapshot(symbol, closes, day=DAY):
    points = tuple(
        MarketSnapshotPoint(event_time=f"{day}T05:30:00Z" if i == len(closes) - 1 else "2026-11-10T05:30:00Z",
                            close=c, source_id=f"s-{symbol}-{i}", connector_id="tw-twse-tpex-official-market",
                            content_ref=f"tw-official:{symbol}:{i}", ingest_run_id="run-1")
        for i, c in enumerate(closes))
    return LatestMarketSnapshot(symbol=symbol.replace(".TW", ".TWSE"), points=points, observed_at=DAY + "T05:45:00Z", market="TW")


def _write(path, snapshots):
    lines = []
    for snap in snapshots:
        state = snap.to_dict()
        lines.append(json.dumps({"state": state, "checksum_algorithm": "sha256", "checksum": _checksum(state)}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def source(tmp_path, monkeypatch):
    log = tmp_path / "latest.jsonl"
    store = LatestMarketSnapshotStore(log)
    app = FastAPI()
    app.include_router(create_ingest_operations_router(SimpleNamespace(
        latest_market_snapshot_store=store, MARKET_DATA_STORAGE_ROOT=tmp_path / "store")))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_URL", f"http://127.0.0.1:{port}")
    requests = []
    real = pmi.fetch_source_snapshot
    monkeypatch.setattr(pmi, "fetch_source_snapshot",
                        lambda url, symbol: (requests.append(symbol), real(url, symbol))[1])
    def publish(snapshots):
        _write(log, snapshots)
        store.reload()

    yield SimpleNamespace(publish=publish, requests=requests, root=tmp_path / "store")
    server.should_exit = True
    thread.join(timeout=5)


def _binding_for(artifact):
    binding = _binding(artifact, binding_id="rb-pool", include_market_input=False)
    binding["capital_pool_id"] = "pool-1"
    binding["market_data_policy"] = {"owner": "source-ingest", "contract": "latest_stored_normalized",
                                     "minimum_closes": 2, "max_age_seconds": 86400}
    return binding


def _service(binding, tmp_path, summary):
    strategy = CurrentArtifactStrategy(expression_layer=True, expression_inputs=production_inputs(summary),
                                       candidates_builder=cash_candidates)
    store, telemetry = InMemoryPendingSignalStore(), _Telemetry()
    producer = PaperSignalProducer(lambda _: store, strategy)
    service = PaperRuntimeService(store=store, identity=_identity(binding),
        runtime_manager_client=_RuntimeManager(_runtime_binding(binding)), telemetry_emitter=telemetry,
        lifecycle_outbox_path=tmp_path / "lifecycle.json", capital_pool_reader=lambda _: _pool())
    service._algo._in_process_broker = True
    service._algo.Time = datetime.fromisoformat(DAY + "T06:00:00+00:00")
    return producer, service


def test_two_member_three_symbol_pool_orders_from_real_source(source, tmp_path):
    artifact = multi_envelope(weight=.8)
    artifact["expression_policy"]["parameters"]["notional_fraction_at_full_strength"] = .2
    binding = _binding_for(artifact)
    assert "market_inputs" not in binding
    source.publish([_snapshot(s, [100, 102]) for s in SYMBOLS])
    producer, service = _service(binding, tmp_path, _summary())
    started = time.monotonic()
    assert producer.produce(binding, NOW) == 3, producer.degraded_bindings
    latency = time.monotonic() - started
    for _ in range(4):
        snapshot = service.drain_once()
    assert snapshot["status"] == "ok", snapshot
    assert {p["symbol"] for p in service._algo.positions()} == set(SYMBOLS)
    # one request per envelope symbol per tick, bounded latency
    assert sorted(source.requests) == sorted(SYMBOLS)
    assert latency < 10


def test_one_symbol_failing_admission_records_no_decision_for_whole_pool(source, tmp_path):
    artifact = multi_envelope(weight=.8)
    binding = _binding_for(artifact)
    source.publish([_snapshot(s, [100, 102]) for s in SYMBOLS[:2]])  # 2454.TW has no snapshot
    producer, service = _service(binding, tmp_path, _summary())
    assert producer.produce(binding, NOW) == 0
    assert "market_input_missing" in json.dumps(producer.degraded_bindings, default=str)
    assert service._algo.positions() == []


def test_symbol_on_an_older_bar_records_no_decision_for_whole_pool(source, tmp_path):
    artifact = multi_envelope(weight=.8)
    binding = _binding_for(artifact)
    source.publish([_snapshot("2330.TW", [100, 102], day="2026-11-12"),
                    _snapshot("2317.TW", [100, 102]), _snapshot("2454.TW", [100, 102])])
    producer, _ = _service(binding, tmp_path, _summary())
    assert producer.produce(binding, NOW) == 0
    # The real Taiwan admission rule rejects the older bar and names the symbol; no partial net view.
    assert "pool symbol 2330.TW failed admission" in json.dumps(producer.degraded_bindings, default=str)


SCREEN_DAY = "2026-11-02"  # Monday after the last recorded session (Friday 2026-10-30)
SCREEN_NOW = SCREEN_DAY + "T04:00:00Z"


def _store_recorded_market(root, drop=None):
    """Write the recorded official TWSE bars as the governed normalized tw_price_daily partitions."""
    from services.registry.test_candidate_pool import recorded_market

    market = recorded_market()
    for symbol, payload in market["symbols"].items():
        for bar in payload["bars"]:
            if drop and drop(symbol, bar):
                continue
            day = bar["event_time"][:10]
            path = root / "normalized" / "tw_price_daily" / f"date={day}" / "run-1.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            row = {"date": day, "symbol_canonical": symbol, "symbol": symbol[:4], "venue": "TWSE",
                   "close": bar["close"], "open": bar["open"], "high": bar["high"], "low": bar["low"],
                   "volume": bar["volume"], "value": bar["traded_value"], "available_time": bar["available_time"]}
            with path.open("a") as handle:
                handle.write(json.dumps({"source_id": f"s-{symbol}-{day}", "dataset": "tw_price_daily",
                                         "metadata": {"normalized_row": row, "available_time": bar["available_time"]}}) + "\n")
    return market


def _screened_pool():
    from services.registry.portfolio_envelope import build_portfolio_envelope
    from services.registry.test_candidate_pool import screen_artifact
    from services.registry.test_strategy_artifact import _artifact_with_expression_policy

    screened = screen_artifact()
    screened["parameters"]["symbols"] = ["2330.TW"]
    constant = _artifact_with_expression_policy()
    constant["parameters"].update(symbols=["2330.TW", "2317.TW"], market="TW",
                                  data_source=screened["parameters"]["data_source"])
    constant["mutation_surface"]["immutable_parameters"].append("market")
    # The recorded closes fall into the decision day; this member buys weakness.
    constant["strategy_logic"].update(positive_action="SELL", non_positive_action="BUY")
    constant["parameters"]["zero_momentum_action"] = "BUY"
    artifact = build_portfolio_envelope(artifact_id="screened-pool", strategy_id="screened-pool",
        version="1.0.0", members=[{"member_id": "screen", "artifact": screened, "risk_weight": .5},
                                  {"member_id": "constant", "artifact": constant, "risk_weight": .5}])
    artifact["expression_policy"]["parameters"]["notional_fraction_at_full_strength"] = .2
    return artifact


def test_screened_member_pool_orders_from_governed_normalized_market(source, tmp_path, monkeypatch):
    _store_recorded_market(source.root)
    artifact = _screened_pool()
    binding = _binding_for(artifact)
    binding["symbol"] = None
    assert "features" not in binding and "market_inputs" not in binding
    calls = []
    real = pmi.fetch_candidate_pool_market
    monkeypatch.setattr(pmi, "fetch_candidate_pool_market",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    producer, service = _service(binding, tmp_path, _summary())
    service._algo.Time = datetime.fromisoformat(SCREEN_DAY + "T04:00:00+00:00")
    started = time.monotonic()
    produced = producer.produce(binding, SCREEN_NOW)
    first = time.monotonic() - started
    assert produced > 0, producer.degraded_bindings
    for _ in range(4):
        snapshot = service.drain_once()
    assert snapshot["status"] == "ok", snapshot
    assert service._algo.positions()
    # The whole market is requested once per decision day, not again on the next tick.
    started = time.monotonic()
    producer.produce(binding, SCREEN_DAY + "T04:01:00Z")
    assert len(calls) == 1
    assert first < 30 and time.monotonic() - started < 30
    assert source.requests == []  # every needed symbol is inside the governed market


def test_missing_governed_dataset_is_needs_data_naming_it(source, tmp_path):
    binding = _binding_for(_screened_pool())
    producer, service = _service(binding, tmp_path, _summary())
    assert producer.produce(binding, SCREEN_NOW) == 0
    assert "needs_data" in json.dumps(producer.degraded_bindings, default=str)
    assert "tw_price_daily" in json.dumps(producer.degraded_bindings, default=str)
    assert service._algo.positions() == []
