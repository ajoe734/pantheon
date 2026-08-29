"""Tests for paper_signal_producer (closes the missing-signal-source gap)."""
import unittest
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import json
import io

from services.execution.lean_runtime.paper_signal_producer import (
    BindingRef,
    PaperSignalProducer,
    SmokeStrategy,
    BoundedPaperStrategy,
    build_smoke_signal,
    fetch_eligible_paper_bindings,
    healthcheck,
    main,
)
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.signal_consumer import SignalConsumer
from services.trade_journey.correlation_envelope import validate_envelope

_NOW = "2026-06-14T10:30:00Z"
_BINDING = BindingRef(binding_id="rb-test-001", strategy_id="strategy-test-001", symbol="AAPL.US")


class TestSmokeSignalContract(unittest.TestCase):
    def test_smoke_signal_passes_store_and_consumer_validation(self) -> None:
        sig = build_smoke_signal(_BINDING, _NOW)
        # store-side transport validation (raises on failure)
        store = InMemoryPendingSignalStore()
        store.enqueue(sig)
        self.assertEqual(store.queue_depth(), 1)
        # consumer-side canonical validation (research/schema.json)
        consumer = SignalConsumer(store_client=MagicMock())
        validated = consumer._validate(sig)
        self.assertIsNotNone(validated, "smoke signal must pass consumer _validate")
        self.assertEqual(validated["action"], "BUY")
        self.assertEqual(validated["symbol"], "AAPL.US")
        envelope = validate_envelope(validated["correlation_envelope"])
        self.assertEqual(envelope["journey_id"], validated["journey_id"])
        self.assertEqual(envelope["tenant_id"], "default")
        self.assertEqual(envelope["environment"], "paper")
        self.assertTrue(validated["run_id"].startswith("run-rb-test-001-"))


class TestPaperSignalProducer(unittest.TestCase):
    def test_producer_enqueues_per_binding(self) -> None:
        stores = {}

        def store_for(binding: Any) -> InMemoryPendingSignalStore:
            bid = getattr(binding, "binding_id", "")
            if not bid and isinstance(binding, dict):
                bid = binding.get("binding_id", "")
            return stores.setdefault(bid, InMemoryPendingSignalStore())

        bindings = [
            BindingRef("rb-a", "strategy-a"),
            BindingRef("rb-b", "strategy-b"),
        ]
        producer = PaperSignalProducer(store_for=store_for, strategy=SmokeStrategy())
        result = producer.tick(bindings, _NOW)

        self.assertEqual(result, {"rb-a": 1, "rb-b": 1})
        self.assertEqual(stores["rb-a"].queue_depth(), 1)
        self.assertEqual(stores["rb-b"].queue_depth(), 1)
        # signals round-trip and carry the binding's own strategy/binding ids
        drained = stores["rb-a"].get_pending()
        self.assertEqual(len(drained), 1)
        self.assertEqual(drained[0]["strategy_id"], "strategy-a")
        self.assertEqual(drained[0]["binding_id"], "rb-a")
        self.assertEqual(
            drained[0]["journey_id"],
            validate_envelope(drained[0]["correlation_envelope"])["journey_id"],
        )

    def test_smoke_strategy_emits_unique_signal_ids_across_ticks(self) -> None:
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(store_for=lambda b: store, strategy=SmokeStrategy())
        producer.produce(_BINDING, _NOW)
        producer.produce(_BINDING, _NOW)
        ids = {s["signal_id"] for s in store.get_pending()}
        self.assertEqual(len(ids), 2, "each tick must emit a distinct signal_id")

    def test_parse_bindings_env(self) -> None:
        from services.execution.lean_runtime.paper_signal_producer import parse_bindings_env
        out = parse_bindings_env('[{"binding_id":"rb-x","strategy_id":"s-x"},{"binding_id":"rb-y","strategy_id":"s-y","symbol":"2330.TW"}]')
        self.assertEqual([b.binding_id for b in out], ["rb-x", "rb-y"])
        self.assertEqual(out[1].symbol, "2330.TW")
        self.assertEqual(parse_bindings_env(""), [])

    @patch.dict("os.environ", {"PANTHEON_TENANT_ID": "test-tenant-123"})
    def test_bounded_paper_strategy_and_decision_producer_flow(self) -> None:
        stores = {}

        def store_for(binding: Any) -> InMemoryPendingSignalStore:
            bid = binding if isinstance(binding, str) else binding.get("binding_id")
            if not bid:
                bid = getattr(binding, "binding_id", "")
            return stores.setdefault(bid, InMemoryPendingSignalStore())

        binding = {
            "binding_id": "rb-tw-test",
            "runtime_id": "rt-tw-test",
            "capital_pool_id": "pool-tw-test",
            "artifact_id": "art-tw-test",
            "artifact_version": "2.0.0",
            "persona_capital_binding_id": "pcb-personaX-001",
            "symbol": "2330.TW",
            "metadata": {
                "strategy_id": "tw_momentum_v1"
            }
        }

        producer = PaperSignalProducer(store_for=store_for, strategy=BoundedPaperStrategy())
        result = producer.produce(binding, _NOW)
        self.assertEqual(result, 1)

        store = stores["rb-tw-test"]
        self.assertEqual(store.queue_depth(), 1)
        [sig] = store.get_pending()

        self.assertEqual(sig["version"], "1.0")
        self.assertEqual(sig["binding_id"], "rb-tw-test")
        self.assertEqual(sig["runtime_id"], "rt-tw-test")
        self.assertEqual(sig["strategy_id"], "tw_momentum_v1")
        self.assertEqual(sig["symbol"], "2330.TW")
        self.assertEqual(sig["action"], "BUY")
        self.assertEqual(sig["direction"], "LONG")
        envelope = validate_envelope(sig["correlation_envelope"])
        self.assertEqual(envelope["tenant_id"], "test-tenant-123")
        self.assertEqual(envelope["environment"], "paper")
        self.assertEqual(envelope["journey_id"], sig["journey_id"])
        self.assertTrue(sig["run_id"].startswith("run-rb-tw-test-"))

        # Verify metadata / identities
        self.assertEqual(sig["metadata"]["tenant_id"], "test-tenant-123")
        self.assertEqual(sig["metadata"]["persona_id"], "personaX")
        self.assertEqual(sig["metadata"]["persona_capital_binding_id"], "pcb-personaX-001")
        self.assertEqual(sig["metadata"]["capital_pool_id"], "pool-tw-test")
        self.assertEqual(sig["metadata"]["artifact_id"], "art-tw-test")
        self.assertEqual(sig["metadata"]["artifact_version"], "2.0.0")
        self.assertFalse(sig["metadata"]["is_real_order"])
        self.assertFalse(sig["metadata"]["is_real_capital"])

    def test_bounded_paper_strategy_evaluates_strategy_artifact(self) -> None:
        stores = {}

        def store_for(binding: Any) -> InMemoryPendingSignalStore:
            bid = binding if isinstance(binding, str) else binding.get("binding_id")
            if not bid:
                bid = getattr(binding, "binding_id", "")
            return stores.setdefault(bid, InMemoryPendingSignalStore())

        binding = {
            "binding_id": "rb-artifact-eval-test",
            "runtime_id": "rt-eval-test",
            "capital_pool_id": "pool-eval-test",
            "artifact_id": "art-eval-test",
            "artifact_version": "1.0.0",
            "persona_capital_binding_id": "pcb-eval-001",
            "symbol": "2330.TW",
            "metadata": {
                "strategy_id": "tw_momentum_v1",
                "recent_closes": [100.0, 95.0],
                "strategy_artifact": {
                    "artifact_schema_version": "1.0",
                    "artifact_id": "art-eval-test",
                    "strategy_id": "tw_momentum_v1",
                    "version": "1.0.0",
                    "algorithm_ref": {
                        "engine": "lean",
                        "repository": "ajoe734/pantheon-lean",
                        "commit": "5ad0249432459c119f26718007e083808ef7995d",
                        "path": "pantheon_algo/base.py",
                        "entrypoint": "pantheon_algo.base:PantheonAlgoBase",
                        "signal_interface": "services.execution.lean_runtime.paper_signal_producer:Strategy",
                        "signal_schema_version": "1.0",
                        "logic_interpreter": "services.registry.strategy_artifact:evaluate_strategy_action"
                    },
                    "strategy_logic": {
                        "kind": "close_to_close_momentum",
                        "lookback_parameter": "lookback_bars",
                        "threshold_parameter": "momentum_threshold",
                        "positive_action": "BUY",
                        "non_positive_action": "SELL"
                    },
                    "parameters": {
                        "lookback_bars": 2,
                        "momentum_threshold": 0.0
                    },
                    "mutation_surface": {
                        "controls": [
                            {
                                "parameter_key": "lookback_bars",
                                "value_type": "integer",
                                "current_value": 2,
                                "allowed_range": {"min": 2, "max": 60},
                                "step": 1
                            },
                            {
                                "parameter_key": "momentum_threshold",
                                "value_type": "number",
                                "current_value": 0.0,
                                "allowed_range": {"min": 0.0, "max": 0.05},
                                "step": 0.001
                            }
                        ],
                        "immutable_parameters": []
                    },
                    "lineage": {"source_run_ids": ["EVO-001"]}
                }
            }
        }

        producer = PaperSignalProducer(store_for=store_for, strategy=BoundedPaperStrategy())
        producer.produce(binding, _NOW)

        store = stores["rb-artifact-eval-test"]
        [sig] = store.get_pending()

        # momentum is (95 - 100) / 100 = -0.05 <= 0.0 -> SELL / SHORT
        self.assertEqual(sig["action"], "SELL")
        self.assertEqual(sig["direction"], "SHORT")

    @patch("urllib.request.urlopen")
    def test_fetch_eligible_paper_bindings(self, mock_urlopen) -> None:
        desired_response = MagicMock()
        desired_response.read.return_value = json.dumps({
            "bindings": [
                {
                    "binding_id": "rb-1",
                    "status": "active",
                    "deployment_mode": "paper",
                },
                {
                    "binding_id": "rb-2",
                    "status": "paused",
                    "deployment_mode": "paper",
                }
            ],
            "excluded": []
        }).encode("utf-8")
        detail_response = MagicMock()
        detail_response.read.return_value = json.dumps(
            {
                "artifact_id": "artifact-1",
                "artifact_version": "1.0.0",
                "binding_id": "rb-1",
                "metadata": {"strategy_id": "strategy-1"},
                "status": "active",
            }
        ).encode("utf-8")
        desired_context = MagicMock()
        desired_context.__enter__.return_value = desired_response
        detail_context = MagicMock()
        detail_context.__enter__.return_value = detail_response
        mock_urlopen.side_effect = [desired_context, detail_context]

        bindings = fetch_eligible_paper_bindings("http://runtime-manager:8081", "token-xyz")
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["binding_id"], "rb-1")
        self.assertEqual(bindings[0]["status"], "active")
        self.assertEqual(bindings[0]["metadata"]["strategy_id"], "strategy-1")
        self.assertEqual(mock_urlopen.call_count, 2)
        detail_request = mock_urlopen.call_args_list[1].args[0]
        self.assertEqual(
            detail_request.full_url,
            "http://runtime-manager:8081/api/runtime-bindings/rb-1",
        )
        self.assertEqual(
            detail_request.get_header("Authorization"),
            "Bearer token-xyz",
        )

    def test_health_requires_recent_paper_only_tick(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            health_file = Path(directory) / "paper-producer-health.json"
            env = {
                "SIGNAL_STORE_URL": "redis://signal-store:6379",
                "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
                "PAPER_PRODUCER_INTERVAL_SECONDS": "1",
                "PAPER_PRODUCER_MAX_TICKS": "1",
                "PAPER_PRODUCER_HEALTH_FILE": str(health_file),
                "PANTHEON_LIVE_BROKER_ENABLED": "false",
                "PANTHEON_CANARY_EXECUTION_ENABLED": "false",
            }
            with patch.dict("os.environ", env, clear=False), patch(
                "services.execution.lean_runtime.paper_signal_producer."
                "fetch_eligible_paper_bindings",
                return_value=[],
            ):
                self.assertEqual(main(), 0)
                self.assertEqual(healthcheck(), 0)

            state = json.loads(health_file.read_text(encoding="utf-8"))
            self.assertEqual(state["worker_name"], "paper-signal-producer")
            self.assertEqual(state["status"], "ok")
            self.assertEqual(state["ticks"], 1)
            self.assertEqual(state["execution_mode"], "paper")
            self.assertFalse(state["live_capital_enabled"])
            self.assertFalse(state["live_order_submission_enabled"])

    @patch("urllib.request.urlopen")
    def test_market_input_fetched_from_source_ingest_api(self, mock_urlopen) -> None:
        from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy
        from services.execution.lean_runtime.test_current_artifact_signal import _artifact, _binding
        artifact = _artifact()
        binding = _binding(artifact, binding_id="rb-source-ingest-test", include_market_input=False)
        binding["symbol"] = "AAPL.US"
        binding["market_data_policy"] = {
            "owner": "source-ingest",
            "contract": "latest_stored_normalized",
            "max_age_seconds": 300,
            "minimum_closes": 2,
        }
        
        response = MagicMock()
        response.read.return_value = json.dumps({
            "symbol": "AAPL.US",
            "closes": [100.0, 105.0, 110.0],
            "snapshot_id": "mss-source-ingest-test",
            "event_time": _NOW,
            "source_ref": "source-ingest://snapshots/mss-source-ingest-test",
            "observed_at": _NOW,
            "lineage": {
                "source_ids": ["source-aapl-test"],
                "connector_ids": ["stored-price-test"],
                "content_refs": ["source-test://AAPL.US"],
                "ingest_run_ids": ["run-source-ingest-test"],
            },
        }).encode("utf-8")
        context = MagicMock()
        context.__enter__.return_value = response
        mock_urlopen.return_value = context

        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(
            store_for=lambda _: store,
            strategy=CurrentArtifactStrategy(),
        )

        with patch.dict("os.environ", {"PANTHEON_SOURCE_INGEST_URL": "http://source-ingest:8080"}):
            count = producer.produce(binding, _NOW)

        self.assertEqual(count, 1)
        self.assertEqual(store.queue_depth(), 1)
        [sig] = store.get_pending()
        self.assertEqual(sig["symbol"], "AAPL.US")
        self.assertEqual(sig["metadata"]["market_input_ref"], "source-ingest://snapshots/mss-source-ingest-test")
        self.assertEqual(sig["metadata"]["market_input_snapshot_id"], "mss-source-ingest-test")
        self.assertEqual(producer.degraded_bindings, {})

    @patch("urllib.request.urlopen")
    def test_tw_alias_snapshot_passes_producer_admission_with_official_lineage(
        self,
        mock_urlopen,
    ) -> None:
        from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy
        from services.execution.lean_runtime.test_current_artifact_signal import _artifact, _binding

        artifact = _artifact()
        artifact["parameters"]["symbols"] = ["2330.TW"]
        binding = _binding(
            artifact,
            binding_id="rb-source-ingest-tw-alias",
            include_market_input=False,
        )
        binding["symbol"] = "2330.TW"
        binding["market_data_policy"] = {
            "owner": "source-ingest",
            "contract": "latest_stored_normalized",
            "max_age_seconds": 86400,
            "minimum_closes": 2,
        }

        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "symbol": "2330.TW",
                "closes": [950.0, 955.0],
                "snapshot_id": "mss-official-twse-2330",
                "event_time": _NOW,
                "source_ref": "source-ingest://snapshots/mss-official-twse-2330",
                "observed_at": _NOW,
                "lineage": {
                    "source_ids": [
                        "tw-official:tw_price_daily:TWSE:2330:checksummed"
                    ],
                    "connector_ids": ["tw-twse-tpex-official-market"],
                    "content_refs": [
                        "tw-official://tw_price_daily/TWSE/2330/2026-06-14/checksummed"
                    ],
                    "ingest_run_ids": ["ingest-official-twse-2330"],
                },
            }
        ).encode("utf-8")
        context = MagicMock()
        context.__enter__.return_value = response
        mock_urlopen.return_value = context

        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(
            store_for=lambda _: store,
            strategy=CurrentArtifactStrategy(),
        )

        with patch.dict(
            "os.environ",
            {"PANTHEON_SOURCE_INGEST_URL": "http://source-ingest:8080"},
        ):
            count = producer.produce(binding, _NOW)

        self.assertEqual(count, 1)
        [signal] = store.get_pending()
        self.assertEqual(signal["symbol"], "2330.TW")
        self.assertEqual(
            signal["metadata"]["market_input_snapshot_id"],
            "mss-official-twse-2330",
        )
        self.assertEqual(
            signal["metadata"]["market_input_lineage"]["source_ids"],
            ["tw-official:tw_price_daily:TWSE:2330:checksummed"],
        )
        self.assertEqual(producer.degraded_bindings, {})

    @patch("urllib.request.urlopen")
    def test_stale_source_snapshot_emits_no_signal_with_typed_reason(self, mock_urlopen) -> None:
        from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy
        from services.execution.lean_runtime.test_current_artifact_signal import _artifact, _binding

        binding = _binding(_artifact(), binding_id="rb-source-ingest-stale", include_market_input=False)
        binding["symbol"] = "AAPL.US"
        binding["market_data_policy"] = {
            "owner": "source-ingest",
            "contract": "latest_stored_normalized",
            "max_age_seconds": 60,
            "minimum_closes": 2,
        }
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "symbol": "AAPL.US",
                "closes": [100.0, 105.0],
                "snapshot_id": "mss-stale-test",
                "event_time": "2026-06-14T10:00:00Z",
                "observed_at": "2026-06-14T10:00:00Z",
                "source_ref": "source-ingest://snapshots/mss-stale-test",
                "lineage": {"source_ids": ["old-source"]},
            }
        ).encode("utf-8")
        context = MagicMock()
        context.__enter__.return_value = response
        mock_urlopen.return_value = context
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(store_for=lambda _: store, strategy=CurrentArtifactStrategy())

        with patch.dict("os.environ", {"PANTHEON_SOURCE_INGEST_URL": "http://source-ingest:8080"}):
            count = producer.produce(binding, _NOW)

        self.assertEqual(count, 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertTrue(producer.degraded_bindings["rb-source-ingest-stale"].startswith("market_input_stale:"))

    @patch("urllib.request.urlopen")
    def test_recent_closes_never_replaces_a_source_snapshot(self, mock_urlopen) -> None:
        from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy
        from services.execution.lean_runtime.test_current_artifact_signal import _artifact, _binding

        binding = _binding(_artifact(), binding_id="rb-no-static-closes", include_market_input=False)
        binding["recent_closes"] = [100.0, 105.0]
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(store_for=lambda _: store, strategy=CurrentArtifactStrategy())

        count = producer.produce(binding, _NOW)

        self.assertEqual(count, 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertTrue(producer.degraded_bindings["rb-no-static-closes"].startswith("market_input_missing:"))
        mock_urlopen.assert_not_called()


class TestSharedSnapshotAdmissionDecisions(unittest.TestCase):
    """Verify that pure snapshot admission rule returns correct and deterministic decisions."""

    def test_shared_admission_fresh_snapshot(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        now = "2026-08-22T12:00:00Z"
        snapshot = {
            "snapshot_id": "snap-pass-001",
            "symbol": "AAPL.US",
            "event_time": "2026-08-22T11:59:00Z",
            "observed_at": "2026-08-22T11:59:05Z",
            "source_ref": "source-ref-1",
            "lineage": {"source": "manual"},
            "closes": [150.0, 151.0, 152.0],
        }
        dec = admit_market_snapshot(
            snapshot,
            expected_symbol="AAPL.US",
            max_age_seconds=86400,
            minimum_closes=2,
            now_iso=now,
        )
        self.assertTrue(dec.admitted)
        self.assertEqual(dec.snapshot_id, "snap-pass-001")
        self.assertAlmostEqual(dec.age_seconds, 60.0, places=1)
        self.assertIsNone(dec.reason_code)

    def test_shared_admission_stale_snapshot(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        now = "2026-08-22T12:00:00Z"
        snapshot = {
            "snapshot_id": "snap-stale-001",
            "symbol": "AAPL.US",
            "event_time": "2026-08-20T00:00:00Z",
            "observed_at": "2026-08-20T00:00:00Z",
            "source_ref": "source-ref-1",
            "lineage": {"source": "manual"},
            "closes": [150.0, 151.0],
        }
        dec = admit_market_snapshot(
            snapshot,
            expected_symbol="AAPL.US",
            max_age_seconds=86400,
            minimum_closes=2,
            now_iso=now,
        )
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_stale")
        self.assertEqual(dec.snapshot_id, "snap-stale-001")
        self.assertTrue(dec.age_seconds > 86400)

    def test_shared_admission_future_timestamp(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        now = "2026-08-22T12:00:00Z"
        snapshot = {
            "snapshot_id": "snap-future-001",
            "symbol": "AAPL.US",
            "event_time": "2026-08-22T13:00:00Z",
            "observed_at": "2026-08-22T12:00:00Z",
            "source_ref": "source-ref-1",
            "lineage": {"source": "manual"},
            "closes": [150.0, 151.0],
        }
        dec = admit_market_snapshot(
            snapshot,
            expected_symbol="AAPL.US",
            max_age_seconds=86400,
            minimum_closes=2,
            now_iso=now,
        )
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_invalid")
        self.assertIn("future", dec.detail)

    def test_shared_admission_missing_or_insufficient_closes(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        now = "2026-08-22T12:00:00Z"
        snap_no_closes = {
            "snapshot_id": "snap-no-closes",
            "symbol": "AAPL.US",
            "event_time": "2026-08-22T11:59:00Z",
            "source_ref": "ref",
            "lineage": {},
        }
        dec1 = admit_market_snapshot(snap_no_closes, max_age_seconds=86400, now_iso=now)
        self.assertFalse(dec1.admitted)
        self.assertEqual(dec1.reason_code, "market_input_missing")

        snap_one_close = {
            **snap_no_closes,
            "closes": [150.0],
        }
        dec2 = admit_market_snapshot(snap_one_close, minimum_closes=2, max_age_seconds=86400, now_iso=now)
        self.assertFalse(dec2.admitted)
        self.assertEqual(dec2.reason_code, "market_input_insufficient")

    @staticmethod
    def _tw_snapshot(event_time, observed_at, symbol="2330.TWSE"):
        return {
            "snapshot_id": "mss-tw-freshness",
            "symbol": symbol,
            "event_time": event_time,
            "observed_at": observed_at,
            "source_ref": "source-ingest://snapshots/mss-tw-freshness",
            "lineage": {
                "source_ids": ["tw-official:tw_price_daily:TWSE:2330:checksummed"],
                "connector_ids": ["tw-twse-tpex-official-market"],
            },
            "closes": [950.0, 955.0],
        }

    @staticmethod
    def _tw_calendar_evidence(
        *,
        holidays=None,
        trading_days=None,
        sessions=None,
        coverage_start="2026-01-01",
        coverage_end="2026-12-31",
        source_url="https://www.twse.com.tw/en/trading/calendar.html",
        authority="TWSE/TPEx announced holiday schedule",
        version="2026.1",
        checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        timezone="Asia/Taipei",
        market="TW",
        venue="TWSE",
        fetched_at="2026-01-01T00:00:00Z",
        extra=None,
    ):
        ev = {
            "market": market,
            "venue": venue,
            "timezone": timezone,
            "authority": authority,
            "source_url": source_url,
            "fetched_at": fetched_at,
            "version": version,
            "checksum": checksum,
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
            "holidays": holidays if holidays is not None else {
                "2026-02-16": {"name": "Lunar New Year (eve)"},
                "2026-02-17": {"name": "Lunar New Year"},
                "2026-02-18": {"name": "Lunar New Year"},
                "2026-02-19": {"name": "Lunar New Year"},
                "2026-02-20": {"name": "Lunar New Year (makeup)"},
            },
        }
        if trading_days is not None:
            ev["trading_days"] = trading_days
        if sessions is not None:
            ev["sessions"] = sessions
        if extra:
            ev.update(extra)
        return ev

    def test_tw_friday_close_admitted_on_saturday(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Friday close evaluated on Saturday: deterministic weekend requires no calendar feed.
        snapshot = self._tw_snapshot("2026-08-28T05:30:00Z", "2026-08-29T11:00:00Z")
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-08-29T12:00:00Z")
        self.assertTrue(dec.admitted)
        self.assertIsNone(dec.reason_code)

    def test_tw_holiday_span_admitted_with_calendar_evidence(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Friday 2026-02-13 close, evaluated 2026-02-23 after the official
        # Lunar New Year holiday table (2026-02-16..20) plus weekends
        # fully explain the gap to Monday 2026-02-23 before market close.
        snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence()
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertTrue(dec.admitted)
        self.assertIsNone(dec.reason_code)

    def test_tw_calendar_evidence_missing_weekday_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Evidence covers 2026-02-16..18 but is missing 2026-02-19 and 2026-02-20.
        snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            holidays={
                "2026-02-16": {"name": "LNY 1"},
                "2026-02-17": {"name": "LNY 2"},
                "2026-02-18": {"name": "LNY 3"},
            },
            coverage_start=None,
            coverage_end=None,
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("missing explicit session record for weekday", dec.detail)

    def test_tw_calendar_evidence_no_coverage_range_inference_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 1: coverage_start/end must NOT infer that unlisted weekdays are trading.
        # An unlisted completed weekday must fail closed as market_input_calendar_unverifiable.
        snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            holidays={
                "2026-02-16": {"name": "LNY 1"},
                "2026-02-17": {"name": "LNY 2"},
                "2026-02-18": {"name": "LNY 3"},
                # 2026-02-19 and 2026-02-20 are missing from holidays and trading_days
            },
            coverage_start="2026-02-01",
            coverage_end="2026-02-28",
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("missing explicit session record for weekday 2026-02-19", dec.detail)

    def test_tw_calendar_evidence_incomplete_range_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Evidence range starts on 2026-02-17, missing 2026-02-16.
        snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            coverage_start="2026-02-17",
            coverage_end="2026-02-28",
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("before coverage_start", dec.detail)

    def test_tw_weekday_close_without_calendar_evidence_rejected_as_unverifiable(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 2: Missing calendar evidence for a completed weekday session must return
        # market_input_calendar_unverifiable (not market_input_stale).
        snapshot = self._tw_snapshot("2026-08-28T05:30:00Z", "2026-08-31T05:45:00Z")
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-08-31T06:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("no official Taiwan calendar evidence provided", dec.detail)

    def test_tw_weekday_close_with_explicit_trading_session_rejected_as_stale(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 2: Stale is returned only when an explicit validated trading session occurred.
        snapshot = self._tw_snapshot("2026-08-28T05:30:00Z", "2026-08-31T05:45:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            holidays={},
            trading_days=["2026-08-31"],
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-08-31T06:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_stale")
        self.assertIn("a newer official Taiwan session closed", dec.detail)

    def test_tw_holiday_lookup_authority_only_rejected(self) -> None:
        from datetime import datetime

        from services.execution.market_snapshot_admission import evaluate_taiwan_market_freshness

        # Defect 3: holiday_lookup returning authority-only dict without full contract is rejected.
        def authority_only_lookup(_date_iso: str):
            return {"authority": "TWSE"}

        ok, reason_code, detail = evaluate_taiwan_market_freshness(
            event_time_dt=datetime.fromisoformat("2026-08-28T05:30:00+00:00"),
            now_dt=datetime.fromisoformat("2026-08-31T06:00:00+00:00"),
            refresh_receipt_dt=datetime.fromisoformat("2026-08-31T05:45:00+00:00"),
            lineage={
                "source_ids": ["tw-official:tw_price_daily:TWSE:2330:checksummed"],
                "connector_ids": ["tw-twse-tpex-official-market"],
            },
            max_refresh_age_seconds=86400,
            holiday_lookup=authority_only_lookup,
        )
        self.assertFalse(ok)
        self.assertEqual(reason_code, "market_input_calendar_unverifiable")
        self.assertIn("validation failed", detail)

    def test_tw_calendar_evidence_taifex_or_tw_venue_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 4: TAIFEX or TW venues must be rejected; only cash venues TWSE/TPEX are accepted.
        for bad_venue in ("TAIFEX", "TW", "HKEX"):
            snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
            snapshot["calendar_evidence"] = self._tw_calendar_evidence(venue=bad_venue)
            dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
            self.assertFalse(dec.admitted)
            self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
            self.assertIn("not an official Taiwan cash venue", dec.detail)

    def test_tw_calendar_evidence_utc_timezone_alias_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 4: UTC aliases like UTC+8, +08:00, UTC must be rejected; exact Asia/Taipei required.
        for bad_tz in ("UTC+8", "+08:00", "UTC+08:00", "UTC", "Asia/Tokyo"):
            snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
            snapshot["calendar_evidence"] = self._tw_calendar_evidence(timezone=bad_tz)
            dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
            self.assertFalse(dec.admitted)
            self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
            self.assertIn("expected exact 'Asia/Taipei'", dec.detail)

    def test_tw_calendar_evidence_http_scheme_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 5: HTTP scheme is rejected; HTTPS is required.
        snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            source_url="http://www.twse.com.tw/en/trading/calendar.html",
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("scheme 'http' must be https", dec.detail)

    def test_tw_calendar_evidence_bad_checksum_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 5: Checksum must be 64-hex SHA-256 matching external pin.
        for bad_cs in ("short", "not_a_hex_string_with_64_characters_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"):
            snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
            snapshot["calendar_evidence"] = self._tw_calendar_evidence(checksum=bad_cs)
            dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
            self.assertFalse(dec.admitted)
            self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
            self.assertIn("checksum", dec.detail)

    def test_tw_calendar_evidence_self_asserted_checksum_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 5: Self-asserted checksums cannot bypass the external trusted pin.
        untrusted_sha = "1111111111111111111111111111111111111111111111111111111111111111"
        snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            checksum=untrusted_sha,
            extra={"expected_checksum": untrusted_sha, "expected_sha256": untrusted_sha},
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("does not match", dec.detail)

    def test_tw_calendar_evidence_future_fetched_at_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 6: Future fetched_at timestamp (>300s) is rejected.
        snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            fetched_at="2026-02-23T10:00:00Z",  # 7 hours after now_iso (03:00)
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("fetched_at '2026-02-23T10:00:00Z' is in the future", dec.detail)

    def test_tw_calendar_evidence_non_iso_dates_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 6: Strict ISO date validation for coverage, holiday, session, and trading dates.
        # Case A: non-ISO coverage_start
        snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(coverage_start="2026/01/01")
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("invalid coverage_start", dec.detail)

        # Case B: non-ISO holiday date key
        snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            holidays={"2026-2-16": {"name": "LNY"}},
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("invalid holiday date", dec.detail)

        # Case C: non-ISO session date
        snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            sessions={"02/16/2026": {"type": "holiday"}},
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("invalid session date", dec.detail)

        # Case D: non-ISO trading_days
        snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            trading_days=["2026.02.16"],
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("invalid trading date", dec.detail)

    def test_tw_calendar_evidence_unofficial_url_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        snapshot = self._tw_snapshot("2026-02-13T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            source_url="https://example.com/unverified-calendar.json",
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("not an official TWSE/TPEx domain", dec.detail)

    def test_tw_stale_refresh_receipt_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # observed_at (refresh receipt) is itself more than max_age_seconds old.
        snapshot = self._tw_snapshot("2026-08-28T05:30:00Z", "2026-08-27T11:00:00Z")
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-08-29T12:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_stale_refresh")

    def test_tw_future_event_time_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        snapshot = self._tw_snapshot("2026-08-30T05:30:00Z", "2026-08-29T11:00:00Z")
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-08-29T12:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_invalid")

    def test_tw_non_official_lineage_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        snapshot = self._tw_snapshot("2026-08-28T05:30:00Z", "2026-08-29T11:00:00Z")
        snapshot["lineage"] = {"source_ids": ["some-other-vendor:feed"]}
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-08-29T12:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_non_official_lineage")

    def test_tw_missing_calendar_evidence_rejected(self) -> None:
        from datetime import datetime

        from services.execution.market_snapshot_admission import (
            CALENDAR_EVIDENCE_UNVERIFIABLE,
            evaluate_taiwan_market_freshness,
        )

        def unverifiable_lookup(_date_iso: str):
            return CALENDAR_EVIDENCE_UNVERIFIABLE

        ok, reason_code, detail = evaluate_taiwan_market_freshness(
            event_time_dt=datetime.fromisoformat("2026-08-28T05:30:00+00:00"),
            now_dt=datetime.fromisoformat("2026-09-01T12:00:00+00:00"),
            refresh_receipt_dt=datetime.fromisoformat("2026-09-01T11:00:00+00:00"),
            lineage={
                "source_ids": ["tw-official:tw_price_daily:TWSE:2330:checksummed"],
                "connector_ids": ["tw-twse-tpex-official-market"],
            },
            max_refresh_age_seconds=999_999,
            holiday_lookup=unverifiable_lookup,
        )
        self.assertFalse(ok)
        self.assertEqual(reason_code, "market_input_calendar_unverifiable")
        self.assertIn("unverifiable", detail)

    def test_non_taiwan_max_age_behavior_is_unchanged(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # A non-Taiwan symbol with the same multi-day gap still uses the
        # flat max_age_seconds rule, not the Taiwan session-aware rule.
        snapshot = {
            "snapshot_id": "snap-us-stale",
            "symbol": "AAPL.US",
            "event_time": "2026-08-28T05:30:00Z",
            "observed_at": "2026-08-29T11:00:00Z",
            "source_ref": "source-ref-1",
            "lineage": {"source": "manual"},
            "closes": [150.0, 151.0],
        }
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-08-29T12:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_stale")


if __name__ == "__main__":
    unittest.main()
