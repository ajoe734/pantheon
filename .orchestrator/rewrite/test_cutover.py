"""Cutover regression tests — Phase 1b / Phase 3b (SUPERVISOR_REWRITE_PLAN.md §4).

Once the incumbent functions delegate to the rewrite modules by default, the
shadow validator (which compares the incumbent against the rewrite) agrees
trivially — it can no longer prove the cutover preserved behaviour. These tests
close that gap by using the **legacy path itself as the oracle**: the incumbent
body is one flag away (`use_rewrite_concurrency` / `use_rewrite_dispatch_reason`
= false), so for a matrix of configs we assert

    live_fn(flag=on, rewrite path) == live_fn(flag=off, legacy path)

for every agent / (task, agent) pair. This guarantees the cutover is exactly
behaviour-preserving across config shapes the live shadow never exercised
(custom status sets, display-name overrides, ghost slots, unmet deps, …), not
just the single live config.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# .orchestrator on the path so we can import the live incumbent.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import supervisor  # noqa: E402


def _cap(config, agent, *, rewrite: bool):
    settings = supervisor.ready_dispatch_settings(config)
    settings["use_rewrite_concurrency"] = rewrite
    return supervisor.agent_dispatch_capacity(config, agent, settings)


def _with_flag(config, *, rewrite: bool):
    cfg = {**config}
    rd = dict(cfg.get("ready_dispatcher", {}) or {})
    rd["use_rewrite_dispatch_reason"] = rewrite
    cfg["ready_dispatcher"] = rd
    return cfg


# --- capacity (Phase 1b) config matrix ------------------------------------
CAPACITY_CONFIGS = [
    {"agents": {"claude": {}}},
    {"agents": {"claude": {}}, "ready_dispatcher": {"max_tasks_per_agent": 4}},
    {"agents": {"claude": {}}, "ready_dispatcher": {"max_tasks_per_agent": 0}},
    {  # worker_slots incl. a ghost (non-agent) that must not be counted
        "agents": {"claude": {"worker_slots": ["claude_1", "claude_2", "ghost"]},
                   "claude_1": {}, "claude_2": {}},
        "ready_dispatcher": {"max_tasks_per_agent": 2},
    },
    {  # dispatch_slot_for back-pointers
        "agents": {"codex": {}, "codex_1": {"dispatch_slot_for": "codex"},
                   "codex_2": {"dispatch_slot_for": "codex"}},
        "ready_dispatcher": {"max_tasks_per_agent": 1},
    },
    {  # default larger than slot count -> default wins
        "agents": {"claude": {"worker_slots": ["claude_1"]}, "claude_1": {}},
        "ready_dispatcher": {"max_tasks_per_agent": 5},
    },
    {  # per-agent override by normalized id
        "agents": {"claude": {"worker_slots": ["claude_1", "claude_2"]},
                   "claude_1": {}, "claude_2": {}},
        "ready_dispatcher": {"max_tasks_per_agent": 9,
                             "max_tasks_per_agent_by_agent": {"claude": 1}},
    },
    {  # per-agent override by display name
        "agents": {"claude": {"display_name": "Claude"}},
        "ready_dispatcher": {"max_tasks_per_agent_by_agent": {"Claude": 7}},
    },
    {  # union dedupe (slot listed AND back-pointing)
        "agents": {"a": {"worker_slots": ["a_1"]},
                   "a_1": {"dispatch_slot_for": "a"},
                   "a_2": {"dispatch_slot_for": "a"}},
    },
]


class CapacityCutoverParityTests(unittest.TestCase):
    def test_rewrite_equals_legacy_for_every_agent(self) -> None:
        for i, cfg in enumerate(CAPACITY_CONFIGS):
            for agent in cfg.get("agents", {}):
                norm = supervisor.normalize_agent_id(agent)
                if not norm:
                    continue
                legacy = _cap(cfg, norm, rewrite=False)
                new = _cap(cfg, norm, rewrite=True)
                self.assertEqual(
                    legacy, new,
                    msg=f"config[{i}] agent={norm}: legacy={legacy} rewrite={new}",
                )


# --- account cap (Phase 1b) config matrix ---------------------------------
def _acct(config, agent, *, rewrite: bool):
    settings = supervisor.ready_dispatch_settings(config)
    settings["use_rewrite_concurrency"] = rewrite
    return supervisor.quota_group_concurrency_limit(config, agent, settings)


ACCOUNT_CONFIGS = [
    {"agents": {"claude": {"provider": "anthropic"}},
     "providers": {"anthropic": {}}},
    {"agents": {"claude": {"provider": "anthropic"}},
     "providers": {"anthropic": {}},
     "ready_dispatcher": {"max_concurrent_per_quota_group": 2}},
    {  # dict keyed by account_group shared across two providers
        "agents": {"claude": {"provider": "anthropic"}, "claude2": {"provider": "anthropic2"}},
        "providers": {"anthropic": {"account_group": "acct_shared"},
                      "anthropic2": {"account_group": "acct_shared"}},
        "ready_dispatcher": {"max_concurrent_per_quota_group": {"acct_shared": 3}},
    },
    {  # dict keyed by quota_group alias
        "agents": {"codex": {"provider": "openai"}},
        "providers": {"openai": {"quota_group": "oai_pool"}},
        "ready_dispatcher": {"max_concurrent_per_quota_group": {"oai_pool": 1}},
    },
    {  # dict with no matching key -> None
        "agents": {"gemini": {"provider": "google"}},
        "providers": {"google": {}},
        "ready_dispatcher": {"max_concurrent_per_quota_group": {"unrelated": 9}},
    },
    {  # target shape: two providers share one explicit account
        "agents": {"claude": {"provider": "anthropic"}, "claude2": {"provider": "anthropic2"}},
        "providers": {"anthropic": {"account": "acct_shared"},
                      "anthropic2": {"account": "acct_shared"}},
        "ready_dispatcher": {"max_concurrent_per_account": {"acct_shared": 3}},
    },
    {  # target key wins if a transitional config still carries the legacy map
        "agents": {"codex": {"provider": "openai"}},
        "providers": {"openai": {"account": "oai_pool"}},
        "ready_dispatcher": {
            "max_concurrent_per_account": {"oai_pool": 2},
            "max_concurrent_per_quota_group": {"oai_pool": 9},
        },
    },
]


class AccountCapCutoverParityTests(unittest.TestCase):
    def test_rewrite_equals_legacy_for_every_agent(self) -> None:
        for i, cfg in enumerate(ACCOUNT_CONFIGS):
            for agent in cfg.get("agents", {}):
                norm = supervisor.normalize_agent_id(agent)
                if not norm:
                    continue
                legacy = _acct(cfg, norm, rewrite=False)
                new = _acct(cfg, norm, rewrite=True)
                self.assertEqual(
                    legacy, new,
                    msg=f"config[{i}] agent={norm}: legacy={legacy} rewrite={new}",
                )


# --- dispatch priority (Phase 3b) config + task matrix --------------------
CANONICAL_RD = {"review_statuses": ["review"], "finalize_statuses": ["review_approved"],
                "dependency_done_statuses": ["done"]}
CUSTOM_RD = {"review_statuses": ["reviewing"], "finalize_statuses": ["awaiting_finalize"],
             "dependency_done_statuses": ["complete"]}

STATUSES = ["todo", "in_progress", "review", "review_approved", "done", "blocked",
            "reviewing", "awaiting_finalize", "", "bogus"]


def _priority_configs():
    schema = {"assignee_field": "owner", "reviewer_field": "reviewer"}
    return [
        {"schema": schema, "ready_dispatcher": dict(CANONICAL_RD)},
        {"schema": schema, "ready_dispatcher": dict(CUSTOM_RD)},
    ]


def _tasks():
    tasks = []
    for status in STATUSES:
        for owner in ("alice", "bob"):
            for reviewer in ("bob", "carol"):
                # deps satisfied vs a dependency that is not done
                tasks.append({"id": f"T-{status}-{owner}-{reviewer}-nodep",
                              "status": status, "owner": owner, "reviewer": reviewer})
                tasks.append({"id": f"T-{status}-{owner}-{reviewer}-dep",
                              "status": status, "owner": owner, "reviewer": reviewer,
                              "depends_on": ["MISSING-DEP"]})
    return tasks


class DispatchPriorityCutoverParityTests(unittest.TestCase):
    def test_rewrite_equals_legacy_for_every_task_agent(self) -> None:
        agents = ["alice", "bob", "carol", "dave"]
        for i, base_cfg in enumerate(_priority_configs()):
            cfg_on = _with_flag(base_cfg, rewrite=True)
            cfg_off = _with_flag(base_cfg, rewrite=False)
            for task in _tasks():
                for agent in agents:
                    legacy = supervisor.dispatch_priority_for_task(cfg_off, task, agent)
                    new = supervisor.dispatch_priority_for_task(cfg_on, task, agent)
                    self.assertEqual(
                        legacy, new,
                        msg=f"config[{i}] task={task['id']} agent={agent}: "
                            f"legacy={legacy} rewrite={new}",
                    )


if __name__ == "__main__":
    unittest.main()
