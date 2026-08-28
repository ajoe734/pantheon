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
import copy
import inspect
import json
import os
import subprocess
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
from adapters.base import DeliveryResult


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

    def test_schedule_reconstructable_worker_retry_does_not_raise_without_state_param(self) -> None:
        """schedule_reconstructable_worker_retry must accept state, not close over a
        caller-scope name of the same spelling.

        It calls request_for_worker(config, state, worker), which requires an
        actual `state` parameter. A prior version of this function had no
        such parameter -- Python resolved the bare name `state` used inside
        the call by raising NameError at call time (not import time), so
        nothing caught it until reconcile_runtime_on_boot's boot-reconciliation
        path actually hit a missing-process worker. No existing test built a
        worker record with a dead process, so the gap went unnoticed until it
        fired live and repeatedly failed 'apply_post_dispatch_maintenance'
        (16 times in one live session on 2026-08-17, non-fatal but recurring)
        before OPS-SUPERVISOR-RETRY-MISSING-STATE-20260817 fixed the missing
        parameter. This test calls the function directly (not through the
        much larger reconcile_runtime_on_boot) so a future regression is
        caught by a NameError at the narrowest possible point.
        """

        config = config_fixture()
        state = {"workers": {}, "queue": {"events": {}}}
        worker = {
            "provider": "codex",
            "request_snapshot": {
                "agent_id": "codex",
                "provider": "codex",
                "delivery_mode": "codex",
                "message": "wake",
            },
        }

        result = supervisor.schedule_reconstructable_worker_retry(
            config, state, worker, "worker process missing"
        )

        self.assertTrue(result)
        self.assertEqual(worker.get("status"), "retry_backoff")

    def test_reconstructable_worker_retry_honors_canonical_explicit_hold(self) -> None:
        config = config_fixture()
        task = task_fixture(status="blocked")
        task["waiting_for"] = "Human/Ops"
        status = {
            "tasks": [task],
            "blockers": [
                {
                    "task_id": "TASK-1",
                    "status": "open",
                    "waiting_for": "Human/Ops",
                }
            ],
        }
        state = {"workers": {}, "queue": {"events": {}}}
        worker = {
            "provider": "codex",
            "request_snapshot": {
                "agent_id": "codex",
                "provider": "codex",
                "delivery_mode": "codex",
                "message": "wake",
            },
        }

        with mock.patch.object(
            supervisor,
            "request_for_worker",
            side_effect=AssertionError("explicit hold must stop before retry reconstruction"),
        ):
            result = supervisor.schedule_reconstructable_worker_retry(
                config,
                state,
                worker,
                "worker process missing",
                status=status,
                task=task,
            )

        self.assertFalse(result)
        self.assertNotIn("status", worker)

    def test_reconstructable_worker_retry_stops_at_total_retry_budget(self) -> None:
        config = config_fixture()
        config["worker_retry"] = {"enabled": True, "max_attempts": 1}
        state = {"workers": {}, "queue": {"events": {}}}
        worker = {
            "provider": "codex",
            "retry_count": 1,
            "request_snapshot": {
                "agent_id": "codex",
                "provider": "codex",
                "delivery_mode": "codex",
                "message": "wake",
            },
        }

        self.assertFalse(
            supervisor.schedule_reconstructable_worker_retry(
                config, state, worker, "worker lease expired"
            )
        )
        self.assertNotIn("status", worker)

    def test_boot_reconciliation_completes_worker_on_explicit_task_hold(self) -> None:
        config = config_fixture()
        task = task_fixture(status="blocked")
        task["waiting_for"] = "Human/Ops"
        status = {
            "tasks": [task],
            "blockers": [
                {
                    "task_id": "TASK-1",
                    "status": "open",
                    "waiting_for": "Human/Ops",
                }
            ],
        }
        worker = {
            "run_id": "run-finalize",
            "status": "running",
            "task_id": "TASK-1",
            "provider": "codex",
            "agent_id": "codex",
            "pid": 999999,
            "request_snapshot": {"reason": supervisor.REASON_OWNED_FINALIZE},
        }
        state = {"workers": {"run-finalize": worker}, "queue": {"events": {}}}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "update_worker_runtime_markers", return_value=False),
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize,
            mock.patch.object(supervisor, "write_activity_log") as activity,
            mock.patch.object(
                supervisor,
                "schedule_reconstructable_worker_retry",
                side_effect=AssertionError("explicit canonical hold must not retry"),
            ),
        ):
            changed = supervisor.reconcile_runtime_on_boot(config, state)

        self.assertTrue(changed)
        self.assertEqual(worker["status"], "completed")
        finalize.assert_called_once_with(config, state, worker, "completed")
        self.assertEqual(
            activity.call_args.args[1]["type"],
            "worker_completed_on_explicit_task_hold",
        )

    def test_boot_reconciliation_retries_expired_lease_from_original_intent(self) -> None:
        config = config_fixture()
        task = task_fixture(status="in_progress")
        status = {"tasks": [task], "blockers": []}
        worker = {
            "run_id": "run-expired-boot",
            "status": "running",
            "task_id": "TASK-1",
            "provider": "codex",
            "agent_id": "codex",
            "pid": 4242,
            "request_snapshot": {
                "agent_id": "codex",
                "provider": "codex",
                "delivery_mode": "codex",
                "message": "resume exact intent",
            },
        }
        state = {"workers": {"run-expired-boot": worker}, "queue": {"events": {}}}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "update_worker_runtime_markers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "worker_lease_is_expired", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid", return_value=True),
            mock.patch.object(supervisor, "canonical_worker_terminal_status", return_value=None),
            mock.patch.object(supervisor, "worker_runner_succeeded", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertTrue(supervisor.reconcile_runtime_on_boot(config, state))

        self.assertEqual(worker["status"], "retry_backoff")
        self.assertEqual(worker["retry_count"], 1)

    def test_command_runtime_health_failure_is_one_global_dispatch_hold(self) -> None:
        config = config_fixture()
        with mock.patch.object(
            supervisor,
            "status_command_runtime_env",
            side_effect=RuntimeError(
                "PANTHEON_COMMAND_ROOT contains dirty executable/import file: scripts/ai_status.py"
            ),
        ):
            health = supervisor.inspect_command_runtime_health(config)

        state = {"supervisor": {}}
        self.assertTrue(supervisor.record_command_runtime_health(state, health))
        reason = supervisor.command_runtime_dispatch_block_reason(state)
        self.assertFalse(health["healthy"])
        self.assertIn("scripts/ai_status.py", reason)
        with mock.patch.object(
            supervisor,
            "load_status",
            side_effect=AssertionError("blocked queue must not read or launch task work"),
        ):
            self.assertFalse(supervisor.process_queue(config, state))

    def test_terminal_facts_satisfy_dependencies_without_archive_lookup(self) -> None:
        config = config_fixture()
        child = task_fixture(task_id="CHILD", depends_on=["MERGED-LEGACY"])
        status = {
            "tasks": [child],
            "terminal_facts": {
                "MERGED-LEGACY": {
                    "status": "done",
                    "terminal_outcome": "completed",
                    "generation": 4,
                    "recorded_at": "2026-08-14T00:00:00Z",
                }
            },
        }

        task_map = supervisor.task_index_from_status(config, status)
        resolver = supervisor.task_resolver_for_config(config, task_map)

        self.assertEqual(resolver.source("MERGED-LEGACY"), "active")
        self.assertTrue(
            supervisor.dependencies_satisfied(
                child,
                resolver,
                {"done"},
            )
            )

    def test_terminal_fact_completion_track_is_preserved_for_dispatch(self) -> None:
        config = config_fixture()
        child = task_fixture(
            task_id="CHILD-HOSTED",
            status="in_progress",
            depends_on=["MERGED-LEGACY"],
        )
        child["dependency_tracks"] = {"MERGED-LEGACY": "hosted"}
        status = {
            "tasks": [child],
            "terminal_facts": {
                "MERGED-LEGACY": {
                    "status": "done",
                    "terminal_outcome": "completed",
                    "generation": 4,
                    "recorded_at": "2026-08-14T00:00:00Z",
                    "completion_tracks": {
                        "hosted": {
                            "status": "done",
                            "updated_at": "2026-08-15T00:00:00Z",
                        }
                    },
                }
            },
        }

        task_map = supervisor.task_index_from_status(config, status)
        resolver = supervisor.task_resolver_for_config(config, task_map)

        self.assertEqual(
            resolver.get("MERGED-LEGACY")["completion_tracks"]["hosted"]["status"],
            "done",
        )
        self.assertTrue(
            supervisor.dependencies_satisfied(
                child,
                resolver,
                {"done"},
            )
        )
        self.assertEqual(
            supervisor.task_execution_dispatch_candidate(
                config,
                child,
                "Codex",
                resolver,
            ),
            (supervisor.REASON_OWNED_IN_PROGRESS, 2),
        )

    def test_terminal_fact_external_wait_does_not_release_dispatch(self) -> None:
        config = config_fixture()
        child = task_fixture(
            task_id="CHILD-HOSTED-WAIT",
            status="in_progress",
            depends_on=["MERGED-LEGACY"],
        )
        child["dependency_tracks"] = {"MERGED-LEGACY": "hosted"}
        status = {
            "tasks": [child],
            "terminal_facts": {
                "MERGED-LEGACY": {
                    "status": "done",
                    "terminal_outcome": "completed",
                    "generation": 4,
                    "recorded_at": "2026-08-14T00:00:00Z",
                    "completion_tracks": {
                        "hosted": {"status": "external_wait"},
                    },
                }
            },
        }

        task_map = supervisor.task_index_from_status(config, status)
        resolver = supervisor.task_resolver_for_config(config, task_map)

        self.assertFalse(
            supervisor.dependencies_satisfied(
                child,
                resolver,
                {"done"},
            )
        )
        self.assertIsNone(
            supervisor.task_execution_dispatch_candidate(
                config,
                child,
                "Codex",
                resolver,
            )
        )

    def test_dispatch_key_ignores_observability_timestamp(self) -> None:
        config = config_fixture()
        task = task_fixture()
        resolver = supervisor.task_resolver_for_config(config, {"TASK-1": task})
        first = supervisor.ready_dispatch_signature(
            task, supervisor.REASON_OWNED_READY, resolver
        )
        task["last_update"] = "2026-08-15T12:00:00Z"
        second = supervisor.ready_dispatch_signature(
            task, supervisor.REASON_OWNED_READY, resolver
        )

        self.assertEqual(first, second)

    def test_dispatch_payload_projects_canonical_dependency_truth(self) -> None:
        task = task_fixture(depends_on=["DEP"])
        dependency = task_fixture(task_id="DEP", status="done")
        dependency["terminal_outcome"] = "completed"

        event = supervisor.build_dispatch_event(
            task,
            "Codex",
            supervisor.REASON_OWNED_READY,
            {"TASK-1": task, "DEP": dependency},
        )

        self.assertEqual(event["task"]["depends_on"], ["DEP"])
        self.assertEqual(
            event["task"]["dependency_truth"],
            [{"task_id": "DEP", "status": "done", "satisfied": True}],
        )

    def test_dispatch_payload_projects_explicit_completion_track_truth(self) -> None:
        task = task_fixture(depends_on=["DEP"])
        task["dependency_tracks"] = {"DEP": "functional"}
        dependency = task_fixture(task_id="DEP", status="blocked")
        dependency["completion_tracks"] = {
            "functional": {"status": "done"},
            "hosted": {"status": "external_wait"},
        }

        event = supervisor.build_dispatch_event(
            task,
            "Codex",
            supervisor.REASON_OWNED_READY,
            {"TASK-1": task, "DEP": dependency},
        )

        self.assertEqual(
            event["task"]["dependency_truth"],
            [{
                "task_id": "DEP",
                "track": "functional",
                "status": "done",
                "satisfied": True,
            }],
        )

    def test_build_dispatch_event_preserves_target_repo_and_metadata(self) -> None:
        task = task_fixture(task_id="FE-TASK-001")
        task["target_repo"] = "execute-plans"
        task["metadata"] = {"feature_flag": "enabled"}
        task["source_ref"] = {"branch": "dev"}

        event = supervisor.build_dispatch_event(
            task,
            "Codex",
            supervisor.REASON_OWNED_READY,
            {"FE-TASK-001": task},
        )

        self.assertEqual(event["task"]["target_repo"], "execute-plans")
        self.assertEqual(event["task"]["metadata"], {"feature_flag": "enabled"})
        self.assertEqual(event["task"]["source_ref"], {"branch": "dev"})

    def test_reserved_phase_can_publish_launch_intent_after_state_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_root = root / ".orchestrator"
            runtime_root.mkdir()
            config = {
                "paths": {
                    "status_file": str(root / "ai-status.json"),
                    "state_file": str(runtime_root / "state.json"),
                }
            }
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

    def test_lost_reserved_phase_cannot_leave_a_detached_queue_intent(self) -> None:
        """A CAS loser must leave neither a lease nor an untracked intent."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".orchestrator").mkdir()
            config = config_fixture(root)
            runtime_state.save_runtime_state(config, runtime_state.default_state())
            intent = {
                "event_id": "evt-cas-loser",
                "task_id": "TASK-CAS",
                "task_generation": 1,
                "event_key": "task-cas-v1",
                "reason": supervisor.REASON_OWNED_READY,
                "target_agent": "codex",
            }

            def mutate_scratch_then_advance_runtime(
                scratch: dict[str, object],
            ) -> bool:
                runtime_state.store_queue_event(scratch, intent)
                with runtime_state.runtime_state_update(config) as current:
                    current["supervisor"]["last_heartbeat_at"] = "2026-08-14T12:00:00Z"
                return True

            with mock.patch.object(supervisor, "write_activity_log"):
                self.assertFalse(
                    supervisor._run_reserved_runtime_phase(
                        config,
                        "process_queue",
                        mutate_scratch_then_advance_runtime,
                    )
                )
            final_state = runtime_state.load_runtime_state(config)

        self.assertNotIn("evt-cas-loser", final_state["queue"]["events"])
        self.assertEqual(final_state["supervisor"]["last_heartbeat_at"], "2026-08-14T12:00:00Z")


def config_fixture(root: Path | None = None) -> dict[str, object]:
    paths: dict[str, str] = {}
    if root is not None:
        paths = {
            "status_file": str(root / "ai-status.json"),
            "state_file": str(root / ".orchestrator" / "supervisor.json"),
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
    execution_resources: list[str] | None = None,
) -> dict[str, object]:
    result = {
        "id": task_id,
        "generation": 1,
        "status": status,
        "owner": owner,
        "reviewer": reviewer,
        "depends_on": list(depends_on or []),
        "last_update": "2026-08-11T00:00:00Z",
    }
    if execution_resources is not None:
        result["execution_resources"] = list(execution_resources)
    return result


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


def with_queue_intents(
    state: dict[str, object],
    *events: dict[str, object],
) -> dict[str, object]:
    """Build a state-owned queue fixture without reviving external JSONL."""

    for event in events:
        runtime_state.store_queue_event(state, event)
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


class PantheonWorkerTaskBriefHygieneTests(unittest.TestCase):
    TASK_ID = "SUP-BRIEF-HYGIENE-TEST"
    BRIEF_PATH = ".orchestrator/task-briefs/sup_brief_hygiene_test.md"

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout.strip()

    def _fixture(
        self,
        root: Path,
        *,
        tracked_brief: str | None,
    ) -> tuple[dict[str, object], dict[str, object], Path, Path]:
        status_root = root / "status"
        source_root = root / "pantheon"
        status_root.mkdir()
        source_root.mkdir()
        self._git(source_root, "init", "-b", "dev")
        self._git(source_root, "config", "user.name", "Test")
        self._git(source_root, "config", "user.email", "test@example.com")
        (source_root / ".gitignore").write_text(
            ".orchestrator/worker-runtime/\n",
            encoding="utf-8",
        )
        (source_root / "AI_COLLABORATION_GUIDE.md").write_text(
            "worker instructions\n",
            encoding="utf-8",
        )
        if tracked_brief is not None:
            brief = source_root / self.BRIEF_PATH
            brief.parent.mkdir(parents=True)
            brief.write_text(tracked_brief, encoding="utf-8")
        self._git(source_root, "add", ".gitignore", "AI_COLLABORATION_GUIDE.md")
        if tracked_brief is not None:
            self._git(source_root, "add", self.BRIEF_PATH)
        self._git(source_root, "commit", "-m", "initial")
        head = self._git(source_root, "rev-parse", "HEAD")
        self._git(
            source_root,
            "remote",
            "add",
            "origin",
            "https://github.com/ajoe734/pantheon.git",
        )
        self._git(source_root, "update-ref", "refs/remotes/origin/dev", head)

        config = config_fixture(status_root)
        config.update(
            {
                "branch_workflow": {
                    "task_branch_prefix": "task/",
                    "dev_branch": "dev",
                },
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(root / "worker-worktrees"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                    "execution_reasons": [
                        supervisor.REASON_OWNED_READY,
                        supervisor.REASON_REVIEW_READY,
                    ],
                },
                "worker_worktree_cleanup": {
                    "enabled": True,
                    "cleanup_inactive_leases": True,
                    "archive_dirty_worktrees": True,
                    "force_remove_archived_dirty": True,
                    "archive_root": str(root / "worktree-archive"),
                    "max_removals_per_tick": 10,
                },
                "coordination": {
                    "repositories": {
                        "pantheon": {
                            "local_path": str(source_root),
                        }
                    }
                },
            }
        )
        task = task_fixture(self.TASK_ID)
        task.update(
            {
                "title": "Keep task briefs clean",
                "summary_zh": "brief hygiene",
                "next": "owner implementation",
                "artifacts": [".orchestrator/supervisor.py"],
            }
        )
        return config, task, status_root, source_root

    @staticmethod
    def _request(
        task: dict[str, object],
        *,
        agent_id: str,
        reason: str,
    ) -> supervisor.DeliveryRequest:
        return supervisor.DeliveryRequest(
            agent_id=agent_id,
            provider=agent_id,
            delivery_mode="codex",
            message=(
                "read task context\n"
                f"- {PantheonWorkerTaskBriefHygieneTests.BRIEF_PATH}\n"
            ),
            task_id=str(task["id"]),
            reason=reason,
            context_files=[PantheonWorkerTaskBriefHygieneTests.BRIEF_PATH],
            target_files=list(task["artifacts"]),
            metadata={"task": task, "task_generation": 1},
        )

    def _prepare(
        self,
        config: dict[str, object],
        state: dict[str, object],
        task: dict[str, object],
        *,
        agent_id: str,
        reason: str,
        queue_event_id: str,
    ) -> supervisor.DeliveryRequest:
        request = self._request(task, agent_id=agent_id, reason=reason)
        with (
            mock.patch.object(
                supervisor,
                "_fetch_worker_base_ref",
                return_value=(True, None),
            ),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(
                supervisor,
                "load_status",
                return_value={"tasks": [task]},
            ),
        ):
            ok, error = supervisor.prepare_worker_workspace(
                config,
                state,
                request,
                queue_event_id=queue_event_id,
                target_agent=agent_id,
            )
        self.assertTrue(ok, error)
        return request

    def test_tracked_brief_is_read_only_for_owner_and_reviewer_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reviewed_bytes = "reviewed branch task brief\n"
            config, task, status_root, _source_root = self._fixture(
                root,
                tracked_brief=reviewed_bytes,
            )
            status_brief = status_root / self.BRIEF_PATH
            status_brief.parent.mkdir(parents=True)
            status_brief.write_text(
                "live owner status and next text\n",
                encoding="utf-8",
            )
            state: dict[str, object] = {"worker_worktrees": {"leases": {}}}

            owner_request = self._prepare(
                config,
                state,
                task,
                agent_id="codex",
                reason=supervisor.REASON_OWNED_READY,
                queue_event_id="evt-owner",
            )
            workspace = Path(str(owner_request.metadata["workspace_path"]))
            self.assertEqual(
                (workspace / self.BRIEF_PATH).read_text(encoding="utf-8"),
                reviewed_bytes,
            )
            self.assertEqual(self._git(workspace, "status", "--porcelain"), "")

            task["status"] = "review"
            task["next"] = "dynamic reviewer status and next text"
            status_brief.write_text(
                "dynamic reviewer status and next text\n",
                encoding="utf-8",
            )
            reviewer_request = self._prepare(
                config,
                state,
                task,
                agent_id="codex2",
                reason=supervisor.REASON_REVIEW_READY,
                queue_event_id="evt-reviewer",
            )

            self.assertEqual(
                reviewer_request.context_files,
                [self.BRIEF_PATH],
            )
            self.assertEqual(
                (workspace / self.BRIEF_PATH).read_text(encoding="utf-8"),
                reviewed_bytes,
            )
            self.assertNotIn("dynamic reviewer", reviewer_request.message)
            self.assertEqual(self._git(workspace, "status", "--porcelain"), "")
            self.assertEqual(
                supervisor._classify_worktree_dirt(f" M {self.BRIEF_PATH}")[0],
                "real",
            )

    def test_missing_brief_uses_ignored_context_across_repeated_review_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, task, _status_root, _source_root = self._fixture(
                root,
                tracked_brief=None,
            )
            state: dict[str, object] = {"worker_worktrees": {"leases": {}}}

            owner_request = self._prepare(
                config,
                state,
                task,
                agent_id="codex",
                reason=supervisor.REASON_OWNED_READY,
                queue_event_id="evt-owner-missing",
            )
            workspace = Path(str(owner_request.metadata["workspace_path"]))
            generated_path = (
                ".orchestrator/worker-runtime/task-context/"
                "sup-brief-hygiene-test.md"
            )
            generated_file = workspace / generated_path
            self.assertEqual(owner_request.context_files, [generated_path])
            self.assertIn(f"- {generated_path}", owner_request.message)
            self.assertNotIn(f"- {self.BRIEF_PATH}", owner_request.message)
            self.assertTrue(generated_file.is_file())
            self.assertIn("Status: todo", generated_file.read_text(encoding="utf-8"))
            self.assertEqual(
                self._git(workspace, "check-ignore", generated_path),
                generated_path,
            )
            self.assertEqual(self._git(workspace, "status", "--porcelain"), "")

            legacy_brief = workspace / self.BRIEF_PATH
            legacy_brief.parent.mkdir(parents=True, exist_ok=True)
            legacy_brief.write_text(
                "# Legacy generated context\n\n"
                f"{supervisor._GENERATED_WORKER_TASK_BRIEF_MARKER}\n",
                encoding="utf-8",
            )
            self.assertIn(
                f"?? {self.BRIEF_PATH}",
                self._git(
                    workspace,
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                ),
            )

            task["status"] = "review"
            task["next"] = "review the exact task head"
            reviewer_request = self._prepare(
                config,
                state,
                task,
                agent_id="codex2",
                reason=supervisor.REASON_REVIEW_READY,
                queue_event_id="evt-reviewer-missing",
            )
            self.assertEqual(reviewer_request.context_files, [generated_path])
            generated_text = generated_file.read_text(encoding="utf-8")
            self.assertIn("Status: review", generated_text)
            self.assertIn("review the exact task head", generated_text)
            self.assertFalse(legacy_brief.exists())
            self.assertEqual(
                reviewer_request.metadata[
                    "removed_legacy_generated_context_files"
                ],
                [self.BRIEF_PATH],
            )
            self.assertEqual(self._git(workspace, "status", "--porcelain"), "")

            task["status"] = "review_approved"
            task["next"] = "dynamic approval text must stay out of task source"
            finalize_request = self._prepare(
                config,
                state,
                task,
                agent_id="codex",
                reason=supervisor.REASON_OWNED_FINALIZE,
                queue_event_id="evt-finalize-missing",
            )
            self.assertEqual(finalize_request.context_files, [generated_path])
            finalize_text = generated_file.read_text(encoding="utf-8")
            self.assertIn("query the governed `ai-status.sh show`", finalize_text)
            self.assertNotIn("dynamic approval text", finalize_text)
            self.assertEqual(self._git(workspace, "status", "--porcelain"), "")

            with (
                mock.patch.object(supervisor, "write_activity_log"),
                mock.patch.object(
                    supervisor,
                    "_scan_process_paths_in_root",
                    return_value=set(),
                ),
            ):
                self.assertTrue(
                    supervisor.cleanup_inactive_worker_worktrees(config, state)
                )
            self.assertFalse(workspace.exists())
            self.assertNotIn(
                self.TASK_ID,
                state["worker_worktrees"]["leases"],
            )


class CrossRepositoryWorkerWorkspaceTests(unittest.TestCase):
    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout.strip()

    def test_execute_plans_task_gets_execute_plans_worktree_and_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_root = root / "pantheon"
            execute_root = root / "execute-plans"
            status_root.mkdir()
            execute_root.mkdir()
            self._git(execute_root, "init", "-b", "dev")
            self._git(execute_root, "config", "user.name", "Test")
            self._git(execute_root, "config", "user.email", "test@example.com")
            (execute_root / "README.md").write_text("execute plans\n", encoding="utf-8")
            self._git(execute_root, "add", "README.md")
            self._git(execute_root, "commit", "-m", "initial")
            self._git(
                execute_root,
                "remote",
                "add",
                "origin",
                "https://github.com/ajoe734/execute-plans.git",
            )
            head = self._git(execute_root, "rev-parse", "HEAD")
            self._git(execute_root, "update-ref", "refs/remotes/origin/dev", head)
            self._git(
                execute_root,
                "branch",
                "task/AGORA-FE-CANDIDATE-20260813",
                head,
            )
            (execute_root / "README.md").write_text(
                "execute plans current dev\n", encoding="utf-8"
            )
            self._git(execute_root, "add", "README.md")
            self._git(execute_root, "commit", "-m", "advance dev")
            current_dev_head = self._git(execute_root, "rev-parse", "HEAD")
            self._git(
                execute_root,
                "update-ref",
                "refs/remotes/origin/dev",
                current_dev_head,
            )

            config = config_fixture(status_root)
            config.update(
                {
                    "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                    "worker_worktrees": {
                        "root": str(root / "worker-worktrees"),
                    },
                    "coordination": {
                        "repositories": {
                            "pantheon": {"repo": "ajoe734/pantheon"},
                            "execute_plans": {"local_path": str(execute_root)}
                        }
                    },
                }
            )
            task = task_fixture("AGORA-FE-CANDIDATE-20260813")
            task["artifacts"] = [
                "execute-plans/src/agora/components/CandidateReviewDrawer.tsx"
            ]
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=str(task["id"]),
                reason=supervisor.REASON_OWNED_READY,
                context_files=["AI_COLLABORATION_GUIDE.md"],
                target_files=list(task["artifacts"]),
                metadata={"task": task, "task_generation": 1},
            )
            state: dict[str, object] = {"worker_worktrees": {"leases": {}}}
            worker_base_snapshots: dict[str, dict[str, str]] = {}

            with (
                mock.patch.object(
                    supervisor, "_fetch_worker_base_ref", return_value=(True, None)
                ) as fetch_base,
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, error = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-agora",
                    target_agent="Codex",
                    worker_base_snapshots=worker_base_snapshots,
                )

            self.assertTrue(ok, error)
            workspace = Path(str(request.metadata["workspace_path"]))
            self.assertEqual(
                workspace.parent.name,
                "execute-plans",
            )
            self.assertEqual(request.metadata["workspace_repository_id"], "execute_plans")
            self.assertEqual(request.metadata["workspace_source_root"], str(execute_root))
            self.assertEqual(request.metadata["workspace_base_ref"], "origin/dev")
            self.assertEqual(request.metadata["workspace_base_sha"], current_dev_head)
            self.assertEqual(self._git(workspace, "rev-parse", "--show-toplevel"), str(workspace))
            self.assertEqual(self._git(workspace, "rev-parse", "HEAD"), current_dev_head)
            self.assertEqual(self._git(workspace, "status", "--porcelain"), "")
            lease = state["worker_worktrees"]["leases"][str(task["id"])]
            self.assertEqual(lease["repository_id"], "execute_plans")
            self.assertEqual(lease["source_root"], str(execute_root))
            self.assertEqual(lease["base_sha"], current_dev_head)
            self.assertIn("Cross-repository delivery authority", request.message)
            self.assertEqual(
                request.metadata["workspace_target_files"],
                ["src/agora/components/CandidateReviewDrawer.tsx"],
            )
            replay_request = supervisor.request_from_snapshot(
                supervisor.request_snapshot(request)
            )
            with mock.patch.object(supervisor, "write_activity_log"):
                replay_ok, replay_error = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    replay_request,
                    queue_event_id="evt-agora-replay",
                    target_agent="Codex",
                    worker_base_snapshots=worker_base_snapshots,
                )
            self.assertTrue(replay_ok, replay_error)
            fetch_base.assert_called_once()
            self.assertEqual(
                replay_request.metadata["workspace_repository_id"], "execute_plans"
            )
            second_task = task_fixture("AGORA-FE-CANDIDATE-20260814")
            second_task["artifacts"] = list(task["artifacts"])
            second_request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=str(second_task["id"]),
                reason=supervisor.REASON_OWNED_READY,
                context_files=[],
                target_files=list(second_task["artifacts"]),
                metadata={"task": second_task, "task_generation": 1},
            )
            with mock.patch.object(supervisor, "write_activity_log"):
                second_ok, second_error = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    second_request,
                    queue_event_id="evt-agora-second",
                    target_agent="Codex",
                    worker_base_snapshots=worker_base_snapshots,
                )
            self.assertTrue(second_ok, second_error)
            self.assertEqual(second_request.metadata["workspace_base_sha"], current_dev_head)
            self.assertEqual(
                self._git(Path(str(second_request.metadata["workspace_path"])), "rev-parse", "HEAD"),
                current_dev_head,
            )
            fetch_base.assert_called_once()
            with mock.patch.object(supervisor, "write_activity_log"):
                cleaned = supervisor.cleanup_inactive_worker_worktrees(config, state)
            self.assertTrue(cleaned)
            self.assertFalse(workspace.exists())
            self.assertNotIn(
                str(task["id"]), state["worker_worktrees"]["leases"]
            )

    def test_external_worktree_inlines_missing_task_brief_without_dirtying_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_root = root / "pantheon"
            execute_root = root / "execute-plans"
            status_root.mkdir()
            execute_root.mkdir()
            self._git(execute_root, "init", "-b", "dev")
            self._git(execute_root, "config", "user.name", "Test")
            self._git(execute_root, "config", "user.email", "test@example.com")
            (execute_root / "README.md").write_text("execute plans\n", encoding="utf-8")
            self._git(execute_root, "add", "README.md")
            self._git(execute_root, "commit", "-m", "initial")
            head = self._git(execute_root, "rev-parse", "HEAD")
            self._git(execute_root, "remote", "add", "origin", "https://github.com/ajoe734/execute-plans.git")
            self._git(execute_root, "update-ref", "refs/remotes/origin/dev", head)

            config = config_fixture(status_root)
            config.update(
                {
                    "worker_worktrees": {
                        "enabled": True,
                        "root": str(root / "worker-worktrees"),
                        "base_ref": "origin/dev",
                        "reuse_existing": True,
                        "execution_reasons": [supervisor.REASON_OWNED_READY],
                    },
                    "coordination": {
                        "repositories": {"execute_plans": {"local_path": str(execute_root)}}
                    },
                }
            )
            task = task_fixture("PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY")
            task["artifacts"] = [
                "execute-plans/support/sidecars/PFG-FE-CONSOLIDATE-20260820/caller-inventory-20260824.md"
            ]
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message=(
                    "wake\n\n請先閱讀這些 task-scoped context 檔案：\n"
                    "- .orchestrator/task-briefs/PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY.md\n"
                ),
                task_id=str(task["id"]),
                reason=supervisor.REASON_OWNED_READY,
                context_files=[
                    ".orchestrator/task-briefs/PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY.md"
                ],
                metadata={"task": task, "task_generation": 1},
            )
            state: dict[str, object] = {"worker_worktrees": {"leases": {}}}

            with (
                mock.patch.object(supervisor, "_fetch_worker_base_ref", return_value=(True, None)),
                mock.patch.object(supervisor, "write_activity_log"),
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            ):
                ok, error = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-sidecar-brief",
                    target_agent="Codex",
                )

            self.assertTrue(ok, error)
            workspace = Path(str(request.metadata["workspace_path"]))
            self.assertIn("Generated task-scoped context (inline", request.message)
            self.assertIn("PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY", request.message)
            self.assertNotIn("- .orchestrator/task-briefs/PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY.md", request.message)
            self.assertEqual(
                request.metadata["inline_context_files"],
                [
                    ".orchestrator/task-briefs/PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY.md"
                ],
            )
            self.assertEqual(self._git(workspace, "status", "--porcelain"), "")
            self.assertFalse(
                (workspace / ".orchestrator/task-briefs/PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY.md").exists()
            )

    def test_fe_sidecar_with_target_repo_prepares_execute_plans_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_root = root / "pantheon"
            execute_root = root / "execute-plans"
            status_root.mkdir()
            execute_root.mkdir()
            self._git(execute_root, "init", "-b", "dev")
            self._git(execute_root, "config", "user.name", "Test")
            self._git(execute_root, "config", "user.email", "test@example.com")
            (execute_root / "README.md").write_text("execute plans\n", encoding="utf-8")
            self._git(execute_root, "add", "README.md")
            self._git(execute_root, "commit", "-m", "initial")
            self._git(
                execute_root,
                "remote",
                "add",
                "origin",
                "https://github.com/ajoe734/execute-plans.git",
            )
            head = self._git(execute_root, "rev-parse", "HEAD")
            self._git(execute_root, "update-ref", "refs/remotes/origin/dev", head)
            self._git(
                execute_root,
                "branch",
                "task/AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-24",
                head,
            )

            config = config_fixture(status_root)
            config.update(
                {
                    "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                    "worker_worktrees": {
                        "root": str(root / "worker-worktrees"),
                    },
                    "coordination": {
                        "repositories": {
                            "pantheon": {"repo": "ajoe734/pantheon"},
                            "execute_plans": {"local_path": str(execute_root)},
                        }
                    },
                }
            )
            task = task_fixture("AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-24")
            task["target_repo"] = "execute-plans"
            task["artifacts"] = [
                "support/sidecars/AG-FE-DB-002/evidence.json"
            ]
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=str(task["id"]),
                reason=supervisor.REASON_OWNED_READY,
                context_files=["AI_COLLABORATION_GUIDE.md"],
                target_files=list(task["artifacts"]),
                metadata={"task": task, "task_generation": 1},
            )
            state: dict[str, object] = {"worker_worktrees": {"leases": {}}}
            worker_base_snapshots: dict[str, dict[str, str]] = {}

            with (
                mock.patch.object(
                    supervisor, "_fetch_worker_base_ref", return_value=(True, None)
                ),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, error = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-sidecar",
                    target_agent="Codex",
                    worker_base_snapshots=worker_base_snapshots,
                )

            self.assertTrue(ok, error)
            workspace = Path(str(request.metadata["workspace_path"]))
            self.assertEqual(workspace.parent.name, "execute-plans")
            self.assertEqual(request.metadata["workspace_repository_id"], "execute_plans")
            lease = state["worker_worktrees"]["leases"][str(task["id"])]
            self.assertEqual(lease["repository_id"], "execute_plans")
            self.assertIn("Cross-repository delivery authority", request.message)
            self.assertEqual(
                request.metadata["workspace_target_files"],
                ["support/sidecars/AG-FE-DB-002/evidence.json"],
            )
            self.assertIn(
                "- support/sidecars/AG-FE-DB-002/evidence.json",
                request.message,
            )

    def test_conflicting_or_ambiguous_task_repo_fails_workspace_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_root = root / "pantheon"
            status_root.mkdir()
            config = config_fixture(status_root)

            # Conflicting target_repo vs explicit artifact prefix
            conflicting_task = task_fixture("CONFLICT-TASK")
            conflicting_task["target_repo"] = "pantheon"
            conflicting_task["artifacts"] = ["execute-plans/src/App.tsx"]
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=str(conflicting_task["id"]),
                reason=supervisor.REASON_OWNED_READY,
                context_files=[],
                target_files=list(conflicting_task["artifacts"]),
                metadata={"task": conflicting_task, "task_generation": 1},
            )
            state: dict[str, object] = {"worker_worktrees": {"leases": {}}}
            ok, error = supervisor.prepare_worker_workspace(
                config,
                state,
                request,
                queue_event_id="evt-conflict",
                target_agent="Codex",
                worker_base_snapshots={},
            )
            self.assertFalse(ok)
            self.assertIn("conflicting repository scope", str(error))

            # Ambiguous multi-repo target_repo
            ambiguous_task = task_fixture("AMBIGUOUS-TASK")
            ambiguous_task["target_repo"] = "pantheon+execute-plans"
            ambiguous_task["artifacts"] = ["src/App.tsx"]
            ambiguous_request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=str(ambiguous_task["id"]),
                reason=supervisor.REASON_OWNED_READY,
                context_files=[],
                target_files=list(ambiguous_task["artifacts"]),
                metadata={"task": ambiguous_task, "task_generation": 1},
            )
            ok, error = supervisor.prepare_worker_workspace(
                config,
                state,
                ambiguous_request,
                queue_event_id="evt-ambiguous",
                target_agent="Codex",
                worker_base_snapshots={},
            )
            self.assertFalse(ok)
            self.assertIn("ambiguous multi-repository target_repo", str(error))

            # Unrecognized target_repo
            unknown_task = task_fixture("UNKNOWN-TASK")
            unknown_task["target_repo"] = "bogus-repository-xyz"
            unknown_task["artifacts"] = ["src/App.tsx"]
            unknown_request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=str(unknown_task["id"]),
                reason=supervisor.REASON_OWNED_READY,
                context_files=[],
                target_files=list(unknown_task["artifacts"]),
                metadata={"task": unknown_task, "task_generation": 1},
            )
            ok, error = supervisor.prepare_worker_workspace(
                config,
                state,
                unknown_request,
                queue_event_id="evt-unknown",
                target_agent="Codex",
                worker_base_snapshots={},
            )
            self.assertFalse(ok)
            self.assertIn("unrecognized target_repo", str(error))


class RuntimeConfigurationContractTests(unittest.TestCase):
    def test_repo_config_uses_one_capacity_and_account_schema(self) -> None:
        config = json.loads(Path(__file__).with_name("config.json").read_text())
        supervisor.validate_provider_accounts(config)
        self.assertEqual(
            set(config["worker_worktrees"]),
            {"root"},
        )
        self.assertNotIn("base_branches", config["worker_worktree_cleanup"])
        self.assertIn(
            "orphan_prune_interval_seconds", config["worker_worktree_cleanup"]
        )
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

    def test_repo_claude_accounts_are_isolated_at_requested_capacities(self) -> None:
        config = json.loads(Path(__file__).with_name("config.json").read_text())
        expected = {
            "claude": ("claude1", 3),
            "claude2": ("claude2", 1),
        }
        account_caps = config["ready_dispatcher"]["max_concurrent_per_account"]

        for agent_id, (account_id, capacity) in expected.items():
            with self.subTest(agent_id=agent_id):
                self.assertEqual(config["providers"][agent_id]["account"], account_id)
                self.assertEqual(config["agents"][agent_id]["max_parallel"], capacity)
                self.assertEqual(account_caps[account_id], capacity)
                lane = supervisor.delivery_lane_for_agent(config, agent_id)
                self.assertEqual(lane.max_parallel, capacity)
                self.assertEqual(
                    {endpoint.account_id for endpoint in lane.endpoints},
                    {account_id},
                )

        self.assertNotEqual(
            config["providers"]["claude"]["account"],
            config["providers"]["claude2"]["account"],
        )

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

    def test_reassignment_graph_is_a_closed_known_agent_allow_list(self) -> None:
        config = config_fixture()
        config["worker_reassignment"]["owner_fallbacks"]["Codex"] = ["Unknown"]
        with self.assertRaisesRegex(ValueError, "unknown target"):
            supervisor.validate_provider_accounts(config)

        config = config_fixture()
        config["worker_reassignment"]["reviewer_fallbacks"]["RetiredLane"] = ["Codex"]
        with self.assertRaisesRegex(ValueError, "unknown root"):
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
        task["delivery_binding"] = {
            "kind": "pull_request",
            "pr": 42,
            "head_sha": "a" * 40,
            "head_branch": "task/TASK-1",
            "base": "dev",
        }
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

    def test_functional_completion_track_releases_dependency_without_terminal_closeout(self) -> None:
        dependency = task_fixture("DEP", status="blocked")
        dependency["completion_tracks"] = {
            "functional": {
                "status": "done",
                "evidence": ["e2e/paper-functional.json"],
            }
        }
        task = task_fixture(depends_on=["DEP"])
        task["dependency_tracks"] = {"DEP": "functional"}
        status = {"tasks": [task, dependency]}

        decision = planner_decision(self.config, task, status=status)

        self.assertTrue(decision["eligible"])
        self.assertEqual(decision["reason"], supervisor.REASON_OWNED_READY)

    def test_functional_dependency_does_not_infer_success_from_terminal_status(self) -> None:
        dependency = task_fixture("DEP", status="done")
        task = task_fixture(depends_on=["DEP"])
        task["dependency_tracks"] = {"DEP": "functional"}
        status = {"tasks": [task, dependency]}

        decision = planner_decision(self.config, task, status=status)

        self.assertFalse(decision["eligible"])
        self.assertEqual(decision["first_blocking_gate"], "task_not_dispatchable")

    def test_invalid_dependency_track_fails_closed(self) -> None:
        dependency = task_fixture("DEP", status="done")
        task = task_fixture(depends_on=["DEP"])
        task["dependency_tracks"] = {"DEP": "operator-live"}
        status = {"tasks": [task, dependency]}

        decision = planner_decision(self.config, task, status=status)

        self.assertFalse(decision["eligible"])

    def test_removing_named_track_restores_terminal_dependency_admission(self) -> None:
        dependency = task_fixture("DEP", status="done")
        task = task_fixture(depends_on=["DEP"])
        task["dependency_tracks"] = {"DEP": "hosted"}
        status = {"tasks": [task, dependency]}

        self.assertFalse(planner_decision(self.config, task, status=status)["eligible"])

        task["dependency_tracks"].pop("DEP")
        decision = planner_decision(self.config, task, status=status)

        self.assertTrue(decision["eligible"])
        self.assertEqual(decision["reason"], supervisor.REASON_OWNED_READY)

    def test_planner_consumes_terminal_facts_from_authoritative_projection(self) -> None:
        task = task_fixture(
            "CHILD",
            status="in_progress",
            depends_on=["ARCHIVED-DEP"],
        )
        status = {
            "tasks": [task],
            "terminal_facts": {
                "ARCHIVED-DEP": {
                    "status": "done",
                    "terminal_outcome": "completed",
                    "generation": 2,
                    "recorded_at": "2026-08-14T00:00:00Z",
                }
            },
        }
        state = with_healthy_delivery_health(
            self.config,
            {"workers": {}, "queue": {"events": {}}, "seen_event_keys": {}},
        )
        queued: list[dict[str, object]] = []

        changed = supervisor.dispatch_ready_tasks(
            self.config,
            state,
            agent_ids_override=["codex"],
            status_snapshot=status,
            queue_events_snapshot=[],
            live_total_snapshot=0,
            event_sink=lambda _config, event: queued.append(event) or True,
        )

        self.assertTrue(changed)
        self.assertEqual([event["task_id"] for event in queued], ["CHILD"])
        self.assertEqual(queued[0]["reason"], supervisor.REASON_OWNED_IN_PROGRESS)

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
            mock.patch.object(
                supervisor,
                "load_status",
                return_value={"tasks": [task_fixture()]},
            ),
            mock.patch.object(
                supervisor,
                "_queue_delivery_event_locked",
                side_effect=lambda _config, _state, event: appended.append(event) or True,
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

    def test_review_requires_a_current_delivery_binding(self) -> None:
        task = task_fixture(status="review", reviewer="Codex2")
        rejected = planner_decision(self.config, task, target="Codex2")
        self.assertFalse(rejected["eligible"])
        self.assertEqual(rejected["first_blocking_gate"], "task_not_dispatchable")

        task["delivery_binding"] = {
            "kind": "pull_request",
            "pr": 42,
            "head_sha": "a" * 40,
            "head_branch": "task/TASK-1",
            "base": "dev",
        }
        accepted = planner_decision(self.config, task, target="Codex2")
        self.assertTrue(accepted["eligible"])

    def test_review_binding_retries_after_a_terminal_attempt_left_no_verdict(self) -> None:
        # A reviewer worker can exit (crash, timeout, silent no-op) without
        # ever calling approve/reopen. Since a real verdict necessarily
        # changes task status and therefore the dispatch signature, a
        # terminal queue record that still matches the current signature is
        # proof the binding was never resolved -- it must stay retryable,
        # not get permanently stranded.
        task = task_fixture(status="review", reviewer="Codex2")
        task["delivery_binding"] = {
            "kind": "pull_request",
            "pr": 42,
            "head_sha": "a" * 40,
            "head_branch": "task/TASK-1",
            "base": "dev",
        }
        event = supervisor.build_dispatch_event(
            task,
            "Codex2",
            supervisor.REASON_REVIEW_READY,
            {"TASK-1": task},
        )
        state = with_healthy_delivery_health(
            self.config,
            {
                "workers": {},
                "queue": {"events": {"evt-review": {"event_key": event["key"], "status": "completed"}}},
            },
        )
        retried = planner_decision(self.config, task, state=state, target="Codex2")
        self.assertTrue(retried["eligible"])

        task["delivery_binding"] = {
            **task["delivery_binding"],
            "head_sha": "b" * 40,
        }
        accepted = planner_decision(self.config, task, state=state, target="Codex2")
        self.assertTrue(accepted["eligible"])

    def test_review_binding_in_flight_duplicate_is_still_rejected(self) -> None:
        # planner_decision() hardcodes events=[] when deriving pending_event_keys,
        # so an already-queued (non-terminal) intent needs a direct
        # evaluate_dispatch_candidate() call to exercise duplicate_event.
        task = task_fixture(status="review", reviewer="Codex2")
        task["delivery_binding"] = {
            "kind": "pull_request",
            "pr": 42,
            "head_sha": "a" * 40,
            "head_branch": "task/TASK-1",
            "base": "dev",
        }
        event = supervisor.build_dispatch_event(
            task,
            "Codex2",
            supervisor.REASON_REVIEW_READY,
            {"TASK-1": task},
        )
        event.update({"event_id": "evt-review", "event_key": event["key"]})
        state = with_healthy_delivery_health(
            self.config,
            with_queue_intents({"workers": {}}, event),
        )
        task_map = {"TASK-1": task}
        _agents, pending_pairs, pending_keys = supervisor.outstanding_delivery_indexes(
            self.config, state, None, task_map
        )
        pending_ids = {task_id for task_id, _agent in pending_pairs if task_id}
        rejected = supervisor.evaluate_dispatch_candidate(
            self.config,
            state,
            {"tasks": [task]},
            task,
            "Codex2",
            supervisor.task_resolver_for_config(self.config, task_map),
            settings=supervisor.ready_dispatch_settings(self.config),
            active_task_ids=set(),
            pending_task_ids=pending_ids,
            pending_event_keys=pending_keys,
            agent_loads={},
            active_account_loads={},
            pending_account_loads={},
            seen_event_keys={},
            checked_at="2026-08-11T00:00:01Z",
            cooldown_seconds=0,
        )
        self.assertFalse(rejected["eligible"])
        self.assertIn(
            rejected["first_blocking_gate"],
            {"duplicate_event", "task_pending"},
        )

    def test_review_binding_terminal_attempt_still_honors_unchanged_cooldown(self) -> None:
        # Retryable does not mean instantly re-spammed every tick: a terminal
        # attempt still feeds seen_event_keys, so the same cooldown gate that
        # protects owned dispatch reasons applies to review too.
        task = task_fixture(status="review", reviewer="Codex2")
        task["delivery_binding"] = {
            "kind": "pull_request",
            "pr": 42,
            "head_sha": "a" * 40,
            "head_branch": "task/TASK-1",
            "base": "dev",
        }
        event = supervisor.build_dispatch_event(
            task,
            "Codex2",
            supervisor.REASON_REVIEW_READY,
            {"TASK-1": task},
        )
        state = with_healthy_delivery_health(
            self.config,
            {
                "workers": {},
                "queue": {"events": {"evt-review": {"event_key": event["key"], "status": "completed"}}},
            },
        )
        task_map = {"TASK-1": task}
        active_statuses = set(
            supervisor.ready_dispatch_settings(self.config)["active_worker_statuses"]
        )
        _agents, pending_pairs, pending_keys = supervisor.outstanding_delivery_indexes(
            self.config, state, [], task_map
        )
        pending_ids = {task_id for task_id, _agent in pending_pairs if task_id}

        def decide(cooldown_seconds: float, seen_event_keys: dict[str, object]) -> dict[str, object]:
            return supervisor.evaluate_dispatch_candidate(
                self.config,
                state,
                {"tasks": [task]},
                task,
                "Codex2",
                supervisor.task_resolver_for_config(self.config, task_map),
                settings=supervisor.ready_dispatch_settings(self.config),
                active_task_ids=set(),
                pending_task_ids=pending_ids,
                pending_event_keys=pending_keys,
                agent_loads={},
                active_account_loads={},
                pending_account_loads={},
                seen_event_keys=seen_event_keys,
                checked_at="2026-08-11T00:10:00Z",
                cooldown_seconds=cooldown_seconds,
            )

        cooling_down = decide(900, {event["key"]: "2026-08-11T00:00:01Z"})
        self.assertFalse(cooling_down["eligible"])
        self.assertEqual(cooling_down["first_blocking_gate"], "unchanged_cooldown")

        elapsed = decide(900, {event["key"]: "2026-08-10T23:00:00Z"})
        self.assertTrue(elapsed["eligible"])

    def test_task_review_reopen_revision_sources_and_bounds(self) -> None:
        task = task_fixture(task_id="TASK-1", status="in_progress")
        self.assertEqual(supervisor.task_review_reopen_revision(task), 0)

        task_explicit = {**task, "review_reopen_revision": 2}
        self.assertEqual(supervisor.task_review_reopen_revision(task_explicit), 2)

        task_str = {**task, "reopen_revision": "3"}
        self.assertEqual(supervisor.task_review_reopen_revision(task_str), 3)

        activity_events = [
            {"ts": "2026-08-24T12:00:00Z", "agent": "Codex", "type": "start", "task_id": "TASK-1"},
            {"ts": "2026-08-24T12:01:00Z", "agent": "Codex", "type": "handoff", "task_id": "TASK-1"},
            {"ts": "2026-08-24T12:02:00Z", "agent": "Codex2", "type": "reopen", "task_id": "TASK-1", "message": "fix 1"},
            {"ts": "2026-08-24T12:03:00Z", "agent": "Codex2", "type": "reopen", "task_id": "TASK-2", "message": "other task"},
            {"ts": "2026-08-24T12:04:00Z", "agent": "Codex2", "type": "reopen", "task_id": "TASK-1", "message": "fix 2"},
        ]
        self.assertEqual(
            supervisor.task_review_reopen_revision(task, activity_events=activity_events),
            2,
        )
        task_2 = task_fixture(task_id="TASK-2", status="in_progress")
        self.assertEqual(
            supervisor.task_review_reopen_revision(task_2, activity_events=activity_events),
            1,
        )

    def test_review_reopen_advances_reopen_revision_and_redispatches_owner_once(self) -> None:
        task = task_fixture(task_id="TASK-1", status="in_progress", owner="Codex", reviewer="Codex2")
        task_map = {"TASK-1": task}
        state = with_healthy_delivery_health(self.config, {"workers": {}, "queue": {"events": {}}})
        resolver = supervisor.task_resolver_for_config(self.config, task_map)
        settings = supervisor.ready_dispatch_settings(self.config)

        # 1. Initial in-progress state before reopen has revision 0
        first_event = supervisor.build_dispatch_event(
            task,
            "Codex",
            supervisor.REASON_OWNED_IN_PROGRESS,
            resolver,
            activity_events=[],
        )
        self.assertNotIn("review_reopen_revision", first_event)
        seen_event_keys = {first_event["key"]: "2026-08-24T12:00:00Z"}

        # Ordinary in-progress polling is blocked by unchanged cooldown
        decision_initial_poll = supervisor.evaluate_dispatch_candidate(
            self.config,
            state,
            {"tasks": [task]},
            task,
            "Codex",
            resolver,
            settings=settings,
            active_task_ids=set(),
            pending_task_ids=set(),
            pending_event_keys=set(),
            agent_loads={},
            active_account_loads={},
            pending_account_loads={},
            seen_event_keys=seen_event_keys,
            checked_at="2026-08-24T12:01:00Z",
            cooldown_seconds=900,
            activity_events=[],
        )
        self.assertFalse(decision_initial_poll["eligible"])
        self.assertEqual(decision_initial_poll["first_blocking_gate"], "unchanged_cooldown")

        # 2. Reviewer reopens the task (owner, reviewer, generation remain unchanged)
        reopen_events_1 = [
            {"ts": "2026-08-24T12:02:00Z", "agent": "Codex2", "type": "reopen", "task_id": "TASK-1", "message": "fix requested"}
        ]
        decision_after_reopen = supervisor.evaluate_dispatch_candidate(
            self.config,
            state,
            {"tasks": [task]},
            task,
            "Codex",
            resolver,
            settings=settings,
            active_task_ids=set(),
            pending_task_ids=set(),
            pending_event_keys=set(),
            agent_loads={},
            active_account_loads={},
            pending_account_loads={},
            seen_event_keys=seen_event_keys,
            checked_at="2026-08-24T12:02:05Z",
            cooldown_seconds=900,
            activity_events=reopen_events_1,
        )
        self.assertTrue(decision_after_reopen["eligible"])
        self.assertEqual(decision_after_reopen["reason"], supervisor.REASON_OWNED_IN_PROGRESS)
        reopened_event_1 = decision_after_reopen["event"]
        self.assertEqual(reopened_event_1["review_reopen_revision"], 1)
        self.assertEqual(reopened_event_1["task"]["review_reopen_revision"], 1)
        self.assertNotEqual(reopened_event_1["key"], first_event["key"])

        # 3. Simulate dispatch of reopened task: record new key in seen_event_keys
        seen_event_keys[reopened_event_1["key"]] = "2026-08-24T12:02:05Z"

        # Subsequent in-progress polling with the same reopen state is suppressed
        decision_reopen_poll = supervisor.evaluate_dispatch_candidate(
            self.config,
            state,
            {"tasks": [task]},
            task,
            "Codex",
            resolver,
            settings=settings,
            active_task_ids=set(),
            pending_task_ids=set(),
            pending_event_keys=set(),
            agent_loads={},
            active_account_loads={},
            pending_account_loads={},
            seen_event_keys=seen_event_keys,
            checked_at="2026-08-24T12:02:30Z",
            cooldown_seconds=900,
            activity_events=reopen_events_1,
        )
        self.assertFalse(decision_reopen_poll["eligible"])
        self.assertEqual(decision_reopen_poll["first_blocking_gate"], "unchanged_cooldown")

        # 4. A second rejection advances revision to 2 and redispatches owner again
        reopen_events_2 = reopen_events_1 + [
            {"ts": "2026-08-24T12:05:00Z", "agent": "Codex2", "type": "reopen", "task_id": "TASK-1", "message": "second fix"}
        ]
        decision_after_reopen_2 = supervisor.evaluate_dispatch_candidate(
            self.config,
            state,
            {"tasks": [task]},
            task,
            "Codex",
            resolver,
            settings=settings,
            active_task_ids=set(),
            pending_task_ids=set(),
            pending_event_keys=set(),
            agent_loads={},
            active_account_loads={},
            pending_account_loads={},
            seen_event_keys=seen_event_keys,
            checked_at="2026-08-24T12:05:05Z",
            cooldown_seconds=900,
            activity_events=reopen_events_2,
        )
        self.assertTrue(decision_after_reopen_2["eligible"])
        reopened_event_2 = decision_after_reopen_2["event"]
        self.assertEqual(reopened_event_2["review_reopen_revision"], 2)
        self.assertNotEqual(reopened_event_2["key"], reopened_event_1["key"])

    def test_stale_dispatch_skip_message_with_review_reopen_revision(self) -> None:
        task = task_fixture(task_id="TASK-1", status="in_progress", owner="Codex")
        resolver = supervisor.task_resolver_for_config(self.config, {"TASK-1": task})
        reopen_events_1 = [
            {"ts": "2026-08-24T12:00:00Z", "agent": "Codex2", "type": "reopen", "task_id": "TASK-1"}
        ]
        event = supervisor.build_dispatch_event(
            task,
            "Codex",
            supervisor.REASON_OWNED_IN_PROGRESS,
            resolver,
            activity_events=reopen_events_1,
        )
        event.update({"event_key": event["key"], "target_display_name": "Codex"})

        # Event matches current task state at revision 1
        self.assertIsNone(
            supervisor.stale_dispatch_skip_message(
                self.config,
                event,
                {"TASK-1": task},
                activity_events=reopen_events_1,
            )
        )

        # When a second reopen occurs, the queued revision 1 event is recognized as stale
        reopen_events_2 = reopen_events_1 + [
            {"ts": "2026-08-24T12:05:00Z", "agent": "Codex2", "type": "reopen", "task_id": "TASK-1"}
        ]
        skip_msg = supervisor.stale_dispatch_skip_message(
            self.config,
            event,
            {"TASK-1": task},
            activity_events=reopen_events_2,
        )
        self.assertIsNotNone(skip_msg)
        self.assertIn("task state changed after the wake-up was queued", skip_msg or "")

    def test_explain_dispatch_reflects_reopen_eligibility(self) -> None:
        config = copy.deepcopy(self.config)
        config["ready_dispatcher"]["unchanged_task_cooldown_seconds"] = 900
        task = task_fixture(task_id="TASK-1", status="in_progress", owner="Codex", reviewer="Codex2")
        resolver = supervisor.task_resolver_for_config(config, {"TASK-1": task})
        first_event = supervisor.build_dispatch_event(
            task,
            "Codex",
            supervisor.REASON_OWNED_IN_PROGRESS,
            resolver,
            activity_events=[],
        )
        now = supervisor.utc_now()
        state = with_healthy_delivery_health(
            config,
            {"workers": {}, "seen_event_keys": {first_event["key"]: now}},
        )

        # Before reopen, explain shows Codex blocked under unchanged cooldown
        explanation_before = supervisor.explain_dispatch_for_task(
            config,
            state,
            "TASK-1",
            status={"tasks": [task]},
            activity_events=[],
        )
        self.assertTrue(explanation_before["agents"]["Codex"]["blocked"])
        self.assertEqual(
            explanation_before["agents"]["Codex"]["first_blocking_gate"],
            "unchanged_cooldown",
        )

        # After reviewer reopen, explain shows Codex eligible
        reopen_events = [
            {"ts": now, "agent": "Codex2", "type": "reopen", "task_id": "TASK-1"}
        ]
        explanation_after = supervisor.explain_dispatch_for_task(
            config,
            state,
            "TASK-1",
            status={"tasks": [task]},
            activity_events=reopen_events,
        )
        self.assertFalse(explanation_after["agents"]["Codex"]["blocked"])
        self.assertEqual(
            explanation_after["agents"]["Codex"]["candidate_reason"],
            supervisor.REASON_OWNED_IN_PROGRESS,
        )


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

    def test_owned_ready_dispatch_prepares_the_only_canonical_launch_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = config_fixture(Path(tmpdir))
            task = task_fixture(status="todo")
            event = supervisor.build_dispatch_event(
                task,
                "Codex",
                supervisor.REASON_OWNED_READY,
                {"TASK-1": task},
            )
            event["target_display_name"] = "Codex"

            mutation = supervisor.prepare_dispatched_task_status_mutation(
                config,
                event,
                run_id="run-ready",
                workspace_path=Path(tmpdir) / "task-worktree",
                task_map={"TASK-1": task},
            )

        self.assertIsNotNone(mutation)
        self.assertEqual(mutation["command"], "start")
        self.assertEqual(mutation["expected_statuses"], ["todo"])

    def test_resume_and_finalize_dispatch_receipts_do_not_mutate_task_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = config_fixture(Path(tmpdir))
            cases = (
                ("in_progress", supervisor.REASON_OWNED_IN_PROGRESS),
                ("review_approved", supervisor.REASON_OWNED_FINALIZE),
            )
            for status, reason in cases:
                with self.subTest(status=status, reason=reason):
                    task = task_fixture(status=status)
                    event = supervisor.build_dispatch_event(
                        task,
                        "Codex",
                        reason,
                        {"TASK-1": task},
                    )
                    event["target_display_name"] = "Codex"

                    mutation = supervisor.prepare_dispatched_task_status_mutation(
                        config,
                        event,
                        run_id=f"run-{status}",
                        workspace_path=Path(tmpdir) / "task-worktree",
                        task_map={"TASK-1": task},
                    )

                    self.assertIsNone(mutation)

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
            queued = runtime_state.queue_events(state)
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

    def test_terminal_reassignment_dispatches_once_on_the_next_cycle(self) -> None:
        """Recovery mutates assignment only; the next planner cycle owns launch."""

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".orchestrator").mkdir()
            config = config_fixture(root)
            task = task_fixture(reviewer="Human/Ops")
            status = {"tasks": [task]}
            state = {
                "workers": {},
                "queue": {"events": {}},
                "seen_event_keys": {},
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

            def persist(_config: dict[str, object], **kwargs: object) -> bool:
                task["owner"] = kwargs["new_owner"]
                task["reviewer"] = kwargs["new_reviewer"]
                task["generation"] = int(task["generation"]) + 1
                return True

            with (
                mock.patch.object(supervisor, "load_status", return_value=status),
                mock.patch.object(supervisor, "queue_events", return_value=[]),
                mock.patch.object(supervisor, "persist_task_reassignment", side_effect=persist),
                mock.patch.object(
                    supervisor,
                    "start_worker_for_request",
                    side_effect=AssertionError("recovery must not launch"),
                ),
            ):
                self.assertTrue(supervisor.reconcile_unavailable_assignments(config, state))

            self.assertEqual((task["owner"], task["generation"]), ("Codex2", 2))
            plan = supervisor.build_dispatch_plan(config, state, status, [], live_total=0)
            self.assertEqual(len(plan["events"]), 1)
            self.assertEqual(plan["events"][0]["task_generation"], 2)
            self.assertEqual(plan["events"][0]["target_agent"], "Codex2")

            with (
                mock.patch.object(supervisor, "load_status", return_value=status),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                self.assertTrue(supervisor.reserve_dispatch_plan(config, state, plan))
            queued = runtime_state.queue_events(state)
            self.assertEqual(len(queued), 1)
            self.assertEqual(queued[0]["task_generation"], 2)

            with (
                mock.patch.object(supervisor, "load_status", return_value=status),
                mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)),
                mock.patch.object(supervisor, "check_worker_tree_clean", return_value=(True, None)),
                mock.patch.object(
                    supervisor,
                    "start_worker_for_request",
                    return_value=(True, "run-generation-2", {"auto_delivered": True}),
                ) as launch,
                mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                self.assertTrue(supervisor.process_queue(config, state))

            self.assertEqual(launch.call_count, 1)
            request = launch.call_args.args[2]
            self.assertEqual((request.agent_id, request.metadata["task_generation"]), ("codex2", 2))

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
        with_queue_intents(state, self.event)
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
            mock.patch.object(supervisor, "queue_events", return_value=[self.event]),
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
                with_queue_intents(state, self.event)
                with (
                    mock.patch.object(supervisor, "queue_events", return_value=[self.event]),
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

    def test_pending_intent_collects_its_own_health_refresh_demand(self) -> None:
        """A pending intent must hand back which endpoints need re-probing.

        Before this test's companion fix, a pending intent only recorded
        ``last_wait_reason``/``last_health_refresh_requested_at`` timestamps
        and dropped the actual endpoint identifiers -- nothing downstream
        ever re-probed them, so an intent stuck on a lane whose cached health
        had simply gone stale (not a durable failure) could wait forever.
        Diagnosed 2026-08-17 on AGORA-HOSTED-SERVICE-PROOF-20260815: a retried
        queue event sat "pending: health_refresh_required" indefinitely after
        its endpoint's health TTL lapsed mid-retry.
        """

        state = {"workers": {}, "queue": {"events": {}}, "delivery_health": {}}
        with_queue_intents(state, self.event)
        demand: list[dict[str, str]] = []
        with (
            mock.patch.object(supervisor, "queue_events", return_value=[self.event]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [self.task]}),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                side_effect=AssertionError("unproven auth must not launch"),
            ),
        ):
            supervisor.process_queue(self.config, state, health_refresh_demand=demand)
        record = state["queue"]["events"]["evt-1"]
        self.assertEqual(record["status"], "pending")
        self.assertIn({"scope": "endpoint", "id": "codex"}, demand)

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
        with_queue_intents(state, self.event, second_event)
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
            mock.patch.object(supervisor, "queue_events", return_value=[self.event, second_event]),
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
        self.assertEqual(state["queue"]["events"]["evt-2"]["status"], "queued")

    def test_assignment_change_invalidates_queued_event(self) -> None:
        changed_task = {**self.task, "owner": "Codex2"}
        state = {"workers": {}, "queue": {"events": {}}}
        with_queue_intents(state, self.event)
        with (
            mock.patch.object(supervisor, "queue_events", return_value=[self.event]),
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

    def test_spawn_boundary_rejects_stale_assignment_snapshots(self) -> None:
        stale_assignments = {
            "generation": {**self.task, "generation": 2},
            "owner": {**self.task, "owner": "Codex2"},
            "reviewer": {**self.task, "reviewer": "Human/Ops"},
        }
        for changed_field, stale_task in stale_assignments.items():
            with (
                self.subTest(changed_field=changed_field),
                tempfile.TemporaryDirectory() as tmpdir,
            ):
                root = Path(tmpdir)
                (root / ".orchestrator").mkdir()
                config = config_fixture(root)
                state = with_healthy_delivery_health(
                    config, runtime_state.default_state()
                )
                event = supervisor.build_dispatch_event(
                    self.task,
                    "Codex",
                    supervisor.REASON_OWNED_IN_PROGRESS,
                    {"TASK-1": self.task},
                )
                event.update(
                    {
                        "event_id": "evt-spawn-boundary",
                        "event_key": event["key"],
                        "target_agent": "codex",
                        "target_display_name": "Codex",
                        "delivery_endpoint_id": "codex",
                        "message": "wake",
                    }
                )
                with_queue_intents(state, event)
                runtime_state.save_runtime_state(config, state)
                adapter = mock.Mock()

                with (
                    mock.patch.object(
                        supervisor,
                        "load_status",
                        side_effect=[
                            {"tasks": [self.task]},
                            {"tasks": [self.task]},
                            {"tasks": [stale_task]},
                        ],
                    ),
                    mock.patch.object(
                        supervisor, "build_adapter", return_value=adapter
                    ),
                    mock.patch.object(
                        supervisor,
                        "prepare_worker_workspace",
                        return_value=(True, None),
                    ),
                    mock.patch.object(
                        supervisor,
                        "check_worker_tree_clean",
                        return_value=(True, None),
                    ),
                    mock.patch.object(
                        supervisor,
                        "evaluate_queued_delivery_admission",
                        return_value=mock.Mock(eligible=True),
                    ),
                    mock.patch.object(
                        supervisor, "status_command_runtime_env", return_value={}
                    ),
                    mock.patch.object(
                        supervisor,
                        "status_command_runtime_record_from_env",
                        return_value={},
                    ),
                    mock.patch.object(supervisor, "write_activity_log"),
                ):
                    self.assertTrue(
                        supervisor._run_reserved_runtime_phase(
                            config,
                            "process_queue",
                            lambda scratch: supervisor.process_queue(config, scratch),
                        )
                    )

                adapter.deliver.assert_not_called()
                final_state = runtime_state.load_runtime_state(config)
                record = final_state["queue"]["events"]["evt-spawn-boundary"]
                self.assertEqual(record["status"], "completed", record)
                self.assertEqual(
                    record["skip_reason"],
                    "task_generation_changed_before_launch",
                )
                self.assertEqual(record["attempt_count"], 0)
                self.assertEqual(final_state["workers"], {})
                self.assertNotIn(
                    "process_queue",
                    final_state["supervisor"]["runtime_phase_reservations"],
                )

    def test_spawn_boundary_launches_only_the_current_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".orchestrator").mkdir()
            config = config_fixture(root)
            state = with_healthy_delivery_health(
                config, runtime_state.default_state()
            )
            event = supervisor.build_dispatch_event(
                self.task,
                "Codex",
                supervisor.REASON_OWNED_IN_PROGRESS,
                {"TASK-1": self.task},
            )
            event.update(
                {
                    "event_id": "evt-current-assignment",
                    "event_key": event["key"],
                    "target_agent": "codex",
                    "target_display_name": "Codex",
                    "delivery_endpoint_id": "codex",
                    "message": "wake",
                }
            )
            with_queue_intents(state, event)
            runtime_state.save_runtime_state(config, state)
            adapter = mock.Mock()
            adapter.deliver.return_value = DeliveryResult(
                ok=True,
                adapter="test",
                mode="codex",
                target="codex",
                auto_delivered=True,
                manual_confirmation_required=False,
                run_id="run-current-assignment",
            )

            with (
                mock.patch.object(
                    supervisor,
                    "load_status",
                    side_effect=[
                        {"tasks": [self.task]},
                        {"tasks": [self.task]},
                        {"tasks": [self.task]},
                        {"tasks": [self.task]},
                    ],
                ),
                mock.patch.object(
                    supervisor, "build_adapter", return_value=adapter
                ),
                mock.patch.object(
                    supervisor,
                    "prepare_worker_workspace",
                    return_value=(True, None),
                ),
                mock.patch.object(
                    supervisor,
                    "check_worker_tree_clean",
                    return_value=(True, None),
                ),
                mock.patch.object(
                    supervisor,
                    "evaluate_queued_delivery_admission",
                    return_value=mock.Mock(eligible=True),
                ),
                mock.patch.object(
                    supervisor, "status_command_runtime_env", return_value={}
                ),
                mock.patch.object(
                    supervisor,
                    "status_command_runtime_record_from_env",
                    return_value={},
                ),
                mock.patch.object(
                    supervisor, "sync_dispatched_task_status", return_value=True
                ),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                self.assertTrue(
                    supervisor._run_reserved_runtime_phase(
                        config,
                        "process_queue",
                        lambda scratch: supervisor.process_queue(config, scratch),
                    )
                )

            adapter.deliver.assert_called_once()
            final_state = runtime_state.load_runtime_state(config)
            record = final_state["queue"]["events"]["evt-current-assignment"]
            self.assertEqual(record["status"], "started")
            self.assertEqual(record["attempt_count"], 1)
            worker = final_state["workers"]["run-current-assignment"]
            self.assertEqual(worker["agent_id"], "codex")
            self.assertEqual(worker["task_generation"], 1)
            self.assertNotIn(
                "process_queue",
                final_state["supervisor"]["runtime_phase_reservations"],
            )

    def test_nonplanner_queue_reason_is_never_launched(self) -> None:
        legacy_event = {**self.event, "reason": "github_retry"}
        state = {"workers": {}, "queue": {"events": {}}}
        with_queue_intents(state, legacy_event)
        with (
            mock.patch.object(supervisor, "queue_events", return_value=[legacy_event]),
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
        with_queue_intents(state, self.event)
        with (
            mock.patch.object(supervisor, "queue_events", return_value=[self.event]),
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
        with_queue_intents(state, self.event)
        with (
            mock.patch.object(supervisor, "queue_events", return_value=[self.event]),
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

    def test_queued_intent_without_worker_is_not_reaped_as_an_orphan(self) -> None:
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
                        "intent": event,
                        "status": "queued",
                        "event_key": event_key,
                    }
                }
            },
            "seen_event_keys": {event_key: "2026-08-13T03:05:38Z"},
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [self.task]}),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertFalse(supervisor.reconcile_queue_intents(self.config, state))

        self.assertEqual(state["seen_event_keys"][event_key], "2026-08-13T03:05:38Z")
        self.assertEqual(state["queue"]["events"]["evt-1"]["intent"], event)

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
            "queue": {"events": {}},
        }
        with_queue_intents(state, self.event)
        state["queue"]["events"]["evt-1"]["status"] = "retry_backoff"
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

    def test_idle_health_refresh_targets_due_configured_endpoints_without_tasks(self) -> None:
        """Startup planning must refresh stale lanes even with an empty board.

        The codex1 and claude2 accounts are distinct: a successful exact
        endpoint probe must replace the prior endpoint/auth and account/quota
        holds atomically, rather than waiting for an unrelated dispatch.
        """

        self.config["agents"] = {
            "codex1": {
                "display_name": "Codex1",
                "provider": "codex1",
                "adapter": "codex",
                "max_parallel": 1,
            },
            "claude2": {
                "display_name": "Claude2",
                "provider": "claude2",
                "adapter": "claude_cli",
                "max_parallel": 1,
            },
        }
        self.config["providers"] = {
            "codex1": {"delivery_mode": "codex", "account": "codex1"},
            "claude2": {"delivery_mode": "claude_cli", "account": "claude2"},
        }
        self.config["ready_dispatcher"]["max_concurrent_per_account"] = {
            "codex1": 1,
            "claude2": 1,
        }
        state = {
            "workers": {},
            "queue": {"events": {}},
            "delivery_health": {
                "version": 1,
                "endpoints": {
                    "codex1": {
                        "state": "unavailable",
                        "reason_kind": "auth",
                        "retry_at": "2000-01-01T00:00:00Z",
                    },
                    "claude2": {
                        "state": "healthy",
                        "valid_until": "2000-01-01T00:00:00Z",
                    },
                },
                "accounts": {
                    "codex1": {
                        "state": "retry_after",
                        "reason_kind": "quota_terminal",
                        "retry_at": "2000-01-01T00:00:00Z",
                    },
                    "claude2": {
                        "state": "retry_after",
                        "reason_kind": "quota_terminal",
                        "retry_at": "2000-01-01T00:00:00Z",
                    },
                },
            },
        }

        plan = supervisor.build_dispatch_plan(
            self.config,
            state,
            {"tasks": []},
            [],
            live_total=0,
        )

        self.assertEqual(plan["events"], [])
        self.assertEqual(
            plan["health_refresh_targets"],
            [
                {"scope": "endpoint", "id": "codex1"},
                {"scope": "endpoint", "id": "claude2"},
            ],
        )
        observations = [
            {
                "endpoint_id": "codex1",
                "account_id": "codex1",
                "probe": {
                    "ready": True,
                    "status": "ready",
                    "source": "live",
                    "checked_at": "2026-08-21T12:00:00Z",
                },
            },
            {
                "endpoint_id": "claude2",
                "account_id": "claude2",
                "probe": {
                    "ready": True,
                    "status": "ready",
                    "source": "live",
                    "checked_at": "2026-08-21T12:00:00Z",
                },
            },
        ]
        self.assertTrue(
            supervisor.apply_delivery_health_observations(
                self.config, state, observations
            )
        )
        for identity in ("codex1", "claude2"):
            self.assertEqual(
                state["delivery_health"]["endpoints"][identity]["state"],
                "healthy",
            )
            self.assertEqual(
                state["delivery_health"]["accounts"][identity]["state"],
                "healthy",
            )

    def test_idle_health_refresh_excludes_orphan_provider_and_respects_probe_bound(self) -> None:
        self.config["delivery_health"] = {"refresh_max_per_cycle": 1}
        # A configured provider may share an account without being a delivery
        # endpoint.  It must not create a second, unrelated probe demand.
        self.config["providers"]["orphan"] = {
            "delivery_mode": "codex",
            "account": "codex_account",
        }
        state = {"workers": {}, "queue": {"events": {}}, "delivery_health": {}}

        targets = supervisor.idle_delivery_health_refresh_targets(self.config, state)

        self.assertEqual(
            targets,
            [
                {"scope": "endpoint", "id": "codex"},
                {"scope": "endpoint", "id": "codex2"},
            ],
        )
        with mock.patch.object(
            supervisor,
            "probe_provider_auth",
            return_value={"ready": True, "status": "ready", "source": "live"},
        ) as probe:
            observations = supervisor.probe_demanded_delivery_health(
                self.config, targets, quiet=True
            )
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["endpoint_id"], "codex")
        probe.assert_called_once_with(self.config, "codex", force=True)

    def test_topology_reconciliation_migrates_shared_claude_health_once(self) -> None:
        """The former shared Claude row cannot survive a split topology.

        Configured claude1/claude2 records and unrelated runtime state must be
        preserved exactly.  A second pass is a no-op so an unchanged topology
        does not manufacture an every-cycle health write.
        """

        self.config["agents"] = {
            "claude1": {
                "display_name": "Claude1",
                "provider": "claude1",
                "adapter": "claude_cli",
                "max_parallel": 1,
            },
            "claude2": {
                "display_name": "Claude2",
                "provider": "claude2",
                "adapter": "claude_cli",
                "max_parallel": 1,
            },
        }
        self.config["providers"] = {
            "claude1": {"delivery_mode": "claude_cli", "account": "claude1"},
            "claude2": {"delivery_mode": "claude_cli", "account": "claude2"},
        }
        self.config["ready_dispatcher"]["max_concurrent_per_account"] = {
            "claude1": 1,
            "claude2": 1,
        }
        claude1_health = {
            "state": "healthy",
            "valid_until": "2999-01-01T00:00:00Z",
            "detail": "preserve claude1 evidence",
        }
        claude2_health = {
            "state": "retry_after",
            "retry_at": "2999-01-01T00:00:00Z",
            "detail": "preserve claude2 evidence",
        }
        state = {
            "workers": {"unrelated": {"status": "completed"}},
            "queue": {"events": {}},
            "watchdog": {"safe_mode_reason": "preserve unrelated state"},
            "delivery_health": {
                "version": 1,
                "endpoints": {
                    "claude1": dict(claude1_health),
                    "claude2": dict(claude2_health),
                    "claude_shared": {
                        "state": "unavailable",
                        "detail": "retired endpoint",
                    },
                },
                "accounts": {
                    "claude1": dict(claude1_health),
                    "claude2": dict(claude2_health),
                    "claude_account_shared_max_1": {
                        "state": "retry_after",
                        "detail": "retired shared account",
                    },
                },
                "projection_note": "preserve unrelated health metadata",
            },
        }

        self.assertTrue(
            supervisor.reconcile_delivery_health_topology(self.config, state)
        )
        self.assertEqual(
            state["delivery_health"]["endpoints"],
            {"claude1": claude1_health, "claude2": claude2_health},
        )
        self.assertEqual(
            state["delivery_health"]["accounts"],
            {"claude1": claude1_health, "claude2": claude2_health},
        )
        self.assertEqual(
            state["delivery_health"]["projection_note"],
            "preserve unrelated health metadata",
        )
        self.assertEqual(state["workers"], {"unrelated": {"status": "completed"}})
        self.assertEqual(
            state["watchdog"],
            {"safe_mode_reason": "preserve unrelated state"},
        )
        reconciled = json.loads(json.dumps(state))
        self.assertFalse(
            supervisor.reconcile_delivery_health_topology(self.config, state)
        )
        self.assertEqual(state, reconciled)

    def test_post_dispatch_maintenance_prunes_provider_only_health_projection(self) -> None:
        """A provider without a delivery endpoint owns no durable health row."""

        self.config["providers"]["orphan"] = {
            "delivery_mode": "codex",
            "account": "orphan_account",
        }
        state = {
            "workers": {},
            "queue": {"events": {}},
            "delivery_health": healthy_delivery_health(self.config),
        }
        state["delivery_health"]["endpoints"]["orphan"] = {
            "state": "unavailable",
        }
        state["delivery_health"]["accounts"]["orphan_account"] = {
            "state": "retry_after",
        }

        maintenance_helpers = (
            "reconcile_runtime_on_boot",
            "reconcile_unavailable_assignments",
            "reconcile_failure_loops",
            "reconcile_queue_records",
            "reconcile_queue_intents",
            "reconcile_ownerless_in_progress_tasks",
            "maybe_auto_commit_archive",
        )
        patches = [
            mock.patch.object(supervisor, helper, return_value=False)
            for helper in maintenance_helpers
        ]
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
        ):
            self.assertTrue(
                supervisor.apply_post_dispatch_maintenance(
                    self.config,
                    state,
                    delivery_health_observations=[],
                    task_state_projection_snapshot=None,
                    assistant_dev_bridge_snapshot=None,
                    quiet=True,
                )
            )

        self.assertNotIn("orphan", state["delivery_health"]["endpoints"])
        self.assertNotIn("orphan_account", state["delivery_health"]["accounts"])
        self.assertEqual(
            set(state["delivery_health"]["endpoints"]),
            {"codex", "codex2"},
        )
        self.assertEqual(
            set(state["delivery_health"]["accounts"]),
            {"codex_account", "codex2_account"},
        )

    def test_authorized_refresh_bypasses_future_retry_at_on_startup(self) -> None:
        """A stale account whose retry_at has not elapsed still blocks the
        due-only scan; the authorized scan targets it anyway on a fresh
        (startup) supervisor state, while skipping the already-healthy
        codex2 lane so it never spends an unneeded probe."""

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

        self.assertEqual(
            supervisor.idle_delivery_health_refresh_targets(self.config, state), []
        )
        self.assertEqual(
            supervisor.authorized_delivery_health_refresh_targets(self.config, state),
            [{"scope": "endpoint", "id": "codex"}],
        )

    def test_authorized_refresh_consumed_once_per_topology(self) -> None:
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

        self.assertNotEqual(
            supervisor.authorized_delivery_health_refresh_targets(self.config, state), []
        )
        self.assertTrue(
            supervisor.record_delivery_health_refresh_authority_consumed(self.config, state)
        )
        # Same topology, next cycle: the bypass must not fire again.
        self.assertEqual(
            supervisor.authorized_delivery_health_refresh_targets(self.config, state), []
        )
        self.assertFalse(
            supervisor.record_delivery_health_refresh_authority_consumed(self.config, state)
        )

        # A real topology change (new delivery endpoint) authorizes the
        # bypass again exactly once.
        self.config["agents"]["codex3"] = dict(self.config["agents"]["codex2"])
        self.config["providers"]["codex3"] = dict(self.config["providers"]["codex2"])
        self.assertNotEqual(
            supervisor.authorized_delivery_health_refresh_targets(self.config, state), []
        )

    def test_human_ops_request_bypasses_future_retry_at_without_topology_change(self) -> None:
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
        supervisor.record_delivery_health_refresh_authority_consumed(self.config, state)
        self.assertEqual(
            supervisor.authorized_delivery_health_refresh_targets(self.config, state), []
        )

        self.assertTrue(supervisor.request_delivery_health_refresh(state))
        self.assertEqual(
            supervisor.authorized_delivery_health_refresh_targets(self.config, state),
            [{"scope": "endpoint", "id": "codex"}],
        )
        supervisor.record_delivery_health_refresh_authority_consumed(self.config, state)
        self.assertEqual(
            supervisor.authorized_delivery_health_refresh_targets(self.config, state), []
        )

    def test_failed_idle_probe_keeps_endpoint_unavailable(self) -> None:
        state = {"workers": {}, "queue": {"events": {}}, "delivery_health": {}}
        observations = [
            {
                "endpoint_id": "codex",
                "account_id": "codex_account",
                "probe": {
                    "ready": False,
                    "status": "auth_material_missing",
                    "source": "live",
                    "checked_at": "2026-08-21T12:00:00Z",
                },
            }
        ]

        self.assertTrue(
            supervisor.apply_delivery_health_observations(
                self.config, state, observations
            )
        )
        self.assertEqual(
            state["delivery_health"]["endpoints"]["codex"]["state"],
            "unavailable",
        )
        self.assertNotIn("codex_account", state["delivery_health"]["accounts"])

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
            mock.patch.object(supervisor, "queue_events", return_value=[]),
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

    def test_terminal_pending_intent_does_not_block_reviewer_reassignment(self) -> None:
        task = task_fixture(status="review", owner="Human/Ops", reviewer="Codex")
        task["delivery_binding"] = {
            "kind": "pull_request",
            "pr": 42,
            "head_sha": "a" * 40,
            "head_branch": "task/TASK-1",
            "base": "dev",
        }
        event = supervisor.build_dispatch_event(
            task,
            "Codex",
            supervisor.REASON_REVIEW_READY,
            {"TASK-1": task},
        )
        event.update(
            {
                "event_id": "evt-terminal-review",
                "event_key": event["key"],
                "target_agent": "codex",
                "target_display_name": "Codex",
                "delivery_endpoint_id": "codex",
                "message": "review",
            }
        )
        state = {
            "workers": {},
            "queue": {"events": {}},
            "delivery_health": {
                "version": 1,
                "endpoints": {
                    "codex": {
                        "state": "unavailable",
                        "reason_kind": "auth",
                    },
                    "codex2": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    },
                },
                "accounts": {
                    "codex_account": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    },
                    "codex2_account": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    },
                },
            },
        }
        with_queue_intents(state, event)
        state["queue"]["events"]["evt-terminal-review"].update(
            {
                "status": "pending",
                "last_wait_reason": "health_refresh_required",
            }
        )

        def persist(_config: dict[str, object], **kwargs: object) -> bool:
            task["reviewer"] = kwargs["new_reviewer"]
            task["generation"] = int(task["generation"]) + 1
            return True

        with (
            mock.patch.object(
                supervisor, "load_status", return_value={"tasks": [task]}
            ),
            mock.patch.object(
                supervisor,
                "persist_task_reassignment",
                side_effect=persist,
            ) as persisted,
        ):
            self.assertTrue(
                supervisor.reconcile_unavailable_assignments(self.config, state)
            )
            self.assertTrue(supervisor.reconcile_queue_intents(self.config, state))

        self.assertEqual(persisted.call_args.kwargs["new_reviewer"], "Codex2")
        record = state["queue"]["events"]["evt-terminal-review"]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["skip_reason"], "stale_dispatch_event")

    def test_active_worker_still_blocks_terminal_reassignment(self) -> None:
        task = task_fixture(reviewer="Human/Ops")
        state = {
            "workers": {
                "run-active": {
                    "run_id": "run-active",
                    "task_id": "TASK-1",
                    "agent_id": "codex",
                    "status": "running",
                }
            },
            "queue": {"events": {}},
            "delivery_health": {
                "version": 1,
                "endpoints": {
                    "codex": {
                        "state": "unavailable",
                        "reason_kind": "auth",
                    },
                    "codex2": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    },
                },
                "accounts": {
                    "codex_account": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    },
                    "codex2_account": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    },
                },
            },
        }
        with (
            mock.patch.object(
                supervisor, "load_status", return_value={"tasks": [task]}
            ),
            mock.patch.object(supervisor, "persist_task_reassignment") as persisted,
        ):
            self.assertFalse(
                supervisor.reconcile_unavailable_assignments(self.config, state)
            )

        persisted.assert_not_called()

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
            mock.patch.object(supervisor, "queue_events", return_value=[]),
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

    def test_configured_fallback_never_escapes_to_an_unrelated_healthy_roster(self) -> None:
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
            mock.patch.object(supervisor, "queue_events", return_value=[]),
            mock.patch.object(
                supervisor, "persist_task_reassignment", return_value=True
            ) as persist,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertFalse(changed)
        persist.assert_not_called()

    def test_terminal_assignment_demands_health_for_configured_fallback(self) -> None:
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
            {"scope": "endpoint", "id": "codex2"},
            plan["health_refresh_targets"],
        )

    def test_terminal_owner_reassignment_also_demands_reviewer_health(self) -> None:
        """An owner reassignment needs a healthy reviewer to pair with too.

        plan_task_assignment_pair validates the incumbent reviewer (and its
        fallback chain) with the same strictness as the candidate owner, so a
        reviewer nobody has dispatched to recently -- not durably unavailable,
        just never probed -- must also be demanded here. Before this test's
        companion fix, only the owner-fallback candidate's health was
        demanded; a stale-but-not-broken reviewer silently starved recovery
        because it was never re-probed and the planner could never confirm it
        healthy enough to pair with the new owner. Diagnosed 2026-08-17 on
        AGORA-HOSTED-SERVICE-PROOF-20260815 after Codex2 hit quota_terminal
        with reviewer Claude sitting on ~40h-stale health.
        """

        task = task_fixture(reviewer="Claude")
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
                    "codex2": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    },
                    # Claude has no entry at all: never probed, not a durable
                    # failure -- exactly the gap this test guards.
                },
                "accounts": {
                    "codex_account": {
                        "state": "retry_after",
                        "reason_kind": "quota_terminal",
                        "retry_at": "2999-01-01T00:00:00Z",
                    },
                    "codex2_account": {
                        "state": "healthy",
                        "valid_until": "2999-01-01T00:00:00Z",
                    },
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
            mock.patch.object(supervisor, "queue_events", return_value=[]),
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
            mock.patch.object(supervisor, "queue_events", return_value=[]),
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
            mock.patch.object(supervisor, "queue_events", return_value=[]),
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
            mock.patch.object(supervisor, "queue_events", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertFalse(changed)
        persist.assert_not_called()


class LoadBalanceReassignmentTests(unittest.TestCase):
    """Saturated-but-healthy-lane reassignment (assignment_saturated_recoverable),
    the load-balance branch of reconcile_unavailable_assignments. Off by
    default; only fires once a lane has been continuously full for
    ``min_saturated_seconds`` and a configured fallback has spare capacity.
    """

    def setUp(self) -> None:
        self.config = config_fixture()
        self.config["worker_reassignment"]["load_balance"] = {
            "enabled": True,
            "min_saturated_seconds": 900,
        }

    @staticmethod
    def _filler_worker(run_id: str, task_id: str, agent_id: str) -> dict[str, object]:
        return {
            "run_id": run_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "status": "running",
            "request_snapshot": {"reason": supervisor.REASON_OWNED_IN_PROGRESS},
        }

    def _saturated_state(self) -> dict[str, object]:
        return {
            "workers": {
                "run-a": self._filler_worker("run-a", "TASK-A", "codex"),
                "run-b": self._filler_worker("run-b", "TASK-B", "codex"),
            },
            "queue": {"events": {}},
            "delivery_health": {
                "version": 1,
                "endpoints": {
                    "codex": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                    "codex2": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                },
                "accounts": {
                    "codex_account": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                    "codex2_account": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                },
            },
        }

    def test_disabled_by_default_never_reassigns_a_saturated_but_healthy_lane(self) -> None:
        self.config["worker_reassignment"]["load_balance"]["enabled"] = False
        task = task_fixture(status="todo", reviewer="Human/Ops")
        state = self._saturated_state()
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "queue_events", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment") as persisted,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertFalse(changed)
        persisted.assert_not_called()

    def test_saturated_lane_does_not_reassign_before_the_hold_duration(self) -> None:
        task = task_fixture(status="todo", reviewer="Human/Ops")
        state = self._saturated_state()
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "queue_events", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment") as persisted,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertFalse(changed)
        persisted.assert_not_called()
        self.assertIn("TASK-1", state["load_balance_watch"])
        self.assertEqual(state["load_balance_watch"]["TASK-1"]["owner"], "Codex")

    def test_saturated_lane_reassigns_to_idle_fallback_after_the_hold_duration(self) -> None:
        task = task_fixture(status="todo", reviewer="Human/Ops")
        state = self._saturated_state()
        state["load_balance_watch"] = {
            "TASK-1": {"first_seen_at": "2000-01-01T00:00:00Z", "owner": "Codex"}
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "queue_events", return_value=[]),
            mock.patch.object(
                supervisor, "persist_task_reassignment", return_value=True
            ) as persisted,
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                side_effect=AssertionError("load-balance recovery must not launch"),
            ),
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertTrue(changed)
        self.assertEqual(persisted.call_args.kwargs["new_owner"], "Codex2")
        self.assertEqual(persisted.call_args.kwargs["expected_owner"], "Codex")
        self.assertNotIn("TASK-1", state["load_balance_watch"])

    def test_saturated_lane_does_not_reassign_when_the_fallback_is_also_full(self) -> None:
        task = task_fixture(status="todo", reviewer="Human/Ops")
        state = self._saturated_state()
        state["workers"]["run-c"] = self._filler_worker("run-c", "TASK-C", "codex2")
        state["workers"]["run-d"] = self._filler_worker("run-d", "TASK-D", "codex2")
        state["load_balance_watch"] = {
            "TASK-1": {"first_seen_at": "2000-01-01T00:00:00Z", "owner": "Codex"}
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "queue_events", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment") as persisted,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertFalse(changed)
        persisted.assert_not_called()

    def test_unsaturated_lane_never_starts_the_hold_timer(self) -> None:
        task = task_fixture(status="todo", reviewer="Human/Ops")
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
                    "codex_account": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                    "codex2_account": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                },
            },
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "queue_events", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment") as persisted,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertFalse(changed)
        persisted.assert_not_called()
        self.assertEqual(state.get("load_balance_watch", {}), {})

    @staticmethod
    def _stale_probe_state() -> dict[str, object]:
        """Codex healthy, Codex2's probe cache expired (state="healthy" but
        valid_until in the past) -- reads back as UNKNOWN, not durably
        terminal, so assignment_terminal_unavailability does NOT fire.
        """
        return {
            "workers": {},
            "queue": {"events": {}},
            "delivery_health": {
                "version": 1,
                "endpoints": {
                    "codex": {"state": "healthy", "valid_until": "2000-01-01T00:00:00Z"},
                    "codex2": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                },
                "accounts": {
                    "codex_account": {"state": "healthy", "valid_until": "2000-01-01T00:00:00Z"},
                    "codex2_account": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                },
            },
        }

    def test_owner_with_expired_probe_does_not_reassign_before_the_hold_duration(self) -> None:
        task = task_fixture(status="todo", reviewer="Human/Ops")
        state = self._stale_probe_state()
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "queue_events", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment") as persisted,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertFalse(changed)
        persisted.assert_not_called()
        self.assertIn("TASK-1", state["load_balance_watch"])

    def test_owner_with_expired_probe_reassigns_to_healthy_fallback_after_the_hold_duration(self) -> None:
        task = task_fixture(status="todo", reviewer="Human/Ops")
        state = self._stale_probe_state()
        state["load_balance_watch"] = {
            "TASK-1": {"first_seen_at": "2000-01-01T00:00:00Z", "owner": "Codex"}
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "queue_events", return_value=[]),
            mock.patch.object(
                supervisor, "persist_task_reassignment", return_value=True
            ) as persisted,
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                side_effect=AssertionError("load-balance recovery must not launch"),
            ),
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertTrue(changed)
        self.assertEqual(persisted.call_args.kwargs["new_owner"], "Codex2")
        self.assertIn(
            "blocked from auto-dispatch",
            persisted.call_args.kwargs["message"],
        )
        self.assertNotIn("TASK-1", state["load_balance_watch"])

    def test_owner_with_expired_probe_does_not_reassign_when_fallback_also_stale(self) -> None:
        task = task_fixture(status="todo", reviewer="Human/Ops")
        state = self._stale_probe_state()
        state["delivery_health"]["endpoints"]["codex2"]["valid_until"] = "2000-01-01T00:00:00Z"
        state["delivery_health"]["accounts"]["codex2_account"]["valid_until"] = "2000-01-01T00:00:00Z"
        state["load_balance_watch"] = {
            "TASK-1": {"first_seen_at": "2000-01-01T00:00:00Z", "owner": "Codex"}
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "queue_events", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment") as persisted,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertFalse(changed)
        persisted.assert_not_called()

    def test_healthy_owner_never_treated_as_transiently_blocked(self) -> None:
        task = task_fixture(status="todo", reviewer="Human/Ops")
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
                    "codex_account": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                    "codex2_account": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                },
            },
            "load_balance_watch": {
                "TASK-1": {"first_seen_at": "2000-01-01T00:00:00Z", "owner": "Codex"}
            },
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "queue_events", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment") as persisted,
        ):
            changed = supervisor.reconcile_unavailable_assignments(self.config, state)
        self.assertFalse(changed)
        persisted.assert_not_called()
        self.assertNotIn("TASK-1", state["load_balance_watch"])


class RecentTaskFailureCountsTests(unittest.TestCase):
    """recent_task_failure_counts: a bounded, offset-free tail read of the
    activity log -- never a stored cross-cycle byte position, so it stays
    correct across log rotation for free.
    """

    def _config(self, tmp_path: Path) -> dict[str, object]:
        return {"paths": {"activity_log": str(tmp_path / "ai-activity-log.jsonl")}}

    def _write_log(self, tmp_path: Path, entries: list[dict[str, object]]) -> None:
        path = tmp_path / "ai-activity-log.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")

    def test_counts_worker_failed_within_window_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp_path = Path(raw)
            now = datetime.now(timezone.utc)
            recent = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            old = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._write_log(
                tmp_path,
                [
                    {"type": "worker_failed", "task_id": "TASK-1", "ts": recent},
                    {"type": "worker_failed", "task_id": "TASK-1", "ts": recent},
                    {"type": "worker_failed", "task_id": "TASK-1", "ts": old},
                    {"type": "worker_started", "task_id": "TASK-1", "ts": recent},
                    {"type": "worker_failed", "task_id": "TASK-2", "ts": recent},
                ],
            )
            counts = supervisor.recent_task_failure_counts(
                self._config(tmp_path), window_seconds=3600
            )
        self.assertEqual(counts.get("TASK-1"), 2)
        self.assertEqual(counts.get("TASK-2"), 1)

    def test_missing_log_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp_path = Path(raw)
            counts = supervisor.recent_task_failure_counts(
                self._config(tmp_path), window_seconds=3600
            )
        self.assertEqual(counts, {})

    def test_only_reads_the_tail_not_the_whole_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp_path = Path(raw)
            now = datetime.now(timezone.utc)
            recent = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            padding = [
                {"type": "worker_started", "task_id": "PADDING", "ts": recent, "note": "x" * 200}
                for _ in range(5000)
            ]
            self._write_log(
                tmp_path,
                padding + [{"type": "worker_failed", "task_id": "TASK-1", "ts": recent}],
            )
            counts = supervisor.recent_task_failure_counts(
                self._config(tmp_path), window_seconds=3600, tail_bytes=4096
            )
        self.assertEqual(counts.get("TASK-1"), 1)
        self.assertNotIn("PADDING", counts)


class FailureLoopAutoGovernanceTests(unittest.TestCase):
    """reconcile_failure_loops: bounded auto-reassign, then escalate to a
    Human/Ops hold, for a task that keeps failing under its owner. Off by
    default.
    """

    def setUp(self) -> None:
        self.config = config_fixture()
        self.config["worker_reassignment"]["failure_loop"] = {
            "enabled": True,
            "max_failures_in_window": 3,
            "window_seconds": 3600,
            "max_auto_reassignments": 1,
        }

    def test_disabled_by_default_never_touches_a_failing_task(self) -> None:
        self.config["worker_reassignment"]["failure_loop"]["enabled"] = False
        task = task_fixture(status="in_progress", reviewer="Human/Ops")
        state = {"workers": {}}
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(
                supervisor, "recent_task_failure_counts", return_value={"TASK-1": 5}
            ),
            mock.patch.object(supervisor, "persist_task_reassignment") as persisted,
            mock.patch.object(supervisor, "record_failure_loop_blocker") as blocked,
        ):
            changed = supervisor.reconcile_failure_loops(self.config, state)
        self.assertFalse(changed)
        persisted.assert_not_called()
        blocked.assert_not_called()

    def test_below_threshold_does_not_reassign(self) -> None:
        task = task_fixture(status="todo", reviewer="Human/Ops")
        state = {"workers": {}}
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(
                supervisor, "recent_task_failure_counts", return_value={"TASK-1": 2}
            ),
            mock.patch.object(supervisor, "persist_task_reassignment") as persisted,
        ):
            changed = supervisor.reconcile_failure_loops(self.config, state)
        self.assertFalse(changed)
        persisted.assert_not_called()

    def test_active_worker_blocks_failure_loop_action(self) -> None:
        task = task_fixture(status="in_progress", reviewer="Human/Ops")
        state = {
            "workers": {
                "run-a": {
                    "run_id": "run-a",
                    "task_id": "TASK-1",
                    "agent_id": "codex",
                    "status": "running",
                }
            }
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(
                supervisor, "recent_task_failure_counts", return_value={"TASK-1": 5}
            ),
            mock.patch.object(supervisor, "persist_task_reassignment") as persisted,
        ):
            changed = supervisor.reconcile_failure_loops(self.config, state)
        self.assertFalse(changed)
        persisted.assert_not_called()

    def test_already_on_hold_is_left_alone(self) -> None:
        task = task_fixture(status="todo", reviewer="Human/Ops")
        task["waiting_for"] = "Human/Ops"
        state = {"workers": {}}
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(
                supervisor, "recent_task_failure_counts", return_value={"TASK-1": 5}
            ),
            mock.patch.object(supervisor, "persist_task_reassignment") as persisted,
            mock.patch.object(supervisor, "record_failure_loop_blocker") as blocked,
        ):
            changed = supervisor.reconcile_failure_loops(self.config, state)
        self.assertFalse(changed)
        persisted.assert_not_called()
        blocked.assert_not_called()

    def test_first_breach_auto_reassigns_to_the_next_fallback(self) -> None:
        task = task_fixture(status="todo", reviewer="Human/Ops")
        state = {
            "workers": {},
            "delivery_health": {
                "version": 1,
                "endpoints": {
                    "codex": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                    "codex2": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                },
                "accounts": {
                    "codex_account": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                    "codex2_account": {"state": "healthy", "valid_until": "2999-01-01T00:00:00Z"},
                },
            },
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(
                supervisor, "recent_task_failure_counts", return_value={"TASK-1": 3}
            ),
            mock.patch.object(
                supervisor, "persist_task_reassignment", return_value=True
            ) as persisted,
        ):
            changed = supervisor.reconcile_failure_loops(self.config, state)
        self.assertTrue(changed)
        self.assertEqual(persisted.call_args.kwargs["new_owner"], "Codex2")
        self.assertEqual(persisted.call_args.kwargs["expected_owner"], "Codex")
        self.assertEqual(state["failure_loop_watch"]["TASK-1"]["auto_reassignments"], 1)

    def test_breach_after_reassignment_budget_exhausted_escalates_to_hold(self) -> None:
        task = task_fixture(status="todo", reviewer="Human/Ops")
        state = {
            "workers": {},
            "failure_loop_watch": {"TASK-1": {"auto_reassignments": 1}},
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(
                supervisor, "recent_task_failure_counts", return_value={"TASK-1": 3}
            ),
            mock.patch.object(supervisor, "persist_task_reassignment") as persisted,
            mock.patch.object(
                supervisor,
                "record_failure_loop_blocker",
                return_value={"task_id": "TASK-1"},
            ) as blocked,
        ):
            changed = supervisor.reconcile_failure_loops(self.config, state)
        self.assertTrue(changed)
        persisted.assert_not_called()
        self.assertEqual(blocked.call_args.kwargs["task_id"], "TASK-1")
        self.assertNotIn("TASK-1", state["failure_loop_watch"])


class CanonicalReassignmentGovernanceTests(unittest.TestCase):
    @staticmethod
    def _owner_worker() -> dict[str, object]:
        return {
            "run_id": "run-owner",
            "task_id": "TASK-1",
            "provider": "codex",
            "agent_id": "codex",
            "logical_agent_id": "codex",
            "status": "running",
            "lease_acquired_at": "2026-08-21T08:00:00Z",
            "request_snapshot": {"reason": supervisor.REASON_OWNED_IN_PROGRESS},
        }

    @staticmethod
    def _event(actor: str) -> dict[str, object]:
        transition = supervisor.rewrite_task_machine.assignment_transition(
            "Codex",
            "Codex2",
            "Codex2",
            "Codex",
            actor=actor,
            reason="Move the active owner lane",
        )
        return supervisor.rewrite_task_machine.build_assignment_activity_event(
            task_id="TASK-1",
            timestamp="2026-08-21T08:01:00Z",
            assignment=transition,
            old_generation=1,
            new_generation=2,
        )

    def test_both_canonical_writers_end_the_reassigned_owner_lease(self) -> None:
        config = config_fixture()
        task = task_fixture(owner="Codex2", reviewer="Codex") | {"generation": 2}
        for actor in ("Orchestrator", "Human/Ops"):
            with self.subTest(actor=actor):
                event = self._event(actor)
                decision = supervisor.active_worker_governance_lease_decision(
                    config,
                    self._owner_worker(),
                    task,
                    activity_events=[event],
                )

                self.assertEqual(decision["action"], "terminate")
                self.assertEqual(decision["reason_code"], "exact_owner_reassignment")
                self.assertEqual(decision["source_event_id"], event["event_id"])

    def test_payload_mutation_cannot_end_the_active_worker_lease(self) -> None:
        event = self._event("Human/Ops")
        event["new_owner"] = "Codex"

        decision = supervisor.active_worker_governance_lease_decision(
            config_fixture(),
            self._owner_worker(),
            task_fixture(owner="Codex2", reviewer="Codex") | {"generation": 2},
            activity_events=[event],
        )

        self.assertEqual(decision["action"], "preserve")
        self.assertEqual(decision["reason_code"], "invalid_reassignment_evidence")

    def test_stale_assignment_generation_cannot_end_the_current_worker_lease(self) -> None:
        decision = supervisor.active_worker_governance_lease_decision(
            config_fixture(),
            self._owner_worker(),
            task_fixture(owner="Codex2", reviewer="Codex") | {"generation": 3},
            activity_events=[self._event("Orchestrator")],
        )

        self.assertEqual(decision["action"], "preserve")
        self.assertEqual(decision["reason_code"], "concurrent_assignment_mutation")


class RuntimeAndFailureSemanticsTests(unittest.TestCase):
    @staticmethod
    def _owner_worker(*, generation: int) -> dict[str, object]:
        worker = {
            "run_id": "run-owner",
            "task_id": "TASK-1",
            "task_generation": generation,
            "provider": "codex",
            "agent_id": "codex",
            "logical_agent_id": "codex",
            "queue_event_id": "evt-owner",
            "status": "running",
            "lease_acquired_at": "2026-08-15T04:00:00Z",
            "pid": 1234,
            "pid_start_ticks": 5678,
            "request_snapshot": {
                "reason": supervisor.REASON_OWNED_IN_PROGRESS,
                "task_generation": generation,
                "metadata": {"task_generation": generation},
            },
        }
        worker["process_generation"] = supervisor.worker_process_generation_id(
            task_id="TASK-1",
            worker_run_id="run-owner",
            queue_event_id="evt-owner",
            pid=1234,
            pid_start_ticks=5678,
        )
        return worker

    @staticmethod
    def _exact_lifecycle_event(
        worker: dict[str, object],
        *,
        event_type: str,
    ) -> dict[str, object]:
        return {
            "event_id": f"event-{event_type}",
            "task_id": "TASK-1",
            "type": event_type,
            "ts": "2026-08-15T04:01:00Z",
            "status_command": {
                "worker_lease": supervisor.worker_process_identity(worker)
            },
        }

    def test_review_handoff_cannot_preserve_a_stale_worker_generation(self) -> None:
        config = config_fixture()
        task = task_fixture(status="review") | {"generation": 2}
        worker = self._owner_worker(generation=1)
        state = {
            "workers": {"run-owner": worker},
            "queue": {
                "events": {
                    "evt-owner": {
                        "intent": {"event_id": "evt-owner"},
                        "status": "started",
                    }
                }
            },
        }

        with (
            mock.patch.object(
                supervisor,
                "terminate_worker_process_generation",
                return_value=True,
            ) as terminate,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            outcome = supervisor.poll_worker_assignment_stage(
                config,
                state,
                worker,
                run_id="run-owner",
                task_map={"TASK-1": task},
                active_worker_statuses={"running"},
                alive=True,
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        terminate.assert_called_once_with(worker)
        self.assertEqual(worker["status"], "superseded")
        self.assertEqual(
            state["queue"]["events"]["evt-owner"]["status"],
            "completed",
        )
        self.assertNotIn("governance_lease_guard", worker)

    def test_review_handoff_preserves_the_current_worker_generation(self) -> None:
        config = config_fixture()
        task = task_fixture(status="review")
        worker = self._owner_worker(generation=1)
        state = {
            "workers": {"run-owner": worker},
            "queue": {
                "events": {
                    "evt-owner": {
                        "intent": {"event_id": "evt-owner"},
                        "status": "started",
                    }
                }
            },
        }

        with (
            mock.patch.object(
                supervisor,
                "terminate_worker_process_generation",
            ) as terminate,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            outcome = supervisor.poll_worker_assignment_stage(
                config,
                state,
                worker,
                run_id="run-owner",
                task_map={"TASK-1": task},
                active_worker_statuses={"running"},
                alive=True,
            )

        self.assertEqual(outcome, {"changed": True, "stop": False})
        terminate.assert_not_called()
        self.assertEqual(worker["status"], "running")
        self.assertEqual(
            worker["governance_lease_guard"]["reason_code"],
            "governance_only_transition",
        )

    def test_owner_handoff_ends_the_exact_worker_that_emitted_it(self) -> None:
        config = config_fixture()
        task = task_fixture(status="review")
        worker = self._owner_worker(generation=1)
        state = {
            "workers": {"run-owner": worker},
            "queue": {
                "events": {
                    "evt-owner": {
                        "intent": {"event_id": "evt-owner"},
                        "status": "started",
                    }
                }
            },
        }

        with (
            mock.patch.object(
                supervisor,
                "terminate_worker_process_generation",
                return_value=True,
            ) as terminate,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            outcome = supervisor.poll_worker_assignment_stage(
                config,
                state,
                worker,
                run_id="run-owner",
                task_map={"TASK-1": task},
                active_worker_statuses={"running"},
                alive=True,
                governance_activity_events=[
                    self._exact_lifecycle_event(worker, event_type="handoff")
                ],
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        terminate.assert_called_once_with(worker)
        self.assertEqual(worker["status"], "superseded")
        self.assertNotIn("governance_lease_guard", worker)

    def test_reviewer_reopen_ends_the_exact_worker_that_emitted_it(self) -> None:
        config = config_fixture()
        task = task_fixture(status="in_progress")
        worker = self._owner_worker(generation=1)
        worker.update(
            {
                "run_id": "run-reviewer",
                "agent_id": "codex2",
                "logical_agent_id": "codex2",
                "queue_event_id": "evt-reviewer",
                "request_snapshot": {
                    "reason": supervisor.REASON_REVIEW_READY,
                    "task_generation": 1,
                    "metadata": {"task_generation": 1},
                },
            }
        )
        worker["process_generation"] = supervisor.worker_process_generation_id(
            task_id="TASK-1",
            worker_run_id="run-reviewer",
            queue_event_id="evt-reviewer",
            pid=1234,
            pid_start_ticks=5678,
        )
        state = {
            "workers": {"run-reviewer": worker},
            "queue": {
                "events": {
                    "evt-reviewer": {
                        "intent": {"event_id": "evt-reviewer"},
                        "status": "started",
                    }
                }
            },
        }

        with (
            mock.patch.object(
                supervisor,
                "terminate_worker_process_generation",
                return_value=True,
            ) as terminate,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            outcome = supervisor.poll_worker_assignment_stage(
                config,
                state,
                worker,
                run_id="run-reviewer",
                task_map={"TASK-1": task},
                active_worker_statuses={"running"},
                alive=True,
                governance_activity_events=[
                    self._exact_lifecycle_event(worker, event_type="reopen")
                ],
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        terminate.assert_called_once_with(worker)
        self.assertEqual(worker["status"], "superseded")
        self.assertNotIn("governance_lease_guard", worker)

    def test_run_once_orders_launch_before_slow_maintenance(self) -> None:
        source = inspect.getsource(supervisor.run_once)
        self.assertNotIn('"sync_github_bus"', source)
        self.assertNotIn('"reconcile_blocked_tasks"', source)
        self.assertLess(source.index('"process_queue_reserved"'), source.index("probe_demanded_delivery_health"))
        self.assertNotIn("dispatch_chair_review", source)
        self.assertNotIn("discussion_planning", source)

    def test_run_once_prunes_existing_approvals_before_worker_polling(self) -> None:
        source = inspect.getsource(supervisor.run_once)
        self.assertLess(source.index('"prune_stale_approvals"'), source.index('"poll_workers_before_plan_reserved"'))

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
                mock.patch.object(supervisor, "reconcile_queue_intents", return_value=False),
                mock.patch.object(supervisor, "trim_worker_history"),
                mock.patch.object(supervisor, "trim_seen_events"),
                mock.patch.object(supervisor, "queue_events", return_value=[]),
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

    def test_exhausted_transient_worker_blocks_task_without_new_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".orchestrator").mkdir()
            config = config_fixture(root)
            config["worker_retry"] = {"enabled": True, "max_attempts": 1}
            task = task_fixture(status="in_progress")
            status = {"tasks": [task], "blockers": []}
            worker = self._owner_worker(generation=1)
            worker["retry_count"] = 1
            state = with_healthy_delivery_health(
                config,
                {
                    "workers": {"run-owner": worker},
                    "queue": {
                        "events": {
                            "evt-owner": {
                                "intent": {
                                    "event_id": "evt-owner",
                                    "task_id": "TASK-1",
                                    "task_generation": 1,
                                },
                                "status": "started",
                            }
                        }
                    },
                },
            )

            with (
                mock.patch.object(
                    supervisor,
                    "detect_worker_failure",
                    return_value="Error: timeout waiting for response",
                ),
                mock.patch.object(
                    supervisor,
                    "classify_worker_failure",
                    return_value={"kind": "transient", "label": "transient", "transient": True},
                ),
                mock.patch.object(supervisor, "write_failure_evidence", return_value="failure-ref"),
                mock.patch.object(supervisor, "maybe_rotate_provider_model", return_value="exhausted"),
                mock.patch.object(
                    supervisor,
                    "schedule_retry_from_worker_failure",
                    return_value=(False, False),
                ),
                mock.patch.object(supervisor, "record_delivery_health_failure"),
                mock.patch.object(supervisor, "load_status", return_value=status),
                mock.patch.object(supervisor, "write_status"),
                mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
                mock.patch.object(supervisor, "write_activity_log"),
                mock.patch.object(supervisor, "canonical_task_state_lock_file") as task_lock,
            ):
                task_lock.return_value.__enter__.return_value = None
                outcome = supervisor.poll_worker_failure_stage(config, state, worker)

            self.assertEqual(outcome, {"changed": True, "stop": True})
            self.assertEqual(worker["status"], "failed")
            self.assertEqual(state["queue"]["events"]["evt-owner"]["status"], "failed")
            self.assertEqual(task["status"], "blocked")
            self.assertEqual(status["blockers"][0]["blocker_kind"], "worker_retry_exhausted")
            self.assertEqual(
                status["status_activity_outbox"]["events"][0]["type"],
                "task_worker_retry_exhausted_blocked",
            )
            plan = supervisor.build_dispatch_plan(config, state, status, [], live_total=0)
            self.assertEqual(plan["events"], [])

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
        self.assertEqual(len(writes), 5)

    def test_file_worktree_quarantine_is_not_task_state(self) -> None:
        source = inspect.getsource(supervisor._quarantine_incomplete_worker_path)
        self.assertIn("ORCHESTRATOR_QUARANTINE.txt", source)
        self.assertNotIn("task[\"status\"]", source)


class WorkerLeaseApprovalWaitProgressTests(unittest.TestCase):
    """A worker legitimately blocked on an unresolved tool-use approval has
    no observable progress signal by design. Reclaiming its lease as "stuck"
    kills a healthy process and surfaces as "Approval state disappeared
    before the worker could resume" on the next reconciliation tick."""

    def setUp(self) -> None:
        self.config = {"worker_runtime": {"work_progress_stale_seconds": 360}}
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.stale_event_at = (self.now - timedelta(seconds=3600)).isoformat().replace("+00:00", "Z")
        self.fresh_event_at = (self.now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")

    def test_waiting_approval_with_stale_progress_is_exempted(self) -> None:
        worker = {"status": "waiting_approval", "last_event_at": self.stale_event_at}
        self.assertTrue(
            supervisor.worker_lease_progress_is_fresh(self.config, worker, self.now)
        )

    def test_suspended_approval_with_stale_progress_is_exempted(self) -> None:
        worker = {"status": "suspended_approval", "last_event_at": self.stale_event_at}
        self.assertTrue(
            supervisor.worker_lease_progress_is_fresh(self.config, worker, self.now)
        )

    def test_running_with_stale_progress_is_still_stale(self) -> None:
        worker = {"status": "running", "last_event_at": self.stale_event_at}
        self.assertFalse(
            supervisor.worker_lease_progress_is_fresh(self.config, worker, self.now)
        )

    def test_running_with_fresh_progress_is_fresh(self) -> None:
        worker = {"status": "running", "last_work_progress_at": self.fresh_event_at}
        self.assertTrue(
            supervisor.worker_lease_progress_is_fresh(self.config, worker, self.now)
        )

    def test_running_with_fresh_log_mtime_but_no_work_progress_is_stale(self) -> None:
        worker = {
            "status": "running",
            "last_event_at": self.fresh_event_at,
            "lease_acquired_at": self.stale_event_at,
        }
        self.assertFalse(
            supervisor.worker_lease_progress_is_fresh(self.config, worker, self.now)
        )

    def test_deferred_lease_termination_preserves_observation_contract(self) -> None:
        """A failed termination probe must not crash the poll driver.

        ``poll_workers`` reads ``observation["alive"]`` before it checks the
        stage's stop flag.  The deferred-termination branch therefore has to
        return the same liveness field as the normal observation path.
        """
        worker = {"pid": 1234, "status": "running", "process_activity_snapshot": {}}
        state = {"queue": {"events": {}}}
        poll_counts = {
            "marker_updates": 0,
            "commit_progress_updates": 0,
            "lease_refreshes": 0,
            "expired_lease_workers_failed": 0,
        }
        with (
            mock.patch.object(supervisor, "update_worker_runtime_markers", return_value=False),
            mock.patch.object(supervisor, "update_from_log", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "worker_process_activity_snapshot", return_value={}),
            mock.patch.object(supervisor, "worker_lease_can_renew", return_value=False),
            mock.patch.object(supervisor, "worker_lease_is_expired", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid", return_value=False),
        ):
            outcome = supervisor.poll_worker_observation_stage(
                self.config,
                state,
                worker,
                now=self.now,
                active_worker_statuses={"running"},
                poll_counts=poll_counts,
            )

        self.assertEqual(outcome, {"changed": False, "alive": True, "stop": True})

    def test_live_expired_lease_reuses_reconstructable_retry(self) -> None:
        config = config_fixture()
        task = task_fixture(status="in_progress")
        status = {"tasks": [task], "blockers": []}
        worker = {
            "run_id": "run-expired",
            "status": "running",
            "task_id": "TASK-1",
            "provider": "codex",
            "agent_id": "codex",
            "queue_event_id": "evt-expired",
        }
        state = {
            "workers": {"run-expired": worker},
            "queue": {"events": {"evt-expired": {"status": "started"}}},
        }
        observation = {
            "changed": False,
            "alive": True,
            "meaningful_progress_advanced": False,
            "commit_progress_advanced": False,
            "lease_expired": True,
            "stop": True,
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "recent_governance_activity_events", return_value=[]),
            mock.patch.object(
                supervisor, "poll_worker_orphan_stage", return_value={"changed": False, "stop": False}
            ),
            mock.patch.object(supervisor, "poll_worker_observation_stage", return_value=observation),
            mock.patch.object(supervisor, "record_delivery_health_for_reaped_worker", return_value=None),
            mock.patch.object(supervisor, "worker_lease_requires_work_progress", return_value=True),
            mock.patch.object(supervisor, "worker_lease_progress_is_fresh", return_value=False),
            mock.patch.object(supervisor, "schedule_reconstructable_worker_retry", return_value=True) as retry,
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "cleanup_inactive_worker_worktrees", return_value=False),
            mock.patch.object(supervisor, "record_worker_runtime_measurement"),
        ):
            self.assertTrue(supervisor.poll_workers(config, state))

        retry.assert_called_once_with(
            config,
            state,
            worker,
            "Worker lease expired after observed work progress became stale.",
            status=status,
            task=task,
        )

    def test_live_expired_lease_without_recovery_is_terminal_not_orphaned(self) -> None:
        config = config_fixture()
        task = task_fixture(status="in_progress")
        status = {"tasks": [task], "blockers": []}
        worker = {
            "run_id": "run-expired-terminal",
            "status": "running",
            "task_id": "TASK-1",
            "provider": "codex",
            "agent_id": "codex",
            "queue_event_id": "evt-expired-terminal",
        }
        record = {
            "intent": {
                "event_id": "evt-expired-terminal",
                "task_id": "TASK-1",
                "task_generation": 1,
                "event_key": "expired-lease-terminal-v1",
                "reason": supervisor.REASON_OWNED_READY,
                "target_agent": "codex",
            },
            "status": "started",
        }
        state = {
            "workers": {"run-expired-terminal": worker},
            "queue": {"events": {"evt-expired-terminal": record}},
        }
        observation = {
            "changed": False,
            "alive": True,
            "meaningful_progress_advanced": False,
            "commit_progress_advanced": False,
            "lease_expired": True,
            "stop": True,
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "recent_governance_activity_events", return_value=[]),
            mock.patch.object(
                supervisor, "poll_worker_orphan_stage", return_value={"changed": False, "stop": False}
            ),
            mock.patch.object(supervisor, "poll_worker_observation_stage", return_value=observation),
            mock.patch.object(supervisor, "record_delivery_health_for_reaped_worker", return_value=None),
            mock.patch.object(supervisor, "worker_lease_requires_work_progress", return_value=True),
            mock.patch.object(supervisor, "worker_lease_progress_is_fresh", return_value=False),
            mock.patch.object(supervisor, "schedule_reconstructable_worker_retry", return_value=False),
            mock.patch.object(supervisor, "record_retry_exhausted_worker_terminal_outcome") as terminal,
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "cleanup_inactive_worker_worktrees", return_value=False),
            mock.patch.object(supervisor, "record_worker_runtime_measurement"),
        ):
            self.assertTrue(supervisor.poll_workers(config, state))

        self.assertEqual(worker["status"], "failed")
        self.assertEqual(record["status"], "failed")
        terminal.assert_called_once()


class ProviderStreamLifecycleTests(unittest.TestCase):
    def test_antigravity_stream_progress_and_result_are_normalized_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "agy.log"
            log_path.write_text(
                "\n".join(
                    json.dumps(event)
                    for event in (
                        {"event": "init", "conversation_id": "conv-1"},
                        {
                            "event": "step_update",
                            "step_update": {"type": "agent_response", "text_delta": "ok"},
                        },
                        {
                            "event": "result",
                            "result": {
                                "status": "success",
                                "usage": {"input_tokens": 13, "output_tokens": 7},
                                "session_url": "https://agy/session/conv-1",
                            },
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            worker = {
                "log_path": str(log_path),
                "command": ["agy", "--output-format", "stream-json"],
                "status": "running",
            }
            now = datetime(2026, 8, 23, tzinfo=timezone.utc)
            self.assertTrue(supervisor.update_from_log({}, worker, now=now))
            self.assertEqual(worker["session_id"], "conv-1")
            self.assertEqual(worker["provider_terminal_status"], "success")
            self.assertEqual(worker["provider_usage"], {"input_tokens": 13, "output_tokens": 7})
            self.assertEqual(worker["last_work_progress_at"], "2026-08-23T00:00:00Z")
            self.assertFalse(supervisor.update_from_log({}, worker, now=now))

    def test_antigravity_result_error_is_authoritative_with_zero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "agy.log"
            log_path.write_text(
                json.dumps({"event": "result", "result": {"status": "error", "error": "quota"}})
                + "\n",
                encoding="utf-8",
            )
            worker = {
                "log_path": str(log_path),
                "command": ["agy", "--output-format", "stream-json"],
                "runner_status": "completed",
                "exit_code": 0,
            }
            self.assertIsNotNone(supervisor.detect_worker_failure(worker))

    def test_terminal_status_requires_exact_worker_lease_event(self) -> None:
        config = config_fixture()
        task = task_fixture(status="review")
        worker = RuntimeAndFailureSemanticsTests._owner_worker(generation=1)
        event = {
            "task_id": "TASK-1",
            "type": "handoff",
            "ts": "2026-08-15T04:01:00Z",
            "agent": "Codex",
            "status_command": {"worker_lease": supervisor.worker_process_identity(worker)},
        }
        self.assertEqual(
            supervisor.canonical_worker_terminal_status(
                config, worker, task, activity_events=[event]
            ),
            "review",
        )
        event["status_command"]["worker_lease"]["pid_start_ticks"] = 9999
        self.assertIsNone(
            supervisor.canonical_worker_terminal_status(
                config, worker, task, activity_events=[event]
            )
        )


class ExecutionResourceAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = config_fixture()
        self.state = runtime_state.default_state()
        self.state["delivery_health"] = healthy_delivery_health(self.config)

    def test_dispatch_ready_tasks_admits_single_pantheon_dev_task(self) -> None:
        task = task_fixture(
            "HOSTED-1",
            status="todo",
            owner="Codex",
            execution_resources=["pantheon-dev"],
        )
        status = {"tasks": [task]}
        dispatched: list[dict[str, Any]] = []

        def capture(_config: dict[str, Any], event: dict[str, Any]) -> bool:
            dispatched.append(event)
            return True

        changed = supervisor.dispatch_ready_tasks(
            self.config,
            self.state,
            status_snapshot=status,
            event_sink=capture,
            live_total_snapshot=0,
        )
        self.assertTrue(changed)
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]["task_id"], "HOSTED-1")
        self.assertEqual(dispatched[0]["target_agent"], "Codex")

    def test_dispatch_ready_tasks_blocks_second_pantheon_dev_task_when_active(self) -> None:
        hosted_1 = task_fixture(
            "HOSTED-1",
            status="todo",
            owner="Codex",
            execution_resources=["pantheon-dev"],
        )
        hosted_2 = task_fixture(
            "HOSTED-2",
            status="todo",
            owner="Codex2",
            execution_resources=["pantheon-dev"],
        )
        worktree_task = task_fixture(
            "WORKTREE-1",
            status="todo",
            owner="Codex2",
        )
        self.state["workers"]["run-hosted-1"] = {
            "task_id": "HOSTED-1",
            "agent_id": "codex",
            "status": "running",
        }
        status = {"tasks": [hosted_1, hosted_2, worktree_task]}
        dispatched: list[dict[str, Any]] = []

        def capture(_config: dict[str, Any], event: dict[str, Any]) -> bool:
            dispatched.append(event)
            return True

        changed = supervisor.dispatch_ready_tasks(
            self.config,
            self.state,
            status_snapshot=status,
            event_sink=capture,
            live_total_snapshot=1,
        )
        self.assertTrue(changed)
        # HOSTED-2 blocked due to pantheon-dev capacity 1, WORKTREE-1 dispatched in parallel
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]["task_id"], "WORKTREE-1")
        self.assertEqual(dispatched[0]["target_agent"], "Codex2")

    def test_dispatch_ready_tasks_blocks_second_pantheon_dev_task_when_queued(self) -> None:
        hosted_1 = task_fixture(
            "HOSTED-1",
            status="todo",
            owner="Codex",
            execution_resources=["pantheon-dev"],
        )
        hosted_2 = task_fixture(
            "HOSTED-2",
            status="todo",
            owner="Codex2",
            execution_resources=["pantheon-dev"],
        )
        worktree_task = task_fixture(
            "WORKTREE-1",
            status="todo",
            owner="Codex2",
        )
        status = {"tasks": [hosted_1, hosted_2, worktree_task]}
        dispatched: list[dict[str, Any]] = []

        def capture(_config: dict[str, Any], event: dict[str, Any]) -> bool:
            dispatched.append(event)
            return True

        changed = supervisor.dispatch_ready_tasks(
            self.config,
            self.state,
            status_snapshot=status,
            event_sink=capture,
            live_total_snapshot=0,
        )
        self.assertTrue(changed)
        dispatched_ids = {item["task_id"] for item in dispatched}
        self.assertIn("HOSTED-1", dispatched_ids)
        self.assertNotIn("HOSTED-2", dispatched_ids)
        self.assertIn("WORKTREE-1", dispatched_ids)

    def test_evaluate_queued_delivery_admission_rejects_when_resource_active(self) -> None:
        hosted_1 = task_fixture(
            "HOSTED-1",
            status="todo",
            owner="Codex",
            execution_resources=["pantheon-dev"],
        )
        hosted_2 = task_fixture(
            "HOSTED-2",
            status="todo",
            owner="Codex2",
            execution_resources=["pantheon-dev"],
        )
        task_map = {"HOSTED-1": hosted_1, "HOSTED-2": hosted_2}
        event_2 = {
            "event_id": "evt-hosted-2",
            "task_id": "HOSTED-2",
            "target_agent": "Codex2",
            "delivery_endpoint_id": "codex2",
            "reason": "owned_ready",
        }
        self.state["workers"]["run-hosted-1"] = {
            "task_id": "HOSTED-1",
            "agent_id": "codex",
            "status": "running",
        }
        decision = supervisor.evaluate_queued_delivery_admission(
            self.config,
            self.state,
            event_2,
            task_map,
            [event_2],
        )
        self.assertIsNotNone(decision)
        self.assertFalse(decision.eligible)
        self.assertEqual(
            decision.reason,
            supervisor.rewrite_dispatch_admission.DispatchBlockReason.RESOURCE_CAPACITY_REACHED,
        )

    def test_resource_released_after_worker_completes(self) -> None:
        hosted_1 = task_fixture(
            "HOSTED-1",
            status="todo",
            owner="Codex",
            execution_resources=["pantheon-dev"],
        )
        task_map = {"HOSTED-1": hosted_1}
        self.state["workers"]["run-hosted-1"] = {
            "task_id": "HOSTED-1",
            "agent_id": "codex",
            "status": "completed",
        }
        active_statuses = {"starting", "running", "waiting_approval"}
        counts = supervisor.active_execution_resource_counts(
            self.state, task_map, active_statuses
        )
        self.assertEqual(counts, {})

    def test_resource_released_after_queue_event_fails(self) -> None:
        hosted_1 = task_fixture(
            "HOSTED-1",
            status="todo",
            owner="Codex",
            execution_resources=["pantheon-dev"],
        )
        task_map = {"HOSTED-1": hosted_1}
        event = {
            "event_id": "evt-hosted-1",
            "task_id": "HOSTED-1",
            "target_agent": "Codex",
            "delivery_endpoint_id": "codex",
            "reason": "owned_ready",
        }
        self.state["queue"]["events"]["evt-hosted-1"] = {"status": "failed"}
        counts = supervisor.queued_execution_resource_counts(
            self.config, self.state, [event], task_map
        )
        self.assertEqual(counts, {})

    def test_explain_dispatch_shows_resource_capacity_reached(self) -> None:
        hosted_1 = task_fixture(
            "HOSTED-1",
            status="todo",
            owner="Codex",
            execution_resources=["pantheon-dev"],
        )
        hosted_2 = task_fixture(
            "HOSTED-2",
            status="todo",
            owner="Codex2",
            execution_resources=["pantheon-dev"],
        )
        self.state["workers"]["run-hosted-1"] = {
            "task_id": "HOSTED-1",
            "agent_id": "codex",
            "status": "running",
        }
        status = {"tasks": [hosted_1, hosted_2]}
        explanation = supervisor.explain_dispatch_for_task(
            self.config,
            self.state,
            "HOSTED-2",
            status=status,
            live_total=1,
        )
        codex2_trace = explanation["agents"]["Codex2"]
        self.assertTrue(codex2_trace["blocked"])
        self.assertEqual(
            codex2_trace["first_blocking_gate"],
            "resource_capacity_reached",
        )


    def test_evaluate_queued_delivery_admission_deterministic_oldest_wins_multiple_pending(self) -> None:
        hosted_1 = task_fixture(
            "HOSTED-1",
            status="todo",
            owner="Codex",
            execution_resources=["pantheon-dev"],
        )
        hosted_2 = task_fixture(
            "HOSTED-2",
            status="todo",
            owner="Codex2",
            execution_resources=["pantheon-dev"],
        )
        task_map = {"HOSTED-1": hosted_1, "HOSTED-2": hosted_2}
        event_1 = {
            "event_id": "evt-hosted-1",
            "created_at": "2026-08-25T10:00:00Z",
            "task_id": "HOSTED-1",
            "target_agent": "Codex",
            "delivery_endpoint_id": "codex",
            "reason": "owned_ready",
        }
        event_2 = {
            "event_id": "evt-hosted-2",
            "created_at": "2026-08-25T10:05:00Z",
            "task_id": "HOSTED-2",
            "target_agent": "Codex2",
            "delivery_endpoint_id": "codex2",
            "reason": "owned_ready",
        }
        queue_events = [event_2, event_1]  # Even if presented in arbitrary order

        # Oldest event (event_1) is eligible
        decision_1 = supervisor.evaluate_queued_delivery_admission(
            self.config,
            self.state,
            event_1,
            task_map,
            queue_events,
        )
        self.assertIsNotNone(decision_1)
        self.assertTrue(decision_1.eligible)
        self.assertIsNone(decision_1.reason)

        # Newer event (event_2) is blocked by the pending oldest claim
        decision_2 = supervisor.evaluate_queued_delivery_admission(
            self.config,
            self.state,
            event_2,
            task_map,
            queue_events,
        )
        self.assertIsNotNone(decision_2)
        self.assertFalse(decision_2.eligible)
        self.assertEqual(
            decision_2.reason,
            supervisor.rewrite_dispatch_admission.DispatchBlockReason.RESOURCE_CAPACITY_REACHED,
        )

    def test_process_queue_launches_oldest_pending_event_and_admits_next_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".orchestrator").mkdir()
            config = config_fixture(root)
            hosted_1 = task_fixture(
                "HOSTED-1",
                status="todo",
                owner="Codex",
                execution_resources=["pantheon-dev"],
            )
            hosted_2 = task_fixture(
                "HOSTED-2",
                status="todo",
                owner="Codex2",
                execution_resources=["pantheon-dev"],
            )
            status = {"tasks": [hosted_1, hosted_2]}
            event_1 = {
                "event_id": "evt-hosted-1",
                "created_at": "2026-08-25T10:00:00Z",
                "task_id": "HOSTED-1",
                "task_generation": 1,
                "target_agent": "codex",
                "delivery_endpoint_id": "codex",
                "reason": "owned_ready_dispatch",
                "message": "Start HOSTED-1",
            }
            event_2 = {
                "event_id": "evt-hosted-2",
                "created_at": "2026-08-25T10:05:00Z",
                "task_id": "HOSTED-2",
                "task_generation": 1,
                "target_agent": "codex2",
                "delivery_endpoint_id": "codex2",
                "reason": "owned_ready_dispatch",
                "message": "Start HOSTED-2",
            }
            runtime_state.store_queue_event(self.state, event_2)
            runtime_state.store_queue_event(self.state, event_1)

            with mock.patch.object(supervisor, "load_status", return_value=status), \
                 mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)), \
                 mock.patch.object(
                     supervisor,
                     "start_worker_for_request",
                     side_effect=lambda cfg, st, request, **kwargs: (
                         True,
                         f"run-{request.task_id.lower()}",
                         {"auto_delivered": True},
                     ),
                 ), \
                 mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True), \
                 mock.patch.object(supervisor, "write_activity_log"):
                # First tick launches the oldest event (HOSTED-1)
                changed = supervisor.process_queue(config, self.state)
                self.assertTrue(changed)
                record_1 = self.state["queue"]["events"]["evt-hosted-1"]
                record_2 = self.state["queue"]["events"]["evt-hosted-2"]
                self.assertEqual(record_1["status"], "started")
                self.assertEqual(record_2["status"], "queued")

                # Simulate worker running for HOSTED-1
                self.state["workers"]["run-hosted-1"] = {
                    "run_id": "run-hosted-1",
                    "task_id": "HOSTED-1",
                    "queue_event_id": "evt-hosted-1",
                    "agent_id": "codex",
                    "status": "running",
                }

                # Next tick: HOSTED-2 is evaluated and marked pending due to active pantheon-dev resource
                changed = supervisor.process_queue(config, self.state)
                self.assertTrue(changed)
                record_2 = self.state["queue"]["events"]["evt-hosted-2"]
                self.assertEqual(record_2["status"], "pending")
                self.assertEqual(record_2["last_wait_reason"], "resource_capacity_reached")
                self.assertIsNone(record_2.get("run_id"))

                # Simulate HOSTED-1 worker completing and queue event marked completed
                self.state["workers"]["run-hosted-1"]["status"] = "completed"
                record_1["status"] = "completed"

                # Next tick: HOSTED-2 is now eligible and launches
                changed = supervisor.process_queue(config, self.state)
                self.assertTrue(changed)
                record_2 = self.state["queue"]["events"]["evt-hosted-2"]
                self.assertEqual(record_2["status"], "started")

    def test_task_execution_resources_strict_validation_and_rejections(self) -> None:
        # Missing field / None task defaults to []
        self.assertEqual(supervisor.task_execution_resources(None), [])
        self.assertEqual(supervisor.task_execution_resources({}), [])
        self.assertEqual(supervisor.task_execution_resources({"id": "TASK-1"}), [])
        self.assertEqual(supervisor.task_execution_resources({"id": "TASK-1", "execution_resources": []}), [])

        # Explicit None (null) is malformed and must fail closed
        with self.assertRaisesRegex(ValueError, "must be a list, got null"):
            supervisor.task_execution_resources({"id": "TASK-1", "execution_resources": None})

        # Valid allowlisted list
        self.assertEqual(
            supervisor.task_execution_resources({"id": "TASK-1", "execution_resources": ["pantheon-dev"]}),
            ["pantheon-dev"],
        )

        # Raw string must fail closed
        with self.assertRaisesRegex(ValueError, "must be a list"):
            supervisor.task_execution_resources({"id": "TASK-1", "execution_resources": "pantheon-dev"})

        # Duplicates must fail closed
        with self.assertRaisesRegex(ValueError, "duplicate resource"):
            supervisor.task_execution_resources({
                "id": "TASK-1",
                "execution_resources": ["pantheon-dev", "pantheon-dev"],
            })

        # Non-string elements must fail closed
        with self.assertRaisesRegex(ValueError, "elements must be strings"):
            supervisor.task_execution_resources({"id": "TASK-1", "execution_resources": [123]})

        # Empty or whitespace string elements must fail closed
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            supervisor.task_execution_resources({"id": "TASK-1", "execution_resources": [""]})
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            supervisor.task_execution_resources({"id": "TASK-1", "execution_resources": ["   "]})

        # Unknown/unallowlisted resources must fail closed
        with self.assertRaisesRegex(ValueError, "unallowlisted resource"):
            supervisor.task_execution_resources({"id": "TASK-1", "execution_resources": ["unknown-res"]})

        # Non-list container types must fail closed
        with self.assertRaisesRegex(ValueError, "must be a list"):
            supervisor.task_execution_resources({"id": "TASK-1", "execution_resources": {"pantheon-dev": 1}})
        with self.assertRaisesRegex(ValueError, "must be a list"):
            supervisor.task_execution_resources({"id": "TASK-1", "execution_resources": 1})

    def test_validate_execution_resource_limits_rejections(self) -> None:
        # Default when None or empty
        self.assertEqual(supervisor.validate_execution_resource_limits(None), {"pantheon-dev": 1})
        self.assertEqual(supervisor.validate_execution_resource_limits({}), {"pantheon-dev": 1})
        self.assertEqual(
            supervisor.validate_execution_resource_limits({"pantheon-dev": 1}),
            {"pantheon-dev": 1},
        )

        # Rejection of bool
        with self.assertRaisesRegex(ValueError, "boolean True is not allowed"):
            supervisor.validate_execution_resource_limits({"pantheon-dev": True})
        with self.assertRaisesRegex(ValueError, "boolean False is not allowed"):
            supervisor.validate_execution_resource_limits({"pantheon-dev": False})

        # Rejection of string values
        with self.assertRaisesRegex(ValueError, "expected int, got str"):
            supervisor.validate_execution_resource_limits({"pantheon-dev": "1"})

        # Rejection of zero or negative
        with self.assertRaisesRegex(ValueError, "value must be 1, got 0"):
            supervisor.validate_execution_resource_limits({"pantheon-dev": 0})
        with self.assertRaisesRegex(ValueError, "value must be 1, got -1"):
            supervisor.validate_execution_resource_limits({"pantheon-dev": -1})

        # Rejection of values > 1
        with self.assertRaisesRegex(ValueError, "value must be 1, got 2"):
            supervisor.validate_execution_resource_limits({"pantheon-dev": 2})

        # Rejection of unknown keys
        with self.assertRaisesRegex(ValueError, "Unknown execution resource limit key"):
            supervisor.validate_execution_resource_limits({"custom-res": 1})
        with self.assertRaisesRegex(ValueError, "Unknown execution resource limit key"):
            supervisor.validate_execution_resource_limits({"PANTHEON-DEV": 1, "unknown": 1})

        # Rejection of non-dict types
        with self.assertRaisesRegex(ValueError, "must be a dict or null"):
            supervisor.validate_execution_resource_limits("invalid")
        with self.assertRaisesRegex(ValueError, "must be a dict or null"):
            supervisor.validate_execution_resource_limits([1])

    def test_reserve_dispatch_plan_re_evaluates_admission_rejects_post_plan_human_ops_hold(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".orchestrator").mkdir()
            config = config_fixture(root)
            hosted_task = task_fixture(
                "HOSTED-HOLD-1",
                status="todo",
                owner="Codex",
                execution_resources=["pantheon-dev"],
            )
            initial_status = {"tasks": [copy.deepcopy(hosted_task)]}

            # Planning phase produces 1 event
            with mock.patch.object(supervisor, "load_status", return_value=initial_status):
                plan = supervisor.build_dispatch_plan(
                    config,
                    self.state,
                    initial_status,
                    supervisor.queue_events(self.state),
                    live_total=0,
                )
            self.assertEqual(len(plan["events"]), 1)

            # TOCTOU: Before reservation, Human/Ops places a hold on canonical task
            held_task = copy.deepcopy(hosted_task)
            held_task["waiting_for"] = "Human/Ops"
            held_status = {"tasks": [held_task]}

            with (
                mock.patch.object(supervisor, "load_status", return_value=held_status),
                mock.patch.object(
                    supervisor,
                    "_queue_delivery_event_locked",
                    side_effect=AssertionError("Held task must not be queued"),
                ),
            ):
                supervisor.reserve_dispatch_plan(config, self.state, plan)

            # Proves zero queue event, zero pending slot, zero pantheon-dev claim
            self.assertEqual(supervisor.queue_events(self.state), [])
            self.assertNotIn(plan["events"][0]["key"], self.state.get("seen_event_keys", {}))
            self.assertEqual(
                supervisor.queued_execution_resource_counts(
                    config, self.state, task_map={"HOSTED-HOLD-1": held_task}
                ),
                {},
            )

    def test_queued_execution_resource_counts_releases_stale_task_state(self) -> None:
        hosted_1 = task_fixture(
            "HOSTED-STALE-1",
            status="todo",
            owner="Codex",
            execution_resources=["pantheon-dev"],
        )
        task_map = {"HOSTED-STALE-1": hosted_1}
        event = {
            "event_id": "evt-hosted-stale-1",
            "task_id": "HOSTED-STALE-1",
            "task_generation": 1,
            "target_agent": "Codex",
            "delivery_endpoint_id": "codex",
            "reason": "owned_ready_dispatch",
        }
        # Initially pending queue event claims resource
        counts = supervisor.queued_execution_resource_counts(
            self.config, self.state, [event], task_map
        )
        self.assertEqual(counts, {"pantheon-dev": 1})

        # When task transitions to terminal/done, event becomes stale and resource is released
        hosted_1_done = copy.deepcopy(hosted_1)
        hosted_1_done["status"] = "done"
        stale_map = {"HOSTED-STALE-1": hosted_1_done}
        counts_stale = supervisor.queued_execution_resource_counts(
            self.config, self.state, [event], stale_map
        )
        self.assertEqual(counts_stale, {})


if __name__ == "__main__":
    unittest.main()
