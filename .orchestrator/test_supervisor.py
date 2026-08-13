#!/usr/bin/env python3
"""Contract tests for Supervisor Authority V2.

The previous file mirrored thousands of lines of implementation detail for
retired chair, discussion-planning, failure-streak, fallback,
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
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import supervisor
import runtime_state


_OLD_ENV: dict[str, str] = {}


@contextmanager
def _runtime_state_update(state: dict[str, object]):
    yield state


def setUpModule() -> None:
    global _OLD_ENV
    _OLD_ENV = dict(os.environ)
    for key in list(os.environ):
        if key.startswith("PANTHEON_"):
            del os.environ[key]


def tearDownModule() -> None:
    os.environ.clear()
    os.environ.update(_OLD_ENV)


class V2StartupCacheTests(unittest.TestCase):
    def test_task_projection_report_declares_authoritative_mode(self) -> None:
        state = runtime_state.default_state()
        snapshot = {
            "event_count": 1,
            "state": {"tasks": []},
            "state_sha256": "state-sha",
        }
        verification = {
            "ok": True,
            "projected_state_sha256": "state-sha",
            "expected_state_sha256": "state-sha",
        }

        with (
            mock.patch.object(
                supervisor,
                "task_state_store_runtime_env",
                return_value={"PANTHEON_TASK_STATE_EVENT_LOG": "/runtime/tasks-v2.jsonl"},
            ),
            mock.patch.object(
                supervisor,
                "config_path",
                return_value=Path("/runtime/ai-status.json"),
            ),
            mock.patch.object(supervisor, "canonical_task_state_lock_file") as lock,
            mock.patch.object(supervisor, "load_json", return_value={"tasks": []}),
            mock.patch.object(
                supervisor.rewrite_task_state_store,
                "load_snapshot",
                return_value=snapshot,
            ),
            mock.patch.object(
                supervisor.rewrite_task_state_store,
                "sha256_json",
                return_value="state-sha",
            ),
            mock.patch.object(
                supervisor.rewrite_task_state_store,
                "verify_snapshot",
                return_value=verification,
            ),
        ):
            lock.return_value.__enter__.return_value = None
            self.assertFalse(supervisor.sync_task_state_projection({}, state))

        report = state["supervisor"]["task_state_projection"]
        self.assertEqual(report["mode"], "authoritative")
        self.assertTrue(report["ok"])
        self.assertTrue(report["caught_up"])

    def test_ordinary_bootstrap_restores_existing_v2_cache(self) -> None:
        config = {"paths": {}}
        existing = runtime_state.default_state()
        existing["workers"] = {
            "run-active": {"status": "running", "queue_event_id": "evt-active"}
        }

        with (
            mock.patch.object(
                supervisor,
                "runtime_state_update",
                return_value=_runtime_state_update(existing),
            ) as update,
            mock.patch.object(supervisor, "stamp_supervisor_runtime_state"),
        ):
            result = supervisor.bootstrap_supervisor_runtime_state(config)

        self.assertIs(result, existing)
        update.assert_called_once_with(config)
        self.assertIn("run-active", result["workers"])

    def test_boot_reconciliation_does_not_require_retired_account_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".orchestrator").mkdir()
            config = config_fixture(root)
            state = runtime_state.default_state()
            with mock.patch.object(supervisor, "load_status", return_value={"tasks": []}):
                self.assertFalse(supervisor.reconcile_runtime_on_boot(config, state))

    def test_reserved_phase_can_publish_launch_intent_after_state_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_root = root / ".orchestrator"
            runtime_root.mkdir()
            config = {
                "paths": {
                    "status_file": str(root / "ai-status.json"),
                    "state_file": str(runtime_root / "state.json"),
                    "event_queue": str(runtime_root / "event-queue.jsonl"),
                }
            }
            (runtime_root / "event-queue.jsonl").write_text("", encoding="utf-8")
            runtime_state.save_runtime_state(config, runtime_state.default_state())

            def publish_intent(state: dict[str, object]) -> bool:
                request = supervisor.DeliveryRequest(
                    agent_id="codex",
                    provider="codex",
                    delivery_mode="codex",
                    message="wake",
                    task_id="TASK-V2",
                    reason=supervisor.REASON_OWNED_READY,
                    metadata={"task_generation": 1},
                )
                supervisor._persist_runtime_phase_launch_intent(
                    config,
                    state,
                    request=request,
                    queue_event_id="evt-v2",
                    attempt_count=1,
                    event_id_for_log="evt-v2",
                    parent_run_id=None,
                    adapter_name="codex",
                    activity_type="worker_started",
                    activity_message=None,
                )
                return True

            self.assertTrue(
                supervisor._run_reserved_runtime_phase(
                    config,
                    "process_queue",
                    publish_intent,
                )
            )
            final_state = runtime_state.load_runtime_state(config)
            self.assertNotIn(
                "process_queue",
                final_state["supervisor"]["runtime_phase_reservations"],
            )


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


def healthy_delivery_health(config: dict[str, object]) -> dict[str, object]:
    """Explicit live evidence for tests that are not about cold admission."""

    endpoints: dict[str, object] = {}
    accounts: dict[str, object] = {}
    for agent_id, agent in config["agents"].items():
        provider = str(agent["provider"])
        account = supervisor.normalize_agent_id(
            str(config["providers"][provider]["account"])
        )
        endpoints[agent_id] = {
            "state": "healthy",
            "valid_until": "2999-01-01T00:00:00Z",
        }
        accounts[account] = {
            "state": "healthy",
            "valid_until": "2999-01-01T00:00:00Z",
        }
    return {"version": 1, "endpoints": endpoints, "accounts": accounts}


def with_healthy_delivery_health(
    config: dict[str, object], state: dict[str, object]
) -> dict[str, object]:
    state.setdefault("delivery_health", healthy_delivery_health(config))
    return state


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
    state = with_healthy_delivery_health(
        config, state or {"workers": {}, "queue": {"events": {}}}
    )
    status = status or {"tasks": [task]}
    task_map = {str(item["id"]): item for item in status["tasks"]}
    active_statuses = set(
        supervisor.ready_dispatch_settings(config)["active_worker_statuses"]
    )
    _agents, active_pairs = supervisor.active_worker_indexes(state, active_statuses)
    _agents, pending_pairs, pending_keys = supervisor.outstanding_delivery_indexes(
        config, state, [], task_map
    )
    active_ids = {task_id for task_id, _agent in active_pairs if task_id}
    pending_ids = {task_id for task_id, _agent in pending_pairs if task_id}
    return supervisor.evaluate_dispatch_candidate(
        config,
        state,
        status,
        task,
        target,
        supervisor.task_resolver_for_config(config, task_map),
        settings=supervisor.ready_dispatch_settings(config),
        active_task_ids=active_task_ids if active_task_ids is not None else active_ids,
        pending_task_ids=pending_task_ids if pending_task_ids is not None else pending_ids,
        pending_event_keys=pending_keys,
        agent_loads=supervisor.agent_dispatch_loads(
            config, state, active_statuses, [], task_map
        ),
        active_account_loads=supervisor.active_account_counts(
            config, state, active_statuses
        ),
        pending_account_loads=supervisor.queued_account_counts(
            config, state, [], task_map
        ),
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

    def test_repo_codex_slots_inherit_logical_lane_capacity(self) -> None:
        config = json.loads(Path(__file__).with_name("config.json").read_text())
        for agent_id in ("codex", "codex2"):
            with self.subTest(agent_id=agent_id):
                lane = supervisor.delivery_lane_for_agent(config, agent_id)
                self.assertTrue(lane.endpoints)
                self.assertTrue(all(endpoint.enabled for endpoint in lane.endpoints))
                self.assertTrue(
                    all(endpoint.account_id for endpoint in lane.endpoints)
                )
                for slot_id in supervisor.logical_worker_slot_ids(config, agent_id):
                    self.assertNotIn("max_parallel", config["agents"][slot_id])

    def test_retired_capacity_fields_fail_closed(self) -> None:
        for retired in (
            "disabled_agents",
            "max_tasks_per_agent",
            "max_tasks_per_agent_by_agent",
            "max_concurrent_per_quota_group",
            "preferred_lane_order",
            "preferredLaneOrder",
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
        decision = planner_decision(
            config,
            task_fixture(),
            state=with_healthy_delivery_health(config, {"workers": {}, "queue": {"events": {}}}),
        )
        self.assertEqual(decision["first_blocking_gate"], "account_capacity_reached")


class SharedPlannerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = config_fixture()

    def test_owner_todo_is_dispatchable(self) -> None:
        decision = planner_decision(self.config, task_fixture())
        self.assertTrue(decision["eligible"])
        self.assertEqual(decision["reason"], supervisor.REASON_OWNED_READY)

    def test_retired_lane_hints_and_priority_cannot_reenter_dispatch(self) -> None:
        """Legacy L12 metadata must not override owner/reviewer admission.

        PR #4795-era packets may still contain these historical fields in the
        canonical board.  They remain readable, but the planner must neither
        reroute them nor let their declared priority bypass the shared
        admission evaluator and canonical board order.
        """

        self.config["ready_dispatcher"]["max_concurrent_workers"] = 1
        legacy_task = task_fixture(
            "SUP-L12-HELPER-CLAIM-BUSY-PREFERRED-LANE-20260729",
        )
        legacy_task.update(
            {
                "preferred_lane_order": ["Codex2"],
                "preferredLaneOrder": ["Codex2"],
                "priority": "P9",
            }
        )
        ordinary_task = task_fixture("TASK-ORDINARY-1")
        ordinary_task["priority"] = "P0"
        queued: list[dict[str, object]] = []
        state = with_healthy_delivery_health(
            self.config,
            {"workers": {}, "queue": {"events": {}}, "seen_event_keys": {}},
        )

        changed = supervisor.dispatch_ready_tasks(
            self.config,
            state,
            agent_ids_override=["codex"],
            status_snapshot={"tasks": [legacy_task, ordinary_task]},
            queue_events_snapshot=[],
            live_total_snapshot=0,
            event_sink=lambda _config, event: queued.append(event) or True,
        )

        self.assertTrue(changed)
        self.assertEqual([event["task_id"] for event in queued], [legacy_task["id"]])
        self.assertEqual(queued[0]["target_agent"], "Codex")
        self.assertNotIn("preferred_lane_order", queued[0]["task"])
        self.assertNotIn("preferredLaneOrder", queued[0]["task"])
        wrong_owner = planner_decision(self.config, legacy_task, target="Codex2")
        self.assertFalse(wrong_owner["eligible"])
        self.assertEqual(wrong_owner["first_blocking_gate"], "task_not_dispatchable")

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
            "task_not_dispatchable",
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
        self.assertEqual(active["first_blocking_gate"], "task_leased")
        self.assertEqual(pending["first_blocking_gate"], "task_pending")

    def test_planner_uses_only_supplied_snapshots(self) -> None:
        self.config["ready_dispatcher"]["worker_os_duplicate_guard"] = True
        with (
            mock.patch.object(
                supervisor,
                "scan_live_worker_pids_by_agent",
                side_effect=AssertionError("planner must not scan live processes"),
            ),
        ):
            decision = planner_decision(self.config, task_fixture())
        self.assertTrue(decision["eligible"])

    def test_dispatch_reserves_intent_without_launching(self) -> None:
        task = task_fixture()
        state = with_healthy_delivery_health(
            self.config, {"workers": {}, "queue": {"events": {}}, "seen_event_keys": {}}
        )
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
        state = with_healthy_delivery_health(
            self.config, {"workers": {}, "queue": {"events": {}}, "seen_event_keys": {}}
        )
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
            with_healthy_delivery_health(
                self.config, {"workers": {}, "queue": {"events": {}}, "seen_event_keys": {}}
            ),
            {"tasks": [task_fixture()]},
            [],
            live_total=0,
        )
        self.assertEqual(len(plan["events"]), 1)
        self.config["ready_dispatcher"]["max_concurrent_per_account"] = {
            "codex-account": 0
        }
        state = with_healthy_delivery_health(
            self.config, {"workers": {}, "queue": {"events": {}}, "seen_event_keys": {}}
        )
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
        self.assertEqual(decision["first_blocking_gate"], "account_capacity_reached")


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
                "delivery_endpoint_id": "codex",
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
            state = with_healthy_delivery_health(
                config, {"workers": {}, "queue": {"events": {}}, "seen_event_keys": {}}
            )
            plan = supervisor.build_dispatch_plan(
                config,
                state,
                {"tasks": [task]},
                [],
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
                    supervisor.process_queue(config, state)
                )
            request = launch.call_args.args[2]
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
        state = with_healthy_delivery_health(
            self.config, {"workers": {}, "queue": {"events": {}}}
        )
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
            changed = supervisor.process_queue(self.config, state)
        self.assertTrue(changed)
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "started")
        launch.assert_called_once()

    def test_missing_or_stale_auth_evidence_stays_pending(self) -> None:
        stale = {
            "version": 1,
            "endpoints": {
                "codex": {"state": "healthy", "valid_until": "2000-01-01T00:00:00Z"}
            },
            "accounts": {
                "codex-account": {"state": "healthy", "valid_until": "2000-01-01T00:00:00Z"}
            },
        }
        for label, health in (("missing", {}), ("stale", stale)):
            with self.subTest(label=label):
                state = {"workers": {}, "queue": {"events": {}}, "delivery_health": health}
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
                    supervisor.process_queue(self.config, state)
                record = state["queue"]["events"]["evt-1"]
                self.assertEqual(record["status"], "pending")
                self.assertEqual(record["last_wait_reason"], "health_refresh_required")

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
                "delivery_endpoint_id": "codex",
                "message": "wake",
            }
        )
        state = with_healthy_delivery_health(
            self.config, {"workers": {}, "queue": {"events": {}}}
        )
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
            supervisor.process_queue(self.config, state)
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
            supervisor.process_queue(self.config, state)
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
            supervisor.process_queue(self.config, state)
        record = state["queue"]["events"]["evt-1"]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["skip_reason"], "unsupported_dispatch_reason")

    def test_delivery_revalidates_account_capacity(self) -> None:
        self.config["providers"]["codex2"]["account"] = "codex-account"
        state = with_healthy_delivery_health(self.config, {
            "workers": {
                "busy": {
                    "run_id": "busy",
                    "agent_id": "codex2",
                    "provider": "codex2",
                    "task_id": "OTHER",
                    "status": "running",
                }
            },
            "queue": {"events": {}},
        })
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
            supervisor.process_queue(self.config, state)
        record = state["queue"]["events"]["evt-1"]
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["last_wait_reason"], "account_capacity_reached")

    def test_delivery_allows_parallel_non_slot_endpoint_within_lane_capacity(self) -> None:
        state = with_healthy_delivery_health(self.config, {
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
        })
        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[self.event]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [self.task]}),
            mock.patch.object(
                supervisor,
                "build_request",
                return_value=supervisor.DeliveryRequest(
                    agent_id="codex",
                    provider="codex",
                    delivery_mode="codex",
                    message="wake",
                    task_id="TASK-1",
                    reason=supervisor.REASON_OWNED_READY,
                    metadata={"task_generation": 1, "workspace_path": "/tmp/task-1"},
                ),
            ),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                return_value=(True, "run-2", {"auto_delivered": True}),
            ) as launch,
            mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)),
            mock.patch.object(supervisor, "check_worker_tree_clean", return_value=(True, None)),
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
        ):
            supervisor.process_queue(self.config, state)
        record = state["queue"]["events"]["evt-1"]
        self.assertEqual(record["status"], "started")
        launch.assert_called_once()

    def test_pruned_orphan_does_not_cool_down_the_unserved_task(self) -> None:
        event = {
            **self.event,
            "created_at": "2026-08-01T00:00:00Z",
        }
        event_key = str(event["event_key"])
        state = {
            "workers": {},
            "queue": {
                "events": {
                    "evt-1": {
                        "status": "queued",
                        "event_key": event_key,
                    }
                }
            },
            "seen_event_keys": {event_key: "2026-08-13T03:05:38Z"},
        }
        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[event]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [self.task]}),
            mock.patch.object(supervisor, "save_event_queue") as save_queue,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertTrue(supervisor.prune_event_queue(self.config, state))

        self.assertNotIn(event_key, state["seen_event_keys"])
        self.assertEqual(state["queue"]["events"], {})
        save_queue.assert_called_once_with(self.config, [])

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
                datetime.now(timezone.utc),
            )
        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-old"]["status"], "retry_queued")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "queued")


class AccountHealthAndRecoveryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = config_fixture()

    def test_runtime_health_normalizes_once(self) -> None:
        state = {"delivery_health": {"endpoints": [], "accounts": []}}
        self.assertTrue(supervisor.normalize_runtime_delivery_health(state))
        self.assertEqual(state["delivery_health"], {"version": 1, "endpoints": {}, "accounts": {}})
        self.assertFalse(supervisor.normalize_runtime_delivery_health(state))

    def test_terminal_pause_reassigns_once_without_launching(self) -> None:
        task = task_fixture(reviewer="Human/Ops")
        state = {
            "workers": {},
            "queue": {"events": {}},
            "delivery_health": {
                "version": 1,
                "endpoints": {
                    "codex": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                    "codex2": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                },
                "accounts": {
                    "codex_account": {
                        "state": "retry_after",
                        "reason_kind": "quota_terminal",
                        "retry_at": "2999-01-01T00:00:00Z",
                    },
                    "codex2_account": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                },
            },
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
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

    def test_terminal_static_endpoint_reassigns_without_waiting_forever(self) -> None:
        task = task_fixture(reviewer="Human/Ops")
        self.config["agents"]["codex"]["provider"] = "missing-provider"
        state = {
            "workers": {},
            "queue": {"events": {}},
            "delivery_health": {
                "version": 1,
                "endpoints": {
                    "codex2": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    }
                },
                "accounts": {
                    "codex2_account": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    }
                },
            },
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(
                supervisor, "persist_task_reassignment", return_value=True
            ) as persist,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertTrue(changed)
        self.assertEqual(persist.call_args.kwargs["new_owner"], "Codex2")
        self.assertIn(
            "configured_no_delivery_endpoint",
            persist.call_args.kwargs["message"],
        )

    def test_configured_fallback_cycle_exhausts_remaining_healthy_roster(self) -> None:
        task = task_fixture(reviewer="Human/Ops")
        self.config["agents"]["claude"] = {
            "display_name": "Claude",
            "provider": "claude",
            "adapter": "codex",
            "max_parallel": 1,
        }
        self.config["providers"]["claude"] = {
            "delivery_mode": "codex",
            "account": "claude-account",
        }
        self.config["ready_dispatcher"]["max_concurrent_per_account"][
            "claude-account"
        ] = 1
        state = {
            "workers": {},
            "queue": {"events": {}},
            "delivery_health": {
                "version": 1,
                "endpoints": {
                    "codex": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    },
                    "claude": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    },
                },
                "accounts": {
                    "codex_account": {
                        "state": "retry_after",
                        "reason_kind": "quota_terminal",
                        "retry_at": "2999-01-01T00:00:00Z",
                    },
                    "claude_account": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    },
                },
            },
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(
                supervisor, "persist_task_reassignment", return_value=True
            ) as persist,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertTrue(changed)
        self.assertEqual(persist.call_args.kwargs["new_owner"], "Claude")

    def test_terminal_assignment_demands_health_for_unknown_fallback(self) -> None:
        task = task_fixture(reviewer="Human/Ops")
        self.config["agents"]["claude"] = {
            "display_name": "Claude",
            "provider": "claude",
            "adapter": "codex",
            "max_parallel": 1,
        }
        self.config["providers"]["claude"] = {
            "delivery_mode": "codex",
            "account": "claude-account",
        }
        self.config["ready_dispatcher"]["max_concurrent_per_account"][
            "claude-account"
        ] = 1
        state = {
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
                        "state": "retry_after",
                        "reason_kind": "quota_terminal",
                        "retry_at": "2999-01-01T00:00:00Z",
                    }
                },
            },
        }
        plan = supervisor.build_dispatch_plan(
            self.config,
            state,
            {"tasks": [task]},
            [],
            live_total=0,
        )
        self.assertIn(
            {"scope": "endpoint", "id": "claude"},
            plan["health_refresh_targets"],
        )

    def test_terminal_pause_reopens_stale_blocked_assignment_for_normal_dispatch(self) -> None:
        task = task_fixture(status="blocked", reviewer="Human/Ops")
        state = {
            "workers": {},
            "queue": {"events": {}},
            "delivery_health": {
                "version": 1,
                "endpoints": {
                    "codex": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                    "codex2": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                },
                "accounts": {
                    "codex_account": {
                        "state": "retry_after",
                        "reason_kind": "quota_terminal",
                        "retry_at": "2999-01-01T00:00:00Z",
                    },
                    "codex2_account": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                },
            },
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(
                supervisor,
                "persist_task_reassignment",
                return_value=True,
            ) as persist,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertTrue(changed)
        self.assertEqual(persist.call_args.kwargs["new_owner"], "Codex2")
        self.assertEqual(
            persist.call_args.kwargs["lifecycle_action"],
            supervisor.rewrite_task_machine.TaskAction.REOPEN,
        )
        self.assertEqual(persist.call_args.kwargs["expected_status"], "blocked")
        resumed = task_fixture(
            status=supervisor.rewrite_task_machine.transition(
                "blocked", supervisor.rewrite_task_machine.TaskAction.REOPEN.value
            ).value,
            owner="Codex2",
            reviewer="Human/Ops",
        )
        self.assertTrue(
            planner_decision(self.config, resumed, target="Codex2")["eligible"]
        )

    def test_terminal_pause_never_reopens_explicit_human_ops_hold(self) -> None:
        task = task_fixture(status="blocked", reviewer="Human/Ops")
        task["waiting_for"] = "Human/Ops"
        state = {"workers": {}, "queue": {"events": {}}}
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertFalse(changed)
        persist.assert_not_called()

    def test_unknown_or_missing_probe_does_not_reassign_known_lane(self) -> None:
        task = task_fixture()
        state = {"workers": {}, "queue": {"events": {}}}
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
        state = {"workers": {}, "queue": {"events": {}}}
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
        self.assertNotIn('"reconcile_blocked_tasks"', source)
        self.assertLess(source.index('"process_queue_reserved"'), source.index("probe_demanded_delivery_health"))
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
