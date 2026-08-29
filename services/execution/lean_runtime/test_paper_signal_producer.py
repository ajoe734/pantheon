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


if __name__ == "__main__":
    unittest.main()
