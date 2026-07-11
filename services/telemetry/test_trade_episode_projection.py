"""Unit and integration tests for Trade Episode Projections and replay logic."""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from services.telemetry.trade_episode_projection import TradeEpisodeProjectionStore
from services.telemetry.ingest_svc import TelemetryIngestService

class TestTradeEpisodeProjectionStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.proj_path = Path(self.tmp_dir.name) / "trade_episode_projections.json"
        self.events_path = Path(self.tmp_dir.name) / "trade_episode_events.json"
        self.store = TradeEpisodeProjectionStore(
            projections_path=self.proj_path,
            events_path=self.events_path,
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_opened_creates_projection(self) -> None:
        episode_id = str(uuid.uuid4())
        opened_event = {
            "event_id": str(uuid.uuid4()),
            "schema_version": "1.0",
            "event_type": "trade_episode.opened",
            "occurred_at": "2026-07-11T12:00:00Z",
            "ingested_at": "2026-07-11T12:00:05Z",
            "trace_id": str(uuid.uuid4()),
            "trade_episode_id": episode_id,
            "persona_id": "persona-macro",
            "environment": "paper",
            "producer": "trade-journal-service",
            "sequence_number": 1,
            "payload": {
                "strategy_id": "strategy-quant-01",
                "instrument_id": "SPY",
                "side": "long",
                "thesis": "Fed meeting catalyst",
                "requested_quantity": 100.0,
            }
        }

        proj = self.store.project_event(opened_event)
        self.assertIsNotNone(proj)
        self.assertEqual(proj["trade_episode_id"], episode_id)
        self.assertEqual(proj["status"], "open")
        self.assertEqual(proj["instrument_id"], "SPY")
        self.assertEqual(proj["side"], "long")
        self.assertEqual(proj["requested_quantity"], 100.0)
        self.assertEqual(proj["thesis"], "Fed meeting catalyst")
        self.assertEqual(proj["last_event_sequence"], 1)
        self.assertEqual(proj["coverage"]["state"], "complete")

    def test_out_of_order_sequencing(self) -> None:
        episode_id = str(uuid.uuid4())
        
        # 1. Update event with seq 2 (comes first!)
        updated_event = {
            "event_id": str(uuid.uuid4()),
            "schema_version": "1.0",
            "event_type": "trade_episode.updated",
            "occurred_at": "2026-07-11T12:05:00Z",
            "ingested_at": "2026-07-11T12:05:05Z",
            "trace_id": str(uuid.uuid4()),
            "trade_episode_id": episode_id,
            "persona_id": "persona-macro",
            "environment": "paper",
            "producer": "trade-journal-service",
            "sequence_number": 2,
            "payload": {
                "filled_quantity": 50.0,
                "vwap": 450.0,
                "status": "partially_filled",
            }
        }

        # 2. Opened event with seq 1 (comes second!)
        opened_event = {
            "event_id": str(uuid.uuid4()),
            "schema_version": "1.0",
            "event_type": "trade_episode.opened",
            "occurred_at": "2026-07-11T12:00:00Z",
            "ingested_at": "2026-07-11T12:00:05Z",
            "trace_id": str(uuid.uuid4()),
            "trade_episode_id": episode_id,
            "persona_id": "persona-macro",
            "environment": "paper",
            "producer": "trade-journal-service",
            "sequence_number": 1,
            "payload": {
                "strategy_id": "strategy-quant-01",
                "instrument_id": "SPY",
                "side": "long",
                "thesis": "Fed meeting catalyst",
                "requested_quantity": 100.0,
            }
        }

        # Project updated first
        proj = self.store.project_event(updated_event)
        self.assertEqual(proj["status"], "partially_filled")
        self.assertEqual(proj["filled_quantity"], 50.0)
        self.assertEqual(proj["instrument_id"], "unknown")  # Not open yet!
        self.assertEqual(proj["coverage"]["state"], "partial")

        # Project opened next
        proj2 = self.store.project_event(opened_event)
        # Should re-sort and apply seq 1 then seq 2
        self.assertEqual(proj2["instrument_id"], "SPY")
        self.assertEqual(proj2["requested_quantity"], 100.0)
        # Seq 2 updates should persist
        self.assertEqual(proj2["filled_quantity"], 50.0)
        self.assertEqual(proj2["vwap"], 450.0)
        self.assertEqual(proj2["status"], "partially_filled")
        self.assertEqual(proj2["coverage"]["state"], "complete")

    def test_as_of_queries(self) -> None:
        episode_id = str(uuid.uuid4())
        
        opened_event = {
            "event_id": str(uuid.uuid4()),
            "schema_version": "1.0",
            "event_type": "trade_episode.opened",
            "occurred_at": "2026-07-11T12:00:00Z",
            "ingested_at": "2026-07-11T12:00:05Z",
            "trace_id": str(uuid.uuid4()),
            "trade_episode_id": episode_id,
            "persona_id": "persona-macro",
            "environment": "paper",
            "producer": "trade-journal-service",
            "sequence_number": 1,
            "payload": {
                "strategy_id": "strategy-quant-01",
                "instrument_id": "SPY",
                "side": "long",
                "requested_quantity": 100.0,
            }
        }

        updated_event = {
            "event_id": str(uuid.uuid4()),
            "schema_version": "1.0",
            "event_type": "trade_episode.updated",
            "occurred_at": "2026-07-11T12:05:00Z",
            "ingested_at": "2026-07-11T12:05:05Z",
            "trace_id": str(uuid.uuid4()),
            "trade_episode_id": episode_id,
            "persona_id": "persona-macro",
            "environment": "paper",
            "producer": "trade-journal-service",
            "sequence_number": 2,
            "payload": {
                "filled_quantity": 50.0,
                "vwap": 450.0,
                "status": "partially_filled",
            }
        }

        self.store.project_event(opened_event)
        self.store.project_event(updated_event)

        # Query as of sequence 1
        proj_seq1 = self.store.get(episode_id, as_of_sequence=1)
        self.assertIsNotNone(proj_seq1)
        self.assertEqual(proj_seq1["filled_quantity"], 0.0)
        self.assertEqual(proj_seq1["status"], "open")

        # Query as of time 12:02:00
        proj_time1 = self.store.get(episode_id, as_of="2026-07-11T12:02:00Z")
        self.assertIsNotNone(proj_time1)
        self.assertEqual(proj_time1["filled_quantity"], 0.0)

        # Query as of sequence 2
        proj_seq2 = self.store.get(episode_id, as_of_sequence=2)
        self.assertEqual(proj_seq2["filled_quantity"], 50.0)

    def test_list_pagination_and_filters(self) -> None:
        # Create a few episodes
        ep1 = str(uuid.uuid4())
        ep2 = str(uuid.uuid4())
        ep3 = str(uuid.uuid4())

        self.store.project_event({
            "event_id": str(uuid.uuid4()),
            "schema_version": "1.0",
            "event_type": "trade_episode.opened",
            "occurred_at": "2026-07-11T12:00:00Z",
            "trade_episode_id": ep1,
            "persona_id": "persona-macro",
            "environment": "paper",
            "sequence_number": 1,
            "payload": {"strategy_id": "strat-a", "instrument_id": "SPY", "side": "long", "realized_pnl": 100.0}
        })
        self.store.project_event({
            "event_id": str(uuid.uuid4()),
            "schema_version": "1.0",
            "event_type": "trade_episode.opened",
            "occurred_at": "2026-07-11T13:00:00Z",
            "trade_episode_id": ep2,
            "persona_id": "persona-macro",
            "environment": "paper",
            "sequence_number": 1,
            "payload": {"strategy_id": "strat-b", "instrument_id": "AAPL", "side": "short", "realized_pnl": -50.0}
        })
        self.store.project_event({
            "event_id": str(uuid.uuid4()),
            "schema_version": "1.0",
            "event_type": "trade_episode.opened",
            "occurred_at": "2026-07-11T14:00:00Z",
            "trade_episode_id": ep3,
            "persona_id": "persona-micro",
            "environment": "live",
            "sequence_number": 1,
            "payload": {"strategy_id": "strat-a", "instrument_id": "SPY", "side": "long", "realized_pnl": 0.0}
        })

        # List all
        res = self.store.list()
        self.assertEqual(res["count"], 3)
        # Sorted by opened_at descending, so ep3 (14:00) then ep2 (13:00) then ep1 (12:00)
        self.assertEqual(res["projections"][0]["trade_episode_id"], ep3)
        self.assertEqual(res["projections"][1]["trade_episode_id"], ep2)
        self.assertEqual(res["projections"][2]["trade_episode_id"], ep1)

        # Filter by strategy
        res_strat = self.store.list(strategy_id="strat-a")
        self.assertEqual(res_strat["count"], 2)

        # Filter by environment
        res_env = self.store.list(environment="live")
        self.assertEqual(res_env["count"], 1)

        # Filter by outcome profitable
        res_prof = self.store.list(outcome="profitable")
        self.assertEqual(res_prof["count"], 1)
        self.assertEqual(res_prof["projections"][0]["trade_episode_id"], ep1)

        # Pagination with limit=1
        res_p1 = self.store.list(limit=1)
        self.assertEqual(len(res_p1["projections"]), 1)
        self.assertEqual(res_p1["projections"][0]["trade_episode_id"], ep3)
        self.assertIsNotNone(res_p1["next_cursor"])

        # Page 2 using cursor
        res_p2 = self.store.list(limit=1, cursor=res_p1["next_cursor"])
        self.assertEqual(len(res_p2["projections"]), 1)
        self.assertEqual(res_p2["projections"][0]["trade_episode_id"], ep2)

    def test_regular_telemetry_event_joins(self) -> None:
        episode_id = str(uuid.uuid4())
        
        # Opened event
        opened_event = {
            "event_id": str(uuid.uuid4()),
            "schema_version": "1.0",
            "event_type": "trade_episode.opened",
            "occurred_at": "2026-07-11T12:00:00Z",
            "trade_episode_id": episode_id,
            "persona_id": "persona-macro",
            "environment": "paper",
            "sequence_number": 1,
            "payload": {
                "strategy_id": "strategy-quant-01",
                "instrument_id": "SPY",
                "requested_quantity": 100.0,
            }
        }
        self.store.project_event(opened_event)

        # Simulated fill regular telemetry event (joined via trade_episode_id)
        fill_event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "paper_fill_simulated",
            "created_at": "2026-07-11T12:05:00Z",
            "deployment_stage": "paper",
            "execution_mode": "paper",
            "binding_id": str(uuid.uuid4()),
            "runtime_id": "rt-01",
            "capital_pool_id": "pool-a",
            "artifact_id": "art-01",
            "artifact_version": "1.0.0",
            "plan_id": "plan-01",
            "persona_capital_binding_id": "pcb-01",
            "target": {"strategy_id": "strategy-quant-01"},
            "metrics": {
                "fill_quantity": 50.0,
                "fill_price": 450.0,
                "fees": 2.0,
            },
            "metadata": {
                "trade_episode_id": episode_id,
            },
            "sequence_number": 2
        }
        
        proj = self.store.project_event(fill_event)
        self.assertIsNotNone(proj)
        self.assertEqual(proj["filled_quantity"], 50.0)
        self.assertEqual(proj["vwap"], 450.0)
        self.assertEqual(proj["fees"], 2.0)
        self.assertEqual(proj["status"], "partially_filled")

        # Position snapshot showing zero position -> closes the episode!
        pos_event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "position_snapshot",
            "created_at": "2026-07-11T12:10:00Z",
            "deployment_stage": "paper",
            "execution_mode": "paper",
            "binding_id": str(uuid.uuid4()),
            "runtime_id": "rt-01",
            "capital_pool_id": "pool-a",
            "artifact_id": "art-01",
            "artifact_version": "1.0.0",
            "plan_id": "plan-01",
            "persona_capital_binding_id": "pcb-01",
            "target": {"strategy_id": "strategy-quant-01"},
            "metrics": {
                "position_quantity": 0.0,
            },
            "metadata": {
                "trade_episode_id": episode_id,
            },
            "sequence_number": 3
        }

        proj2 = self.store.project_event(pos_event)
        self.assertEqual(proj2["status"], "closed")
        self.assertEqual(proj2["closed_at"], "2026-07-11T12:10:00Z")


if __name__ == "__main__":
    unittest.main()
