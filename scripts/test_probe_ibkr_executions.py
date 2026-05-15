#!/usr/bin/env python3
from __future__ import annotations

import unittest

import probe_ibkr_executions as executions


class ProbeIbkrExecutionsTest(unittest.TestCase):
    def test_matches_target_allows_expected_execution(self) -> None:
        execution = {
            "account": "U19859952",
            "symbol": "AAPL",
            "order_id": 1,
            "perm_id": 204599504,
        }

        self.assertTrue(
            executions.matches_target(
                execution,
                account="U19859952",
                symbol="aapl",
                order_id=1,
                perm_id=204599504,
            )
        )

    def test_matches_target_rejects_wrong_perm_id(self) -> None:
        execution = {
            "account": "U19859952",
            "symbol": "AAPL",
            "order_id": 1,
            "perm_id": 204599504,
        }

        self.assertFalse(
            executions.matches_target(
                execution,
                account="U19859952",
                symbol="AAPL",
                order_id=1,
                perm_id=999,
            )
        )

    def test_summarize_fill_status_reports_shares(self) -> None:
        summary = executions.summarize_fill_status(
            [
                {"shares": "0.25"},
                {"shares": 0.75},
            ]
        )

        self.assertEqual(summary["fill_status"], "fills_observed")
        self.assertEqual(summary["matching_execution_count"], 2)
        self.assertEqual(summary["matching_shares"], 1.0)

    def test_summarize_fill_status_reports_no_matching_executions(self) -> None:
        summary = executions.summarize_fill_status([])

        self.assertEqual(summary["fill_status"], "no_matching_executions")
        self.assertEqual(summary["matching_execution_count"], 0)
        self.assertEqual(summary["matching_shares"], 0.0)


if __name__ == "__main__":
    unittest.main()
