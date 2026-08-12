from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import concurrency


class MaxParallelTests(unittest.TestCase):
    def test_missing_capacity_fails_closed(self) -> None:
        cfg = {"agents": {"claude": {}}}
        self.assertEqual(concurrency.max_parallel(cfg, "claude", settings={}), 0)

    def test_explicit_target_shape_max_parallel_field(self) -> None:
        cfg = {"agents": {"claude": {"max_parallel": 3}}}
        self.assertEqual(concurrency.max_parallel(cfg, "claude", settings={}), 3)

    def test_zero_disables_agent(self) -> None:
        cfg = {"agents": {"claude": {"max_parallel": 0}}}
        self.assertEqual(concurrency.max_parallel(cfg, "claude", settings={}), 0)


class AccountLimitTests(unittest.TestCase):
    def test_missing_schema_closes_account(self) -> None:
        self.assertEqual(concurrency.account_limit("grp", settings={}), 0)

    def test_empty_schema_closes_account(self) -> None:
        self.assertEqual(
            concurrency.account_limit("grp", settings={"max_concurrent_per_account": ""})
            , 0
        )

    def test_scalar_legacy_schema_closes_account(self) -> None:
        self.assertEqual(
            concurrency.account_limit("grp", settings={"max_concurrent_per_account": 3}), 0
        )

    def test_scalar_cap_floored_at_zero(self) -> None:
        self.assertEqual(
            concurrency.account_limit("grp", settings={"max_concurrent_per_account": -5}), 0
        )

    def test_scalar_invalid_closes_account(self) -> None:
        self.assertEqual(
            concurrency.account_limit("grp", settings={"max_concurrent_per_account": "abc"})
            , 0
        )

    def test_dict_uses_account_id(self) -> None:
        settings = {"max_concurrent_per_account": {"acct_a": 4}}
        self.assertEqual(concurrency.account_limit("acct_a", settings=settings), 4)

    def test_dict_no_match_closes_account(self) -> None:
        settings = {"max_concurrent_per_account": {"other": 4}}
        self.assertEqual(concurrency.account_limit("acct_a", settings=settings), 0)

    def test_dict_non_int_value_closes_account(self) -> None:
        settings = {"max_concurrent_per_account": {"acct_a": "lots"}}
        self.assertEqual(concurrency.account_limit("acct_a", settings=settings), 0)

    def test_account_keys_are_normalized(self) -> None:
        settings = {"max_concurrent_per_account": {"acct-a": 2}}
        self.assertEqual(concurrency.account_limit("acct_a", settings=settings), 2)


if __name__ == "__main__":
    unittest.main()
