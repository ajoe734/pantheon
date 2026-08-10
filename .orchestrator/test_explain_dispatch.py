import copy
import json
import os
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from explain_dispatch import (
    DispatchStateLoadError,
    bind_status_root_paths,
    explain_dispatch_for_task,
    load_orchestrator_state,
)



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
                "current_work": "/tmp/mock-current-work.md",
                "dashboard": "/tmp/mock-dashboard.html",
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
        task = {
            "id": "TASK-001",
            "title": "Test Task",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
            "depends_on": [],
        }
        config = dict(self.config)
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}):
            res = explain_dispatch_for_task(config, state, "TASK-001", target_agent_filter="Codex")
            self.assertNotIn("global_block_reason", res)
            self.assertIn("Codex", res["agents"])
            self.assertFalse(res["agents"]["Codex"]["blocked"])
            self.assertEqual(res["agents"]["Codex"]["candidate_reason"], "owned_ready_dispatch")

    def test_capability_blocked_agent(self) -> None:
        import copy
        config = copy.deepcopy(self.config)
        state = {"seen_event_keys": {}}
        task = {
            "id": "TASK-002",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
            "depends_on": [],
        }
        # agent_can_take_task returns False when agent is sidecar-only for non-sidecar task
        config["ready_dispatcher"]["sidecar_only_agents"] = ["Antigravity"]
        task_antigravity = {
            "id": "TASK-002",
            "status": "todo",
            "owner": "Antigravity",
            "reviewer": "Claude2",
            "depends_on": [],
        }
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task_antigravity]}):
            res = explain_dispatch_for_task(config, state, "TASK-002", target_agent_filter="Antigravity")
            self.assertNotIn("global_block_reason", res)
            self.assertTrue(res["agents"]["Antigravity"]["blocked"])
            self.assertEqual(res["agents"]["Antigravity"]["first_blocking_gate"], "agent_can_take_task")
            self.assertIn("sidecar-only restriction", res["agents"]["Antigravity"]["block_reason"])

    def test_auto_dispatch_gate_is_reported_before_other_agent_gates(self) -> None:
        state = {"seen_event_keys": {}}
        task = {
            "id": "TASK-AUTO-BLOCK",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
        }
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}), \
             unittest.mock.patch(
                 "explain_dispatch.agent_auto_dispatch_block_reason",
                 return_value="dispatch is paused for Codex",
             ):
            res = explain_dispatch_for_task(self.config, state, task["id"], target_agent_filter="Codex")
        self.assertTrue(res["agents"]["Codex"]["blocked"])
        self.assertEqual(
            res["agents"]["Codex"]["first_blocking_gate"],
            "agent_auto_dispatch_block_reason",
        )
        self.assertEqual(res["agents"]["Codex"]["block_reason"], "dispatch is paused for Codex")

    def test_quota_exhausted_agent(self) -> None:
        import copy
        config = copy.deepcopy(self.config)
        config["providers"] = {
            "codex": {
                "account": "codex_group",
            }
        }
        config["ready_dispatcher"]["max_tasks_per_agent"] = 10
        config["ready_dispatcher"]["max_concurrent_per_quota_group"] = {"codex_group": 1}
        config["agents"]["codex"]["provider"] = "codex"
        config["agents"]["codex"].pop("quota_group_concurrency_limit", None)
        state = {
            "queue": {
                "events": {
                    "e1": {
                        "event_id": "e1",
                        "status": "pending",
                    }
                }
            }
        }
        task = {
            "id": "TASK-003",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
        }
        with unittest.mock.patch("supervisor.load_event_queue", return_value=[{"event_id": "e1", "target_agent": "codex"}]), \
             unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}):
            res = explain_dispatch_for_task(config, state, "TASK-003", target_agent_filter="Codex")
            self.assertTrue(res["agents"]["Codex"]["blocked"])
            self.assertEqual(res["agents"]["Codex"]["first_blocking_gate"], "quota_group_concurrency_limit")
            self.assertIn("Quota group codex_group limit reached", res["agents"]["Codex"]["block_reason"])

    def test_capacity_exhausted_agent(self) -> None:
        import copy
        config = copy.deepcopy(self.config)
        config["ready_dispatcher"]["max_tasks_per_agent"] = 1
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
            self.assertEqual(res["agents"]["Codex"]["first_blocking_gate"], "task_execution_dispatch_candidate")

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
            self.assertFalse(res["agents"]["Codex"]["blocked"])
            self.assertIn("helper_notes", res["agents"]["Codex"])
            self.assertTrue(any("catalog locked" in note for note in res["agents"]["Codex"]["helper_notes"]))

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
            res1 = explain_dispatch_for_task(self.config, state, "TASK-007", target_agent_filter="Codex")
            self.assertFalse(res1["agents"]["Codex"]["blocked"])

            from supervisor import build_dispatch_event, task_resolver_for_config, utc_now
            resolver = task_resolver_for_config(self.config, {"TASK-007": task})
            evt = build_dispatch_event(task, "Codex", "owned_ready_dispatch", resolver)
            state["seen_event_keys"][evt["key"]] = utc_now()

            res2 = explain_dispatch_for_task(self.config, state, "TASK-007", target_agent_filter="Codex")
            self.assertTrue(res2["agents"]["Codex"]["blocked"])
            self.assertEqual(res2["agents"]["Codex"]["first_blocking_gate"], "dispatch_event_is_in_unchanged_cooldown")

    def test_review_cooldown_redispatch_is_not_reported_as_blocked(self) -> None:
        state = {"seen_event_keys": {}}
        task = {
            "id": "TASK-REVIEW-REDISPATCH",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude2",
        }
        from supervisor import build_dispatch_event, task_resolver_for_config, utc_now

        resolver = task_resolver_for_config(self.config, {task["id"]: task})
        event = build_dispatch_event(task, "Claude2", "review_ready_dispatch", resolver)
        state["seen_event_keys"][event["key"]] = utc_now()
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}), \
             unittest.mock.patch("explain_dispatch.pending_review_handoff", return_value={"id": "handoff-1"}), \
             unittest.mock.patch("explain_dispatch.terminal_review_worker_for_redispatch", return_value={"run_id": "worker-1"}):
            res = explain_dispatch_for_task(self.config, state, task["id"], target_agent_filter="Claude2")
        self.assertFalse(res["agents"]["Claude2"]["blocked"])
        self.assertTrue(res["agents"]["Claude2"]["review_redispatch"])

    def test_failure_loop_skip_is_reported_as_blocked(self) -> None:
        state = {"seen_event_keys": {}}
        task = {
            "id": "TASK-FAILURE-LOOP",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
        }
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}), \
             unittest.mock.patch(
                 "explain_dispatch.failure_loop_task_agents_for_task_map",
                 return_value={(task["id"], "Codex")},
             ):
            res = explain_dispatch_for_task(self.config, state, task["id"], target_agent_filter="Codex")
        self.assertTrue(res["agents"]["Codex"]["blocked"])
        self.assertEqual(res["agents"]["Codex"]["first_blocking_gate"], "failure_loop_task_agents")

    def test_chair_reassignment_skip_is_reported_as_blocked(self) -> None:
        state = {"seen_event_keys": {}}
        task = {
            "id": "TASK-CHAIR-TRIAGE",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
        }
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}), \
             unittest.mock.patch(
                 "explain_dispatch.chair_reassignment_triage_needed_for_task",
                 return_value=True,
             ):
            res = explain_dispatch_for_task(self.config, state, task["id"], target_agent_filter="Codex")
        self.assertTrue(res["agents"]["Codex"]["blocked"])
        self.assertEqual(
            res["agents"]["Codex"]["first_blocking_gate"],
            "chair_reassignment_triage_needed_for_task",
        )

    def test_relative_state_path_uses_status_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            state_path = root / ".orchestrator" / "state.json"
            state_path.parent.mkdir()
            state_path.write_text('{"seen_event_keys": {}}', encoding="utf-8")
            config = {"paths": {"state_file": ".orchestrator/state.json"}}
            with unittest.mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(root)}, clear=False):
                self.assertEqual(load_orchestrator_state(config), {"seen_event_keys": {}})

    def test_runtime_paths_use_status_root_without_mutating_input_config(self) -> None:
        config = {
            "paths": {
                "state_file": ".orchestrator/state.json",
                "provider_capabilities": ".orchestrator/provider_capabilities.json",
                "approval_queue": ".orchestrator/approval-queue.json",
            }
        }
        with unittest.mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": "/tmp/status-root"}, clear=False):
            bound = bind_status_root_paths(config)
        self.assertEqual(
            bound["paths"]["state_file"],
            "/tmp/status-root/.orchestrator/state.json",
        )
        self.assertEqual(
            bound["paths"]["provider_capabilities"],
            "/tmp/status-root/.orchestrator/provider_capabilities.json",
        )
        self.assertEqual(
            bound["paths"]["approval_queue"],
            "/tmp/status-root/.orchestrator/approval-queue.json",
        )
        self.assertEqual(config["paths"]["state_file"], ".orchestrator/state.json")

    def test_missing_state_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            config = {"paths": {"state_file": ".orchestrator/state.json"}}
            with unittest.mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": temporary_root}, clear=False):
                with self.assertRaises(DispatchStateLoadError):
                    load_orchestrator_state(config)


    def test_single_probe_failure_hysteresis(self) -> None:
        import supervisor
        report = {"providers": {"codex": {"auth_ready": True}}}
        single_failure = {"provider": "codex", "ready": False, "status": "timeout", "error": "probe timeout"}
        
        # Single failure should NOT mark auth_ready as False due to default threshold (3)
        health = supervisor.apply_provider_probe_to_report(report, "codex", single_failure, config=self.config)
        self.assertTrue(report["providers"]["codex"]["auth_ready"])
        self.assertEqual(report["providers"]["codex"]["consecutive_probe_failures"], 1)

        state = {"seen_event_keys": {}}
        task = {"id": "TASK-HYST-1", "status": "todo", "owner": "Codex", "reviewer": "Claude2"}
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}), \
             unittest.mock.patch("explain_dispatch.load_provider_report", return_value=report):
            res = explain_dispatch_for_task(self.config, state, "TASK-HYST-1", target_agent_filter="Codex")
            self.assertFalse(res["agents"]["Codex"]["blocked"])

    def test_n_consecutive_probe_failures_blocks_dispatch(self) -> None:
        import supervisor
        report = {"providers": {"codex": {"auth_ready": True}}}
        failure = {"provider": "codex", "ready": False, "status": "timeout", "error": "probe timeout"}
        
        config = dict(self.config)
        config["supervisor"] = {"provider_probe_failure_hysteresis_threshold": 2}
        
        # Probe 1: 1 failure < threshold 2
        supervisor.apply_provider_probe_to_report(report, "codex", failure, config=config)
        self.assertTrue(report["providers"]["codex"]["auth_ready"])

        # Probe 2: 2 failures == threshold 2 -> effective_ready = False
        activity_logs = []
        with unittest.mock.patch("supervisor.write_activity_log", side_effect=lambda cfg, entry: activity_logs.append(entry)):
            supervisor.apply_provider_probe_to_report(report, "codex", failure, config=config)

        self.assertFalse(report["providers"]["codex"]["auth_ready"])
        self.assertEqual(report["providers"]["codex"]["consecutive_probe_failures"], 2)
        self.assertTrue(any(e.get("type") == "provider_capability_transitioned" for e in activity_logs))

        state = {"seen_event_keys": {}}
        task = {"id": "TASK-HYST-2", "status": "todo", "owner": "Codex", "reviewer": "Claude2"}
        with unittest.mock.patch("explain_dispatch.load_status_data", return_value={"tasks": [task]}), \
             unittest.mock.patch("explain_dispatch.load_provider_report", return_value=report):
            res = explain_dispatch_for_task(config, state, "TASK-HYST-2", target_agent_filter="Codex")
            self.assertTrue(res["agents"]["Codex"]["blocked"])


    def test_provider_capabilities_hysteresis_end_to_end(self) -> None:
        import provider_permissions
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "config.json"
            caps_file = Path(tmpdir) / "provider_capabilities.json"
            cfg = copy.deepcopy(self.config)
            cfg["paths"]["provider_capabilities"] = str(caps_file)
            cfg.setdefault("supervisor", {})["provider_probe_failure_hysteresis_threshold"] = 3
            # Initial healthy capabilities report with auth_ready=True
            initial_report = {
                "providers": {
                    "claude": {
                        "auth_ready": True,
                        "consecutive_probe_failures": 0,
                    }
                }
            }
            caps_file.write_text(json.dumps(initial_report))

            # Mock _claude_provider_report returning a fresh live timeout probe
            timeout_probe = {"ready": False, "status": "probe_timeout", "error": "timeout", "source": "live"}
            mock_claude_report = {
                "auth_ready": False,
                "auth_probe": timeout_probe,
            }
            with mock.patch("provider_permissions._claude_provider_report", return_value=mock_claude_report):
                report = provider_permissions.provider_capabilities(cfg)
                # Hysteresis must hold auth_ready=True on 1st transient failure when baseline had auth_ready=True
                self.assertTrue(report["providers"]["claude"]["auth_ready"])
                self.assertEqual(report["providers"]["claude"]["consecutive_probe_failures"], 1)

    def test_reconcile_fresh_provider_probe_failures_skip_ready_true(self) -> None:
        import supervisor
        previous_report = {"providers": {"newadapter": {"auth_ready": True}}}
        current_report = {
            "providers": {
                "newadapter": {
                    "auth_ready": True,
                    "auth_probe": {"ready": None, "status": "unsupported_probe", "source": "live", "checked_at": "2026-08-06T00:00:00Z"},
                }
            }
        }
        state = {"paused_providers": {}}
        changed = supervisor.reconcile_fresh_provider_probe_failures(
            self.config, state, previous_report, current_report
        )
        self.assertFalse(changed)
        self.assertNotIn("newadapter", state["paused_providers"])


if __name__ == "__main__":
    unittest.main()

