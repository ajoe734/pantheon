"""Real TWSE recorded bars through paper, vectorbt and runtime entry points."""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy, PaperSignalProducer
from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.test_current_artifact_signal import (
    _binding, _identity, _runtime_binding, _RuntimeManager, _Telemetry,
)
from services.registry.test_portfolio_envelope import envelope, member_artifact
from services.registry.strategy_artifact import evaluate_strategy_view
from services.research.vectorbt.adapter.vectorbt_adapter import (
    BacktestConfig, GovernedVectorbtInputAdapter, VectorbtBackend,
    _artifact_action_sequence, _artifact_view_strength, _bar_features_for_index,
    run_vectorbt_workflow,
)
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore


EVIDENCE = Path(__file__).resolve().parents[3] / "docs/deployment/evidence/PERSONA-PORTFOLIO-COMBINATION-20261112"


def recorded_dataset():
    records = []
    for symbol in ("2330", "2317"):
        for month in ("202410", "202411"):
            payload = json.loads((EVIDENCE / f"twse-{symbol}-{month}.json").read_text())
            assert payload["stat"] == "OK"
            for row in payload["data"]:
                y, m, d = map(int, row[0].split("/"))
                number = lambda value: float(value.replace(",", ""))
                records.append({"instrument": symbol + ".TWSE", "date": f"{y+1911:04}-{m:02}-{d:02}",
                    "open": number(row[3]), "high": number(row[4]), "low": number(row[5]),
                    "close": number(row[6]), "volume": number(row[1])})
    return {"dataset_id": "recorded:twse:202410-202411", "strategy_id": "persona-portfolio",
            "source_dataset_refs": ["recorded:twse:STOCK_DAY:202410-202411:2330:2317"],
            "data_frequency": "daily", "records": records}


def recorded_binding(artifact, prepared, index):
    binding = _binding(artifact, binding_id="rb-portfolio-parity")
    snapshots = {}
    for instrument in prepared.instruments:
        features, date = _bar_features_for_index(prepared, instrument, index)
        symbol = instrument.replace(".TWSE", ".TW")
        snapshots[symbol] = {"symbol": symbol, "closes": features["close"], "features": features,
            "event_time": date + "T05:30:00Z", "observed_at": date + "T05:30:00Z",
            "source_ref": "recorded:twse:STOCK_DAY:202410-202411:" + symbol}
    binding.pop("market_input", None)
    binding["market_inputs"] = snapshots
    return binding


class PortfolioParityTests(unittest.TestCase):
    def setUp(self):
        self.dataset = recorded_dataset()
        self.prepared = GovernedVectorbtInputAdapter().prepare(self.dataset)
        symbols = ["2330.TW", "2317.TW"]
        self.long = member_artifact(symbols=symbols)
        self.short = member_artifact("short", symbols=symbols, bearish=True)
        self.portfolio = envelope([
            {"member_id": "long", "artifact": self.long, "risk_weight": .6},
            {"member_id": "short", "artifact": self.short, "risk_weight": .4},
            {"member_id": "trial", "artifact": self.short, "risk_weight": 0, "virtual_only": True},
        ])

    def test_recorded_bars_match_paper_actions_strengths_and_real_vectorbt(self):
        actions = _artifact_action_sequence(self.prepared, self.portfolio)
        _, strengths = _artifact_view_strength(self.prepared, self.portfolio)
        producer_strategy = CurrentArtifactStrategy(expression_layer=False)
        for index in range(1, self.prepared.bars_per_instrument["2330.TWSE"]):
            binding = recorded_binding(self.portfolio, self.prepared, index)
            decisions = producer_strategy(binding, "2026-11-12T05:30:00Z")["portfolio_decisions"]
            for decision in decisions:
                instrument = decision["symbol"].replace(".TW", ".TWSE") if decision["symbol"].endswith(".TW") else decision["symbol"]
                paper_view = decision["metadata"]["portfolio_net_view"]
                self.assertEqual(decision["action"], actions[instrument][index])
                self.assertEqual(paper_view["strength"], strengths[instrument][index])
                single_view = evaluate_strategy_view(self.long, binding["market_inputs"][instrument.replace(".TWSE", ".TW")]["closes"])
                expected = .2 if single_view["direction"] == "LONG" else -.2
                self.assertAlmostEqual(paper_view["strength"], abs(expected))
                self.assertEqual(paper_view["direction"], single_view["direction"])
                self.assertEqual(len(decision["metadata"]["portfolio_member_views"]), 3)
        with patch.dict(os.environ, {"PANTHEON_VECTORBT_BACKEND": "real"}):
            result = run_vectorbt_workflow(self.dataset, backend=VectorbtBackend(),
                config=BacktestConfig(strategy_artifact=self.portfolio))
        self.assertEqual(result.backtest_result.backend, "vectorbt_portfolio")
        self.assertEqual(set(result.backtest_result.per_instrument_metrics), set(self.prepared.instruments))

    def test_runtime_virtual_only_members_and_summary_without_account_fills(self):
        portfolio = copy.deepcopy(self.portfolio)
        portfolio["strategy_logic"]["members"][0]["risk_weight"] = .5
        portfolio["strategy_logic"]["members"][1]["risk_weight"] = .5
        store, telemetry = InMemoryPendingSignalStore(), _Telemetry()
        producer = PaperSignalProducer(lambda _: store, CurrentArtifactStrategy(expression_layer=False))
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with tempfile.TemporaryDirectory() as directory:
            binding = recorded_binding(portfolio, self.prepared, 1)
            service = PaperRuntimeService(store=store, identity=_identity(binding),
                runtime_manager_client=_RuntimeManager(_runtime_binding(binding)), telemetry_emitter=telemetry,
                lifecycle_outbox_path=Path(directory) / "lifecycle.json")
            for index in range(1, 5):
                binding = recorded_binding(portfolio, self.prepared, index)
                self.assertEqual(producer.produce(binding, now), 2)
                for _ in range(3):
                    snapshot = service.drain_once()
                self.assertEqual(snapshot["status"], "ok", snapshot)
            books = snapshot["paper_state"]["member_virtual_books"]
            self.assertEqual(len(books), 3)
            self.assertTrue(all(len(book["underlyings"]) == 2 for book in books))
            self.assertTrue(all(book["observation_count"] == 3 for book in books))
            self.assertTrue(any(book["virtual_only"] and book["total_return"] != 0 for book in books))
            self.assertFalse(any(e["event_type"] == "paper_fill_simulated" for e in telemetry.events))
            # Replay the same signal after process replacement: same bars never
            # create a second virtual return, even before consumer deduplication.
            replacement = PaperRuntimeService(store=store, identity=_identity(binding),
                runtime_manager_client=_RuntimeManager(_runtime_binding(binding)), telemetry_emitter=telemetry,
                lifecycle_outbox_path=Path(directory) / "lifecycle.json")
            self.assertEqual(replacement.snapshot()["paper_state"]["member_virtual_books"], books)
            for decision in CurrentArtifactStrategy(expression_layer=False)(binding, now)["portfolio_decisions"]:
                # A producer restart still uses the store's completed decision fence.
                self.assertIn(decision["metadata"]["portfolio_net_view"]["action"], {"HOLD"})
            heartbeat = {"event_id": "hb-portfolio", "event_type": "heartbeat", "created_at": now,
                "tenant_id": "default", "runtime_id": binding["runtime_id"], "deployment_stage": "paper",
                "binding_id": binding["binding_id"], "metadata": {"member_virtual_books": books}, "metrics": {}}
            summary = RuntimeSummaryProjectionStore().project_event(heartbeat)
            self.assertEqual(summary["member_virtual_books"], books)

    def test_missing_underlying_or_corrupt_member_queues_nothing(self):
        binding = recorded_binding(self.portfolio, self.prepared, 2)
        del binding["market_inputs"]["2317.TW"]
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(lambda _: store, CurrentArtifactStrategy(expression_layer=False))
        self.assertEqual(producer.produce(binding, "2026-11-12T05:30:00Z"), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertIn("market_input_missing", producer.degraded_bindings[binding["binding_id"]])
        bad = copy.deepcopy(self.portfolio)
        bad["strategy_logic"]["members"][0]["checksum"] = "sha256:" + "0" * 64
        binding = recorded_binding(bad, self.prepared, 2)
        self.assertEqual(producer.produce(binding, "2026-11-12T05:30:00Z"), 0)
        self.assertEqual(store.queue_depth(), 0)

