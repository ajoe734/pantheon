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
        # No fee evidence on open: fees stay unknown and coverage says so.
        self.assertIsNone(proj["fees"])
        self.assertEqual(proj["coverage"]["state"], "partial")
        self.assertIn("fees", proj["coverage"]["missing_refs"])

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
        self.assertIsNone(proj2["fees"])
        self.assertEqual(proj2["coverage"]["state"], "partial")

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

    def test_zero_quantity_fill_with_slippage_does_not_crash(self) -> None:
        episode_id = str(uuid.uuid4())

        # opened event carries an initial slippage value in its payload
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
                "slippage": 5.0,
            },
        }
        self.store.project_event(opened_event)

        # a fill-type event that reports zero quantity but a nonzero slippage
        # metric must not crash episode replay; it should be a no-op for
        # filled_quantity/vwap while still being coverage-safe.
        degenerate_fill_event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "paper_fill_simulated",
            "created_at": "2026-07-11T12:01:00Z",
            "trade_episode_id": episode_id,
            "metrics": {"fill_quantity": 0.0, "slippage": 2.0},
            "sequence_number": 2,
        }

        proj = self.store.project_event(degenerate_fill_event)
        self.assertIsNotNone(proj)
        self.assertEqual(proj["filled_quantity"], 0.0)
        self.assertEqual(proj["slippage"], 5.0)

        # a subsequent legitimate fill must still project correctly
        fill_event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "paper_fill_simulated",
            "created_at": "2026-07-11T12:02:00Z",
            "trade_episode_id": episode_id,
            "metrics": {"fill_quantity": 50.0, "fill_price": 450.0, "slippage": 3.0},
            "sequence_number": 3,
        }
        proj2 = self.store.project_event(fill_event)
        self.assertEqual(proj2["filled_quantity"], 50.0)
        self.assertEqual(proj2["vwap"], 450.0)

    def test_short_position_lifecycle_and_exact_flat_close(self) -> None:
        episode_id = str(uuid.uuid4())
        tenant_id = "tenant-alpha"
        persona_id = "persona-short"
        instrument_id = "TSLA"

        # 1. Order submitted for short
        submit_event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "order_submitted",
            "occurred_at": "2026-07-11T13:00:00Z",
            "trade_episode_id": episode_id,
            "tenant_id": tenant_id,
            "persona_id": persona_id,
            "instrument_id": instrument_id,
            "metrics": {"order_quantity": 100.0, "order_price": 200.0},
            "metadata": {"side": "short"},
            "sequence_number": 1,
        }
        proj = self.store.project_event(submit_event)
        self.assertEqual(proj["side"], "short")
        self.assertEqual(proj["status"], "submitted")

        # 2. First short fill (negative quantity)
        fill1 = {
            "event_id": str(uuid.uuid4()),
            "event_type": "paper_fill_simulated",
            "occurred_at": "2026-07-11T13:01:00Z",
            "trade_episode_id": episode_id,
            "tenant_id": tenant_id,
            "persona_id": persona_id,
            "instrument_id": instrument_id,
            "metrics": {"fill_quantity": -50.0, "fill_price": 200.0},
            "metadata": {"side": "short"},
            "sequence_number": 2,
        }
        proj = self.store.project_event(fill1)
        self.assertEqual(proj["side"], "short")
        self.assertEqual(proj["filled_quantity"], 50.0)
        self.assertEqual(proj["vwap"], 200.0)
        self.assertEqual(proj["status"], "partially_filled")

        # 3. Position snapshot with negative position qty stays open
        snap1 = {
            "event_id": str(uuid.uuid4()),
            "event_type": "position_snapshot",
            "occurred_at": "2026-07-11T13:01:05Z",
            "trade_episode_id": episode_id,
            "tenant_id": tenant_id,
            "persona_id": persona_id,
            "instrument_id": instrument_id,
            "metrics": {"position_qty": -50.0, "price": 200.0},
            "sequence_number": 3,
        }
        proj = self.store.project_event(snap1)
        self.assertIsNone(proj["closed_at"])
        self.assertNotEqual(proj["status"], "closed")

        # 4. Scale-in short fill
        fill2 = {
            "event_id": str(uuid.uuid4()),
            "event_type": "paper_fill_simulated",
            "occurred_at": "2026-07-11T13:02:00Z",
            "trade_episode_id": episode_id,
            "tenant_id": tenant_id,
            "persona_id": persona_id,
            "instrument_id": instrument_id,
            "metrics": {"fill_quantity": -50.0, "fill_price": 204.0},
            "metadata": {"side": "short"},
            "sequence_number": 4,
        }
        proj = self.store.project_event(fill2)
        self.assertEqual(proj["filled_quantity"], 100.0)
        self.assertEqual(proj["vwap"], 202.0)
        self.assertEqual(proj["status"], "open")

        # 5. Order rejection does not close open episode
        reject_event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "order_rejection",
            "occurred_at": "2026-07-11T13:02:30Z",
            "trade_episode_id": episode_id,
            "tenant_id": tenant_id,
            "persona_id": persona_id,
            "instrument_id": instrument_id,
            "metadata": {"reject_reason": "bracket_risk_limit_exceeded"},
            "sequence_number": 5,
        }
        proj = self.store.project_event(reject_event)
        self.assertEqual(proj["status"], "open")
        self.assertEqual(len(proj["rejects"]), 1)
        self.assertEqual(proj["rejects"][0]["reason"], "bracket_risk_limit_exceeded")

        # 6. Degenerate position snapshot with missing/invalid qty does not close episode
        degen_snap = {
            "event_id": str(uuid.uuid4()),
            "event_type": "position_snapshot",
            "occurred_at": "2026-07-11T13:03:00Z",
            "trade_episode_id": episode_id,
            "tenant_id": tenant_id,
            "persona_id": persona_id,
            "instrument_id": instrument_id,
            "metrics": {"position_qty": None},
            "sequence_number": 6,
        }
        proj = self.store.project_event(degen_snap)
        self.assertEqual(proj["status"], "open")
        self.assertIsNone(proj["closed_at"])

        # 7. Exact flat close via position snapshot with 0.0
        flat_snap = {
            "event_id": str(uuid.uuid4()),
            "event_type": "position_snapshot",
            "occurred_at": "2026-07-11T13:05:00Z",
            "trade_episode_id": episode_id,
            "tenant_id": tenant_id,
            "persona_id": persona_id,
            "instrument_id": instrument_id,
            "metrics": {"position_qty": 0.0, "price": 195.0},
            "sequence_number": 7,
        }
        proj = self.store.project_event(flat_snap)
        self.assertEqual(proj["status"], "closed")
        self.assertEqual(proj["closed_at"], "2026-07-11T13:05:00Z")

    def _fee_fill(self, episode_id, seq, fees=..., **extra_metrics):
        metrics = {"fill_quantity": 10.0, "fill_price": 100.0, **extra_metrics}
        if fees is not ...:
            metrics["fees"] = fees
        return {
            "event_id": str(uuid.uuid4()),
            "event_type": "paper_fill_simulated",
            "created_at": f"2026-07-11T12:0{seq}:00Z",
            "deployment_stage": "paper",
            "metrics": metrics,
            "metadata": {"trade_episode_id": episode_id},
            "sequence_number": seq,
        }

    def _fee_open(self, episode_id, **payload):
        return {
            "event_id": str(uuid.uuid4()),
            "event_type": "trade_episode.opened",
            "occurred_at": "2026-07-11T12:00:00Z",
            "trade_episode_id": episode_id,
            "persona_id": "persona-macro",
            "environment": "paper",
            "sequence_number": 1,
            "payload": {"strategy_id": "s", "instrument_id": "SPY", **payload},
        }

    def test_missing_fee_evidence_is_null_not_zero(self) -> None:
        absent, zero, nonzero = (str(uuid.uuid4()) for _ in range(3))
        self.assertIsNone(self.store.project_event(self._fee_fill(absent, 2))["fees"])
        self.assertEqual(self.store.project_event(self._fee_fill(zero, 2, fees=0.0))["fees"], 0.0)
        self.assertEqual(self.store.project_event(self._fee_fill(nonzero, 2, fees=2.5))["fees"], 2.5)
        absent_proj = self.store.get(absent)
        self.assertIn("fees", absent_proj["coverage"]["missing_refs"])
        self.assertNotIn("fees", self.store.get(zero)["coverage"]["missing_refs"])
        self.assertNotIn("fees", self.store.get(nonzero)["coverage"]["missing_refs"])

        opened_absent, opened_zero = str(uuid.uuid4()), str(uuid.uuid4())
        self.assertIsNone(self.store.project_event(self._fee_open(opened_absent))["fees"])
        self.assertEqual(self.store.project_event(self._fee_open(opened_zero, fees=0))["fees"], 0.0)

    def test_invalid_fee_evidence_fails_closed_to_null(self) -> None:
        for bad in (-1.0, float("nan"), float("inf"), "abc"):
            episode_id = str(uuid.uuid4())
            proj = self.store.project_event(self._fee_fill(episode_id, 2, fees=bad))
            self.assertIsNone(proj["fees"], bad)
            self.assertIn("fees", proj["coverage"]["missing_refs"])

    def test_boolean_fee_evidence_is_not_coerced_to_numeric(self) -> None:
        for bad in (False, True):
            opened = str(uuid.uuid4())
            proj = self.store.project_event(self._fee_open(opened, fees=bad))
            self.assertIsNone(proj["fees"], bad)
            self.assertIn("fees", proj["coverage"]["missing_refs"])

            native = str(uuid.uuid4())
            proj = self.store.project_event(self._fee_fill(native, 2, fees=bad))
            self.assertIsNone(proj["fees"], bad)
            self.assertIn("fees", proj["coverage"]["missing_refs"])

            updated = str(uuid.uuid4())
            self.store.project_event(self._fee_open(updated, fees=3.0))
            update = dict(
                self._fee_open(updated, fees=bad),
                event_id=str(uuid.uuid4()),
                event_type="trade_episode.updated",
                sequence_number=2,
            )
            proj = self.store.project_event(update)
            self.assertIsNone(proj["fees"], bad)
            self.assertIn("fees", proj["coverage"]["missing_refs"])

    def test_mixed_missing_and_known_fill_fees_not_advertised_as_total(self) -> None:
        for order in ((1.5, ...), (..., 1.5)):
            episode_id = str(uuid.uuid4())
            proj = None
            for i, fee in enumerate(order, start=2):
                proj = self.store.project_event(self._fee_fill(episode_id, i, fees=fee))
            self.assertIsNone(proj["fees"])
            self.assertEqual(proj["coverage"]["state"], "partial")
            self.assertIn("fees", proj["coverage"]["missing_refs"])

        # Known fees across all fills still accumulate; explicit zero is real.
        episode_id = str(uuid.uuid4())
        self.store.project_event(self._fee_fill(episode_id, 2, fees=0.0))
        proj = self.store.project_event(self._fee_fill(episode_id, 3, fees=2.0))
        self.assertEqual(proj["fees"], 2.0)

    def test_authoritative_fee_update_resolves_missing_and_survives_reopen(self) -> None:
        episode_id = str(uuid.uuid4())
        self.store.project_event(self._fee_open(episode_id))
        self.store.project_event(self._fee_fill(episode_id, 2))
        self.assertIsNone(self.store.get(episode_id)["fees"])
        update = {
            "event_id": str(uuid.uuid4()),
            "event_type": "trade_episode.updated",
            "occurred_at": "2026-07-11T12:09:00Z",
            "trade_episode_id": episode_id,
            "persona_id": "persona-macro",
            "environment": "paper",
            "sequence_number": 3,
            "payload": {"fees": 4.25},
        }
        proj = self.store.project_event(update)
        self.assertEqual(proj["fees"], 4.25)
        self.assertNotIn("fees", proj["coverage"]["missing_refs"])
        reopened = TradeEpisodeProjectionStore(projections_path=self.proj_path, events_path=self.events_path)
        self.assertEqual(reopened.get(episode_id)["fees"], 4.25)
        # Authoritative null update clears the fee and is not hidden by coverage.
        bad = dict(update, event_id=str(uuid.uuid4()), sequence_number=4, payload={"fees": None})
        cleared = self.store.project_event(bad)
        self.assertIsNone(cleared["fees"])
        self.assertIn("fees", cleared["coverage"]["missing_refs"])
        self.assertEqual(cleared["coverage"]["state"], "partial")
        reopened = TradeEpisodeProjectionStore(projections_path=self.proj_path, events_path=self.events_path)
        self.assertIsNone(reopened.get(episode_id)["fees"])
        self.assertIsNone(reopened.get(episode_id, as_of_sequence=4)["fees"])

    def test_legacy_cached_zero_without_fee_evidence_reopens_unknown(self) -> None:
        episode_id = str(uuid.uuid4())
        self.store.project_event(self._fee_open(episode_id))
        self.store.project_event(self._fee_fill(episode_id, 2))
        # Simulate baseline cache: fabricated 0.0 persisted beside fee-less raw events.
        cache = json.loads(Path(self.proj_path).read_text(encoding="utf-8"))
        cache[episode_id]["fees"] = 0.0
        cache[episode_id]["coverage"] = {**cache[episode_id]["coverage"], "state": "complete", "missing_refs": []}
        Path(self.proj_path).write_text(json.dumps(cache), encoding="utf-8")
        reopened = TradeEpisodeProjectionStore(projections_path=self.proj_path, events_path=self.events_path)
        ordinary = reopened.get(episode_id)
        replay = reopened.get(episode_id, as_of_sequence=2)
        self.assertIsNone(ordinary["fees"])
        self.assertEqual(ordinary["fees"], replay["fees"])
        self.assertEqual(ordinary["coverage"], replay["coverage"])

    def test_cache_only_legacy_zero_without_events_reopens_unknown(self) -> None:
        episode_id = str(uuid.uuid4())
        cov = {"state": "partial", "missing_refs": ["opened_event", "attribution"]}
        cache = {
            episode_id: {"trade_episode_id": episode_id, "fees": 0.0, "coverage": dict(cov)},
            "nonzero": {"trade_episode_id": "nonzero", "fees": 2.5, "coverage": dict(cov)},
        }
        Path(self.proj_path).write_text(json.dumps(cache), encoding="utf-8")
        reopened = TradeEpisodeProjectionStore(projections_path=self.proj_path)
        got = reopened.get(episode_id)
        self.assertIsNone(got["fees"])
        self.assertIn("fees", got["coverage"]["missing_refs"])
        self.assertEqual(reopened.get("nonzero")["fees"], 2.5)
        # Stored file is not rewritten by reading.
        self.assertEqual(json.loads(Path(self.proj_path).read_text(encoding="utf-8")), cache)

    def test_null_fee_persists_and_rebuilds_after_reopen(self) -> None:
        episode_id = str(uuid.uuid4())
        self.store.project_event(self._fee_fill(episode_id, 2))
        reopened = TradeEpisodeProjectionStore(projections_path=self.proj_path, events_path=self.events_path)
        self.assertIsNone(reopened.get(episode_id)["fees"])
        # Next event rebuilds from the persisted canonical events: still honest.
        proj = reopened.project_event(self._fee_fill(episode_id, 3, fees=1.0))
        self.assertIsNone(proj["fees"])
        self.assertIn("fees", proj["coverage"]["missing_refs"])
        # Duplicate delivery of the same event does not change the answer.
        dup = self._fee_fill(episode_id, 3, fees=1.0)
        self.assertIsNone(reopened.project_event(dup)["fees"])

    def test_schema_allows_null_fees_but_forbids_negative(self) -> None:
        schema = json.loads(
            (Path(__file__).parent / "trade_episode_projection.schema.json").read_text(encoding="utf-8")
        )
        fees = schema["properties"]["fees"]
        self.assertEqual(sorted(fees["type"]), ["null", "number"])
        self.assertEqual(fees["minimum"], 0)

    def test_absent_pnl_remains_none_and_isolation_guards(self) -> None:
        episode_id = str(uuid.uuid4())
        fill_event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "paper_fill_simulated",
            "occurred_at": "2026-07-11T14:00:00Z",
            "trade_episode_id": episode_id,
            "tenant_id": "tenant-corp",
            "persona_id": "persona-quant",
            "instrument_id": "NVDA",
            "metrics": {"fill_quantity": 10.0, "fill_price": 120.0},
            "sequence_number": 1,
        }
        proj = self.store.project_event(fill_event)
        self.assertIsNone(proj["realized_pnl"])
        self.assertIn("attribution", proj["coverage"]["missing_refs"])

        # Isolation violation: mismatching tenant
        foreign_event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "position_snapshot",
            "occurred_at": "2026-07-11T14:01:00Z",
            "trade_episode_id": episode_id,
            "tenant_id": "tenant-other",
            "persona_id": "persona-quant",
            "instrument_id": "NVDA",
            "metrics": {"position_qty": 10.0},
            "sequence_number": 2,
        }
        rejected = self.store.project_event(foreign_event)
        self.assertIsNone(rejected)
        proj_after = self.store.get(episode_id)
        self.assertIsNotNone(proj_after)
        self.assertEqual(proj_after["tenant_id"], "tenant-corp")
        self.assertEqual(len(proj_after["position_snapshot_refs"]), 0)

    def test_missing_tenant_fails_closed_and_metadata_cannot_override(self) -> None:
        episode_id = str(uuid.uuid4())
        fill1 = {
            "event_id": str(uuid.uuid4()),
            "event_type": "paper_fill_simulated",
            "occurred_at": "2026-07-11T14:00:00Z",
            "trade_episode_id": episode_id,
            "tenant_id": "tenant-corp",
            "persona_id": "persona-quant",
            "instrument_id": "NVDA",
            "metrics": {"fill_quantity": 10.0, "fill_price": 120.0},
            "sequence_number": 1,
        }
        proj = self.store.project_event(fill1)
        self.assertIsNotNone(proj)
        self.assertEqual(proj["status"], "open")
        self.assertEqual(proj["tenant_id"], "tenant-corp")

        # Missing tenant fails closed
        missing_tenant_ev = {
            "event_id": str(uuid.uuid4()),
            "event_type": "paper_fill_simulated",
            "occurred_at": "2026-07-11T14:01:00Z",
            "trade_episode_id": episode_id,
            "metrics": {"fill_quantity": -5.0, "fill_price": 125.0},
            "sequence_number": 2,
        }
        self.assertIsNone(self.store.project_event(missing_tenant_ev))

        # Metadata-only tenant fails closed
        meta_only_tenant_ev = {
            "event_id": str(uuid.uuid4()),
            "event_type": "paper_fill_simulated",
            "occurred_at": "2026-07-11T14:02:00Z",
            "trade_episode_id": episode_id,
            "metadata": {"tenant_id": "tenant-corp"},
            "metrics": {"fill_quantity": -5.0, "fill_price": 125.0},
            "sequence_number": 3,
        }
        self.assertIsNone(self.store.project_event(meta_only_tenant_ev))

        # Projection remains unchanged
        proj_final = self.store.get(episode_id)
        self.assertIsNotNone(proj_final)
        self.assertEqual(proj_final["status"], "open")
        self.assertEqual(proj_final["filled_quantity"], 10.0)
        self.assertEqual(len(proj_final["fill_ids"]), 1)

    def test_reversal_cross_tenant_isolation_fails_closed(self) -> None:
        foreign_ep = str(uuid.uuid4())
        self.store.project_event({
            "event_id": "owned-first",
            "event_type": "paper_fill_simulated",
            "trade_episode_id": foreign_ep,
            "tenant_id": "tenant-b",
            "persona_id": "persona-b",
            "instrument_id": "SPY",
            "metrics": {"fill_quantity": 10.0, "fill_price": 100.0},
        })
        self.assertEqual(self.store.get(foreign_ep)["status"], "open")

        new_ep = str(uuid.uuid4())
        res = self.store.project_event({
            "event_id": "owned-reversal",
            "event_type": "paper_fill_simulated",
            "trade_episode_id": new_ep,
            "tenant_id": "tenant-a",
            "persona_id": "persona-a",
            "instrument_id": "SPY",
            "metrics": {"fill_quantity": -20.0, "fill_price": 100.0},
            "metadata": {
                "reversal": {
                    "closed_episode_id": foreign_ep,
                    "close_quantity": -10.0,
                    "opened_episode_id": new_ep,
                    "open_quantity": -10.0,
                }
            },
        })
        self.assertIsNone(res)
        self.assertEqual(self.store.get(foreign_ep)["status"], "open")
        self.assertEqual(self.store.get(foreign_ep)["fill_ids"], ["owned-first"])
        self.assertIsNone(self.store.get(new_ep))

    def test_reversal_closing_fill_without_fee_leaves_closed_leg_unknown(self) -> None:
        old_ep, new_ep = str(uuid.uuid4()), str(uuid.uuid4())
        base = {"tenant_id": "tenant-a", "persona_id": "persona-a", "instrument_id": "SPY"}
        self.store.project_event({
            **base, "event_id": "open-known-fee", "event_type": "paper_fill_simulated",
            "trade_episode_id": old_ep,
            "metrics": {"fill_quantity": 10.0, "fill_price": 100.0, "fees": 2.5},
        })
        self.assertEqual(self.store.get(old_ep)["fees"], 2.5)
        self.store.project_event({
            **base, "event_id": "reverse-no-fee", "event_type": "paper_fill_simulated",
            "trade_episode_id": new_ep,
            "metrics": {"fill_quantity": -20.0, "fill_price": 100.0},
            "metadata": {"reversal": {
                "closed_episode_id": old_ep, "close_quantity": -10.0,
                "opened_episode_id": new_ep, "open_quantity": -10.0,
            }},
        })
        old, new = self.store.get(old_ep), self.store.get(new_ep)
        self.assertEqual(old["status"], "closed")
        self.assertIsNone(old["fees"])
        self.assertIn("fees", old["coverage"]["missing_refs"])
        self.assertIsNone(new["fees"])
        self.assertIn("fees", new["coverage"]["missing_refs"])


if __name__ == "__main__":
    unittest.main()
