import sys
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from explain_dispatch import explain_dispatch_for_task



class TestExplainDispatch(unittest.TestCase):
    def setUp(self) -> None:
        self.admitted_patcher = unittest.mock.patch("explain_dispatch.assistant_dev_bridge_task_is_admitted", return_value=True)
        self.admitted_patcher.start()
        self.config = {

            "paths": {
                "event_queue": "/tmp/mock-event-queue.jsonl",
                "status_file": "/tmp/mock-status.json",
                "state_file": "/tmp/mock-state.json",
                "activity_log": "/tmp/mock-activity.jsonl",
            },
            "agents": {
                "codex": {
                    "display_name": "Codex",
                    "capabilities": ["execution", "integration"],
                    "quota_group": "codex_group",
                    "quota_group_concurrency_limit": 1,
                },
                "claude2": {
                    "display_name": "Claude2",
                    "capabilities": ["governance-review"],
                    "quota_group": "claude_group",
                },
                "antigravity": {
                    "display_name": "Antigravity",
                    "capabilities": ["worker-ops"],
                },
            },
            "ready_dispatcher": {
                "enabled": True,
                "agent_weights": {"codex": 1, "claude2": 1, "antigravity": 1},
                "max_concurrent_workers": 10,
                "active_worker_statuses": ["running", "in_progress"],
                "review_statuses": ["review"],
                "finalize_statuses": ["review_approved"],
                "owned_statuses": ["in_progress", "todo"],
                "dependency_done_statuses": ["done"],
            },

            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
        }

    def tearDown(self) -> None:
        self.admitted_patcher.stop()



    def test_all_clear_case(self) -> None:
        state = {"seen_event_keys": {}}
        # Task is todo, assigned to Codex, dependencies met
        task = {
            "id": "TASK-001",
            "title": "Test Task",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
            "depends_on": [],
        }
        config = dict(self.config)
        # Mock load_status_data indirectly by injecting tasks via config / state if needed,
        # or testing explain_dispatch_for_task with mocked load_status_data.
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}):
            res = explain_dispatch_for_task(config, state, "TASK-001", target_agent_filter="Codex")
            self.assertNotIn("global_block_reason", res)
            self.assertIn("Codex", res["agents"])
            self.assertFalse(res["agents"]["Codex"]["blocked"])
            self.assertEqual(res["agents"]["Codex"]["candidate_reason"], "owned_ready_dispatch")

    def test_capability_blocked_agent(self) -> None:
        import copy
        config = copy.deepcopy(self.config)
        config["ready_dispatcher"]["disabled_agents"] = ["antigravity"]
        state = {"seen_event_keys": {}}
        task = {
            "id": "TASK-002",
            "status": "todo",
            "owner": "Antigravity",
            "reviewer": "Claude2",
            "depends_on": [],
        }
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}):
            res = explain_dispatch_for_task(config, state, "TASK-002", target_agent_filter="Antigravity")
            self.assertNotIn("global_block_reason", res)
            self.assertTrue(res["agents"]["Antigravity"]["blocked"])
            self.assertEqual(res["agents"]["Antigravity"]["first_blocking_gate"], "agent_auto_dispatch_block_reason")


    def test_quota_exhausted_agent(self) -> None:
        import copy
        config = copy.deepcopy(self.config)
        config["ready_dispatcher"]["max_tasks_per_agent"] = 10
        config["ready_dispatcher"]["max_concurrent_per_account"] = {"codex": 1}
        state = {
            "workers": {
                "w1": {
                    "status": "running",
                    "agent_id": "codex",
                    "task_id": "OTHER-TASK",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
            }
        }
        task = {
            "id": "TASK-003",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
        }
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}):
            res = explain_dispatch_for_task(config, state, "TASK-003", target_agent_filter="Codex")
            self.assertTrue(res["agents"]["Codex"]["blocked"])
            self.assertIn(res["agents"]["Codex"]["first_blocking_gate"], ("agent_auto_dispatch_block_reason", "quota_group_concurrency_limit"))






    def test_capacity_exhausted_agent(self) -> None:
        import copy
        config = copy.deepcopy(self.config)
        config["ready_dispatcher"]["max_tasks_per_agent"] = 1
        # Unset quota limit so capacity gate is hit first
        config["agents"]["codex"].pop("quota_group_concurrency_limit", None)
        state = {
            "workers": {
                "w1": {
                    "status": "running",
                    "agent_id": "codex",
                    "task_id": "OTHER-TASK",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
            }
        }
        task = {
            "id": "TASK-004",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
        }
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}):
            res = explain_dispatch_for_task(config, state, "TASK-004", target_agent_filter="Codex")
            self.assertTrue(res["agents"]["Codex"]["blocked"])
            self.assertEqual(res["agents"]["Codex"]["first_blocking_gate"], "agent_dispatch_capacity")




    def test_unmet_dependency(self) -> None:
        state = {}
        dep_task = {"id": "DEP-001", "status": "in_progress"}
        task = {
            "id": "TASK-005",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
            "depends_on": ["DEP-001"],
        }
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [dep_task, task]}):
            res = explain_dispatch_for_task(self.config, state, "TASK-005", target_agent_filter="Codex")
            self.assertTrue(res["agents"]["Codex"]["blocked"])
            self.assertEqual(res["agents"]["Codex"]["first_blocking_gate"], "dependencies_satisfied")

    def test_catalog_locked_task(self) -> None:
        state = {}
        task = {
            "id": "TASK-006",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
            "catalog_task_contract_sha256": "abc123sha256",
        }
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}):
            res = explain_dispatch_for_task(self.config, state, "TASK-006", target_agent_filter="Codex")
            self.assertTrue(res["agents"]["Codex"]["blocked"])
            self.assertEqual(res["agents"]["Codex"]["first_blocking_gate"], "task_assignment_is_catalog_locked")




    def test_cooldown_suppressed_event(self) -> None:
        state = {
            "seen_event_keys": {}
        }
        task = {
            "id": "TASK-007",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
        }
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}):
            # First run to get event key
            res1 = explain_dispatch_for_task(self.config, state, "TASK-007", target_agent_filter="Codex")
            self.assertFalse(res1["agents"]["Codex"]["blocked"])

            # Mock seen_event_keys with wildcard match for this task key
            for key in list(state.get("seen_event_keys", {}).keys()):
                pass
            # Or manually construct the event key prefix
            seen_key = [k for k in state.get("seen_event_keys", {}) if "TASK-007" in k]
            # Set time in seen to now
            import time
            from supervisor import utc_now
            state["seen_event_keys"]["dispatcher:Codex:TASK-007:owned_ready_dispatch:mock"] = utc_now()

            with unittest.mock.patch("explain_dispatch.dispatch_event_is_in_unchanged_cooldown", return_value=True):
                res2 = explain_dispatch_for_task(self.config, state, "TASK-007", target_agent_filter="Codex")
                self.assertTrue(res2["agents"]["Codex"]["blocked"])
                self.assertEqual(res2["agents"]["Codex"]["first_blocking_gate"], "dispatch_event_is_in_unchanged_cooldown")


if __name__ == "__main__":
    unittest.main()
