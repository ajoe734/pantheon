#!/usr/bin/env python3
from __future__ import annotations

import unittest

import run_ibkr_live_order_cancel as live_cancel


class RunIbkrLiveOrderCancelTest(unittest.TestCase):
    def test_validate_request_shape_allows_only_minimal_aapl_order(self) -> None:
        live_cancel.validate_request_shape("U123", "AAPL", 1, 120.0)

        with self.assertRaisesRegex(ValueError, "only AAPL"):
            live_cancel.validate_request_shape("U123", "MSFT", 1, 120.0)
        with self.assertRaisesRegex(ValueError, "quantity"):
            live_cancel.validate_request_shape("U123", "AAPL", 2, 120.0)
        with self.assertRaisesRegex(ValueError, "positive"):
            live_cancel.validate_request_shape("U123", "AAPL", 1, 0.0)

    def test_latest_status_prefers_last_matching_state(self) -> None:
        events = [
            {"status": "PendingSubmit"},
            {"status": "Submitted"},
            {"status": "Cancelled"},
        ]

        self.assertEqual(live_cancel.latest_status(events, live_cancel.ACK_STATES), {"status": "Submitted"})
        self.assertEqual(live_cancel.latest_status(events, live_cancel.CANCEL_STATES), {"status": "Cancelled"})
        self.assertIsNone(live_cancel.latest_status([{"status": "PreSubmitted"}], live_cancel.CANCEL_STATES))


if __name__ == "__main__":
    unittest.main()
