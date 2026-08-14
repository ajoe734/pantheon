import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / ".orchestrator"))

import explain_dispatch
import supervisor


class ExplainDispatchV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "paths": {
                "event_queue": "/tmp/unused-events.jsonl",
                "status_file": "/tmp/unused-status.json",
                "state_file": "/tmp/unused-state.json",
            },
            "providers": {
                "codex": {"account": "codex-account", "enabled": True},
                "claude2": {"account": "claude-account", "enabled": True},
            },
            "agents": {
                "codex": {
                    "display_name": "Codex",
                    "provider": "codex",
                    "enabled": True,
                    "max_parallel": 2,
                },
                "claude2": {
                    "display_name": "Claude2",
                    "provider": "claude2",
                    "enabled": True,
                    "max_parallel": 1,
                },
            },
            "ready_dispatcher": {
                "enabled": True,
                "agent_weights": {"codex": 1, "claude2": 1},
                "max_concurrent_workers": 4,
                "max_concurrent_per_account": {
                    "codex-account": 2,
                    "claude-account": 1,
                },
                "active_worker_statuses": ["running", "retry_backoff"],
                "owned_statuses": ["todo", "in_progress"],
                "review_statuses": ["review"],
                "finalize_statuses": ["review_approved"],
                "dependency_done_statuses": ["done"],
            },
            "schema": {"tasks_path": "tasks", "task_id_field": "id"},
        }

    def decide(self, task, state=None):
        runtime_state = state or {
            "seen_event_keys": {},
            "workers": {},
            "queue": {"events": {}},
            "delivery_health": {
                "version": 1,
                "endpoints": {
                    "codex": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    }
                },
                "accounts": {
                    "codex_account": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    }
                },
            },
        }
        with mock.patch.object(supervisor, "load_event_queue", return_value=[]):
            return supervisor.explain_dispatch_for_task(
                self.config,
                runtime_state,
                task["id"],
                target_agent_filter=task.get("owner") or task.get("reviewer"),
                status={"tasks": [task]},
                live_total=0,
            )

    def test_ready_decision_uses_shared_planner(self) -> None:
        task = {"id": "T1", "status": "todo", "owner": "Codex", "reviewer": "Claude2"}
        result = self.decide(task)
        self.assertFalse(result["agents"]["Codex"]["blocked"])
        self.assertEqual(result["agents"]["Codex"]["candidate_reason"], "owned_ready_dispatch")

    def test_dependency_and_zero_capacity_rejections_are_structured(self) -> None:
        task = {
            "id": "T2",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude2",
            "depends_on": ["MISSING"],
        }
        result = self.decide(task)
        self.assertEqual(
            result["agents"]["Codex"]["first_blocking_gate"],
            "task_not_dispatchable",
        )
        self.config["agents"]["codex"]["max_parallel"] = 0
        task.pop("depends_on")
        result = self.decide(task)
        self.assertEqual(
            result["agents"]["Codex"]["first_blocking_gate"],
            "lane_capacity_reached",
        )

    def test_script_is_only_a_snapshot_adapter(self) -> None:
        task = {"id": "T3", "status": "todo", "owner": "Codex", "reviewer": "Claude2"}
        expected = {"task_id": "T3", "agents": {}}
        with mock.patch.object(explain_dispatch, "load_status", return_value={"tasks": [task]}), \
             mock.patch.object(explain_dispatch, "supervisor_explain_dispatch_for_task", return_value=expected) as shared:
            result = explain_dispatch.explain_dispatch_for_task(
                self.config,
                {},
                "T3",
            )
        self.assertEqual(result, expected)
        shared.assert_called_once()

    def test_relative_state_path_uses_status_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            state_path = Path(temporary_root) / ".orchestrator" / "state.json"
            state_path.parent.mkdir()
            state_path.write_text('{"seen_event_keys": {}}', encoding="utf-8")
            config = {"paths": {"state_file": ".orchestrator/state.json"}}
            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": temporary_root}, clear=False):
                self.assertEqual(explain_dispatch.load_orchestrator_state(config), {"seen_event_keys": {}})

    def test_missing_state_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            config = {"paths": {"state_file": ".orchestrator/state.json"}}
            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": temporary_root}, clear=False):
                with self.assertRaises(explain_dispatch.DispatchStateLoadError):
                    explain_dispatch.load_orchestrator_state(config)

    def test_cli_requires_an_explicit_config(self) -> None:
        with mock.patch.object(sys, "argv", ["explain_dispatch.py", "T1"]):
            with self.assertRaises(SystemExit) as raised:
                explain_dispatch.main()
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
