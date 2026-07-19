from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import concurrency


class WorkerSlotCountTests(unittest.TestCase):
    def test_no_slots(self) -> None:
        cfg = {"agents": {"claude": {}}}
        self.assertEqual(concurrency.worker_slot_count(cfg, "claude"), 0)

    def test_explicit_worker_slots_counted_if_real_agents(self) -> None:
        cfg = {"agents": {"claude": {"worker_slots": ["claude_1", "claude_2", "ghost"]},
                          "claude_1": {}, "claude_2": {}}}
        # ghost is not a real agent -> not counted
        self.assertEqual(concurrency.worker_slot_count(cfg, "claude"), 2)

    def test_dispatch_slot_for_backpointers_counted(self) -> None:
        cfg = {"agents": {"codex": {}, "codex_1": {"dispatch_slot_for": "codex"},
                          "codex_2": {"dispatch_slot_for": "codex"}}}
        self.assertEqual(concurrency.worker_slot_count(cfg, "codex"), 2)

    def test_union_deduplicated(self) -> None:
        cfg = {"agents": {"a": {"worker_slots": ["a_1"]},
                          "a_1": {"dispatch_slot_for": "a"}, "a_2": {"dispatch_slot_for": "a"}}}
        self.assertEqual(concurrency.worker_slot_count(cfg, "a"), 2)  # a_1 counted once


class MaxParallelTests(unittest.TestCase):
    def test_default_one_when_nothing_set(self) -> None:
        cfg = {"agents": {"claude": {}}}
        self.assertEqual(concurrency.max_parallel(cfg, "claude", settings={}), 1)

    def test_global_default(self) -> None:
        cfg = {"agents": {"claude": {}}}
        self.assertEqual(concurrency.max_parallel(cfg, "claude", settings={"max_tasks_per_agent": 4}), 4)

    def test_slots_win_over_default_when_larger(self) -> None:
        cfg = {"agents": {"claude": {"worker_slots": ["claude_1", "claude_2", "claude_3"]},
                          "claude_1": {}, "claude_2": {}, "claude_3": {}}}
        self.assertEqual(concurrency.max_parallel(cfg, "claude", settings={"max_tasks_per_agent": 2}), 3)

    def test_default_wins_when_larger_than_slots(self) -> None:
        cfg = {"agents": {"claude": {"worker_slots": ["claude_1"]}, "claude_1": {}}}
        self.assertEqual(concurrency.max_parallel(cfg, "claude", settings={"max_tasks_per_agent": 5}), 5)

    def test_per_agent_override_beats_slots_and_default(self) -> None:
        cfg = {"agents": {"claude": {"worker_slots": ["claude_1", "claude_2"]}, "claude_1": {}, "claude_2": {}}}
        settings = {"max_tasks_per_agent": 9, "max_tasks_per_agent_by_agent": {"claude": 1}}
        self.assertEqual(concurrency.max_parallel(cfg, "claude", settings=settings), 1)

    def test_override_by_display_name(self) -> None:
        cfg = {"agents": {"claude": {}}}
        settings = {"max_tasks_per_agent_by_agent": {"Claude": 7}}
        self.assertEqual(
            concurrency.max_parallel(cfg, "claude", settings=settings, display_name="Claude"), 7
        )

    def test_explicit_target_shape_max_parallel_field(self) -> None:
        cfg = {"agents": {"claude": {"max_parallel": 3}}}
        # target shape wins; derivation from slots/default not needed
        self.assertEqual(concurrency.max_parallel(cfg, "claude", settings={"max_tasks_per_agent": 9}), 3)

    def test_min_one_floor(self) -> None:
        cfg = {"agents": {"claude": {}}}
        self.assertEqual(concurrency.max_parallel(cfg, "claude", settings={"max_tasks_per_agent": 0}), 1)


class AccountLimitTests(unittest.TestCase):
    def test_no_cap_when_unset(self) -> None:
        self.assertIsNone(concurrency.account_limit("grp", settings={}))

    def test_no_cap_when_empty_string(self) -> None:
        self.assertIsNone(
            concurrency.account_limit("grp", settings={"max_concurrent_per_quota_group": ""})
        )

    def test_scalar_global_cap(self) -> None:
        self.assertEqual(
            concurrency.account_limit("grp", settings={"max_concurrent_per_quota_group": 3}), 3
        )

    def test_scalar_cap_floored_at_zero(self) -> None:
        self.assertEqual(
            concurrency.account_limit("grp", settings={"max_concurrent_per_quota_group": -5}), 0
        )

    def test_scalar_invalid_is_none(self) -> None:
        self.assertIsNone(
            concurrency.account_limit("grp", settings={"max_concurrent_per_quota_group": "abc"})
        )

    def test_dict_first_matching_key_wins(self) -> None:
        settings = {"max_concurrent_per_quota_group": {"acct_b": 5, "acct_a": 2}}
        self.assertEqual(
            concurrency.account_limit("acct_a", settings=settings,
                                      identity_keys=["acct_a", "acct_b"]),
            2,
        )

    def test_dict_default_keys_use_account_id(self) -> None:
        settings = {"max_concurrent_per_quota_group": {"acct_a": 4}}
        self.assertEqual(concurrency.account_limit("acct_a", settings=settings), 4)

    def test_dict_no_match_is_none(self) -> None:
        settings = {"max_concurrent_per_quota_group": {"other": 4}}
        self.assertIsNone(
            concurrency.account_limit("acct_a", settings=settings, identity_keys=["acct_a"])
        )

    def test_dict_non_int_value_is_none(self) -> None:
        settings = {"max_concurrent_per_quota_group": {"acct_a": "lots"}}
        self.assertIsNone(
            concurrency.account_limit("acct_a", settings=settings, identity_keys=["acct_a"])
        )

    def test_dict_skips_empty_keys(self) -> None:
        settings = {"max_concurrent_per_quota_group": {"acct_a": 1}}
        # empty/None keys are skipped; the real one still matches
        self.assertEqual(
            concurrency.account_limit("", settings=settings, identity_keys=["", None, "acct_a"]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
