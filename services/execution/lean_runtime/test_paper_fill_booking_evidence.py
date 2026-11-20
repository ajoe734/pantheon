"""Paper fill events publish the before/after position and cash they booked."""

import unittest
from unittest.mock import patch

from services.execution.lean_runtime.paper_runtime import PaperExecutionAlgorithm

_POLICY = {"risk_policy_id": "rp-test", "status": "active", "gross_limit": 10.0,
           "net_limit": 10.0, "max_leverage": 10.0}


class CashShareFillBookingEvidenceTest(unittest.TestCase):
    def test_sell_fill_carries_before_after_position_and_cash(self):
        events = []
        algo = PaperExecutionAlgorithm(
            initial_cash=100_000.0, event_sink=events.append, risk_policy=_POLICY
        )
        algo._holding("2330.TW").Quantity = 1
        algo.SetCurrentSignalContext(
            {"market_price": 2215.0, "market_price_as_of": "2026-11-16T06:52:00Z",
             "market_price_source": "source-ingest://snapshots/test-2330"}
        )
        order = {"order_id": "c771bc1adfff4f2cbbd709face8c6378", "fill_price": 2215.0,
                 "fill_qty": 1, "side": "sell", "status": "filled"}
        with patch.object(PaperExecutionAlgorithm, "_post_broker_paper_order",
                          staticmethod(lambda url, payload: order)):
            algo.SubmitTaiwanBrokerOrder(
                "2330.TW", signal_id="s1", side="sell", quantity=1,
                quantity_type="SHARES", action="SELL", order_type="MARKET",
            )
        meta = events[0].metadata
        self.assertEqual(meta["position_quantity_before"], 1.0)
        self.assertEqual(meta["position_quantity_after"], 0.0)
        self.assertEqual(meta["cash_before"], 100_000.0)
        self.assertAlmostEqual(
            meta["cash_after"], 100_000.0 + 2215.0 - meta["fee"] - meta["tax"]
        )
        self.assertAlmostEqual(meta["cash_after"], algo._cash)
        self.assertEqual(meta["broker_order_id"], "c771bc1adfff4f2cbbd709face8c6378")
        self.assertEqual(meta["fill_quantity"], 1.0)
        self.assertEqual(meta["fill_price"], 2215.0)


class FuturesFillBookingEvidenceTest(unittest.TestCase):
    INST = "ssf:SRF:202611"

    def test_futures_fill_carries_before_after_position(self):
        events = []
        algo = PaperExecutionAlgorithm(
            initial_cash=5_000_000.0, event_sink=events.append, risk_policy=_POLICY,
            persona_binding_id="persona-c6c25b85060b0f642c64", in_process_broker=True,
        )
        for sig, side, qty in (("s1", "buy", 2.0), ("s2", "sell", 1.0)):
            algo.SetCurrentSignalContext(
                {"persona_capital_binding_id": "persona-c6c25b85060b0f642c64",
                 "instrument_id": self.INST, "kind": "stock_future", "contract": "SRF",
                 "contract_month": "202611", "multiplier": 2000.0,
                 "price_basis": "futures_daily_settlement_prev_session",
                 "futures_settlement_price": 97.1, "price_date": "2026-11-09",
                 "trade_date": "2026-11-10"}
            )
            algo.SubmitTaiwanBrokerOrder(
                "0050.TW", signal_id=sig, side=side, quantity=qty,
                quantity_type="CONTRACTS", action=side.upper(),
            )
        fills = [e for e in events if e.event_type == "paper_fill_simulated"]
        self.assertEqual(
            [(f.metadata["position_quantity_before"], f.metadata["position_quantity_after"])
             for f in fills],
            [(0.0, 2.0), (2.0, 1.0)],
        )
        self.assertNotIn("cash_before", fills[0].metadata)
