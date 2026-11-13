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
    expression_startup_report,
    healthcheck,
    main,
)
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.signal_consumer import SignalConsumer
from services.trade_journey.correlation_envelope import validate_envelope

_NOW = "2026-06-14T10:30:00Z"
_BINDING = BindingRef(binding_id="rb-test-001", strategy_id="strategy-test-001", symbol="AAPL.US")


def test_screen_production_path_rejects_partial_market_without_queueing():
    from services.research.tests.test_candidate_pool_parity import paper_binding
    from services.registry.test_candidate_pool import recorded_market, screen_artifact
    from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy

    binding = paper_binding(screen_artifact(), recorded_market(), "2026-09-02")
    binding["market_inputs"].pop("2317.TW")
    store = InMemoryPendingSignalStore()
    producer = PaperSignalProducer(store_for=lambda _: store, strategy=CurrentArtifactStrategy())
    assert producer.produce(binding, "2026-09-02T12:00:00Z") == 0
    assert store.queue_depth() == 0
    assert "complete governed market" in producer.degraded_bindings[binding["binding_id"]]


def test_pool_removal_expression_must_close_a_held_instrument():
    from services.registry.test_candidate_pool import recorded_market, screen_artifact
    from services.registry.test_strategy_artifact import _artifact_with_expression_policy
    from services.research.tests.test_candidate_pool_parity import paper_binding
    from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy

    artifact = screen_artifact()
    policy_artifact = _artifact_with_expression_policy()
    artifact["expression_policy"] = policy_artifact["expression_policy"]
    artifact["mutation_surface"]["controls"].extend(
        c for c in policy_artifact["mutation_surface"]["controls"]
        if c["parameter_key"] in {"lever_min_strength", "notional_fraction_at_full_strength"}
    )
    market = recorded_market()
    binding = paper_binding(artifact, market, "2026-09-02",
                            {"2330.TW": {"current_direction": "LONG", "holding_bars": 1}})
    budget = {"available_cash": 100000.0, "gross_limit": 200000.0, "gross_used": 0.0}

    def decide(book):
        strategy = CurrentArtifactStrategy(expression_layer=True,
            candidates_builder=_expression_candidates(),
            expression_inputs=lambda _: {"persona_book": book, "risk_budget": budget})
        outputs = strategy(binding, "2026-09-02T12:00:00Z")["portfolio_decisions"]
        [removed] = [o for o in outputs if o["symbol"] == "2330.TW"]
        return removed["expression"]["evaluate"]()["decision"]

    empty = decide({})
    assert empty["reason_code"] == "candidates_unavailable"
    held = decide({"positions": [{"underlying": "2330", "instrument_id": "eq:2330",
                                  "direction": "LONG", "units": 10}]})
    assert held["reason_code"] == "close_leg"
    assert held["side"] == "sell" and held["units"] == 10


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


class TestSmokeSignalRuntimeId(unittest.TestCase):
    def test_smoke_strategy_signal_carries_runtime_id_and_is_accepted(self) -> None:
        binding = BindingRef(
            binding_id="rb-rt-001", strategy_id="strategy-rt-001", runtime_id="paper-runtime-001"
        )
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(store_for=lambda b: store, strategy=SmokeStrategy())
        self.assertEqual(producer.produce(binding, _NOW), 1)
        [sig] = store.get_pending()
        self.assertEqual(sig["runtime_id"], "paper-runtime-001")
        validated = SignalConsumer(store_client=MagicMock())._validate(sig)
        self.assertIsNotNone(validated)
        self.assertEqual(validated["runtime_id"], "paper-runtime-001")


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
                "event_time": "2026-06-12T05:30:00Z",
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
    def test_tw_admitted_snapshot_close_is_carried_as_market_data(
        self,
        mock_urlopen,
    ) -> None:
        from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy
        from services.execution.lean_runtime.test_current_artifact_signal import _artifact, _binding

        artifact = _artifact()
        artifact["parameters"]["symbols"] = ["2330.TW"]
        binding = _binding(
            artifact,
            binding_id="rb-source-ingest-tw-market-data",
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
                "event_time": "2026-06-12T05:30:00Z",
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
        self.assertEqual(
            signal["metadata"]["market_data"],
            {
                "close": 955.0,
                "event_time": "2026-06-12T05:30:00Z",
                "source_ref": "source-ingest://snapshots/mss-official-twse-2330",
            },
        )

        smoke = build_smoke_signal(_BINDING, _NOW)
        self.assertNotIn("market_data", smoke["metadata"])
        bounded_store = InMemoryPendingSignalStore()
        PaperSignalProducer(
            store_for=lambda _: bounded_store, strategy=BoundedPaperStrategy()
        ).produce(
            {**binding, "symbol": "2330.TW", "market_data_policy": None}, _NOW
        )
        [bounded] = bounded_store.get_pending()
        self.assertNotIn("market_data", bounded["metadata"])

    def test_source_projection_holiday_snapshot_produces_healthy_tick(self) -> None:
        """Real adapter records survive Source storage and produce a healthy tick."""
        from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy
        from services.execution.lean_runtime.test_current_artifact_signal import _artifact, _binding
        from services.source_ingestion.connectors.taiwan_official import (
            TaiwanOfficialMarketDatasetAdapter,
        )
        from services.source_ingestion.requirement_state import LatestMarketSnapshotStore

        records = TaiwanOfficialMarketDatasetAdapter(max_records=10).records_from_payload(
            "tw_price_daily",
            "TWSE",
            [
                {"Date": "1150210", "Code": "2330", "ClosingPrice": "950.00"},
                {"Date": "1150211", "Code": "2330", "ClosingPrice": "955.00"},
            ],
            trace_id="trace-producer-governed-calendar",
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_store = LatestMarketSnapshotStore(Path(tmp_dir) / "snapshots.jsonl")
            snapshot_store.append_normalized_records(
                records,
                ingest_run_id="ingest-twse-lny-calendar",
                observed_at="2026-02-23T02:00:00Z",
            )
            source_snapshot = snapshot_store.get("2330.TWSE").to_public_dict()

        artifact = _artifact()
        artifact["parameters"]["symbols"] = ["2330.TWSE"]
        binding = _binding(
            artifact,
            binding_id="rb-source-projection-holiday",
            include_market_input=False,
        )
        binding["symbol"] = "2330.TWSE"
        binding["market_data_policy"] = {
            "owner": "source-ingest",
            "contract": "latest_stored_normalized",
            "max_age_seconds": 86400,
            "minimum_closes": 2,
        }
        binding["market_input"] = source_snapshot

        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(
            store_for=lambda _: store,
            strategy=CurrentArtifactStrategy(),
        )
        self.assertEqual(producer.tick([binding], "2026-02-23T03:00:00Z"), {
            "rb-source-projection-holiday": 1,
        })
        self.assertEqual(store.queue_depth(), 1)
        self.assertEqual(producer.degraded_bindings, {})

    def test_same_day_tw_close_is_rejected_before_own_session_completes(self) -> None:
        from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy
        from services.execution.lean_runtime.test_current_artifact_signal import _artifact, _binding

        artifact = _artifact()
        artifact["parameters"]["symbols"] = ["2330.TWSE"]
        binding = _binding(
            artifact,
            binding_id="rb-tw-pre-close",
            include_market_input=False,
        )
        binding["symbol"] = "2330.TWSE"
        binding["market_data_policy"] = {
            "owner": "source-ingest",
            "contract": "latest_stored_normalized",
            "max_age_seconds": 86400,
            "minimum_closes": 2,
        }
        binding["market_input"] = TestSharedSnapshotAdmissionDecisions._tw_snapshot(
            "2026-08-31T00:00:00Z",
            "2026-08-31T01:00:00Z",
        )
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(
            store_for=lambda _: store,
            strategy=CurrentArtifactStrategy(),
        )

        self.assertEqual(producer.tick([binding], "2026-08-31T01:00:00Z"), {
            "rb-tw-pre-close": 0,
        })
        self.assertEqual(store.queue_depth(), 0)
        self.assertIn("market_input_invalid", producer.degraded_bindings["rb-tw-pre-close"])
        self.assertIn("has not completed", producer.degraded_bindings["rb-tw-pre-close"])

    def test_same_day_tw_close_rejects_receipt_from_before_session_close(self) -> None:
        from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy
        from services.execution.lean_runtime.test_current_artifact_signal import _artifact, _binding

        artifact = _artifact()
        artifact["parameters"]["symbols"] = ["2330.TWSE"]
        binding = _binding(
            artifact,
            binding_id="rb-tw-premature-receipt",
            include_market_input=False,
        )
        binding["symbol"] = "2330.TWSE"
        binding["market_data_policy"] = {
            "owner": "source-ingest",
            "contract": "latest_stored_normalized",
            "max_age_seconds": 86400,
            "minimum_closes": 2,
        }
        binding["market_input"] = TestSharedSnapshotAdmissionDecisions._tw_snapshot(
            "2026-08-31T00:00:00Z",
            "2026-08-31T01:00:00Z",
        )
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(
            store_for=lambda _: store,
            strategy=CurrentArtifactStrategy(),
        )

        self.assertEqual(producer.tick([binding], "2026-08-31T06:00:00Z"), {
            "rb-tw-premature-receipt": 0,
        })
        self.assertEqual(store.queue_depth(), 0)
        detail = producer.degraded_bindings["rb-tw-premature-receipt"]
        self.assertIn("market_input_invalid", detail)
        self.assertIn("predates event trade date", detail)

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
        trading_days=("2026-02-11", "2026-02-23"),
        sessions=None,
        coverage_start="2026-02-11",
        coverage_end="2026-02-23",
        source_url="https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=json&queryYear=115",
        authority="Taiwan Stock Exchange 115 年市場開休市日期",
        version="twse-2026-lny-v1",
        checksum="55b2e23b9bd30af666a99c98da2dbbfad568dcd655631b1c6347d12ee8381596",
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
                "2026-02-12": {"name": "市場無交易，僅辦理結算交割作業"},
                "2026-02-13": {"name": "市場無交易，僅辦理結算交割作業"},
                "2026-02-16": {"name": "農曆除夕及春節"},
                "2026-02-17": {"name": "農曆除夕及春節"},
                "2026-02-18": {"name": "農曆除夕及春節"},
                "2026-02-19": {"name": "農曆除夕及春節"},
                "2026-02-20": {"name": "農曆除夕及春節"},
            },
        }
        if trading_days is not None:
            ev["trading_days"] = list(trading_days)
        if sessions is not None:
            ev["sessions"] = sessions
        if extra:
            ev.update(extra)
        return ev

    @staticmethod
    def _pin_calendar_evidence(evidence):
        """Create a caller-owned test pin for a deliberately varied payload."""
        from services.execution.market_snapshot_admission import (
            _canonical_calendar_payload_sha256,
        )

        payload = {
            "authority": evidence["authority"],
            "coverage_end": evidence.get("coverage_end"),
            "coverage_start": evidence.get("coverage_start"),
            "holidays": evidence.get("holidays") or {},
            "market": evidence["market"],
            "source_url": evidence["source_url"],
            "timezone": evidence["timezone"],
            "trading_days": sorted(evidence.get("trading_days") or []),
            "venue": evidence["venue"],
            "version": evidence["version"],
        }
        digest = _canonical_calendar_payload_sha256(payload)
        evidence["checksum"] = digest
        return {evidence["version"]: digest}

    def test_tw_friday_close_admitted_on_saturday(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Friday close evaluated on Saturday: deterministic weekend requires no calendar feed.
        snapshot = self._tw_snapshot("2026-08-28T05:30:00Z", "2026-08-29T11:00:00Z")
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-08-29T12:00:00Z")
        self.assertTrue(dec.admitted)
        self.assertIsNone(dec.reason_code)

    def test_tw_friday_close_admitted_on_monday_morning_with_calendar_evidence(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot
        from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence

        # Friday close with Friday 07:00Z receipt is admitted on Monday 02:00Z (67h old)
        snapshot = self._tw_snapshot("2026-10-16T05:30:00Z", "2026-10-16T07:00:00Z")
        ev = governed_taiwan_calendar_evidence(venue="TWSE", trade_date="2026-10-16")
        snapshot["calendar_evidence"] = ev
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-10-19T02:00:00Z")
        self.assertTrue(dec.admitted)
        self.assertIsNone(dec.reason_code)

        # Same receipt is rejected after Monday close (06:00Z)
        dec_stale = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-10-19T06:00:00Z")
        self.assertFalse(dec_stale.admitted)
        self.assertEqual(dec_stale.reason_code, "market_input_stale_refresh")


    def test_tw_same_day_close_rejected_before_session_completion(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        snapshot = self._tw_snapshot("2026-08-31T00:00:00Z", "2026-08-31T01:00:00Z")
        dec = admit_market_snapshot(
            snapshot,
            max_age_seconds=86400,
            now_iso="2026-08-31T01:00:00Z",
        )
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_invalid")
        self.assertIn("has not completed its 13:30 Asia/Taipei cash session", dec.detail)

    def test_tw_same_day_close_rejects_receipt_before_own_session_close(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        snapshot = self._tw_snapshot("2026-08-31T00:00:00Z", "2026-08-31T01:00:00Z")
        dec = admit_market_snapshot(
            snapshot,
            max_age_seconds=86400,
            now_iso="2026-08-31T06:00:00Z",
        )
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_invalid")
        self.assertIn("predates event trade date 2026-08-31 session close", dec.detail)

    def test_tw_weekend_event_trade_date_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # A fresh receipt and official-looking lineage cannot turn a Saturday
        # into an official Taiwan cash-market close.
        snapshot = self._tw_snapshot("2026-08-29T05:30:00Z", "2026-08-29T05:45:00Z")
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-08-29T06:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_invalid")
        self.assertIn("market weekend", dec.detail)

    def test_tw_governed_holiday_event_trade_date_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # A date explicitly marked closed by governed evidence is never an
        # official close, even if it otherwise has a fresh receipt and lineage.
        snapshot = self._tw_snapshot("2026-02-16T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence()
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_invalid")
        self.assertIn("explicit Taiwan market closure", dec.detail)

    def test_tw_covered_event_date_requires_explicit_trading_record(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        evidence = self._tw_calendar_evidence(trading_days=("2026-02-23",))
        evidence["version"] = "test-event-date-record-v1"
        pins = self._pin_calendar_evidence(evidence)
        snapshot["calendar_evidence"] = evidence
        dec = admit_market_snapshot(
            snapshot,
            max_age_seconds=86400,
            now_iso="2026-02-23T03:00:00Z",
            trusted_calendar_pins=pins,
        )
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("event date 2026-02-11", dec.detail)

    def test_tw_holiday_span_admitted_with_calendar_evidence(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Wednesday 2026-02-11 was the official final close.  Evaluated on
        # 2026-02-23 before reopening close, official 2/12..2/20 closures
        # fully explain the gap to Monday 2026-02-23 before market close.
        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence()
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertTrue(dec.admitted)
        self.assertIsNone(dec.reason_code)

    def test_tw_calendar_evidence_missing_weekday_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Evidence covers 2026-02-16..18 but is missing 2026-02-19 and 2026-02-20.
        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        evidence = self._tw_calendar_evidence(
            holidays={
                "2026-02-12": {"name": "closed"},
                "2026-02-13": {"name": "closed"},
                "2026-02-16": {"name": "LNY 1"},
                "2026-02-17": {"name": "LNY 2"},
                "2026-02-18": {"name": "LNY 3"},
            },
            coverage_start=None,
            coverage_end=None,
        )
        evidence["version"] = "test-missing-weekday-v1"
        pins = self._pin_calendar_evidence(evidence)
        snapshot["calendar_evidence"] = evidence
        dec = admit_market_snapshot(
            snapshot,
            max_age_seconds=86400,
            now_iso="2026-02-23T03:00:00Z",
            trusted_calendar_pins=pins,
        )
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("missing explicit session record for weekday", dec.detail)

    def test_tw_calendar_evidence_no_coverage_range_inference_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 1: coverage_start/end must NOT infer that unlisted weekdays are trading.
        # An unlisted completed weekday must fail closed as market_input_calendar_unverifiable.
        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        evidence = self._tw_calendar_evidence(
            holidays={
                "2026-02-12": {"name": "closed"},
                "2026-02-13": {"name": "closed"},
                "2026-02-16": {"name": "LNY 1"},
                "2026-02-17": {"name": "LNY 2"},
                "2026-02-18": {"name": "LNY 3"},
                # 2026-02-19 and 2026-02-20 are missing from holidays and trading_days
            },
            coverage_start="2026-02-01",
            coverage_end="2026-02-28",
        )
        evidence["version"] = "test-no-range-inference-v1"
        pins = self._pin_calendar_evidence(evidence)
        snapshot["calendar_evidence"] = evidence
        dec = admit_market_snapshot(
            snapshot,
            max_age_seconds=86400,
            now_iso="2026-02-23T03:00:00Z",
            trusted_calendar_pins=pins,
        )
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("missing explicit session record for weekday 2026-02-19", dec.detail)

    def test_tw_calendar_evidence_incomplete_range_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Evidence range starts on 2026-02-17, missing 2026-02-16.
        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        evidence = self._tw_calendar_evidence(
            coverage_start="2026-02-17",
            coverage_end="2026-02-28",
        )
        evidence["version"] = "test-incomplete-range-v1"
        pins = self._pin_calendar_evidence(evidence)
        snapshot["calendar_evidence"] = evidence
        dec = admit_market_snapshot(
            snapshot,
            max_age_seconds=86400,
            now_iso="2026-02-23T03:00:00Z",
            trusted_calendar_pins=pins,
        )
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
        evidence = self._tw_calendar_evidence(
            holidays={},
            trading_days=["2026-08-31"],
            coverage_start="2026-08-31",
            coverage_end="2026-08-31",
        )
        evidence["version"] = "test-explicit-session-v1"
        pins = self._pin_calendar_evidence(evidence)
        snapshot["calendar_evidence"] = evidence
        dec = admit_market_snapshot(
            snapshot,
            max_age_seconds=86400,
            now_iso="2026-08-31T06:00:00Z",
            trusted_calendar_pins=pins,
        )
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

    def test_tw_calendar_evidence_requires_one_explicit_cash_venue(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Venue cannot fall back from market and cannot combine two venues.
        for bad_venue in ("TAIFEX", "TW", "TWSE/TPEX", "HKEX", "", None):
            with self.subTest(venue=bad_venue):
                snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
                snapshot["calendar_evidence"] = self._tw_calendar_evidence(venue=bad_venue)
                dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
                self.assertFalse(dec.admitted)
                self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
                if bad_venue:
                    self.assertIn("not an official Taiwan cash venue", dec.detail)
                else:
                    self.assertIn("missing required explicit venue", dec.detail)

    def test_tw_calendar_evidence_utc_timezone_alias_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 4: UTC aliases like UTC+8, +08:00, UTC must be rejected; exact Asia/Taipei required.
        for bad_tz in ("UTC+8", "+08:00", "UTC+08:00", "UTC", "Asia/Tokyo"):
            snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
            snapshot["calendar_evidence"] = self._tw_calendar_evidence(timezone=bad_tz)
            dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
            self.assertFalse(dec.admitted)
            self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
            self.assertIn("expected exact 'Asia/Taipei'", dec.detail)

    def test_tw_calendar_evidence_http_scheme_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 5: HTTP scheme is rejected; HTTPS is required.
        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
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
            snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
            snapshot["calendar_evidence"] = self._tw_calendar_evidence(checksum=bad_cs)
            dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
            self.assertFalse(dec.admitted)
            self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
            self.assertIn("checksum", dec.detail)

    def test_tw_calendar_evidence_self_asserted_checksum_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 5: Self-asserted checksums cannot bypass the external trusted pin.
        untrusted_sha = "1111111111111111111111111111111111111111111111111111111111111111"
        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            checksum=untrusted_sha,
            extra={"expected_checksum": untrusted_sha, "expected_sha256": untrusted_sha},
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("does not match", dec.detail)

    def test_tw_calendar_evidence_tamper_with_unchanged_checksum_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        evidence = self._tw_calendar_evidence()
        evidence["holidays"]["2026-02-20"]["name"] = "tampered but checksum retained"
        snapshot["calendar_evidence"] = evidence

        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")

        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("does not match canonical payload sha256", dec.detail)

    def test_tw_calendar_evidence_empty_payload_pin_rejected(self) -> None:
        from services.execution.market_snapshot_admission import EMPTY_SHA256, admit_market_snapshot

        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        evidence = self._tw_calendar_evidence(version="test-empty-pin-v1")
        self._pin_calendar_evidence(evidence)
        snapshot["calendar_evidence"] = evidence

        dec = admit_market_snapshot(
            snapshot,
            max_age_seconds=86400,
            now_iso="2026-02-23T03:00:00Z",
            trusted_calendar_pins={"test-empty-pin-v1": EMPTY_SHA256},
        )

        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("forbidden empty-payload SHA-256", dec.detail)

    def test_tw_calendar_evidence_actual_empty_content_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # A non-empty SHA-256 pin is not sufficient: a calendar whose actual
        # session content is empty must still fail closed.  Keep `sessions`
        # absent so this covers exactly the empty holidays/trading-days shape
        # that must not be treated as calendar evidence.
        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        evidence = self._tw_calendar_evidence(
            holidays={},
            trading_days=[],
            sessions=None,
            version="test-empty-calendar-content-v1",
        )
        self.assertNotIn("sessions", evidence)
        pins = self._pin_calendar_evidence(evidence)
        snapshot["calendar_evidence"] = evidence

        dec = admit_market_snapshot(
            snapshot,
            max_age_seconds=86400,
            now_iso="2026-02-23T03:00:00Z",
            trusted_calendar_pins=pins,
        )

        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("no explicit session, holiday, or trading-day records", dec.detail)

    def test_tw_calendar_evidence_requires_exact_governed_version(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        missing = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        missing["calendar_evidence"] = self._tw_calendar_evidence(version="")
        missing_dec = admit_market_snapshot(missing, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(missing_dec.admitted)
        self.assertIn("missing required governed version", missing_dec.detail)

        unknown = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        evidence = self._tw_calendar_evidence(version="unknown-calendar-v1")
        self._pin_calendar_evidence(evidence)
        unknown["calendar_evidence"] = evidence
        unknown_dec = admit_market_snapshot(unknown, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(unknown_dec.admitted)
        self.assertIn("is not governed by a trusted pin", unknown_dec.detail)

    def test_tw_calendar_evidence_any_future_fetched_at_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            fetched_at="2026-02-23T03:00:01Z",
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("fetched_at '2026-02-23T03:00:01Z' is in the future", dec.detail)

    def test_tw_calendar_evidence_non_iso_dates_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        # Defect 6: Strict ISO date validation for coverage, holiday, session, and trading dates.
        # Case A: non-ISO coverage_start
        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(coverage_start="2026/01/01")
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("invalid coverage_start", dec.detail)

        # Case B: non-ISO holiday date key
        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            holidays={"2026-2-16": {"name": "LNY"}},
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("invalid holiday date", dec.detail)

        # Case C: non-ISO session date
        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            sessions={"02/16/2026": {"type": "holiday"}},
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("invalid session date", dec.detail)

        # Case D: non-ISO trading_days
        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
        snapshot["calendar_evidence"] = self._tw_calendar_evidence(
            trading_days=["2026.02.16"],
        )
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-02-23T03:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_calendar_unverifiable")
        self.assertIn("invalid trading date", dec.detail)

    def test_tw_calendar_evidence_unofficial_url_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        snapshot = self._tw_snapshot("2026-02-11T05:30:00Z", "2026-02-23T02:00:00Z")
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

    def test_tw_receipt_older_than_14_days_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot
        from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence

        snapshot = self._tw_snapshot("2026-10-01T05:30:00Z", "2026-10-01T07:00:00Z")
        snapshot["calendar_evidence"] = governed_taiwan_calendar_evidence(venue="TWSE", trade_date="2026-10-01")
        # 15 days later (2026-10-16T02:00:00Z)
        dec = admit_market_snapshot(snapshot, max_age_seconds=999_999_999, now_iso="2026-10-16T02:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_stale_refresh")
        self.assertIn("14 days", str(dec.detail))


    def test_tw_future_event_time_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        snapshot = self._tw_snapshot("2026-08-29T12:00:01Z", "2026-08-29T12:00:00Z")
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-08-29T12:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_invalid")
        self.assertIn("future", dec.detail)

    def test_tw_future_observed_at_by_one_second_rejected(self) -> None:
        from services.execution.market_snapshot_admission import admit_market_snapshot

        snapshot = self._tw_snapshot("2026-08-28T05:30:00Z", "2026-08-29T12:00:01Z")
        dec = admit_market_snapshot(snapshot, max_age_seconds=86400, now_iso="2026-08-29T12:00:00Z")
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.reason_code, "market_input_invalid")
        self.assertIn("observed_at is in the future", dec.detail)

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


class TestExecutableSymbolContract(unittest.TestCase):
    """Direct regressions for the executable paper symbol contract."""

    def _make_fixture(
        self,
        *,
        symbol: str = "SPY",
        artifact_symbols: list[str] | None = None,
        binding_market: str | None = None,
        binding_venue: str | None = None,
        artifact_market: str | None = None,
        artifact_data_source: str | None = None,
        binding_version: str | None = None,
        market_input_symbol: str | None = None,
        binding_id: str = "rb-contract-test",
    ) -> tuple[dict[str, Any], InMemoryPendingSignalStore, PaperSignalProducer]:
        from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy
        from services.execution.lean_runtime.test_current_artifact_signal import _artifact, _binding

        art = _artifact()
        art["parameters"]["symbols"] = artifact_symbols if artifact_symbols is not None else [symbol]
        if artifact_data_source is not None:
            art["parameters"]["data_source"] = artifact_data_source
            art["lineage"]["source_dataset_refs"] = [artifact_data_source]
        else:
            art["parameters"]["data_source"] = "source-ingest:records/unspecified"
            art["lineage"]["source_dataset_refs"] = ["source-ingest:records/unspecified"]
        if artifact_market is not None:
            art["parameters"]["market"] = artifact_market
            art["mutation_surface"]["immutable_parameters"].append("market")

        b = _binding(art, binding_id=binding_id, include_market_input=False)
        b["symbol"] = symbol
        if binding_market is not None:
            b["market"] = binding_market
        if binding_venue is not None:
            b["venue"] = binding_venue
        if binding_version is not None:
            b["artifact_version"] = binding_version

        mi_sym = market_input_symbol if market_input_symbol is not None else symbol
        b["market_input"] = {
            "symbol": mi_sym,
            "closes": [100.0, 105.0],
            "source_ref": f"source-ingest://snapshots/{binding_id}",
            "observed_at": _NOW,
        }

        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(
            store_for=lambda _: store,
            strategy=CurrentArtifactStrategy(),
        )
        return b, store, producer

    def test_bare_us_ticker_with_explicit_context_produces_executable_us_symbol(self) -> None:
        # Bare SPY with binding.market="US"
        b, store, producer = self._make_fixture(symbol="SPY", binding_market="US")
        self.assertEqual(producer.produce(b, _NOW), 1)
        [sig] = store.get_pending()
        self.assertEqual(sig["symbol"], "SPY.US")
        self.assertEqual(sig["metadata"]["raw_symbol"], "SPY")

        # Bare SPY with binding.venue="ARCA"
        b2, store2, producer2 = self._make_fixture(symbol="SPY", binding_venue="ARCA", binding_id="rb-arca")
        self.assertEqual(producer2.produce(b2, _NOW), 1)
        [sig2] = store2.get_pending()
        self.assertEqual(sig2["symbol"], "SPY.ARCA")

        # Bare SPY with artifact.parameters.market="US"
        b3, store3, producer3 = self._make_fixture(
            symbol="SPY",
            artifact_market="US",
            binding_id="rb-art-market",
        )
        self.assertEqual(producer3.produce(b3, _NOW), 1)
        [sig3] = store3.get_pending()
        self.assertEqual(sig3["symbol"], "SPY.US")

    def test_counterexample_1_approved_foreign_suffix_membership_rejected(self) -> None:
        # Counterexample 1: approved [SPY.TW] plus requested SPY/binding US -> market_input_invalid
        b, store, producer = self._make_fixture(
            symbol="SPY",
            artifact_symbols=["SPY.TW"],
            binding_market="US",
            market_input_symbol="SPY",
            binding_id="rb-counterexample-1",
        )
        self.assertEqual(producer.produce(b, _NOW), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertEqual(
            producer.degraded_bindings[b["binding_id"]].split(":")[0],
            "market_input_invalid",
        )

    def test_counterexample_2_dataset_hint_without_explicit_market_fails_closed(self) -> None:
        # Counterexample 2: No explicit market, binding data_source='dataset://not-us-price/unverified', raw SPY -> market_context_missing
        b, store, producer = self._make_fixture(
            symbol="SPY",
            artifact_symbols=["SPY"],
            binding_id="rb-counterexample-2",
        )
        b["data_source"] = "dataset://not-us-price/unverified"
        self.assertEqual(producer.produce(b, _NOW), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertEqual(
            producer.degraded_bindings[b["binding_id"]].split(":")[0],
            "market_context_missing",
        )

    def test_counterexample_3_unsupported_explicit_market_fails_closed(self) -> None:
        # Counterexample 3: Explicit market='not-us-price', raw/approved SPY -> market_context_unsupported
        b, store, producer = self._make_fixture(
            symbol="SPY",
            artifact_symbols=["SPY"],
            binding_market="not-us-price",
            binding_id="rb-counterexample-3",
        )
        self.assertEqual(producer.produce(b, _NOW), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertEqual(
            producer.degraded_bindings[b["binding_id"]].split(":")[0],
            "market_context_unsupported",
        )

    def test_bare_ticker_missing_market_context_fails_closed(self) -> None:
        # Bare SPY with NO market context anywhere -> fail closed with market_context_missing
        b, store, producer = self._make_fixture(symbol="SPY")
        self.assertEqual(producer.produce(b, _NOW), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertEqual(
            producer.degraded_bindings[b["binding_id"]].split(":")[0],
            "market_context_missing",
        )

    def test_contradictory_market_context_fails_closed(self) -> None:
        # binding says US, artifact says TW
        b, store, producer = self._make_fixture(symbol="SPY", binding_market="US", artifact_market="TW")
        self.assertEqual(producer.produce(b, _NOW), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertEqual(
            producer.degraded_bindings[b["binding_id"]].split(":")[0],
            "market_context_contradictory",
        )

        # binding says market="US", venue="TWSE"
        b2, store2, producer2 = self._make_fixture(
            symbol="SPY",
            binding_market="US",
            binding_venue="TWSE",
            binding_id="rb-contra-2",
        )
        self.assertEqual(producer2.produce(b2, _NOW), 0)
        self.assertEqual(
            producer2.degraded_bindings[b2["binding_id"]].split(":")[0],
            "market_context_contradictory",
        )

    def test_symbol_intrinsic_market_mismatch_fails_closed(self) -> None:
        # AAPL.US has intrinsic "US", but binding says market="TW"
        b, store, producer = self._make_fixture(symbol="AAPL.US", binding_market="TW")
        self.assertEqual(producer.produce(b, _NOW), 0)
        self.assertEqual(
            producer.degraded_bindings[b["binding_id"]].split(":")[0],
            "market_context_mismatch",
        )

        # 2330.TW has intrinsic "TW", but binding says market="US"
        b2, store2, producer2 = self._make_fixture(symbol="2330.TW", binding_market="US", binding_id="rb-tw-us")
        self.assertEqual(producer2.produce(b2, _NOW), 0)
        self.assertEqual(
            producer2.degraded_bindings[b2["binding_id"]].split(":")[0],
            "market_context_mismatch",
        )

    def test_canonical_us_symbol_preserved(self) -> None:
        for sym in ("AAPL.US", "MSFT.NASDAQ"):
            b, store, producer = self._make_fixture(symbol=sym)
            self.assertEqual(producer.produce(b, _NOW), 1)
            [sig] = store.get_pending()
            self.assertEqual(sig["symbol"], sym)

    def test_canonical_tw_symbols_preserved(self) -> None:
        for sym in ("2330.TW", "2330.TWSE", "6488.TWO", "TXF202604.TAIFEX"):
            b, store, producer = self._make_fixture(symbol=sym, binding_id=f"rb-{sym}")
            self.assertEqual(producer.produce(b, _NOW), 1)
            [sig] = store.get_pending()
            self.assertEqual(sig["symbol"], sym)

    def test_bare_tw_ticker_with_explicit_context(self) -> None:
        # Bare 2330 with market="TW" -> 2330.TW
        b, store, producer = self._make_fixture(symbol="2330", binding_market="TW")
        self.assertEqual(producer.produce(b, _NOW), 1)
        [sig] = store.get_pending()
        self.assertEqual(sig["symbol"], "2330.TW")

        # Bare 2330 with venue="TWSE" -> 2330.TWSE
        b2, store2, producer2 = self._make_fixture(symbol="2330", binding_venue="TWSE", binding_id="rb-twse")
        self.assertEqual(producer2.produce(b2, _NOW), 1)
        [sig2] = store2.get_pending()
        self.assertEqual(sig2["symbol"], "2330.TWSE")

        # Bare 6488 with venue="TPEX" -> 6488.TPEX
        b3, store3, producer3 = self._make_fixture(symbol="6488", binding_venue="TPEX", binding_id="rb-tpex")
        self.assertEqual(producer3.produce(b3, _NOW), 1)
        [sig3] = store3.get_pending()
        self.assertEqual(sig3["symbol"], "6488.TPEX")

    def test_crypto_pairs_produce_executable_symbols(self) -> None:
        for sym in ("BTCUSDT", "BTCUSD.KRAKEN"):
            b, store, producer = self._make_fixture(symbol=sym, binding_id=f"rb-{sym}")
            self.assertEqual(producer.produce(b, _NOW), 1)
            [sig] = store.get_pending()
            self.assertEqual(sig["symbol"], sym)

        # BTC/USD with market="CRYPTO"
        b, store, producer = self._make_fixture(
            symbol="BTC/USD",
            artifact_symbols=["BTC/USD"],
            binding_market="CRYPTO",
            market_input_symbol="BTC/USD",
            binding_id="rb-crypto-slash",
        )
        self.assertEqual(producer.produce(b, _NOW), 1)
        [sig] = store.get_pending()
        self.assertEqual(sig["symbol"], "BTCUSD")

    def test_raw_symbol_membership_validation(self) -> None:
        # artifact symbols has ["SPY"], binding requests "MSFT" -> market_input_invalid
        b, store, producer = self._make_fixture(
            symbol="MSFT",
            artifact_symbols=["SPY"],
            binding_market="US",
        )
        self.assertEqual(producer.produce(b, _NOW), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertEqual(
            producer.degraded_bindings[b["binding_id"]].split(":")[0],
            "market_input_invalid",
        )

    def test_wrong_artifact_version_fails_closed(self) -> None:
        import copy
        from services.execution.artifact_loader import ArtifactLoader
        b, store, producer = self._make_fixture(
            symbol="SPY",
            binding_market="US",
        )
        # Store artifact payload under 2.0.0 key in object_store so ArtifactLoader loads it,
        # but with payload StrategyArtifact version="1.0.0" so identity validation fails.
        proj_2 = ArtifactLoader.build_projection("tw_session_momentum", "2.0.0")
        proj_1 = ArtifactLoader.build_projection("tw_session_momentum", "1.0.0")
        meta_2 = copy.deepcopy(b["object_store"][proj_1.metadata_key])
        meta_2["version"] = "2.0.0"
        b["object_store"][proj_2.metadata_key] = meta_2
        b["object_store"][proj_2.artifact_key] = b["object_store"][proj_1.artifact_key]
        b["artifact_version"] = "2.0.0"

        self.assertEqual(producer.produce(b, _NOW), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertEqual(
            producer.degraded_bindings[b["binding_id"]].split(":")[0],
            "artifact_identity_mismatch",
        )

    def test_direct_executor_symbol_parser_verification(self) -> None:
        from services.execution.lean_runtime.symbol_parser import (
            SymbolParseError,
            is_executable_symbol,
            is_taiwan_venue_symbol,
            parse,
            validate_executable_symbol,
        )

        parsed_us = parse("SPY.US")
        self.assertEqual(parsed_us.ticker, "SPY")
        self.assertEqual(parsed_us.lean_market, "Market.USA")
        self.assertEqual(parsed_us.lean_security_type, "SecurityType.Equity")

        parsed_arca = parse("SPY.ARCA")
        self.assertEqual(parsed_arca.ticker, "SPY")
        self.assertEqual(parsed_arca.lean_market, "Market.USA")

        parsed_crypto = parse("BTCUSDT")
        self.assertEqual(parsed_crypto.ticker, "BTCUSDT")
        self.assertEqual(parsed_crypto.lean_market, "Market.Kraken")

        with self.assertRaises(SymbolParseError):
            parse("SPY")

        self.assertTrue(is_taiwan_venue_symbol("2330.TW"))
        self.assertTrue(is_taiwan_venue_symbol("2330.TWSE"))
        self.assertTrue(is_taiwan_venue_symbol("6488.TWO"))
        self.assertTrue(is_taiwan_venue_symbol("6488.TPEX"))
        self.assertTrue(is_taiwan_venue_symbol("TXF202604.TAIFEX"))
        self.assertFalse(is_taiwan_venue_symbol("SPY.US"))
        self.assertFalse(is_taiwan_venue_symbol("2330"))

        validate_executable_symbol("SPY.US")
        validate_executable_symbol("2330.TW")
        validate_executable_symbol("2330.TWSE")
        validate_executable_symbol("BTCUSDT")
        with self.assertRaises(SymbolParseError):
            validate_executable_symbol("SPY")

        self.assertTrue(is_executable_symbol("SPY.US"))
        self.assertTrue(is_executable_symbol("2330.TW"))
        self.assertTrue(is_executable_symbol("2330.TWSE"))
        self.assertTrue(is_executable_symbol("BTCUSDT"))
        self.assertFalse(is_executable_symbol("SPY"))

    def test_unexecutable_market_symbol_fails_closed(self) -> None:
        b, store, producer = self._make_fixture(
            symbol="SPY.INVALID",
            artifact_symbols=["SPY.INVALID"],
            binding_market="US",
        )
        self.assertEqual(producer.produce(b, _NOW), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertEqual(
            producer.degraded_bindings[b["binding_id"]].split(":")[0],
            "market_symbol_unexecutable",
        )


if __name__ == "__main__":
    unittest.main()


class TestDailyBarDecisionIdentity(unittest.TestCase):
    """A daily-bar artifact is one decision per admitted bar, not one per poll."""

    BAR = "2026-06-12T00:00:00+08:00"

    def _binding(self, *, event_time: str | None = None, threshold: float = 0.0, closes=(100.0, 110.0)):
        from services.execution.lean_runtime.test_current_artifact_signal import _artifact, _binding

        art = _artifact()
        art["parameters"]["momentum_threshold"] = threshold
        art["mutation_surface"]["controls"][1]["current_value"] = threshold
        binding = _binding(art, binding_id="rb-daily", include_market_input=False)
        binding["market_input"] = {
            "symbol": "AAPL.US",
            "closes": list(closes),
            "source_ref": "source-ingest://normalized/us-price/AAPL",
            "observed_at": _NOW,
            "event_time": event_time or self.BAR,
        }
        return binding

    def _producer(self, store):
        from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy

        return PaperSignalProducer(store_for=lambda _: store, strategy=CurrentArtifactStrategy())

    @staticmethod
    def _complete_all(store) -> list[str]:
        """Drive the original claim/fence/processed APIs exactly as the consumer does."""
        done = []
        for signal in store.get_pending():
            fence = store.begin_execution(signal)
            assert fence.status == "acquired", fence.status
            assert store.complete_execution(signal["signal_id"], fence.token)
            store.ack(signal)
            done.append(signal["signal_id"])
        return done

    def test_repeated_minute_polls_of_one_bar_execute_once(self) -> None:
        store = InMemoryPendingSignalStore()
        producer = self._producer(store)
        binding = self._binding()
        self.assertEqual(producer.produce(binding, "2026-06-12T10:30:00Z"), 1)
        executed = self._complete_all(store)
        for minute in range(31, 36):
            self.assertEqual(producer.produce(binding, f"2026-06-12T10:{minute}:00Z"), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertEqual(len(executed), 1)

    def test_decision_identity_is_independent_of_poll_time_and_seq(self) -> None:
        first_store, second_store = InMemoryPendingSignalStore(), InMemoryPendingSignalStore()
        binding = self._binding()
        self._producer(first_store).produce(binding, "2026-06-12T10:30:00Z")
        self._producer(second_store).produce(binding, "2026-06-12T11:45:00Z")
        [first] = first_store.get_pending()
        [second] = second_store.get_pending()
        self.assertEqual(first["signal_id"], second["signal_id"])
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["timestamp"], "2026-06-12T10:30:00Z")
        self.assertEqual(second["timestamp"], "2026-06-12T11:45:00Z")
        self.assertEqual(first["metadata"]["market_input_bar"], "2026-06-12")

    def test_producer_restart_does_not_duplicate_a_completed_decision(self) -> None:
        store = InMemoryPendingSignalStore()
        binding = self._binding()
        self.assertEqual(self._producer(store).produce(binding, "2026-06-12T10:30:00Z"), 1)
        self._complete_all(store)
        restarted = self._producer(store)
        self.assertEqual(restarted.produce(binding, "2026-06-12T10:31:00Z"), 0)
        self.assertEqual(store.queue_depth(), 0)

    def test_duplicate_before_completion_is_fenced_by_the_consumer_processed_check(self) -> None:
        store = InMemoryPendingSignalStore()
        producer = self._producer(store)
        binding = self._binding()
        producer.produce(binding, "2026-06-12T10:30:00Z")
        producer.produce(binding, "2026-06-12T10:31:00Z")  # consumer has not run yet
        ids = {signal["signal_id"] for signal in store.get_pending()}
        self.assertEqual(len(ids), 1)
        [signal_id] = ids
        store.mark_processed(signal_id)
        self.assertTrue(SignalConsumer(store_client=store)._is_duplicate({"signal_id": signal_id}))

    def test_new_bar_and_new_model_are_distinct_decisions(self) -> None:
        store = InMemoryPendingSignalStore()
        producer = self._producer(store)
        self.assertEqual(producer.produce(self._binding(), "2026-06-12T10:30:00Z"), 1)
        self._complete_all(store)
        next_bar = self._binding(event_time="2026-06-15T00:00:00+08:00")
        self.assertEqual(producer.produce(next_bar, "2026-06-15T10:30:00Z"), 1)
        new_model = self._binding(threshold=0.01)
        self.assertEqual(producer.produce(new_model, "2026-06-15T10:31:00Z"), 1)
        ids = [signal["signal_id"] for signal in store.get_pending()]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

    def test_inline_input_without_event_time_keys_on_the_close_series(self) -> None:
        store = InMemoryPendingSignalStore()
        producer = self._producer(store)
        binding = self._binding()
        del binding["market_input"]["event_time"]
        self.assertEqual(producer.produce(binding, "2026-06-12T10:30:00Z"), 1)
        self._complete_all(store)
        self.assertEqual(producer.produce(binding, "2026-06-12T10:31:00Z"), 0)
        binding["market_input"]["closes"] = [100.0, 90.0]
        self.assertEqual(producer.produce(binding, "2026-06-12T10:32:00Z"), 1)

    def test_smoke_strategy_keeps_its_per_tick_identity(self) -> None:
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(store_for=lambda _: store, strategy=SmokeStrategy())
        producer.produce(_BINDING, "2026-06-12T10:30:00Z")
        producer.produce(_BINDING, "2026-06-12T10:31:00Z")
        self.assertEqual(len({signal["signal_id"] for signal in store.get_pending()}), 2)

    def test_started_execution_fence_without_processed_marker_suppresses_repeated_ticks(self) -> None:
        from services.execution.lean_runtime.pending_signal_store import RedisPendingSignalStore

        store = InMemoryPendingSignalStore()
        producer = self._producer(store)
        binding = self._binding()
        self.assertEqual(producer.produce(binding, "2026-06-12T16:38:00Z"), 1)
        [signal] = store.get_pending()
        fence = store.begin_execution(signal)
        self.assertEqual(fence.status, "acquired")
        self.assertFalse(store.is_processed(signal["signal_id"]))

        for minute in (39, 40):
            self.assertEqual(producer.produce(binding, f"2026-06-12T16:{minute}:00Z"), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertEqual(
            store._execution_fences[signal["signal_id"]],
            f"started:{fence.token}",
        )

        mock_redis = MagicMock()
        mock_redis.exists.return_value = 0
        mock_redis.get.return_value = "started:worker-1:1"
        redis_store = RedisPendingSignalStore.__new__(RedisPendingSignalStore)
        redis_store._client = mock_redis
        redis_store._queue_key = "pantheon:signals:pending:rb-daily"
        redis_store._processed_prefix = "pantheon:signals:pending:rb-daily:processed:"
        redis_store._execution_prefix = "pantheon:signals:pending:rb-daily:execution:"

        redis_producer = self._producer(redis_store)
        self.assertEqual(redis_producer.produce(binding, "2026-06-12T16:40:00Z"), 0)
        mock_redis.rpush.assert_not_called()
        mock_redis.set.assert_not_called()
        mock_redis.setex.assert_not_called()
        mock_redis.delete.assert_not_called()


def _expression_candidates(*, short_ok: bool = True):
    """Candidate records shaped like instrument_candidates output (0050 fixture figures)."""

    def build(underlying, as_of, book, budget, **kwargs):
        cash = {
            "instrument_id": f"eq:{underlying}",
            "kind": "cash_share",
            "underlying": underlying,
            "side_capability": ["long"],
            "unit": 1,
            "multiplier": 1,
            "price": kwargs["cash_price"],
            "fee_per_unit_per_side": 0.0,
            "tax_rate_per_side": 0.0,
            "max_units_by_risk": 1081,
            "source_refs": [],
        }
        future = {
            "instrument_id": f"ssf:SRF:202611",
            "kind": "stock_future",
            "underlying": underlying,
            "side_capability": ["long", "short"],
            "unit": 1000,
            "multiplier": 1000,
            "price": 92.5,
            "fee_per_unit_per_side": 20.0,
            "tax_rate_per_side": 0.00002,
            "max_units_by_risk": 2 if short_ok else 0,
            "source_refs": [],
        }
        return [cash, future]

    return build


class TestExpressionDecisionStep(unittest.TestCase):
    BAR = "2026-11-06T00:00:00+08:00"
    NOW = "2026-11-09T10:30:00Z"
    INPUTS = {"persona_book": {}, "risk_budget": {"available_cash": 100000.0, "gross_limit": 200000.0, "gross_used": 0.0}}

    def _artifact(self, *, bearish: bool = False, expression: bool = True):
        import copy

        from services.execution.lean_runtime.test_current_artifact_signal import _artifact
        from services.registry.test_strategy_artifact import _artifact_with_expression_policy

        if expression:
            art = copy.deepcopy(_artifact_with_expression_policy())
            base = _artifact(bearish=bearish)
            for key in ("artifact_id", "strategy_id"):
                art[key] = base[key]
            art["strategy_logic"] = base["strategy_logic"]
        else:
            art = _artifact(bearish=bearish)
        art["parameters"]["symbols"] = ["0050.TW"]
        return art

    def _binding(self, art, *, closes=(100.0, 100.3), symbol="0050.TW"):
        from services.execution.lean_runtime.test_current_artifact_signal import _binding

        binding = _binding(art, binding_id="rb-expr", include_market_input=False)
        binding["market_input"] = {
            "symbol": symbol,
            "closes": list(closes),
            "source_ref": "source-ingest://normalized/tw-price/0050",
            "observed_at": self.NOW,
            "event_time": self.BAR,
        }
        return binding

    def _strategy(self, **kwargs):
        from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy

        kwargs.setdefault("expression_layer", True)
        kwargs.setdefault("candidates_builder", _expression_candidates())
        kwargs.setdefault("expression_inputs", lambda _b: self.INPUTS)
        return CurrentArtifactStrategy(**kwargs)

    def _producer(self, store, **kwargs):
        return PaperSignalProducer(store_for=lambda _: store, strategy=self._strategy(**kwargs))

    def test_flag_off_and_legacy_artifact_are_exactly_todays_decision(self) -> None:
        art = self._artifact()
        binding = self._binding(art)
        off = self._strategy(expression_layer=False)(binding, self.NOW)
        on = self._strategy()(binding, self.NOW)
        self.assertNotIn("expression", off)
        self.assertEqual(off["decision_id"], on["decision_id"])
        self.assertEqual(off["signal_id"], on["signal_id"])
        legacy = self._artifact(expression=False)
        legacy_binding = self._binding(legacy)
        legacy_off = self._strategy(expression_layer=False)(legacy_binding, self.NOW)
        legacy_on = self._strategy()(legacy_binding, self.NOW)
        self.assertEqual(legacy_off, legacy_on)
        self.assertNotIn("expression", legacy_on)

    def test_environment_flag_defaults_off(self) -> None:
        from services.execution.lean_runtime.paper_signal_producer import (
            CurrentArtifactStrategy,
            expression_layer_enabled,
        )

        binding = self._binding(self._artifact())
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("PANTHEON_PAPER_EXPRESSION_LAYER", None)
            self.assertFalse(expression_layer_enabled())
            self.assertNotIn("expression", CurrentArtifactStrategy()(binding, self.NOW))
        with patch.dict("os.environ", {"PANTHEON_PAPER_EXPRESSION_LAYER": "on"}):
            self.assertTrue(expression_layer_enabled())

    def test_non_taiwan_symbol_keeps_the_legacy_path_even_when_on(self) -> None:
        art = self._artifact()
        art["parameters"]["symbols"] = ["AAPL.US"]
        binding = self._binding(art, symbol="AAPL.US")
        self.assertNotIn("expression", self._strategy()(binding, self.NOW))

    def test_weak_long_flows_to_the_cash_path_with_the_envelope(self) -> None:
        store = InMemoryPendingSignalStore()
        self.assertEqual(self._producer(store).produce(self._binding(self._artifact()), self.NOW), 1)
        [signal] = store.get_pending()
        meta = signal["metadata"]
        self.assertEqual((signal["action"], signal["direction"], signal["quantity"]), ("BUY", "LONG", 1))
        self.assertEqual(signal["quantity_type"], "SHARES")
        self.assertEqual(meta["instrument_id"], "eq:0050")
        self.assertEqual(meta["kind"], "cash_share")
        self.assertEqual(meta["units"], 1)
        self.assertEqual(meta["multiplier"], 1)
        self.assertEqual(meta["reason_code"], "long_cash")
        self.assertEqual(meta["decision_id"], meta["expression_decision"]["decision_id"])
        self.assertEqual(meta["view_direction"], "LONG")
        self.assertAlmostEqual(meta["strength"], 0.3)
        self.assertIn("momentum", meta)

    def test_short_view_chooses_the_future_and_carries_its_kind(self) -> None:
        store = InMemoryPendingSignalStore()
        art = self._artifact(bearish=True)
        binding = self._binding(art, closes=(100.0, 101.0))
        self.assertEqual(self._producer(store).produce(binding, self.NOW), 1)
        [signal] = store.get_pending()
        meta = signal["metadata"]
        self.assertEqual((signal["action"], signal["direction"]), ("SELL", "SHORT"))
        self.assertEqual(meta["instrument_id"], "ssf:SRF:202611")
        self.assertEqual(meta["kind"], "stock_future")
        self.assertEqual(meta["multiplier"], 1000)
        self.assertEqual(meta["reason_code"], "short_via_future")

    def test_no_trade_is_a_hold_and_a_restart_does_not_retry_the_bar(self) -> None:
        store = InMemoryPendingSignalStore()
        art = self._artifact(bearish=True)
        binding = self._binding(art, closes=(100.0, 101.0))
        producer = self._producer(store, candidates_builder=_expression_candidates(short_ok=False))
        self.assertEqual(producer.produce(binding, self.NOW), 1)
        [signal] = store.get_pending()
        self.assertEqual(signal["action"], "HOLD")
        self.assertEqual(signal["metadata"]["expression_decision"]["reason_code"], "no_feasible_instrument")
        fence = store.begin_execution(signal)
        self.assertTrue(store.complete_execution(signal["signal_id"], fence.token))
        store.ack(signal)
        restarted = self._producer(store)  # a restart that would now find a feasible future
        self.assertEqual(restarted.produce(binding, "2026-11-09T10:31:00Z"), 0)
        self.assertEqual(store.queue_depth(), 0)

    def test_restart_after_persistence_before_fill_reuses_the_stored_decision(self) -> None:
        store = InMemoryPendingSignalStore()
        art = self._artifact(bearish=True)
        binding = self._binding(art, closes=(100.0, 101.0))
        self.assertEqual(self._producer(store).produce(binding, self.NOW), 1)
        [first] = store.get_pending()
        store.ack(first)  # queue claim lost; nothing filled, nothing processed
        self.assertFalse(store.is_processed(first["signal_id"]))
        stored = store.get_decision_record(first["metadata"]["decision_id"])
        self.assertEqual(stored["record"]["instrument_id"], "ssf:SRF:202611")
        # The restarted producer would decide differently (futures now infeasible) ...
        restarted = self._producer(store, candidates_builder=_expression_candidates(short_ok=False))
        self.assertEqual(restarted.produce(binding, "2026-11-09T10:31:00Z"), 1)
        [second] = store.get_pending()
        # ... but it reuses the persisted instrument and units.
        self.assertEqual(second["metadata"]["instrument_id"], "ssf:SRF:202611")
        self.assertEqual(second["metadata"]["units"], first["metadata"]["units"])
        self.assertEqual(second["signal_id"], first["signal_id"])

    def test_decision_is_persisted_before_the_signal_is_enqueued(self) -> None:
        order: list[str] = []

        class Recording(InMemoryPendingSignalStore):
            def put_decision_record(self, decision_id, record):
                order.append("persist")
                return super().put_decision_record(decision_id, record)

            def enqueue(self, payload):
                order.append("enqueue")
                return super().enqueue(payload)

        self._producer(Recording()).produce(self._binding(self._artifact()), self.NOW)
        self.assertEqual(order, ["persist", "enqueue"])

    def test_restart_after_persist_before_enqueue_replays_without_builder_or_decider(self) -> None:
        class CrashOnce(InMemoryPendingSignalStore):
            armed = True

            def enqueue(self, payload):
                if self.armed:
                    self.armed = False
                    raise RuntimeError("crash after persistence")
                return super().enqueue(payload)

        store = CrashOnce()
        binding = self._binding(self._artifact(bearish=True), closes=(100.0, 101.0))
        with self.assertRaises(RuntimeError):
            self._producer(store).produce(binding, self.NOW)
        self.assertEqual(store.queue_depth(), 0)
        calls: list[str] = []

        def forbidden_builder(*a, **k):
            calls.append("builder")
            raise AssertionError("builder must not run on replay")

        def forbidden_decider(*a, **k):
            calls.append("decider")
            raise AssertionError("decider must not run on replay")

        restarted = self._producer(
            store, candidates_builder=forbidden_builder, decider=forbidden_decider
        )
        self.assertEqual(restarted.produce(binding, "2026-11-09T10:31:00Z"), 1)
        self.assertEqual(calls, [])
        [signal] = store.get_pending()
        self.assertEqual(signal["metadata"]["instrument_id"], "ssf:SRF:202611")

    def test_consumed_no_trade_skips_without_builder_or_decider(self) -> None:
        store = InMemoryPendingSignalStore()
        binding = self._binding(self._artifact(bearish=True), closes=(100.0, 101.0))
        producer = self._producer(store, candidates_builder=_expression_candidates(short_ok=False))
        self.assertEqual(producer.produce(binding, self.NOW), 1)
        [signal] = store.get_pending()
        fence = store.begin_execution(signal)
        store.complete_execution(signal["signal_id"], fence.token)
        store.ack(signal)

        def forbidden(*a, **k):
            raise AssertionError("must not run for a consumed bar")

        restarted = self._producer(store, candidates_builder=forbidden, decider=forbidden)
        self.assertEqual(restarted.produce(binding, "2026-11-09T10:31:00Z"), 0)

    def test_forbidden_field_on_no_trade_record_is_rejected(self) -> None:
        for field in ("price", "initial_margin_per_unit"):
            store = InMemoryPendingSignalStore()
            bad = lambda view, c, b, bu, p, _f=field: {  # noqa: E731
                "decision_id": view["decision_id"],
                "reason_code": "flat_view",
                _f: 1,
            }
            binding = self._binding(self._artifact())
            self.assertEqual(self._producer(store, decider=bad).produce(binding, self.NOW), 1)
            [signal] = store.get_pending()
            record = signal["metadata"]["expression_decision"]
            self.assertEqual(record["reason_code"], "instrument_not_in_candidates")
            self.assertNotIn(field, record)
            self.assertEqual(signal["action"], "HOLD")

    def test_replay_input_is_recorded_and_rebuilds_the_decision(self) -> None:
        from services.execution.lean_runtime import expression_replay

        store = InMemoryPendingSignalStore()
        binding = self._binding(self._artifact(bearish=True), closes=(100.0, 101.0))
        self.assertEqual(self._producer(store).produce(binding, self.NOW), 1)
        [signal] = store.get_pending()
        recorded = signal["metadata"]["expression_replay_input"]
        self.assertEqual(recorded["decision_id"], signal["metadata"]["decision_id"])
        self.assertEqual(recorded["budget"], self.INPUTS["risk_budget"])
        self.assertEqual(recorded["book_before"], self.INPUTS["persona_book"])
        self.assertEqual(recorded["view"]["direction"], "SHORT")
        self.assertEqual([c["instrument_id"] for c in recorded["candidates"]], ["eq:0050", "ssf:SRF:202611"])
        self.assertIn("lever_min_strength", recorded["parameters"])

        # Rebuild the day from the recorded event metadata and replay it: the
        # replayed policy outcome equals what the producer decided.
        event = {"event_type": "trade_decision", "metadata": signal["metadata"]}
        records, params = expression_replay.records_from_events(
            [event], {recorded["decision_id"]: {"ssf:SRF:202611": [90.0], "eq:0050": [90.0]}}
        )
        [replayed] = expression_replay.replay(records, params)["decisions"]
        decided = signal["metadata"]["expression_decision"]
        self.assertEqual(replayed["record"]["instrument_id"], decided["instrument_id"])
        self.assertEqual(replayed["record"]["units"], decided["units"])
        self.assertEqual(replayed["record"]["policy_parameters_digest"], decided["policy_parameters_digest"])
        self.assertEqual(replayed["pnl"], round(decided["units"] * (2500.0 - (20.0 * 2 + 92500.0 * 0.00002 * 2)), 6))

    def test_store_that_cannot_persist_fails_closed(self) -> None:
        class Plain:
            def enqueue(self, payload):  # pragma: no cover - must not be reached
                raise AssertionError("must not enqueue")

            def is_processed(self, signal_id):
                return False

        producer = PaperSignalProducer(store_for=lambda _: Plain(), strategy=self._strategy())
        self.assertEqual(producer.produce(self._binding(self._artifact()), self.NOW), 0)
        self.assertIn("expression_store_unsupported", producer.degraded_bindings["rb-expr"])

    def test_malformed_decider_cannot_place_an_order(self) -> None:
        store = InMemoryPendingSignalStore()
        bad = lambda view, candidates, book, budget, params: {  # noqa: E731
            "decision_id": view["decision_id"],
            "instrument_id": "ssf:ZZZ:202611",
            "side": "buy",
            "units": 5,
            "reason_code": "long_lever",
        }
        self.assertEqual(self._producer(store, decider=bad).produce(self._binding(self._artifact()), self.NOW), 1)
        [signal] = store.get_pending()
        self.assertEqual(signal["action"], "HOLD")
        self.assertEqual(signal["metadata"]["expression_decision"]["reason_code"], "instrument_not_in_candidates")
        self.assertNotIn("instrument_id", signal["metadata"])

    def test_forged_close_leg_without_a_holding_cannot_place_an_order(self) -> None:
        store = InMemoryPendingSignalStore()
        forged = lambda view, candidates, book, budget, params: {  # noqa: E731
            "decision_id": view["decision_id"],
            "instrument_id": "eq:0050",
            "side": "buy",
            "units": 1000000,
            "reason_code": "close_leg",
        }
        self.assertEqual(self._producer(store, decider=forged).produce(self._binding(self._artifact()), self.NOW), 1)
        [signal] = store.get_pending()
        self.assertEqual(signal["action"], "HOLD")
        self.assertEqual(signal["metadata"]["expression_decision"]["reason_code"], "instrument_not_in_candidates")
        self.assertNotIn("instrument_id", signal["metadata"])

    def test_missing_inputs_fail_closed_to_a_no_trade(self) -> None:
        store = InMemoryPendingSignalStore()
        art = self._artifact(bearish=True)
        binding = self._binding(art, closes=(100.0, 101.0))
        strategy = self._strategy(expression_inputs=lambda _b: {}, candidates_builder=None)
        producer = PaperSignalProducer(store_for=lambda _: store, strategy=strategy)
        self.assertEqual(producer.produce(binding, self.NOW), 1)
        [signal] = store.get_pending()
        self.assertEqual(signal["action"], "HOLD")
        self.assertIn(
            signal["metadata"]["expression_decision"]["reason_code"],
            {"candidates_unavailable", "no_feasible_instrument"},
        )


class TestExpressionInputsProductionWiring(unittest.TestCase):
    """The production strategy built as ``main`` builds it, fed by the real shapes.

    Nothing is injected past the wiring: ``_runner_strategy`` reads the pool
    from the capital service and the ledger from the telemetry runtime summary;
    only the HTTP transport is replaced, with the shapes read on 2026-11-09.
    """

    NOW = TestExpressionDecisionStep.NOW
    BAR = TestExpressionDecisionStep.BAR
    INPUTS = TestExpressionDecisionStep.INPUTS
    _artifact = TestExpressionDecisionStep._artifact
    _binding = TestExpressionDecisionStep._binding

    POOL_BUDGET = 100000.0
    POLICY = {
        "risk_policy_id": "rp-pool-rb-expr",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 2.0,
        "net_limit": 2.0,
        "max_leverage": 2.0,
        "allowed_asset_classes": ["equity", "future"],
    }
    ENV = {
        "PANTHEON_PAPER_EXPRESSION_LAYER": "on",
        "PANTHEON_CAPITAL_API_URL": "http://capital:8092",
        "DEPLOYMENT_CAPITAL_SERVICE_TOKEN": "capital-reader",
        "PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083",
        "PANTHEON_TELEMETRY_SERVICE_TOKEN": "telemetry-reader",
        "PANTHEON_TENANT_ID": "default",
    }

    def _pool(self, *, policy=True, budget=POOL_BUDGET):
        pool = {
            "pool_id": "pool-rb-expr",
            "tenant_id": "default",
            "name": "persona paper pool",
            "owner_id": "default",
            "owner_type": "org",
            "status": "active",
            "currency": "TWD",
            "budget": budget,
            "single_runtime_enforced": True,
            "metadata": {"internal": True, "execution_context": "paper"},
        }
        if policy is not False:
            pool["metadata"]["risk_policy"] = dict(self.POLICY if policy is True else policy)
        return pool

    @staticmethod
    def _fresh_summary():
        # The hosted summaries of 2026-11-09: no fill yet, positions null.
        return {
            "runtime_id": "runtime-rb-expr",
            "binding_id": "rb-expr",
            "ledger_fill_count": 0,
            "executed_trade_count": 0,
            "positions": None,
        }

    def _fetch(self, *, pool=None, summary=None, summary_error=None):
        calls: list[str] = []
        pool = self._pool() if pool is None else pool
        summary = self._fresh_summary() if summary is None else summary

        def fetch(url, headers, timeout):
            calls.append(url)
            if "/api/capital-pools/" in url:
                assert headers["Authorization"] == "Bearer capital-reader"
                return pool
            if "/api/telemetry/runtime-summaries/" in url:
                assert headers["Authorization"] == "Bearer telemetry-reader"
                if summary_error is not None:
                    raise summary_error
                return summary
            raise AssertionError(url)

        fetch.calls = calls
        return fetch

    def _decide(self, symbol, price, *, fetch, env=None):
        import urllib.parse  # noqa: F401
        from services.execution.lean_runtime import expression_inputs, paper_signal_producer

        art = self._artifact()
        art["parameters"]["symbols"] = [symbol]
        binding = self._binding(art, closes=(price * 0.9552, price), symbol=symbol)
        store = InMemoryPendingSignalStore()
        with patch.dict("os.environ", env or self.ENV, clear=True), patch.object(
            expression_inputs, "_http_get_json", fetch
        ):
            strategy = paper_signal_producer._runner_strategy()
            producer = PaperSignalProducer(store_for=lambda _: store, strategy=strategy)
            self.assertEqual(producer.produce(binding, self.NOW), 1)
        [signal] = store.get_pending()
        return signal

    def test_runner_strategy_carries_the_provider_and_calls_it_per_binding(self) -> None:
        from services.execution.lean_runtime import expression_inputs, paper_signal_producer

        with patch.dict("os.environ", self.ENV, clear=True):
            strategy = paper_signal_producer._runner_strategy()
        self.assertIsInstance(strategy, paper_signal_producer.CurrentArtifactStrategy)
        self.assertIsInstance(strategy._expression_inputs, expression_inputs.ExpressionInputsProvider)
        fetch = self._fetch()
        signal = self._decide("2330.TWSE", 2235.0, fetch=fetch)
        self.assertEqual(len(fetch.calls), 2)
        self.assertTrue(fetch.calls[0].endswith("/api/capital-pools/pool-rb-expr"))
        self.assertTrue(fetch.calls[1].endswith("/api/telemetry/runtime-summaries/runtime-rb-expr"))
        budget = signal["metadata"]["expression_replay_input"]["budget"]
        self.assertEqual(budget["equity"], self.POOL_BUDGET)
        self.assertEqual(budget["available_cash"], self.POOL_BUDGET)
        self.assertEqual(budget["gross_limit"], self.POLICY["gross_limit"] * self.POOL_BUDGET)
        self.assertEqual(budget["gross_used"], 0.0)
        self.assertEqual(budget["max_leverage"], self.POLICY["max_leverage"])
        book = signal["metadata"]["expression_replay_input"]["book_before"]
        self.assertEqual(book, {"positions": [], "gross_notional": 0.0})

    def test_hosted_long_views_choose_the_cash_share_and_never_no_trade(self) -> None:
        from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

        for symbol, price, code in (
            ("2330.TWSE", 2235.0, "2330"),
            ("0050.TWSE", 92.5, "0050"),
            ("2317.TWSE", 225.0, "2317"),
        ):
            with self.subTest(symbol=symbol):
                signal = self._decide(symbol, price, fetch=self._fetch())
                meta = signal["metadata"]
                self.assertEqual(meta["view_direction"], "LONG")
                self.assertEqual(meta["strength"], 1.0)
                decision = meta["expression_decision"]
                self.assertEqual(decision["candidates_considered"], [f"eq:{code}"])
                self.assertEqual(decision["reason_code"], "long_cash")
                self.assertEqual((signal["action"], signal["direction"]), ("BUY", "LONG"))
                # Dev plan 4.4: N* = strength x fraction x remaining gross, units
                # clamp(round(N*/price), 1, max_units_by_risk); max_units_by_risk
                # is bounded by cash (full payment) and by remaining gross.
                remaining = Decimal(str(self.POLICY["gross_limit"])) * Decimal(str(self.POOL_BUDGET))
                spend = min(Decimal(str(self.POOL_BUDGET)), remaining)
                max_units = int((spend / Decimal(str(price))).to_integral_value(rounding=ROUND_DOWN))
                target = Decimal("1.0") * Decimal("1.0") * remaining
                expected = min(
                    max(int((target / Decimal(str(price))).quantize(Decimal(1), rounding=ROUND_HALF_UP)), 1),
                    max_units,
                )
                self.assertEqual(meta["instrument_id"], f"eq:{code}")
                self.assertEqual(meta["units"], expected)
                self.assertEqual(signal["quantity"], expected)
                self.assertLessEqual(expected * price, self.POOL_BUDGET)
                self.assertGreaterEqual(expected, 1)

    def test_policy_missing_is_a_budget_unavailable_no_trade_naming_the_input(self) -> None:
        signal = self._decide("2330.TWSE", 2235.0, fetch=self._fetch(pool=self._pool(policy=False)))
        decision = signal["metadata"]["expression_decision"]
        self.assertEqual(signal["action"], "HOLD")
        self.assertEqual(decision["reason_code"], "candidates_unavailable")
        self.assertTrue(decision["detail"].startswith("budget_unavailable: risk_policy"), decision)
        self.assertNotEqual(decision["detail"], "gross_limit")

    def test_pool_budget_missing_is_named(self) -> None:
        signal = self._decide("2330.TWSE", 2235.0, fetch=self._fetch(pool=self._pool(budget=None)))
        decision = signal["metadata"]["expression_decision"]
        self.assertEqual(decision["reason_code"], "candidates_unavailable")
        self.assertTrue(decision["detail"].startswith("budget_unavailable: pool_budget"), decision)

    def test_ledger_unreadable_is_a_budget_unavailable_no_trade_naming_the_input(self) -> None:
        import urllib.error

        error = urllib.error.HTTPError("http://telemetry", 503, "unavailable", {}, None)
        signal = self._decide("2330.TWSE", 2235.0, fetch=self._fetch(summary_error=error))
        decision = signal["metadata"]["expression_decision"]
        self.assertEqual(signal["action"], "HOLD")
        self.assertEqual(decision["reason_code"], "candidates_unavailable")
        self.assertTrue(decision["detail"].startswith("budget_unavailable: ledger"), decision)

    def test_unconfigured_services_fail_closed_naming_the_inputs(self) -> None:
        env = {"PANTHEON_PAPER_EXPRESSION_LAYER": "on"}
        signal = self._decide("2330.TWSE", 2235.0, fetch=self._fetch(), env=env)
        decision = signal["metadata"]["expression_decision"]
        self.assertEqual(decision["reason_code"], "candidates_unavailable")
        self.assertTrue(decision["detail"].startswith("budget_unavailable: capital_pool"), decision)

    def test_a_real_gross_limit_still_reports_gross_limit(self) -> None:
        policy = dict(self.POLICY, gross_limit=0.01, max_leverage=0.01)  # 1,000 < one 2,235 share
        signal = self._decide("2330.TWSE", 2235.0, fetch=self._fetch(pool=self._pool(policy=policy)))
        decision = signal["metadata"]["expression_decision"]
        self.assertEqual(decision["reason_code"], "no_feasible_instrument")
        self.assertEqual(decision["detail"], "gross_limit")

    def test_flag_off_and_legacy_artifact_never_read_the_services(self) -> None:
        fetch = self._fetch()
        art = self._artifact(expression=False)
        art["parameters"]["symbols"] = ["2330.TWSE"]
        binding = self._binding(art, closes=(2135.0, 2235.0), symbol="2330.TWSE")
        from services.execution.lean_runtime import expression_inputs, paper_signal_producer

        for env, artifact_binding in (
            ({**self.ENV, "PANTHEON_PAPER_EXPRESSION_LAYER": "off"}, binding),
            (self.ENV, binding),
        ):
            with patch.dict("os.environ", env, clear=True), patch.object(
                expression_inputs, "_http_get_json", fetch
            ):
                strategy = paper_signal_producer._runner_strategy()
                decision = strategy(artifact_binding, self.NOW)
            self.assertNotIn("expression", decision)
        self.assertEqual(fetch.calls, [])

    def test_runner_strategy_with_real_dataset_layout_chooses_0050_future_and_2330_2317_cash(self) -> None:
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        from services.source_ingestion.connectors.taiwan_official import (
            TaifexDerivativesChipAdapter,
        )
        from services.source_ingestion.market_data_storage import MarketDataStorageWriter

        fixtures_dir = Path(__file__).resolve().parent / "fixtures" / "taifex_20261106"
        payload = {
            "SingleStockFuturesMargining": json.loads((fixtures_dir / "SingleStockFuturesMargining.json").read_text(encoding="utf-8")),
            "SingleStockFuturesETFMargining": json.loads((fixtures_dir / "SingleStockFuturesETFMargining.json").read_text(encoding="utf-8")),
            "DailyMarketReportFut": json.loads((fixtures_dir / "DailyMarketReportFut.json").read_text(encoding="utf-8")),
            "FinalSettlementPriceSSF": json.loads((fixtures_dir / "FinalSettlementPriceSSF.json").read_text(encoding="utf-8")),
            "stockLists": (fixtures_dir / "stockLists.html").read_text(encoding="utf-8"),
        }
        adapter = TaifexDerivativesChipAdapter()

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "market_data_store"
            writer = MarketDataStorageWriter(root)
            records = adapter.records_from_payload(
                payload,
                dataset="taifex_ssf_contracts",
                available_time="2026-11-06T16:30:00Z",
            )
            writer.write_run(
                result=SimpleNamespace(records=records, run=SimpleNamespace(ingest_run_id="run-20261106")),
                connector=adapter.connector(),
            )
            part_files = list((root / "normalized" / "taifex_ssf_contracts" / "date=2026-11-06").glob("*.jsonl"))
            self.assertEqual(len(part_files), 1)

            env = {**self.ENV, "PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT": str(root)}

            # 1. 0050: SRF candidate has unit=1000, initial_margin_per_unit=8700, max_units_by_risk=2;
            #    unchanged policy chooses ssf:SRF:202611 x 2 (long_lever) because strength=1.0 >= 0.6
            #    and futures exposure 185,000 > cash exposure 92,500.
            sig_0050 = self._decide("0050.TWSE", 92.5, fetch=self._fetch(), env=env)
            meta_0050 = sig_0050["metadata"]
            cands_0050 = {c["instrument_id"]: c for c in meta_0050["expression_replay_input"]["candidates"]}
            self.assertIn("ssf:SRF:202611", cands_0050)
            srf = cands_0050["ssf:SRF:202611"]
            self.assertEqual(srf["unit"], 1000)
            self.assertEqual(srf["initial_margin_per_unit"], 8700.0)
            self.assertEqual(srf["max_units_by_risk"], 2)
            self.assertEqual(meta_0050["instrument_id"], "ssf:SRF:202611")
            self.assertEqual(meta_0050["kind"], "stock_future")
            self.assertEqual(meta_0050["units"], 2)
            self.assertEqual(meta_0050["reason_code"], "long_lever")
            self.assertEqual((sig_0050["action"], sig_0050["direction"], sig_0050["quantity"]), ("BUY", "LONG", 2))
            self.assertEqual(sig_0050["quantity_type"], "SHARES")
            # Critical order gate fields populated on signal metadata:
            self.assertEqual(meta_0050["contract"], "SRF")
            self.assertEqual(meta_0050["contract_month"], "202611")
            self.assertEqual(meta_0050["price_basis"], "futures_daily_settlement_prev_session")
            self.assertEqual(meta_0050["price_date"], "2026-11-06")
            self.assertEqual(meta_0050["futures_settlement_price"], 92.35)
            self.assertEqual(meta_0050["initial_margin_per_unit"], 8700.0)
            self.assertEqual(meta_0050["maintenance_margin_per_unit"], 6700.0)
            self.assertEqual(meta_0050["fee_per_unit_per_side"], 8.0)
            self.assertEqual(meta_0050["tax_rate_per_side"], 0.00002)
            self.assertEqual(meta_0050["expiry"], "2026-11-18")
            self.assertEqual(meta_0050["last_trading_day"], "2026-11-18")
            self.assertEqual(meta_0050["trade_date"], "2026-11-09")
            self.assertIsInstance(meta_0050["candidates"], list)

            # 2. 2330: QFF candidate has max_units_by_risk=0 because one contract's notional 213,300
            #    exceeds the 200,000 gross limit; unchanged policy chooses eq:2330 x 44 (long_cash).
            sig_2330 = self._decide("2330.TWSE", 2235.0, fetch=self._fetch(), env=env)
            meta_2330 = sig_2330["metadata"]
            cands_2330 = {c["instrument_id"]: c for c in meta_2330["expression_replay_input"]["candidates"]}
            self.assertIn("ssf:QFF:202611", cands_2330)
            self.assertEqual(cands_2330["ssf:QFF:202611"]["max_units_by_risk"], 0)
            self.assertEqual(cands_2330["ssf:QFF:202611"]["price"] * cands_2330["ssf:QFF:202611"]["unit"], 213300.0)
            self.assertEqual(meta_2330["instrument_id"], "eq:2330")
            self.assertEqual(meta_2330["kind"], "cash_share")
            self.assertEqual(meta_2330["units"], 44)
            self.assertEqual(meta_2330["reason_code"], "long_cash")

            # 3. 2317: DHF candidate has max_units_by_risk=0 because one contract's notional 450,000
            #    exceeds the 200,000 gross limit; unchanged policy chooses eq:2317 x 444 (long_cash).
            sig_2317 = self._decide("2317.TWSE", 225.0, fetch=self._fetch(), env=env)
            meta_2317 = sig_2317["metadata"]
            cands_2317 = {c["instrument_id"]: c for c in meta_2317["expression_replay_input"]["candidates"]}
            self.assertIn("ssf:DHF:202611", cands_2317)
            self.assertEqual(cands_2317["ssf:DHF:202611"]["max_units_by_risk"], 0)
            self.assertEqual(cands_2317["ssf:DHF:202611"]["price"] * cands_2317["ssf:DHF:202611"]["unit"], 450000.0)
            self.assertEqual(meta_2317["instrument_id"], "eq:2317")
            self.assertEqual(meta_2317["kind"], "cash_share")
            self.assertEqual(meta_2317["units"], 444)
            self.assertEqual(meta_2317["reason_code"], "long_cash")

            # 4. Unset, unreadable, tombstone, and stale dataset roots all offer cash share and record
            #    candidates_unavailable on the cash candidate's source_refs.
            unset_sig = self._decide("0050.TWSE", 92.5, fetch=self._fetch(), env=self.ENV)
            unset_cands = unset_sig["metadata"]["expression_replay_input"]["candidates"]
            self.assertEqual([c["instrument_id"] for c in unset_cands], ["eq:0050"])
            self.assertIn(
                {"reason": "candidates_unavailable", "dataset": "taifex_ssf_contracts"},
                unset_cands[0]["source_refs"],
            )

            unreadable_env = {**self.ENV, "PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT": str(Path(tmpdir) / "nonexistent")}
            unreadable_sig = self._decide("0050.TWSE", 92.5, fetch=self._fetch(), env=unreadable_env)
            unreadable_cands = unreadable_sig["metadata"]["expression_replay_input"]["candidates"]
            self.assertEqual([c["instrument_id"] for c in unreadable_cands], ["eq:0050"])
            self.assertIn(
                {"reason": "candidates_unavailable", "dataset": "taifex_ssf_contracts"},
                unreadable_cands[0]["source_refs"],
            )

            tombstone_root = Path(tmpdir) / "tombstone_store"
            tombstone_part = tombstone_root / "normalized" / "taifex_ssf_contracts" / "date=2026-11-06"
            tombstone_part.mkdir(parents=True, exist_ok=True)
            (tombstone_part / "run-1630.jsonl").write_text(
                "\n".join(json.dumps({"metadata": {"normalized_row": dict(r.metadata["normalized_row"])}}) for r in records)
                + "\n",
                encoding="utf-8",
            )
            tombstone_rows = [
                {
                    **dict(r.metadata["normalized_row"]),
                    "available_time": "2026-11-06T17:00:00Z",
                    "tombstone": True,
                }
                for r in records
                if r.metadata.get("underlying_code") == "0050"
            ]
            (tombstone_part / "run-tombstone.jsonl").write_text(
                "\n".join(json.dumps({"metadata": {"normalized_row": r}}) for r in tombstone_rows) + "\n",
                encoding="utf-8",
            )
            tombstone_env = {**self.ENV, "PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT": str(tombstone_root)}
            tombstone_sig = self._decide("0050.TWSE", 92.5, fetch=self._fetch(), env=tombstone_env)
            tombstone_cands = tombstone_sig["metadata"]["expression_replay_input"]["candidates"]
            self.assertEqual([c["instrument_id"] for c in tombstone_cands], ["eq:0050"])
            self.assertIn(
                {"reason": "candidates_unavailable", "dataset": "taifex_ssf_contracts"},
                tombstone_cands[0]["source_refs"],
            )

            stale_root = Path(tmpdir) / "stale_store"
            stale_part = stale_root / "normalized" / "taifex_ssf_contracts" / "date=2026-10-20"
            stale_part.mkdir(parents=True, exist_ok=True)
            stale_rows = [
                {
                    **dict(r.metadata["normalized_row"]),
                    "market_date": "2026-10-20",
                    "settlement_price_date": "2026-10-20",
                    "margin_date": "2026-10-20",
                }
                for r in records
            ]
            (stale_part / "run-stale.jsonl").write_text(
                "\n".join(json.dumps({"metadata": {"normalized_row": r}}) for r in stale_rows) + "\n",
                encoding="utf-8",
            )
            stale_env = {**self.ENV, "PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT": str(stale_root)}
            stale_sig = self._decide("0050.TWSE", 92.5, fetch=self._fetch(), env=stale_env)
            stale_cands = stale_sig["metadata"]["expression_replay_input"]["candidates"]
            self.assertEqual([c["instrument_id"] for c in stale_cands], ["eq:0050"])
            self.assertIn(
                {"reason": "candidates_unavailable", "dataset": "taifex_ssf_contracts"},
                stale_cands[0]["source_refs"],
            )



class ExpressionStartupReportTest(unittest.TestCase):
    def _provider(self, configured: bool):
        from services.execution.lean_runtime.expression_inputs import ExpressionInputsProvider

        reader = (lambda _pool: {}) if configured else None
        return ExpressionInputsProvider(
            capital_pool_reader=reader, runtime_summary_reader=lambda _rt: {}
        )

    def test_configured_readers_log_without_degraded_reason(self) -> None:
        line, reason = expression_startup_report(self._provider(True), layer_on=True)
        self.assertEqual(line, "expression layer on; capital reader configured; ledger reader configured")
        self.assertIsNone(reason)

    def test_missing_capital_reader_with_layer_on_names_capital_pool(self) -> None:
        line, reason = expression_startup_report(self._provider(False), layer_on=True)
        self.assertIn("capital reader not configured", line)
        self.assertIn("ledger reader configured", line)
        self.assertTrue(reason and reason.startswith("capital_pool:"))

    def test_layer_off_never_degrades_and_line_carries_no_credential(self) -> None:
        line, reason = expression_startup_report(self._provider(False), layer_on=False)
        self.assertIsNone(reason)
        self.assertNotIn("token", line.lower())
        _, none_reason = expression_startup_report(None, layer_on=False)
        self.assertIsNone(none_reason)

    def test_entrypoint_strategy_without_mount_reports_the_gap(self) -> None:
        from services.execution.lean_runtime import paper_signal_producer
        from services.execution.lean_runtime.test_expression_inputs import _compose_environment

        env = _compose_environment("paper-signal-producer", None)
        env["PANTHEON_PAPER_EXPRESSION_LAYER"] = "on"
        env["PAPER_SIGNAL_STRATEGY"] = "artifact"
        env.pop("DEPLOYMENT_CAPITAL_SERVICE_TOKEN", None)
        with patch.dict("os.environ", env, clear=True):
            strategy = paper_signal_producer._runner_strategy()
            line, reason = expression_startup_report(
                strategy._expression_inputs,
                layer_on=paper_signal_producer.expression_layer_enabled(),
            )
        self.assertIn("capital reader not configured", line)
        self.assertIn("capital_pool", reason or "")


class TestInstrumentCounterfactualWriter(unittest.TestCase):
    """PAPER-INSTRUMENT-COUNTERFACTUAL-WRITER-20261110.

    The production strategy (as ``main`` builds it), a dataset in the real
    ``normalized/taifex_ssf_contracts/date=...`` layout and the real
    2026-11-10 decision shapes; the grading rows leave through the producer,
    the runtime and the telemetry summary fold.
    """

    NOW = TestExpressionDecisionStep.NOW
    BAR = TestExpressionDecisionStep.BAR
    INPUTS = TestExpressionDecisionStep.INPUTS
    _artifact = TestExpressionDecisionStep._artifact
    _binding = TestExpressionDecisionStep._binding
    POOL_BUDGET = TestExpressionInputsProductionWiring.POOL_BUDGET
    POLICY = TestExpressionInputsProductionWiring.POLICY
    ENV = TestExpressionInputsProductionWiring.ENV
    _pool = TestExpressionInputsProductionWiring._pool
    _fresh_summary = staticmethod(TestExpressionInputsProductionWiring._fresh_summary)
    _fetch = TestExpressionInputsProductionWiring._fetch

    DECISION_BAR = "2026-11-09T00:00:00+08:00"
    DECISION_NOW = "2026-11-10T07:01:37Z"
    GRADING_BAR = "2026-11-10T00:00:00+08:00"
    GRADING_NOW = "2026-11-11T07:01:37Z"
    CASH_ENTRY = 99.7
    CASH_MARK = 100.9
    SRF_ENTRY = 97.1
    SRF_MARK = 98.8
    NYF_MARK = 98.4

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "market_data_store"

    def _rows(self, day: str, srf_price, nyf_price, *, srf_tombstone=False):
        from services.execution.lean_runtime.test_futures_session_inputs import _srf_row

        def row(contract, price, margin, *, unit=1000, tombstone=False):
            return {
                **_srf_row(day, price, tombstone=tombstone),
                "contract": contract,
                "unit": unit,
                "multiplier": unit,
                "margin_mode": "fixed",
                "initial_margin_amount": margin,
                "maintenance_margin_amount": round(margin * 0.77, 2),
            }

        return [
            row("SRF", srf_price, 8700.0, tombstone=srf_tombstone),
            row("NYF", nyf_price, 87000.0, unit=10000),
        ]

    def _write(self, partition_day: str, rows, run_id: str) -> None:
        from services.execution.lean_runtime.test_futures_session_inputs import (
            _write_ssf_partition,
        )

        _write_ssf_partition(self.root, partition_day, rows, run_id=run_id)

    def _decision_dataset(self) -> None:
        self._write("2026-11-09", self._rows("2026-11-09", self.SRF_ENTRY, self.SRF_ENTRY), "run-d")

    def _grading_dataset(self, **kwargs) -> None:
        self._write(
            "2026-11-11",
            self._rows("2026-11-10", kwargs.get("srf", self.SRF_MARK), self.NYF_MARK,
                       srf_tombstone=kwargs.get("srf_tombstone", False)),
            "run-g",
        )

    def _env(self):
        return {**self.ENV, "PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT": str(self.root)}

    def _produce(self, store, *, bar, now, close, previous, earlier=()):
        from services.execution.lean_runtime import expression_inputs, paper_signal_producer

        art = self._artifact()
        art["parameters"]["symbols"] = ["0050.TWSE"]
        binding = self._binding(art, closes=(*earlier, previous, close), symbol="0050.TWSE")
        binding["market_input"]["event_time"] = bar
        binding["market_input"]["observed_at"] = now
        with patch.dict("os.environ", self._env(), clear=True), patch.object(
            expression_inputs, "_http_get_json", self._fetch()
        ):
            producer = PaperSignalProducer(
                store_for=lambda _: store, strategy=paper_signal_producer._runner_strategy()
            )
            count = producer.produce(binding, now)
        return count, binding

    @staticmethod
    def _terminal(store, decision_id):
        return store.get_decision_record(f"{decision_id}#grading")

    def _first_decision(self, store):
        self._decision_dataset()
        count, binding = self._produce(
            store, bar=self.DECISION_BAR, now=self.DECISION_NOW,
            close=self.CASH_ENTRY, previous=self.CASH_ENTRY / 1.0448,
        )
        self.assertEqual(count, 1)
        [signal] = store.get_pending()
        store.ack(signal)
        return signal, binding

    def test_decision_shape_matches_the_recorded_2026_11_10_fill(self) -> None:
        store = InMemoryPendingSignalStore()
        signal, _ = self._first_decision(store)
        meta = signal["metadata"]
        replay = meta["expression_replay_input"]
        by_id = {c["instrument_id"]: c for c in replay["candidates"]}
        self.assertEqual(sorted(by_id), ["eq:0050", "ssf:NYF:202611", "ssf:SRF:202611"])
        self.assertEqual((by_id["eq:0050"]["price"], by_id["eq:0050"]["price_basis"]),
                         (99.7, "stock_close_prev_session"))
        self.assertEqual(by_id["ssf:SRF:202611"]["price"], 97.1)
        self.assertEqual(by_id["ssf:SRF:202611"]["max_units_by_risk"], 2)
        self.assertEqual(by_id["ssf:NYF:202611"]["max_units_by_risk"], 0)
        self.assertEqual(by_id["ssf:NYF:202611"]["initial_margin_per_unit"], 87000.0)
        self.assertEqual((meta["instrument_id"], meta["units"], meta["reason_code"]),
                         ("ssf:SRF:202611", 2, "long_lever"))
        self.assertNotIn("instrument_counterfactual_rows", meta)

    def _grade_on_next_bar(self, store):
        self._grading_dataset()
        count, binding = self._produce(
            store, bar=self.GRADING_BAR, now=self.GRADING_NOW,
            close=self.CASH_MARK, previous=self.CASH_ENTRY,
        )
        self.assertEqual(count, 1)
        [carrier] = store.get_pending()
        return carrier, binding

    def test_next_session_bar_carries_one_row_per_candidate_with_the_chosen_leg(self) -> None:
        store = InMemoryPendingSignalStore()
        first, _ = self._first_decision(store)
        decision_id = first["metadata"]["decision_id"]
        carrier, _ = self._grade_on_next_bar(store)
        rows = carrier["metadata"]["instrument_counterfactual_rows"]
        self.assertEqual([(r["decision_id"], r["instrument_id"]) for r in rows],
                         [(decision_id, "eq:0050"), (decision_id, "ssf:NYF:202611"), (decision_id, "ssf:SRF:202611")])
        by_id = {r["instrument_id"]: r for r in rows}
        self.assertEqual([r["chosen"] for r in rows], [False, False, True])
        chosen = by_id["ssf:SRF:202611"]
        self.assertEqual((chosen["entry"], chosen["mark"], chosen["units"]), (97.1, 98.8, 2))
        self.assertEqual((chosen["underlying_entry"], chosen["underlying_mark"]), (99.7, 100.9))
        self.assertEqual(by_id["ssf:NYF:202611"]["units"], 0)
        self.assertEqual(by_id["eq:0050"]["mark"], 100.9)
        self.assertGreater(chosen["pnl_cf"], 0)
        self.assertEqual(self._terminal(store, decision_id)["status"], "done")
        # The decision of the grading bar itself is not graded on that bar.
        own_id = carrier["metadata"]["decision_id"]
        self.assertNotIn(own_id, {r["decision_id"] for r in rows})
        self.assertIsNone(self._terminal(store, own_id))

    # -- runtime -> telemetry summary -> BFF grades ---------------------------

    def _run_carrier_through_runtime(self, carrier, binding):
        import os

        from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
        from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
        from services.execution.lean_runtime.test_paper_runtime import (
            _FakeRuntimeManagerClient,
            _FakeTelemetryEmitter,
        )

        identity = RuntimeIdentity.from_env(
            {
                "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
                "PANTHEON_RUNTIME_MODE": "paper",
                "PANTHEON_RUNTIME_ID": binding["runtime_id"],
                "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
                "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
                "PANTHEON_WORKSPACE_REF": "workspace-paper",
                "PANTHEON_AUTH_PROFILE_REF": "auth-profile-paper",
                "PANTHEON_PERSONA_ID": "persona-paper-ops",
                "PANTHEON_SESSION_ID": "session-paper-runtime",
                "PANTHEON_TRACE_ID": "6f1d2c3e-0000-4000-8000-000000000001",
                "PANTHEON_REQUEST_ID": "request-paper-runtime",
            }
        )
        runtime_binding = {
            "binding_id": binding["binding_id"],
            "runtime_id": binding["runtime_id"],
            "capital_pool_id": binding["capital_pool_id"],
            "artifact_id": binding["artifact_id"],
            "artifact_version": binding["artifact_version"],
            "deployment_mode": "paper",
            "plan_id": binding["plan_id"],
            "persona_capital_binding_id": binding["persona_capital_binding_id"],
            "status": "active",
        }
        telemetry = _FakeTelemetryEmitter()
        with tempfile.TemporaryDirectory() as outbox, patch.dict(
            os.environ, {"PANTHEON_LIFECYCLE_OUTBOX_PATH": str(Path(outbox) / "outbox.json")}
        ):
            service = PaperRuntimeService(
                store=InMemoryPendingSignalStore([carrier]),
                identity=identity,
                runtime_manager_client=type(
                    "RM", (), {"list_all": lambda self: [runtime_binding]}
                )(),
                telemetry_emitter=telemetry,
                poll_interval_seconds=3600,
                max_batch_size=10,
            )
            service.drain_once()
            service._consumer.flush_rebalance(carrier["run_id"], service._algo)
        return telemetry.events, runtime_binding

    def test_rows_reach_the_runtime_summary_and_grade_the_0050_decision(self) -> None:
        from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore

        store = InMemoryPendingSignalStore()
        first, _ = self._first_decision(store)
        decision_id = first["metadata"]["decision_id"]
        carrier, binding = self._grade_on_next_bar(store)
        events, runtime_binding = self._run_carrier_through_runtime(carrier, binding)

        types = [e["event_type"] for e in events]
        self.assertEqual(types.count("persona_instrument_counterfactual"), 3)
        generation = next(e for e in events if e["event_type"] == "signal_generation")
        decision = next(e for e in events if e["event_type"] == "trade_decision")
        self.assertNotIn("instrument_counterfactual_rows", generation["metadata"])
        self.assertNotIn("instrument_counterfactual_rows", decision["metadata"])

        projection = RuntimeSummaryProjectionStore()
        summary = {}
        for event in events:
            if event["event_type"] != "persona_instrument_counterfactual":
                continue
            self.assertNotIn("correlation_envelope", event["metadata"])
            summary = projection.project_event(
                {
                    **event,
                    "tenant_id": "default",
                    "environment": "paper",
                    "deployment_stage": "paper",
                    "runtime_id": runtime_binding["runtime_id"],
                    "binding_id": runtime_binding["binding_id"],
                    "capital_pool_id": runtime_binding["capital_pool_id"],
                    "artifact_id": runtime_binding["artifact_id"],
                    "artifact_version": runtime_binding["artifact_version"],
                    "plan_id": runtime_binding["plan_id"],
                    "persona_capital_binding_id": runtime_binding["persona_capital_binding_id"],
                }
            )
        stored = summary["instrument_counterfactuals"]
        self.assertEqual(
            [(r["decision_id"], r["instrument_id"], r["chosen"]) for r in stored],
            [
                (decision_id, "eq:0050", False),
                (decision_id, "ssf:NYF:202611", False),
                (decision_id, "ssf:SRF:202611", True),
            ],
        )
        from services.control_plane.bff.agora.performance.attribution import (
            grade_instrument_decisions,
        )

        graded = grade_instrument_decisions(stored)
        self.assertEqual(graded["direction_graded_count"], 1)
        self.assertEqual(graded["direction_hit_rate"], 1.0)
        self.assertEqual(graded["instrument_regret_graded_count"], 1)
        self.assertIsNotNone(graded["instrument_regret"])

    # -- exactly once ---------------------------------------------------------

    def test_a_decision_is_graded_once_across_repeat_and_restart(self) -> None:
        store = InMemoryPendingSignalStore()
        first, _ = self._first_decision(store)
        decision_id = first["metadata"]["decision_id"]
        carrier, _ = self._grade_on_next_bar(store)
        store.ack(carrier)
        self.assertEqual(self._terminal(store, decision_id)["status"], "done")
        # The same bar again (a repeat poll, then a restarted producer).
        for _ in range(2):
            count, _b = self._produce(
                store, bar=self.GRADING_BAR, now=self.GRADING_NOW,
                close=self.CASH_MARK, previous=self.CASH_ENTRY,
            )
            for signal in store.get_pending():
                self.assertNotIn("instrument_counterfactual_rows", signal["metadata"])
                store.ack(signal)
        # A still later bar does not re-grade either.
        self._write("2026-11-12", self._rows("2026-11-11", 99.0, 99.2), "run-h")
        count, _b = self._produce(
            store, bar="2026-11-11T00:00:00+08:00", now="2026-11-12T07:01:37Z",
            close=101.2, previous=self.CASH_MARK,
        )
        self.assertEqual(count, 1)
        [later] = store.get_pending()
        rows = later["metadata"].get("instrument_counterfactual_rows") or []
        self.assertNotIn(decision_id, {r["decision_id"] for r in rows})
        self.assertEqual(self._terminal(store, decision_id)["status"], "done")

    def test_failed_enqueue_leaves_the_decision_pending_for_the_next_attempt(self) -> None:
        class CrashOnce(InMemoryPendingSignalStore):
            armed = False

            def enqueue(self, payload):
                if self.armed:
                    self.armed = False
                    raise RuntimeError("crash before enqueue")
                return super().enqueue(payload)

        store = CrashOnce()
        first, _ = self._first_decision(store)
        decision_id = first["metadata"]["decision_id"]
        self._grading_dataset()
        store.armed = True
        with self.assertRaises(RuntimeError):
            self._produce(
                store, bar=self.GRADING_BAR, now=self.GRADING_NOW,
                close=self.CASH_MARK, previous=self.CASH_ENTRY,
            )
        self.assertIsNone(self._terminal(store, decision_id))
        count, _b = self._produce(
            store, bar=self.GRADING_BAR, now=self.GRADING_NOW,
            close=self.CASH_MARK, previous=self.CASH_ENTRY,
        )
        self.assertEqual(count, 1)
        [carrier] = store.get_pending()
        self.assertEqual(len(carrier["metadata"]["instrument_counterfactual_rows"]), 3)

    # -- never from a missing, stale or tombstoned price -----------------------

    def _assert_not_graded(self, store, decision_id, reason_part):
        from datetime import date

        [carrier] = store.get_pending()
        self.assertNotIn("instrument_counterfactual_rows", carrier["metadata"])
        # Nothing terminal was recorded: the decision stays pending, with the
        # reason both in the immutable attempt record and on the signal.
        self.assertIsNone(self._terminal(store, decision_id))
        attempt = store.get_decision_record(f"{decision_id}#grading-attempt@2026-11-10")
        self.assertEqual(attempt["status"], "pending")
        self.assertIn(reason_part, attempt["reason"])
        [diagnostic] = [
            d for d in carrier["metadata"]["instrument_counterfactual_grading"]
            if d["decision_id"] == decision_id
        ]
        self.assertEqual((diagnostic["status"], diagnostic["reason"]), ("pending", attempt["reason"]))

    def test_missing_settlement_partition_emits_nothing_and_says_why(self) -> None:
        store = InMemoryPendingSignalStore()
        first, _ = self._first_decision(store)
        count, _b = self._produce(
            store, bar=self.GRADING_BAR, now=self.GRADING_NOW,
            close=self.CASH_MARK, previous=self.CASH_ENTRY,
        )
        self.assertEqual(count, 1)
        self._assert_not_graded(
            store, first["metadata"]["decision_id"], "contract_settlement_unavailable"
        )

    def test_stale_settlement_is_never_reused_as_the_mark(self) -> None:
        store = InMemoryPendingSignalStore()
        first, _ = self._first_decision(store)
        # The newest partition still carries the entry session's settlement.
        self._write("2026-11-11", self._rows("2026-11-09", self.SRF_ENTRY, self.SRF_ENTRY), "run-s")
        self._produce(
            store, bar=self.GRADING_BAR, now=self.GRADING_NOW,
            close=self.CASH_MARK, previous=self.CASH_ENTRY,
        )
        self._assert_not_graded(
            store, first["metadata"]["decision_id"], "settlement_price_stale"
        )

    def test_tombstoned_contract_emits_nothing(self) -> None:
        store = InMemoryPendingSignalStore()
        first, _ = self._first_decision(store)
        self._grading_dataset(srf_tombstone=True)
        self._produce(
            store, bar=self.GRADING_BAR, now=self.GRADING_NOW,
            close=self.CASH_MARK, previous=self.CASH_ENTRY,
        )
        self._assert_not_graded(
            store, first["metadata"]["decision_id"], "ssf:SRF:202611:contract_tombstoned"
        )

    def test_the_entry_bar_itself_grades_nothing(self) -> None:
        store = InMemoryPendingSignalStore()
        first, _ = self._first_decision(store)
        self._grading_dataset()
        # The same bar again with a later clock: the session close is not in yet.
        self._produce(
            store, bar=self.DECISION_BAR, now=self.GRADING_NOW,
            close=self.CASH_ENTRY, previous=self.CASH_ENTRY / 1.0448,
        )
        [again] = store.get_pending()
        self.assertNotIn("instrument_counterfactual_rows", again["metadata"])
        self.assertIsNone(self._terminal(store, first["metadata"]["decision_id"]))

    def test_restart_on_a_later_bar_grades_from_the_recorded_metadata(self) -> None:
        # The 2026-11-10 decision is still ungraded on the 2026-11-11 bar (the
        # producer was down or ran the old build on 2026-11-10 -> 2026-11-11):
        # its grading session close is the snapshot's previous close.
        store = InMemoryPendingSignalStore()
        first, _ = self._first_decision(store)
        decision_id = first["metadata"]["decision_id"]
        self._grading_dataset()
        self._write("2026-11-12", self._rows("2026-11-11", 99.9, 99.9), "run-r")
        count, _b = self._produce(
            store, bar="2026-11-11T00:00:00+08:00", now="2026-11-12T07:01:37Z",
            close=101.2, previous=self.CASH_MARK, earlier=(self.CASH_ENTRY,),
        )
        self.assertEqual(count, 1)
        [carrier] = store.get_pending()
        rows = carrier["metadata"]["instrument_counterfactual_rows"]
        self.assertEqual({r["decision_id"] for r in rows}, {decision_id})
        chosen = next(r for r in rows if r["chosen"])
        # Marked at the grading session (2026-11-10), not at the later bar.
        self.assertEqual((chosen["mark"], chosen["underlying_mark"]), (self.SRF_MARK, self.CASH_MARK))

    def test_decision_older_than_the_snapshot_window_is_not_graded(self) -> None:
        store = InMemoryPendingSignalStore()
        first, _ = self._first_decision(store)
        self._grading_dataset()
        self._write("2026-11-12", self._rows("2026-11-11", 99.9, 99.9), "run-r")
        # Two closes reach back two sessions; the 2026-11-09 decision is three back.
        self._write("2026-11-13", self._rows("2026-11-12", 99.9, 99.9), "run-w")
        self._produce(
            store, bar="2026-11-12T00:00:00+08:00", now="2026-11-13T07:01:37Z",
            close=102.0, previous=101.2,
        )
        [carrier] = store.get_pending()
        self.assertNotIn("instrument_counterfactual_rows", carrier["metadata"])
        self.assertIsNone(self._terminal(store, first["metadata"]["decision_id"]))

    def test_flag_off_produces_exactly_todays_signal(self) -> None:
        from services.execution.lean_runtime import paper_signal_producer

        store = InMemoryPendingSignalStore()
        art = self._artifact(expression=False)
        art["parameters"]["symbols"] = ["0050.TWSE"]
        binding = self._binding(art, closes=(99.0, 99.7), symbol="0050.TWSE")
        env = {**self._env(), "PANTHEON_PAPER_EXPRESSION_LAYER": "off"}
        with patch.dict("os.environ", env, clear=True):
            producer = PaperSignalProducer(
                store_for=lambda _: store, strategy=paper_signal_producer._runner_strategy()
            )
            self.assertEqual(producer.produce(binding, self.DECISION_NOW), 1)
        [signal] = store.get_pending()
        self.assertNotIn("instrument_counterfactual_rows", signal["metadata"])
        self.assertNotIn("instrument_counterfactual_grading", signal["metadata"])
        self.assertIsNone(store.get_decision_record(signal["metadata"]["decision_id"]))

    def test_runtime_row_event_built_by_the_real_emitter_is_schema_valid(self) -> None:
        import jsonschema

        from services.execution.lean_runtime.paper_runtime import RuntimeTelemetryEmitter
        from services.execution.lean_runtime.runtime_identity import RuntimeIdentity

        store = InMemoryPendingSignalStore()
        self._first_decision(store)
        carrier, _binding = self._grade_on_next_bar(store)
        binding = {
            "binding_id": "rb-expr", "runtime_id": "runtime-rb-expr",
            "capital_pool_id": "pool-rb-expr", "artifact_id": "artifact-tw",
            "artifact_version": "1.0.0", "plan_id": "plan-rb-expr",
            "persona_capital_binding_id": "pcb-rb-expr", "deployment_mode": "paper",
        }
        identity = RuntimeIdentity.from_env(
            {
                "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
                "PANTHEON_RUNTIME_MODE": "paper",
                "PANTHEON_RUNTIME_ID": "runtime-rb-expr",
                "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
                "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
                "PANTHEON_WORKSPACE_REF": "workspace-paper",
                "PANTHEON_AUTH_PROFILE_REF": "auth-profile-paper",
                "PANTHEON_PERSONA_ID": "persona-paper-ops",
                "PANTHEON_SESSION_ID": "session-paper-runtime",
                "PANTHEON_TRACE_ID": "6f1d2c3e-0000-4000-8000-000000000001",
                "PANTHEON_REQUEST_ID": "request-paper-runtime",
            }
        )
        emitter = RuntimeTelemetryEmitter(
            identity, type("Resolver", (), {"resolve": lambda self: binding})()
        )
        schema = json.loads(
            (Path(__file__).resolve().parents[2] / "telemetry" / "telemetry_event.schema.json").read_text(
                encoding="utf-8"
            )
        )
        for row in carrier["metadata"]["instrument_counterfactual_rows"]:
            payload = emitter.build_event(
                "persona_instrument_counterfactual",
                {"action": "instrument_counterfactual_graded"},
                metadata={"is_real_order": False, "is_real_capital": False, **row},
                event_id="11111111-1111-4111-8111-111111111111",
                created_at=self.GRADING_NOW,
            )
            self.assertIsNotNone(payload)
            jsonschema.validate(payload, schema)


class TestFactorRulePaperSignalProducer(unittest.TestCase):
    """STRATEGY-ARTIFACT-FACTOR-RULE-KIND-20261110 paper producer tests."""

    def test_paper_producer_passes_feature_map_and_accepts_both_kinds(self) -> None:
        import copy
        from services.execution.lean_runtime.paper_signal_producer import (
            CurrentArtifactStrategy,
            SignalDecisionUnavailable,
        )
        from services.execution.lean_runtime.test_current_artifact_signal import _binding
        from services.registry.test_factor_rule import _sample_factor_rule_artifact

        factor_art = _sample_factor_rule_artifact()
        binding = _binding(
            factor_art,
            binding_id="rb-factor-rule-001",
            include_market_input=False,
        )
        binding["symbol"] = "2330.TW"
        binding["market_input"] = {
            "symbol": "2330.TW",
            "closes": [500.0, 505.0, 510.0, 515.0],
            "features": {
                "rev_yoy": [
                    {"event_time": "2026-01-31", "available_time": "2026-02-10T09:00:00Z", "value": 10.0},
                    {"event_time": "2026-02-28", "available_time": "2026-03-10T09:00:00Z", "value": 12.0},
                    {"event_time": "2026-03-31", "available_time": "2026-04-10T09:00:00Z", "value": 11.0},
                    {"event_time": "2026-04-30", "available_time": "2026-05-10T09:00:00Z", "value": 19.0},
                    # Future observation after event_time must be invisible:
                    {"event_time": "2026-05-31", "available_time": "2026-06-10T09:00:00Z", "value": -200.0},
                ]
            },
            "source_ref": "source-ingest://normalized/tw-price/2330",
            "observed_at": "2026-05-15T06:00:00Z",
            "event_time": "2026-05-15T05:30:00Z",
        }

        strategy = CurrentArtifactStrategy(expression_layer=False)
        decision = strategy(binding, "2026-05-15T06:05:00Z")
        self.assertEqual(decision["action"], "BUY")
        self.assertEqual(decision["direction"], "LONG")
        self.assertEqual(decision["symbol"], "2330.TW")
        self.assertIn("rev_yoy", decision["metadata"]["market_input_feature_keys"])

        # Also accepts factor_rule:evaluate_factor_rule_action in interpreter allowlist
        factor_art_alt = copy.deepcopy(factor_art)
        factor_art_alt["artifact_id"] = "strat-tw-rev-yoy-factor-v1-alt"
        factor_art_alt["algorithm_ref"]["logic_interpreter"] = (
            "services.registry.factor_rule:evaluate_factor_rule_action"
        )
        alt_binding = _binding(
            factor_art_alt,
            binding_id="rb-factor-rule-002",
            include_market_input=False,
        )
        alt_binding["symbol"] = "2330.TW"
        alt_binding["market_input"] = copy.deepcopy(binding["market_input"])
        alt_decision = strategy(alt_binding, "2026-05-15T06:05:00Z")
        self.assertEqual(alt_decision["action"], "BUY")

        # Unknown interpreter is still rejected
        bad_art = copy.deepcopy(factor_art)
        bad_art["artifact_id"] = "strat-tw-rev-yoy-factor-v1-bad"
        bad_art["algorithm_ref"]["logic_interpreter"] = "services.unknown:run"
        bad_binding = _binding(
            bad_art,
            binding_id="rb-factor-rule-bad",
            include_market_input=False,
        )
        bad_binding["market_input"] = copy.deepcopy(binding["market_input"])
        with self.assertRaises(SignalDecisionUnavailable) as ctx:
            strategy(bad_binding, "2026-05-15T06:05:00Z")
        self.assertEqual(ctx.exception.code, "artifact_interpreter_unsupported")

    def test_paper_producer_rejects_inputs_outside_governed_catalogue(self) -> None:
        import copy
        from services.execution.lean_runtime.paper_signal_producer import (
            CurrentArtifactStrategy,
            SignalDecisionUnavailable,
        )
        from services.execution.lean_runtime.test_current_artifact_signal import _binding
        from services.registry.test_factor_rule import _sample_factor_rule_artifact

        art = copy.deepcopy(_sample_factor_rule_artifact())
        art["artifact_id"] = "strat-tw-ungoverned-feature"
        art["strategy_logic"]["inputs"][0]["dataset"] = "tw_ungoverned_dataset"
        binding = _binding(art, binding_id="rb-factor-rule-ungoverned", include_market_input=False)
        binding["symbol"] = "2330.TW"
        binding["market_input"] = {
            "symbol": "2330.TW",
            "closes": [500.0, 505.0, 510.0, 515.0],
            "features": {"rev_yoy": [10.0, 12.0, 11.0, 19.0]},
            "source_ref": "source-ingest://normalized/tw-price/2330",
            "observed_at": "2026-05-15T06:00:00Z",
            "event_time": "2026-05-15T05:30:00Z",
        }
        with self.assertRaises(SignalDecisionUnavailable) as ctx:
            CurrentArtifactStrategy(expression_layer=False)(binding, "2026-05-15T06:05:00Z")
        self.assertEqual(ctx.exception.code, "feature_not_in_catalogue")

    def _factor_rule_binding_with_features(self, features):
        from services.execution.lean_runtime.test_current_artifact_signal import _binding
        from services.registry.test_factor_rule import _sample_factor_rule_artifact

        binding = _binding(_sample_factor_rule_artifact(), binding_id="rb-factor-rule-pit", include_market_input=False)
        binding["symbol"] = "2330.TW"
        binding["market_input"] = {
            "symbol": "2330.TW",
            "closes": [500.0, 505.0, 510.0, 515.0],
            "features": features,
            "source_ref": "source-ingest://normalized/tw-price/2330",
            "observed_at": "2026-05-15T06:00:00Z",
            "event_time": "2026-05-15T05:30:00Z",
        }
        return binding

    def test_paper_producer_requires_catalogued_timestamped_observations(self) -> None:
        from services.execution.lean_runtime.paper_signal_producer import (
            CurrentArtifactStrategy,
            SignalDecisionUnavailable,
        )

        strategy = CurrentArtifactStrategy(expression_layer=False)
        ok = {"event_time": "2026-01-31", "available_time": "2026-02-10T09:00:00Z", "value": 10.0}
        bad_features = {
            "untimestamped": {"rev_yoy": [10.0, 12.0, 11.0, 19.0]},
            "missing_event_time": {"rev_yoy": [{"available_time": "2026-02-10T09:00:00Z", "value": 10.0}]},
            "missing_available_time": {"rev_yoy": [{"event_time": "2026-01-31", "value": 10.0}]},
            "absent": {},
            "all_future": {"rev_yoy": [{"event_time": "2026-05-31", "available_time": "2026-06-10T09:00:00Z", "value": 99.0}]},
            "inside_lag": {"rev_yoy": [{"event_time": "2026-05-10", "available_time": "2026-05-10T00:00:00Z", "value": 99.0}]},
        }
        for label, features in bad_features.items():
            with self.subTest(label), self.assertRaises(SignalDecisionUnavailable):
                strategy(self._factor_rule_binding_with_features(features), "2026-05-15T06:05:00Z")
        # a valid observation still reaches the evaluator past the guard
        try:
            strategy(self._factor_rule_binding_with_features({"rev_yoy": [ok]}), "2026-05-15T06:05:00Z")
        except SignalDecisionUnavailable as exc:
            self.assertNotEqual(exc.code, "feature_not_in_catalogue")
