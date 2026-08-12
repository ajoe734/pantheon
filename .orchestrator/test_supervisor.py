#!/usr/bin/env python3
"""Contract tests for Supervisor Authority V2.

The previous file mirrored thousands of lines of implementation detail for
retired chair, discussion-planning, failure-streak, shadow-writer, fallback,
and priority-preemption paths.  These tests intentionally exercise only the
remaining authority boundaries: one planner, one durable queue, one launcher,
explicit capacity/account health, bounded assignment recovery, leases, and an
authoritative TaskStore projection.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import supervisor


_OLD_ENV: dict[str, str] = {}


def setUpModule() -> None:
    global _OLD_ENV
    _OLD_ENV = dict(os.environ)
    for key in list(os.environ):
        if key.startswith("PANTHEON_"):
            del os.environ[key]


def tearDownModule() -> None:
    os.environ.clear()
    os.environ.update(_OLD_ENV)


def config_fixture(root: Path | None = None) -> dict[str, object]:
    paths: dict[str, str] = {}
    if root is not None:
        paths = {
            "status_file": str(root / "ai-status.json"),
            "event_queue": str(root / ".orchestrator" / "queue.jsonl"),
            "runtime_state": str(root / ".orchestrator" / "supervisor.json"),
            "activity_log": str(root / "ai-activity-log.jsonl"),
            "provider_capabilities": str(root / ".orchestrator" / "providers.json"),
            "task_state_event_log": str(root / ".orchestrator" / "tasks.jsonl"),
        }
    return {
        "schema": {
            "tasks_path": "tasks",
            "task_id_field": "id",
            "status_field": "status",
            "assignee_field": "owner",
            "reviewer_field": "reviewer",
        },
        "paths": paths,
        "ready_dispatcher": {
            "enabled": True,
            "max_concurrent_workers": 4,
            "max_dispatches_per_tick": 4,
            "max_concurrent_per_account": {
                "codex_account": 2,
                "codex2_account": 2,
            },
            "active_worker_statuses": [
                "starting",
                "running",
                "waiting_approval",
            ],
            "owned_statuses": ["todo", "in_progress"],
            "review_statuses": ["review"],
            "finalize_statuses": ["review_approved"],
            "dependency_done_statuses": ["done"],
            "target_workload": {"Codex": 1, "Codex2": 1},
            "unchanged_task_cooldown_seconds": 0,
            "worker_os_duplicate_guard": False,
        },
        "agents": {
            "codex": {
                "id": "codex",
                "display_name": "Codex",
                "provider": "codex",
                "adapter": "codex",
                "max_parallel": 2,
            },
            "codex2": {
                "id": "codex2",
                "display_name": "Codex2",
                "provider": "codex2",
                "adapter": "codex",
                "max_parallel": 2,
            },
        },
        "providers": {
            "codex": {
                "delivery_mode": "codex",
                "account": "codex-account",
            },
            "codex2": {
                "delivery_mode": "codex",
                "account": "codex2-account",
            },
        },
        "worker_reassignment": {
            "enabled": True,
            "max_reassignments_per_cycle": 4,
            "owner_fallbacks": {"Codex": ["Codex2"]},
            "reviewer_fallbacks": {"Codex": ["Codex2"]},
        },
    }


def task_fixture(
    task_id: str = "TASK-1",
    *,
    status: str = "todo",
    owner: str = "Codex",
    reviewer: str = "Codex2",
    depends_on: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": task_id,
        "generation": 1,
        "status": status,
        "owner": owner,
        "reviewer": reviewer,
        "depends_on": list(depends_on or []),
        "last_update": "2026-08-11T00:00:00Z",
    }


def provider_report_fixture(*, ready: bool = True, checked_at: str | None = None) -> dict[str, object]:
    checked = checked_at or supervisor.utc_now()
    providers: dict[str, object] = {}
    for provider in ("codex", "codex2"):
        providers[provider] = {
            "auth_ready": ready,
            "local_cli_worker_supported": ready,
            "supports_auto_approve": ready,
            "auth_probe": {
                "provider": provider,
                "ready": ready,
                "status": "ready" if ready else "not_ready",
                "checked_at": checked,
                "last_auth_probe_at": checked,
                "source": "live",
            },
        }
    return {"providers": providers}


def planner_decision(
    config: dict[str, object],
    task: dict[str, object],
    *,
    state: dict[str, object] | None = None,
    status: dict[str, object] | None = None,
    target: str = "Codex",
    active_task_ids: set[str] | None = None,
    pending_task_ids: set[str] | None = None,
) -> dict[str, object]:
    state = state or {"workers": {}, "queue": {"events": {}}}
    status = status or {"tasks": [task]}
    task_map = {str(item["id"]): item for item in status["tasks"]}
    return supervisor.evaluate_dispatch_candidate(
        config,
        state,
        status,
        task,
        target,
        supervisor.task_resolver_for_config(config, task_map),
        settings=supervisor.ready_dispatch_settings(config),
        provider_report=provider_report_fixture(),
        active_task_ids=active_task_ids or set(),
        pending_task_ids=pending_task_ids or set(),
        pending_event_keys=set(),
        agent_loads={"Codex": [], "Codex2": []},
        active_account_loads={},
        pending_account_loads={},
        seen_event_keys={},
        checked_at="2026-08-11T00:00:01Z",
        cooldown_seconds=0,
    )


class RuntimeConfigurationContractTests(unittest.TestCase):
    def test_repo_config_uses_one_capacity_and_account_schema(self) -> None:
        config = json.loads(Path(__file__).with_name("config.json").read_text())
        supervisor.validate_provider_accounts(config)
        settings = config["ready_dispatcher"]
        self.assertNotIn("disabled_agents", settings)
        self.assertNotIn("max_tasks_per_agent", settings)
        self.assertNotIn("max_tasks_per_agent_by_agent", settings)
        self.assertNotIn("max_concurrent_per_quota_group", settings)
        self.assertNotIn("grok", config["agents"])
        for agent_id, agent in config["agents"].items():
            if supervisor.agent_is_dispatch_slot(agent):
                continue
            self.assertIsInstance(agent.get("max_parallel"), int, agent_id)
        for provider, provider_config in config["providers"].items():
            self.assertTrue(provider_config.get("account"), provider)
            self.assertFalse(
                {"account_group", "quota_group", "dispatch_group"}
                & set(provider_config),
                provider,
            )

    def test_missing_logical_agent_capacity_fails_closed(self) -> None:
        config = config_fixture()
        del config["agents"]["codex"]["max_parallel"]
        with self.assertRaisesRegex(ValueError, "agents.codex.max_parallel"):
            supervisor.validate_provider_accounts(config)
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "codex"), 0)

    def test_retired_capacity_fields_fail_closed(self) -> None:
        for retired in (
            "disabled_agents",
            "max_tasks_per_agent",
            "max_tasks_per_agent_by_agent",
            "max_concurrent_per_quota_group",
        ):
            with self.subTest(retired=retired):
                config = config_fixture()
                config["ready_dispatcher"][retired] = {}
                with self.assertRaisesRegex(ValueError, retired):
                    supervisor.validate_provider_accounts(config)

    def test_manual_delivery_fallback_schema_fails_closed(self) -> None:
        for field, value in (
            ("file_inbox", {"path": ".llm-inbox/codex.md"}),
            ("allow_inbox_fallback", True),
        ):
            with self.subTest(field=field):
                config = config_fixture()
                config["providers"]["codex"][field] = value
                with self.assertRaisesRegex(ValueError, field):
                    supervisor.validate_provider_accounts(config)

        config = config_fixture()
        config["agents"]["codex"]["file_inbox_path"] = ".llm-inbox/codex.md"
        with self.assertRaisesRegex(ValueError, "file_inbox_path"):
            supervisor.validate_provider_accounts(config)

    def test_unknown_adapter_fails_closed_at_startup(self) -> None:
        config = config_fixture()
        config["agents"]["codex"]["adapter"] = "retired_manual_adapter"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            supervisor.validate_provider_accounts(config)

    def test_account_key_normalization_preserves_zero_cap(self) -> None:
        config = config_fixture()
        config["ready_dispatcher"]["max_concurrent_per_account"] = {
            "codex-account": 0
        }
        self.assertEqual(supervisor.account_concurrency_limit(config, "codex"), 0)
        reason = supervisor.delivery_capacity_block_reason(
            config,
            {"workers": {}},
            "codex",
            {"running"},
        )
        self.assertIn("codex_account (0/0)", reason or "")


class SharedPlannerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = config_fixture()

    def test_owner_todo_is_dispatchable(self) -> None:
        decision = planner_decision(self.config, task_fixture())
        self.assertTrue(decision["eligible"])
        self.assertEqual(decision["reason"], supervisor.REASON_OWNED_READY)

    def test_reviewer_review_is_dispatchable(self) -> None:
        task = task_fixture(status="review")
        decision = planner_decision(self.config, task, target="Codex2")
        self.assertTrue(decision["eligible"])
        self.assertEqual(decision["reason"], supervisor.REASON_REVIEW_READY)

    def test_dependency_must_be_done(self) -> None:
        task = task_fixture(depends_on=["DEP"])
        status = {"tasks": [task, task_fixture("DEP", status="in_progress")]}
        decision = planner_decision(self.config, task, status=status)
        self.assertFalse(decision["eligible"])
        self.assertEqual(
            decision["first_blocking_gate"],
            "lifecycle_assignment_dependencies",
        )

    def test_active_or_pending_task_is_never_planned_twice(self) -> None:
        task = task_fixture()
        active = planner_decision(
            self.config,
            task,
            active_task_ids={str(task["id"])},
        )
        pending = planner_decision(
            self.config,
            task,
            pending_task_ids={str(task["id"])},
        )
        self.assertEqual(active["first_blocking_gate"], "active_task_lease")
        self.assertEqual(pending["first_blocking_gate"], "pending_delivery_intent")

    def test_planner_uses_only_supplied_snapshots(self) -> None:
        self.config["ready_dispatcher"]["worker_os_duplicate_guard"] = True
        with (
            mock.patch.object(
                supervisor,
                "scan_live_worker_pids_by_agent",
                side_effect=AssertionError("planner must not scan live processes"),
            ),
            mock.patch.object(
                supervisor,
                "_cached_provider_capabilities",
                side_effect=AssertionError("planner must not read provider cache"),
            ),
        ):
            decision = planner_decision(self.config, task_fixture())
        self.assertTrue(decision["eligible"])

    def test_dispatch_reserves_intent_without_launching(self) -> None:
        task = task_fixture()
        state = {"workers": {}, "queue": {"events": {}}, "seen_event_keys": {}}
        queued: list[dict[str, object]] = []
        with (
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                side_effect=AssertionError("planner must not launch"),
            ),
        ):
            changed = supervisor.dispatch_ready_tasks(
                self.config,
                state,
                provider_report=provider_report_fixture(),
                status_snapshot={"tasks": [task]},
                queue_events_snapshot=[],
                live_total_snapshot=0,
                event_sink=lambda _config, event: queued.append(event) or True,
            )
        self.assertTrue(changed)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["task_id"], "TASK-1")

    def test_planner_cannot_write_the_delivery_queue(self) -> None:
        with self.assertRaisesRegex(ValueError, "in-memory event sink"):
            supervisor.dispatch_ready_tasks(
                self.config,
                {"workers": {}, "queue": {"events": {}}},
                status_snapshot={"tasks": [task_fixture()]},
                queue_events_snapshot=[],
                live_total_snapshot=0,
            )

    def test_build_plan_is_side_effect_free_and_reservation_is_single_writer(self) -> None:
        state = {"workers": {}, "queue": {"events": {}}, "seen_event_keys": {}}
        with mock.patch.object(
            supervisor,
            "_queue_delivery_event_locked",
            side_effect=AssertionError("pure planning must not append"),
        ):
            plan = supervisor.build_dispatch_plan(
                self.config,
                state,
                {"tasks": [task_fixture()]},
                [],
                provider_report_fixture(),
                live_total=0,
            )
        self.assertEqual(len(plan["events"]), 1)

        appended: list[dict[str, object]] = []
        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(
                supervisor,
                "load_status",
                return_value={"tasks": [task_fixture()]},
            ),
            mock.patch.object(
                supervisor,
                "_queue_delivery_event_locked",
                side_effect=lambda _config, event: appended.append(event) or True,
            ),
        ):
            changed = supervisor.reserve_dispatch_plan(self.config, state, plan)
        self.assertTrue(changed)
        self.assertEqual(len(appended), 1)
        self.assertIn(plan["events"][0]["key"], state["seen_event_keys"])

    def test_reservation_rechecks_account_capacity(self) -> None:
        plan = supervisor.build_dispatch_plan(
            self.config,
            {"workers": {}, "queue": {"events": {}}, "seen_event_keys": {}},
            {"tasks": [task_fixture()]},
            [],
            provider_report_fixture(),
            live_total=0,
        )
        self.assertEqual(len(plan["events"]), 1)
        self.config["ready_dispatcher"]["max_concurrent_per_account"] = {
            "codex-account": 0
        }
        state = {"workers": {}, "queue": {"events": {}}, "seen_event_keys": {}}
        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(
                supervisor,
                "load_status",
                return_value={"tasks": [task_fixture()]},
            ),
            mock.patch.object(
                supervisor,
                "_queue_delivery_event_locked",
                side_effect=AssertionError("zero-cap account must not reserve"),
            ),
        ):
            supervisor.reserve_dispatch_plan(self.config, state, plan)
        self.assertNotIn(plan["events"][0]["key"], state["seen_event_keys"])

    def test_account_capacity_applies_across_agents(self) -> None:
        self.config["providers"]["codex2"]["account"] = "codex-account"
        self.config["ready_dispatcher"]["max_concurrent_per_account"] = {
            "codex-account": 1
        }
        state = {
            "workers": {
                "run": {
                    "run_id": "run",
                    "agent_id": "codex",
                    "provider": "codex",
                    "task_id": "OTHER",
                    "status": "running",
                }
            },
            "queue": {"events": {}},
        }
        decision = planner_decision(
            self.config,
            task_fixture(owner="Codex2", reviewer="Codex"),
            state=state,
            target="Codex2",
        )
        self.assertFalse(decision["eligible"])
        self.assertEqual(decision["first_blocking_gate"], "account_health")


class DurableQueueContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = config_fixture()
        self.task = task_fixture(status="in_progress")
        self.event = supervisor.build_dispatch_event(
            self.task,
            "Codex",
            supervisor.REASON_OWNED_IN_PROGRESS,
            {"TASK-1": self.task},
        )
        self.event.update(
            {
                "event_id": "evt-1",
                "event_key": self.event["key"],
                "target_agent": "codex",
                "target_display_name": "Codex",
                "message": "wake",
            }
        )

    def test_process_queue_is_the_only_launch_caller(self) -> None:
        tree = ast.parse(inspect.getsource(supervisor))
        callers: set[str] = set()
        stack: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Name) and node.func.id == "start_worker_for_request":
                    callers.add(stack[-1] if stack else "<module>")
                self.generic_visit(node)

        Visitor().visit(tree)
        self.assertEqual(callers, {"process_queue"})

    def test_planner_reservation_queue_reload_and_delivery_preserve_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = config_fixture(Path(tmpdir))
            (Path(tmpdir) / ".orchestrator").mkdir()
            task = task_fixture(status="in_progress") | {"generation": 7}
            state = {"workers": {}, "queue": {"events": {}}, "seen_event_keys": {}}
            plan = supervisor.build_dispatch_plan(
                config,
                state,
                {"tasks": [task]},
                [],
                provider_report_fixture(),
                live_total=0,
            )
            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                self.assertTrue(supervisor.reserve_dispatch_plan(config, state, plan))
            queued = supervisor.load_event_queue(config)
            self.assertEqual(queued[0]["task_generation"], 7)

            with (
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)),
                mock.patch.object(supervisor, "check_worker_tree_clean", return_value=(True, None)),
                mock.patch.object(
                    supervisor,
                    "start_worker_for_request",
                    return_value=(True, "run-generation-7", {"auto_delivered": True}),
                ) as launch,
                mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                self.assertTrue(
                    supervisor.process_queue(config, state, provider_report_fixture())
                )
            request = launch.call_args.args[3]
            self.assertEqual(request.metadata["task_generation"], 7)

    def test_suspended_approval_returns_to_queue_without_direct_spawn(self) -> None:
        state = {
            "workers": {},
            "queue": {
                "events": {
                    "evt-approval": {
                        "id": "evt-approval",
                        "status": "waiting_approval",
                        "lease_owner": "run-approval",
                        "lease_expires_at": "2999-01-01T00:00:00Z",
                        "run_id": "run-approval",
                    }
                }
            },
        }
        worker = {
            "run_id": "run-approval",
            "task_id": "TASK-1",
            "provider": "claude",
            "queue_event_id": "evt-approval",
            "status": "suspended_approval",
            "last_approval_id": None,
        }
        with mock.patch.object(supervisor, "write_activity_log"):
            outcome = supervisor.poll_worker_approval_stage(
                self.config,
                state,
                worker,
                provider_report={},
                pending=[],
                resolved=[{"approval_id": "approval-1", "decision": "allow"}],
                alive=False,
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "retry_queued")
        record = state["queue"]["events"]["evt-approval"]
        self.assertEqual(record["status"], "queued")
        self.assertNotIn("lease_owner", record)
        self.assertNotIn("run_id", record)

    def test_current_event_launches_after_full_revalidation(self) -> None:
        state = {"workers": {}, "queue": {"events": {}}}
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="TASK-1",
            reason=supervisor.REASON_OWNED_IN_PROGRESS,
            metadata={"workspace_path": "/tmp/task-1"},
        )
        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[self.event]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [self.task]}),
            mock.patch.object(supervisor, "build_request", return_value=request),
            mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)),
            mock.patch.object(supervisor, "check_worker_tree_clean", return_value=(True, None)),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                return_value=(True, "run-1", {"auto_delivered": True}),
            ) as launch,
            mock.patch.object(
                supervisor,
                "probe_provider_auth",
                side_effect=AssertionError("delivery must not run provider subprocesses"),
            ),
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
        ):
            changed = supervisor.process_queue(self.config, state, provider_report_fixture())
        self.assertTrue(changed)
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "started")
        launch.assert_called_once()

    def test_missing_or_stale_auth_evidence_stays_pending(self) -> None:
        stale = provider_report_fixture(
            checked_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        )
        for label, report in (("missing", {}), ("stale", stale)):
            with self.subTest(label=label):
                state = {"workers": {}, "queue": {"events": {}}}
                with (
                    mock.patch.object(supervisor, "load_event_queue", return_value=[self.event]),
                    mock.patch.object(supervisor, "load_status", return_value={"tasks": [self.task]}),
                    mock.patch.object(
                        supervisor,
                        "start_worker_for_request",
                        side_effect=AssertionError("unproven auth must not launch"),
                    ),
                    mock.patch.object(
                        supervisor,
                        "probe_provider_auth",
                        side_effect=AssertionError("delivery must not probe inline"),
                    ),
                ):
                    supervisor.process_queue(self.config, state, report)
                record = state["queue"]["events"]["evt-1"]
                self.assertEqual(record["status"], "pending")
                self.assertRegex(
                    record["last_wait_reason"],
                    r"authentication|provider capability",
                )

    def test_one_runtime_reservation_launches_at_most_one_process(self) -> None:
        second_task = task_fixture("TASK-2", status="in_progress")
        second_event = supervisor.build_dispatch_event(
            second_task,
            "Codex",
            supervisor.REASON_OWNED_IN_PROGRESS,
            {"TASK-1": self.task, "TASK-2": second_task},
        )
        second_event.update(
            {
                "event_id": "evt-2",
                "event_key": second_event["key"],
                "target_agent": "codex",
                "target_display_name": "Codex",
                "message": "wake",
            }
        )
        state = {"workers": {}, "queue": {"events": {}}}
        def request_for_event(_config, event, agent_id_override=None):
            return supervisor.DeliveryRequest(
                agent_id=agent_id_override or "codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=str(event["task_id"]),
                reason=str(event["reason"]),
                metadata={"workspace_path": f"/tmp/{event['task_id'].lower()}"},
            )
        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[self.event, second_event]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [self.task, second_task]}),
            mock.patch.object(supervisor, "build_request", side_effect=request_for_event),
            mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)),
            mock.patch.object(supervisor, "check_worker_tree_clean", return_value=(True, None)),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                return_value=(True, "run-1", {"auto_delivered": True}),
            ) as launch,
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
        ):
            supervisor.process_queue(self.config, state, provider_report_fixture())
        launch.assert_called_once()
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "started")
        self.assertNotIn("evt-2", state["queue"]["events"])

    def test_assignment_change_invalidates_queued_event(self) -> None:
        changed_task = {**self.task, "owner": "Codex2"}
        state = {"workers": {}, "queue": {"events": {}}}
        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[self.event]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [changed_task]}),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                side_effect=AssertionError("stale intent must not launch"),
            ),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            supervisor.process_queue(self.config, state, provider_report_fixture())
        record = state["queue"]["events"]["evt-1"]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["skip_reason"], "stale_dispatch_event")

    def test_nonplanner_queue_reason_is_never_launched(self) -> None:
        legacy_event = {**self.event, "reason": "github_retry"}
        state = {"workers": {}, "queue": {"events": {}}}
        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[legacy_event]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [self.task]}),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                side_effect=AssertionError("nonplanner intent must not launch"),
            ),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            supervisor.process_queue(self.config, state, provider_report_fixture())
        record = state["queue"]["events"]["evt-1"]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["skip_reason"], "unsupported_dispatch_reason")

    def test_delivery_revalidates_account_capacity(self) -> None:
        state = {
            "workers": {
                "busy": {
                    "run_id": "busy",
                    "agent_id": "codex",
                    "provider": "codex",
                    "task_id": "OTHER",
                    "status": "running",
                }
            },
            "queue": {"events": {}},
        }
        self.config["ready_dispatcher"]["max_concurrent_per_account"] = {
            "codex-account": 1
        }
        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[self.event]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [self.task]}),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                side_effect=AssertionError("capacity-blocked intent must not launch"),
            ),
        ):
            supervisor.process_queue(self.config, state, {})
        record = state["queue"]["events"]["evt-1"]
        self.assertEqual(record["status"], "pending")
        self.assertIn("account capacity", record["last_wait_reason"])

    def test_delivery_checks_live_process_duplicate_before_launch(self) -> None:
        self.config["ready_dispatcher"]["worker_os_duplicate_guard"] = True
        state = {"workers": {}, "queue": {"events": {}}}
        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[self.event]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [self.task]}),
            mock.patch.object(
                supervisor,
                "scan_live_worker_pids_by_agent",
                return_value={"Codex": [4321]},
            ),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                side_effect=AssertionError("duplicate process must not launch"),
            ),
        ):
            supervisor.process_queue(self.config, state, provider_report_fixture())
        record = state["queue"]["events"]["evt-1"]
        self.assertEqual(record["status"], "pending")
        self.assertIn("already has live worker", record["last_wait_reason"])

    def test_due_retry_returns_to_queue_and_never_launches(self) -> None:
        due = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        state = {
            "workers": {
                "run-old": {
                    "run_id": "run-old",
                    "task_id": "TASK-1",
                    "queue_event_id": "evt-1",
                    "provider": "codex",
                    "status": "retry_backoff",
                    "next_retry_at": due,
                }
            },
            "queue": {"events": {"evt-1": {"status": "retry_backoff"}}},
        }
        with (
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                side_effect=AssertionError("retry reconciler must not launch"),
            ),
        ):
            changed = supervisor.retry_due_workers(
                self.config,
                state,
                {},
                datetime.now(timezone.utc),
            )
        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-old"]["status"], "retry_queued")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "queued")


class AccountHealthAndRecoveryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = config_fixture()

    def test_runtime_account_migration_is_one_shot(self) -> None:
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "pause_kind": "quota_terminal",
                        "blocked_until": "9999-12-31T23:59:59Z",
                    }
                }
            },
            "workers": {
                "run": {
                    "provider": "codex",
                    "quota_group": "legacy",
                    "status": "running",
                }
            },
        }
        self.assertTrue(supervisor.migrate_runtime_account_state(self.config, state))
        self.assertIn("codex_account", state["provider_guardrails"]["dispatch_pauses"])
        self.assertEqual(state["workers"]["run"]["account"], "codex_account")
        self.assertNotIn("quota_group", state["workers"]["run"])
        self.assertFalse(supervisor.migrate_runtime_account_state(self.config, state))

    def test_runtime_account_migration_reacts_to_governed_topology_change(self) -> None:
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex_account": {
                        "provider": "codex_account",
                        "trigger_provider": "codex",
                        "pause_kind": "quota_terminal",
                        "blocked_until": "9999-12-31T23:59:59Z",
                    }
                }
            },
            "workers": {"run": {"provider": "codex", "account": "codex_account"}},
        }
        self.assertTrue(supervisor.migrate_runtime_account_state(self.config, state))
        self.config["providers"]["codex"]["account"] = "codex-primary"
        self.config["ready_dispatcher"]["max_concurrent_per_account"]["codex_primary"] = 2
        self.assertTrue(supervisor.migrate_runtime_account_state(self.config, state))
        self.assertIn("codex_primary", state["provider_guardrails"]["dispatch_pauses"])
        self.assertNotIn("codex_account", state["provider_guardrails"]["dispatch_pauses"])
        self.assertEqual(state["workers"]["run"]["account"], "codex_primary")

    def test_terminal_pause_reassigns_once_without_launching(self) -> None:
        task = task_fixture(reviewer="Human/Ops")
        state = {
            "account_runtime_schema_version": supervisor.ACCOUNT_RUNTIME_SCHEMA_VERSION,
            "workers": {},
            "queue": {"events": {}},
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex_account": {
                        "provider": "codex_account",
                        "pause_kind": "quota_terminal",
                        "blocked_until": "9999-12-31T23:59:59Z",
                    }
                }
            },
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "agent_provider_auth_blocked", return_value=False),
            mock.patch.object(
                supervisor,
                "persist_task_reassignment",
                return_value=True,
            ) as persist,
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                side_effect=AssertionError("recovery must not launch"),
            ),
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertTrue(changed)
        self.assertEqual(persist.call_args.kwargs["new_owner"], "Codex2")
        self.assertEqual(persist.call_args.kwargs["expected_owner"], "Codex")

    def test_unknown_or_missing_probe_does_not_reassign_known_lane(self) -> None:
        task = task_fixture()
        state = {"workers": {}, "queue": {"events": {}}, "provider_guardrails": {}}
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertFalse(changed)
        persist.assert_not_called()

    def test_explicit_human_reviewer_is_preserved(self) -> None:
        task = task_fixture(status="review", reviewer="Human/Ops")
        state = {"workers": {}, "queue": {"events": {}}, "provider_guardrails": {}}
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertFalse(changed)
        persist.assert_not_called()


class RuntimeAndFailureSemanticsTests(unittest.TestCase):
    def test_run_once_orders_launch_before_slow_maintenance(self) -> None:
        source = inspect.getsource(supervisor.run_once)
        self.assertLess(source.index('"dispatch_plan_transaction"'), source.index('"sync_github_bus"'))
        self.assertLess(source.index('"process_queue_reserved"'), source.index('"sync_github_bus"'))
        self.assertLess(source.index('"process_queue_reserved"'), source.index('"reconcile_blocked_tasks"'))
        self.assertNotIn("dispatch_chair_review", source)
        self.assertNotIn("discussion_planning", source)

    def test_safe_phase_contains_one_failure(self) -> None:
        result = supervisor._safe_phase(
            "injected",
            mock.Mock(side_effect=RuntimeError("boom")),
            quiet=True,
        )
        self.assertIsNone(result)

    def test_process_queue_failure_cannot_refresh_successful_loop(self) -> None:
        metrics = {
            "started_monotonic": 0.0,
            "phases": {},
            "batch_counts": {},
            "critical_phase_errors": [],
        }
        token = supervisor._CYCLE_METRICS.set(metrics)
        try:
            result = supervisor._safe_phase(
                "process_queue_reserved",
                mock.Mock(side_effect=RuntimeError("launch receipt failed")),
                quiet=True,
                critical=True,
            )
            self.assertIsNone(result)
            state = {
                "supervisor": {
                    "pid": os.getpid(),
                    "started_at": "2026-08-11T09:00:00Z",
                    "last_loop_started_at": "2026-08-11T10:00:00Z",
                    "last_successful_loop_at": "2026-08-11T09:59:00Z",
                },
                "workers": {},
                "queue": {"events": {}},
                "worker_worktrees": {"leases": {}},
            }
            config = config_fixture(Path("/tmp/supervisor-critical-phase-test"))
            with (
                mock.patch.object(supervisor, "load_runtime_state", return_value=state),
                mock.patch.object(supervisor, "save_runtime_state"),
                mock.patch.object(supervisor, "reconcile_queue_records", return_value=False),
                mock.patch.object(supervisor, "prune_event_queue", return_value=False),
                mock.patch.object(supervisor, "trim_worker_history"),
                mock.patch.object(supervisor, "trim_seen_events"),
                mock.patch.object(supervisor, "load_event_queue", return_value=[]),
                mock.patch.object(supervisor, "utc_now", return_value="2026-08-11T10:00:01Z"),
            ):
                supervisor._finalize_runtime_cycle_locked(
                    config,
                    quiet=True,
                    critical_phase_errors=tuple(metrics["critical_phase_errors"]),
                )
            self.assertEqual(state["supervisor"]["lifecycle"], "degraded")
            self.assertIn("process_queue_reserved", state["supervisor"]["last_loop_error"])
            self.assertEqual(
                state["supervisor"]["last_successful_loop_at"],
                "2026-08-11T09:59:00Z",
            )
        finally:
            supervisor._CYCLE_METRICS.reset(token)

    def test_task_projection_rejects_shadow_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = config_fixture(root)
            Path(config["paths"]["status_file"]).write_text('{"tasks": []}')
            state: dict[str, object] = {}
            with mock.patch.object(
                supervisor,
                "task_state_store_runtime_env",
                return_value={
                    "PANTHEON_TASK_STATE_STORE_MODE": "shadow",
                    "PANTHEON_TASK_STATE_EVENT_LOG": config["paths"]["task_state_event_log"],
                },
            ):
                changed = supervisor.sync_task_state_shadow(config, state)
        self.assertFalse(changed)
        report = state["supervisor"]["task_state_shadow"]
        self.assertFalse(report["ok"])
        self.assertIn("V2 requires authoritative", report["last_error"])

    def test_source_has_no_retired_control_planes(self) -> None:
        source = inspect.getsource(supervisor)
        retired_symbols = (
            "dispatch_chair_review",
            "apply_chair_review_decision",
            "dispatch_discussion_planning",
            "auto_materialize_discussion_planning",
            "record_task_failure_streak",
            "maybe_reassign_task_after_worker_failure",
            "worker_self_claim",
            "priority_preempt",
        )
        for symbol in retired_symbols:
            self.assertNotIn(symbol, source, symbol)

    def test_every_supervisor_task_status_write_uses_lifecycle_authority(self) -> None:
        tree = ast.parse(inspect.getsource(supervisor))
        writes: list[ast.Assign] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "task"
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "status"
                for target in node.targets
            ):
                writes.append(node)
                self.assertIsInstance(node.value, ast.Attribute)
                self.assertEqual(node.value.attr, "value")
                self.assertIsInstance(node.value.value, ast.Call)
                self.assertEqual(
                    ast.unparse(node.value.value.func),
                    "rewrite_task_machine.transition",
                )
        self.assertEqual(len(writes), 4)

    def test_file_worktree_quarantine_is_not_task_state(self) -> None:
        source = inspect.getsource(supervisor._quarantine_incomplete_worker_path)
        self.assertIn("ORCHESTRATOR_QUARANTINE.txt", source)
        self.assertNotIn("task[\"status\"]", source)


if __name__ == "__main__":
    unittest.main()
