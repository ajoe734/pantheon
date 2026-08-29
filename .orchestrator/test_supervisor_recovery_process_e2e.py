#!/usr/bin/env python3
"""Process-level recovery tests for Supervisor Authority V2.

These tests deliberately cross real process and filesystem boundaries.  They
complement the focused contract tests in ``test_supervisor.py``: no worker
observation stage is mocked, killed PIDs are real Linux process generations,
and review-requeue recovery happens in fresh Python interpreters.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ORCHESTRATOR_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ORCHESTRATOR_ROOT.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
sys.path.insert(0, str(ORCHESTRATOR_ROOT))

import runtime_state
import supervisor
import common
import supervisor_runtime_health
from adapters import ADAPTERS
from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult
from test_supervisor import config_fixture, task_fixture, with_healthy_delivery_health


def _iso_after(seconds: int) -> str:
    value = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return value.isoformat().replace("+00:00", "Z")


class ProcessE2EAdapter(BaseAdapter):
    """Spawn a harmless long-lived process through the real launch boundary."""

    name = "process_e2e"
    processes: list[subprocess.Popen[bytes]] = []

    def capability(self, agent_id: str) -> DeliveryCapability:
        return DeliveryCapability(
            adapter=self.name,
            supported=True,
            requires_manual_confirmation=False,
            can_auto_deliver=True,
            can_auto_approve_edits=True,
            delivery_mode=self.name,
            verified="test",
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        command = [
            sys.executable,
            "-c",
            "import time; time.sleep(300)",
        ]
        process = subprocess.Popen(
            command,
            cwd=str(request.metadata["workspace_path"]),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.processes.append(process)
        run_id = f"replacement-{process.pid}"
        return DeliveryResult(
            ok=True,
            adapter=self.name,
            mode=self.name,
            target=request.agent_id,
            auto_delivered=True,
            manual_confirmation_required=False,
            command=command,
            pid=process.pid,
            run_id=run_id,
        )


class SupervisorRecoveryProcessE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temp_root = Path(self.temporary.name)
        self.status_root = self.temp_root / "status"
        self.runtime_root = self.temp_root / "runtime"
        self.workspace = self.temp_root / "workspace"
        (self.status_root / ".orchestrator").mkdir(parents=True)
        self.runtime_root.mkdir()
        self.workspace.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", "--initial-branch=dev"],
            cwd=self.status_root,
            check=True,
        )

        # Status recovery deliberately refuses a mutable task checkout or a
        # command SHA not contained by its declared base.  Build the same
        # immutable identity shape as a promoted runtime without touching the
        # repository under test: a clean local clone whose origin/dev points
        # at the exact executable commit.
        self.command_root = self.temp_root / "command-runtime"
        subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(REPOSITORY_ROOT), str(self.command_root)],
            check=True,
        )
        subprocess.run(
            ["git", "remote", "set-url", "origin", "https://github.com/ajoe734/pantheon.git"],
            cwd=self.command_root,
            check=True,
        )
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/dev", "HEAD"],
            cwd=self.command_root,
            check=True,
        )
        self.original_common_root = common.ROOT
        common.ROOT = self.command_root
        self.addCleanup(setattr, common, "ROOT", self.original_common_root)

        self.config = config_fixture(self.status_root)
        self.config["paths"]["approval_queue"] = str(
            self.status_root / ".orchestrator" / "approval-queue.json"
        )
        self.config["task_state_store"] = {
            "mode": "authoritative",
            "event_log": str(self.runtime_root / "task-state-events.jsonl"),
        }
        for agent in self.config["agents"].values():
            agent["adapter"] = ProcessE2EAdapter.name
        self.config_path = self.status_root / ".orchestrator" / "config.json"
        self.config_path.write_text(
            json.dumps(self.config, sort_keys=True) + "\n", encoding="utf-8"
        )
        Path(self.config["paths"]["approval_queue"]).write_text(
            '{"version":2,"pending":[],"history":[]}\n', encoding="utf-8"
        )

        self.task = task_fixture(status="in_progress")
        self.status = {"tasks": [self.task], "blockers": [], "handoffs": []}
        supervisor.rewrite_task_state_store.append_state_commit(
            self.config["task_state_store"]["event_log"],
            self.status,
            source="process-e2e-seed",
        )
        Path(self.config["paths"]["status_file"]).write_text(
            json.dumps(self.status, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.state = with_healthy_delivery_health(
            self.config,
            {
                "version": 2,
                "workers": {},
                "queue": {"version": 2, "events": {}},
                "seen_event_keys": {},
            },
        )
        ProcessE2EAdapter.processes = []
        self.processes: list[subprocess.Popen[bytes]] = []
        self.addCleanup(self._stop_processes)

    def _stop_processes(self) -> None:
        for process in [*self.processes, *ProcessE2EAdapter.processes]:
            if process.poll() is not None:
                continue
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)

    def _spawn_worker_process(self) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)"],
            start_new_session=True,
        )
        self.processes.append(process)
        return process

    @staticmethod
    def _kill_process_group(pid: int) -> None:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return

    def _store_started_worker(self, process: subprocess.Popen[bytes]) -> dict[str, object]:
        run_id = f"lost-{process.pid}"
        event_id = f"event-{process.pid}"
        start_ticks = supervisor.worker_pid_start_ticks(process.pid)
        self.assertIsNotNone(start_ticks)
        event = supervisor.build_dispatch_event(
            self.task,
            "Codex",
            supervisor.REASON_OWNED_IN_PROGRESS,
            {"TASK-1": self.task},
            config=self.config,
        )
        event.update(
            {
                "event_id": event_id,
                "event_key": event["key"],
                "target_agent": "codex",
                "target_display_name": "Codex",
                "delivery_endpoint_id": "codex",
                "provider": "codex",
                "message": "process-level lost lease fixture",
                "created_at": supervisor.utc_now(),
            }
        )
        runtime_state.store_queue_event(self.state, event)
        queue_record = self.state["queue"]["events"][event_id]
        queue_record.update(
            {
                "status": "started",
                "run_id": run_id,
                "lease_owner": run_id,
                "lease_expires_at": _iso_after(300),
            }
        )
        worker: dict[str, object] = {
            "run_id": run_id,
            "task_id": "TASK-1",
            "task_generation": 1,
            "provider": "codex",
            "agent_id": "codex",
            "logical_agent_id": "codex",
            "queue_event_id": event_id,
            "status": "running",
            "pid": process.pid,
            "pid_start_ticks": start_ticks,
            "process_generation": supervisor.worker_process_generation_id(
                task_id="TASK-1",
                worker_run_id=run_id,
                queue_event_id=event_id,
                pid=process.pid,
                pid_start_ticks=start_ticks,
            ),
            "lease_acquired_at": supervisor.utc_now(),
            "lease_expires_at": _iso_after(300),
            "request_snapshot": {
                "reason": supervisor.REASON_OWNED_IN_PROGRESS,
                "task_generation": 1,
                "metadata": {"task_generation": 1},
            },
        }
        self.state["workers"][run_id] = worker
        runtime_state.save_runtime_state(self.config, self.state)
        return worker

    def _child(self, body: str, *, expected_returncode: int) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ORCHESTRATOR_ROOT), str(REPOSITORY_ROOT), env.get("PYTHONPATH", "")]
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import common,sys; from pathlib import Path as _P; "
                    "common.ROOT=_P(sys.argv[2]); "
                    + body
                ),
                str(self.config_path),
                str(self.command_root),
            ],
            cwd=REPOSITORY_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            expected_returncode,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )

    def _status_command_environment(self) -> dict[str, str]:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.command_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        event_log = Path(self.config["task_state_store"]["event_log"])
        identity = common.canonical_task_state_identity_for_paths(
            status_root=self.status_root,
            event_log=event_log,
        )
        environment = os.environ.copy()
        environment.update(
            {
                "PANTHEON_LIVE_SUPERVISOR_CONFIG": str(
                    self.temp_root / "no-live-config.json"
                ),
                "PANTHEON_COMMAND_ROOT": str(self.command_root),
                "PANTHEON_COMMAND_RUNTIME_SHA": head,
                "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
                "PANTHEON_COMMAND_BASE_REF": "origin/dev",
                "PANTHEON_STATUS_ROOT": str(self.status_root),
                "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
                "PANTHEON_TASK_STATE_EVENT_LOG": str(event_log),
                "PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON": json.dumps(
                    identity, sort_keys=True, separators=(",", ":")
                ),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        return environment

    def test_sigkill_recovers_receipt_and_launches_one_replacement_generation(self) -> None:
        original = self._spawn_worker_process()
        lost_worker = self._store_started_worker(original)

        # A genuinely live PID must not be recovered or reassigned.
        supervisor.poll_workers(self.config, self.state)
        self.assertEqual(supervisor.load_status(self.config)["tasks"][0]["owner"], "Codex")
        self.assertEqual(lost_worker["status"], "running")
        self.assertNotIn("lost_lease_receipt_id", lost_worker)

        os.killpg(original.pid, signal.SIGKILL)
        original.wait(timeout=5)
        self.assertFalse(supervisor.pid_is_alive(original.pid))

        self.assertTrue(supervisor.poll_workers(self.config, self.state))
        recovered_status = supervisor.load_status(self.config)
        recovered_task = recovered_status["tasks"][0]
        pointer = recovered_task[supervisor.WORKER_RECOVERY_TASK_KEY]
        receipt_id = pointer["receipt_id"]
        receipt = recovered_status[supervisor.WORKER_RECOVERY_RECEIPTS_KEY][receipt_id]
        if receipt["status"] == "pending":
            reconciled = supervisor.reconcile_pending_worker_recoveries(
                self.config, self.state
            )
            if not reconciled:
                activity_path = Path(self.config["paths"]["activity_log"])
                activity = (
                    activity_path.read_text(encoding="utf-8")
                    if activity_path.exists()
                    else "(missing)"
                )
                self.fail(
                    "pending recovery did not reconcile; "
                    f"status={json.dumps(recovered_status, sort_keys=True)}; "
                    f"activity={activity}"
                )
            recovered_status = supervisor.load_status(self.config)
            recovered_task = recovered_status["tasks"][0]
            receipt = recovered_status[supervisor.WORKER_RECOVERY_RECEIPTS_KEY][
                receipt_id
            ]
        self.assertEqual(receipt["status"], "reassigned")
        self.assertEqual(receipt["reason_kind"], "worker_process_missing")
        self.assertEqual(recovered_task["owner"], "Codex2")
        self.assertGreater(recovered_task["generation"], 1)
        self.assertEqual(lost_worker["status"], "superseded")

        replacement_events = [
            event
            for event in supervisor.queue_events(self.state)
            if event.get("recovery_receipt_id") == receipt_id
        ]
        self.assertEqual(len(replacement_events), 1)
        replacement_event = replacement_events[0]
        request = DeliveryRequest(
            agent_id="codex2",
            provider="codex2",
            delivery_mode=ProcessE2EAdapter.name,
            message="process-level replacement",
            task_id="TASK-1",
            reason=str(replacement_event["reason"]),
            metadata={
                "task_generation": recovered_task["generation"],
                "workspace_path": str(self.workspace),
                "status_root": str(self.status_root),
                "recovery_receipt_id": receipt_id,
            },
        )
        ADAPTERS[ProcessE2EAdapter.name] = ProcessE2EAdapter
        self.addCleanup(ADAPTERS.pop, ProcessE2EAdapter.name, None)
        started, replacement_run_id, _ = supervisor.start_worker_for_request(
            self.config,
            self.state,
            request,
            dispatch_event=replacement_event,
            queue_event_id=str(replacement_event["event_id"]),
            attempt_count=1,
            event_id_for_log=str(replacement_event["event_id"]),
        )
        self.assertTrue(started)
        self.assertIsNotNone(replacement_run_id)
        replacement = self.state["workers"][replacement_run_id]

        materialized = supervisor.load_status(self.config)[
            supervisor.WORKER_RECOVERY_RECEIPTS_KEY
        ][receipt_id]
        self.assertEqual(materialized["status"], "materialized")
        self.assertEqual(
            materialized["replacement"]["worker_run_id"], replacement_run_id
        )
        active_current = [
            worker
            for worker in self.state["workers"].values()
            if worker.get("status") == "running"
            and worker.get("task_id") == "TASK-1"
            and worker.get("task_generation") == recovered_task["generation"]
            and supervisor.pid_is_alive(worker.get("pid"))
        ]
        self.assertEqual(len(active_current), 1)

    def test_review_requeue_survives_two_hard_process_crash_boundaries_once(self) -> None:
        pending = {
            "schema_version": supervisor.REVIEW_REQUEUE_INTENT_SCHEMA_VERSION,
            "intent_id": "review-requeue-" + "e" * 64,
            "status": "pending",
            "task_id": "TASK-1",
            "task_generation": 1,
            "owner": "Codex",
            "reviewer": "Codex2",
            "reopened_at": supervisor.utc_now(),
            "reopened_by": "Codex2",
            "reason": "process-level changes requested",
        }
        status = supervisor.load_status(self.config)
        status["tasks"][0][supervisor.REVIEW_REQUEUE_INTENT_KEY] = pending
        supervisor.write_status(self.config, status, source="process-e2e-reopen")
        runtime_state.save_runtime_state(self.config, self.state)

        # Crash after reading the committed intent but before queue reservation.
        self._child(
            "import json,os,sys; from pathlib import Path; import supervisor; "
            "c=json.loads(Path(sys.argv[1]).read_text()); "
            "s=supervisor.load_status(c); "
            "assert s['tasks'][0]['review_requeue_intent']['status']=='pending'; "
            "os._exit(71)",
            expected_returncode=71,
        )
        self.assertEqual(supervisor.queue_events(runtime_state.load_runtime_state(self.config)), [])

        # A fresh process reserves the exact intent, persists runtime truth, and
        # dies before acknowledging the canonical TaskStore row.
        self._child(
            "import json,os,sys; from pathlib import Path; import supervisor,runtime_state; "
            "c=json.loads(Path(sys.argv[1]).read_text()); "
            "s=runtime_state.load_runtime_state(c); st=supervisor.load_status(c); "
            "p=supervisor.build_dispatch_plan(c,s,st,supervisor.queue_events(s),live_total=0); "
            "assert len(p['events'])==1; assert supervisor.reserve_dispatch_plan(c,s,p); "
            "runtime_state.save_runtime_state(c,s); os._exit(72)",
            expected_returncode=72,
        )
        after_reserve = runtime_state.load_runtime_state(self.config)
        self.assertEqual(len(supervisor.queue_events(after_reserve)), 1)
        self.assertEqual(
            supervisor.load_status(self.config)["tasks"][0][
                supervisor.REVIEW_REQUEUE_INTENT_KEY
            ]["status"],
            "pending",
        )

        # Another fresh interpreter performs restart recovery and canonical ack.
        self._child(
            "import json,sys; from pathlib import Path; import supervisor; "
            "c=json.loads(Path(sys.argv[1]).read_text()); "
            "assert supervisor.reconcile_review_requeue_materializations(c)",
            expected_returncode=0,
        )
        final_status = supervisor.load_status(self.config)
        final_intent = final_status["tasks"][0][supervisor.REVIEW_REQUEUE_INTENT_KEY]
        final_state = runtime_state.load_runtime_state(self.config)
        self.assertEqual(final_intent["status"], "materialized")
        self.assertEqual(len(supervisor.queue_events(final_state)), 1)

        # Replaying recovery in yet another process is a no-op and cannot
        # duplicate either the durable queue row or canonical acknowledgement.
        self._child(
            "import json,sys; from pathlib import Path; import supervisor; "
            "c=json.loads(Path(sys.argv[1]).read_text()); "
            "assert not supervisor.reconcile_review_requeue_materializations(c)",
            expected_returncode=0,
        )
        self.assertEqual(
            len(supervisor.queue_events(runtime_state.load_runtime_state(self.config))),
            1,
        )

    def test_human_ops_cli_reopen_reaches_one_worker_through_fresh_scheduler(self) -> None:
        """Exercise the real CLI producer and fresh-process queue consumer."""

        review_task = task_fixture(status="review")
        review_task.update(
            {
                "title": "Process-level reopen",
                "phase": "Supervisor recovery E2E",
                "summary_zh": "驗證 CLI reopen 與 supervisor consumer。",
                "artifacts": [],
                "acceptance": [],
                "next": "Await Human/Ops reopen",
            }
        )
        review_status = {
            "sprint": "process-e2e",
            "objective": "Prove the complete reopen queue path.",
            "canonical_files": [],
            "updated_at": supervisor.utc_now(),
            "agents": [
                {
                    "name": name,
                    "capability_lane": [],
                    "status": "idle",
                    "current_task_ids": [],
                    "branch": "",
                    "next": "",
                    "last_update": None,
                }
                for name in ("Codex", "Codex2")
            ],
            "tasks": [review_task],
            "blockers": [],
            "handoffs": [],
            "workload": {},
            "workload_summary": {},
        }
        supervisor.rewrite_task_state_store.append_state_commit(
            self.config["task_state_store"]["event_log"],
            review_status,
            source="process-e2e-review",
        )
        Path(self.config["paths"]["status_file"]).write_text(
            json.dumps(review_status, sort_keys=True) + "\n", encoding="utf-8"
        )
        runtime_state.save_runtime_state(self.config, self.state)

        command = subprocess.run(
            [
                str(self.command_root / "scripts" / "human-ops-status.sh"),
                "reopen",
                "TASK-1",
                "process-level vertical reopen",
            ],
            cwd=self.command_root,
            env=self._status_command_environment(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            command.returncode,
            0,
            msg=f"stdout={command.stdout}\nstderr={command.stderr}",
        )
        reopened = supervisor.load_status(self.config)["tasks"][0]
        self.assertEqual(reopened["status"], "in_progress")
        self.assertEqual(reopened[supervisor.REVIEW_REQUEUE_INTENT_KEY]["status"], "pending")

        self._child(
            "import json,sys; from pathlib import Path; import supervisor,runtime_state; "
            "from adapters import ADAPTERS; from adapters.base import DeliveryRequest; "
            "from test_supervisor_recovery_process_e2e import ProcessE2EAdapter; "
            "c=json.loads(Path(sys.argv[1]).read_text()); ADAPTERS[ProcessE2EAdapter.name]=ProcessE2EAdapter; "
            "s=runtime_state.load_runtime_state(c); st=supervisor.load_status(c); "
            "p=supervisor.build_dispatch_plan(c,s,st,supervisor.queue_events(s),live_total=0); "
            "assert len(p['events'])==1; assert supervisor.reserve_dispatch_plan(c,s,p); runtime_state.save_runtime_state(c,s); "
            "assert supervisor.reconcile_review_requeue_materializations(c); "
            "e=supervisor.queue_events(s)[0]; t=supervisor.load_status(c)['tasks'][0]; "
            "r=DeliveryRequest(agent_id='codex',provider='codex',delivery_mode=ProcessE2EAdapter.name,message='vertical reopen',task_id='TASK-1',reason=e['reason'],metadata={'task_generation':t['generation'],'workspace_path':str(Path(sys.argv[1]).parent),'status_root':str(Path(sys.argv[1]).parents[1])}); "
            "started,run_id,_=supervisor.start_worker_for_request(c,s,r,dispatch_event=e,queue_event_id=e['event_id'],attempt_count=1,event_id_for_log=e['event_id']); "
            "assert started and run_id",
            expected_returncode=0,
        )
        delivered = runtime_state.load_runtime_state(self.config)
        self.assertEqual(len(delivered["workers"]), 1)
        worker = next(iter(delivered["workers"].values()))
        worker_pid = int(worker["pid"])
        self.addCleanup(self._kill_process_group, worker_pid)
        self.assertTrue(supervisor.pid_is_alive(worker_pid))
        materialized = supervisor.load_status(self.config)["tasks"][0][
            supervisor.REVIEW_REQUEUE_INTENT_KEY
        ]
        self.assertEqual(materialized["status"], "materialized")

        self._child(
            "import json,sys; from pathlib import Path; import supervisor,runtime_state; "
            "c=json.loads(Path(sys.argv[1]).read_text()); s=runtime_state.load_runtime_state(c); st=supervisor.load_status(c); "
            "p=supervisor.build_dispatch_plan(c,s,st,supervisor.queue_events(s),live_total=1); "
            "assert p['events']==[]; assert not supervisor.reconcile_review_requeue_materializations(c)",
            expected_returncode=0,
        )
        replayed = runtime_state.load_runtime_state(self.config)
        self.assertEqual(len(replayed["workers"]), 1)

    def test_spawn_before_receipt_crash_adopts_exact_process_once(self) -> None:
        """Kill the supervisor inside deliver(), before a receipt can exist."""

        adapter_name = "crash_after_spawn_process_e2e"
        for agent in self.config["agents"].values():
            agent["adapter"] = adapter_name
        self.config_path.write_text(
            json.dumps(self.config, sort_keys=True) + "\n", encoding="utf-8"
        )
        event = supervisor.build_dispatch_event(
            self.task,
            "Codex",
            supervisor.REASON_OWNED_IN_PROGRESS,
            {"TASK-1": self.task},
            config=self.config,
        )
        event.update(
            {
                "event_id": "evt-spawn-crash",
                "event_key": event["key"],
                "target_agent": "codex",
                "target_display_name": "Codex",
                "delivery_endpoint_id": "codex",
                "provider": "codex",
                "message": "spawn boundary crash",
                "created_at": supervisor.utc_now(),
            }
        )
        runtime_state.store_queue_event(self.state, event)
        runtime_state.save_runtime_state(self.config, self.state)

        # The recovery scanner deliberately identifies the real wrapper by
        # cmdline plus the immutable ORCH_* environment. This harmless test
        # wrapper sleeps and never invokes a provider CLI.
        wrapper = self.config_path.parent / "worker_runner.py"
        wrapper.write_text("import time\ntime.sleep(300)\n", encoding="utf-8")
        pid_path = self.config_path.parent / "spawn-crash-worker.pid"

        self._child(
            "import json,os,subprocess,sys; from pathlib import Path; "
            "import supervisor,runtime_state; from adapters import ADAPTERS; "
            "from adapters.base import BaseAdapter,DeliveryCapability,DeliveryResult,DeliveryRequest; "
            "cfg=Path(sys.argv[1]); wrapper=cfg.parent/'worker_runner.py'; pidfile=cfg.parent/'spawn-crash-worker.pid'; "
            "exec('class CrashAdapter(BaseAdapter):\\n"
            " name=\\\"crash_after_spawn_process_e2e\\\"\\n"
            " def capability(self,agent_id): return DeliveryCapability(adapter=self.name,supported=True,requires_manual_confirmation=False,can_auto_deliver=True,can_auto_approve_edits=True,delivery_mode=self.name,verified=\\\"test\\\")\\n"
            " def deliver(self,request):\\n"
            "  env=os.environ.copy(); env.update({\\\"ORCH_TASK_ID\\\":str(request.task_id),\\\"ORCH_AGENT_ID\\\":str(request.agent_id),\\\"ORCH_PROVIDER\\\":str(request.provider),\\\"ORCH_RUN_ID\\\":\\\"run-spawn-crash\\\"}); p=subprocess.Popen([sys.executable,str(wrapper)],env=env,start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); pidfile.write_text(str(p.pid)); os._exit(73)'); "
            "ADAPTERS[CrashAdapter.name]=CrashAdapter; c=json.loads(cfg.read_text()); s=runtime_state.load_runtime_state(c); e=supervisor.queue_events(s)[0]; "
            "r=DeliveryRequest(agent_id='codex',provider='codex',delivery_mode=CrashAdapter.name,message='spawn crash',task_id='TASK-1',reason=e['reason'],metadata={'task_generation':1,'workspace_path':str(Path(sys.argv[1]).parent),'status_root':str(Path(sys.argv[1]).parents[1])}); "
            "supervisor._run_reserved_runtime_phase(c,'process_queue',lambda scratch: supervisor.start_worker_for_request(c,scratch,r,dispatch_event=e,queue_event_id=e['event_id'],attempt_count=1,event_id_for_log=e['event_id'])[0])",
            expected_returncode=73,
        )
        worker_pid = int(pid_path.read_text(encoding="utf-8"))
        self.addCleanup(self._kill_process_group, worker_pid)
        self.assertTrue(supervisor.pid_is_alive(worker_pid))
        crashed_state = runtime_state.load_runtime_state(self.config)
        reservation = crashed_state["supervisor"]["runtime_phase_reservations"][
            "process_queue"
        ]
        self.assertIn("launch_intent", reservation)
        self.assertNotIn("launch_receipt", reservation)
        self.assertEqual(crashed_state["workers"], {})

        self._child(
            "import json,sys; from pathlib import Path; import supervisor; "
            "c=json.loads(Path(sys.argv[1]).read_text()); "
            "assert supervisor._recover_runtime_phase_reservation(c,'process_queue') is True",
            expected_returncode=0,
        )
        recovered = runtime_state.load_runtime_state(self.config)
        self.assertNotIn(
            "process_queue",
            recovered.get("supervisor", {}).get("runtime_phase_reservations", {}),
        )
        self.assertEqual(set(recovered["workers"]), {"run-spawn-crash"})
        worker = recovered["workers"]["run-spawn-crash"]
        self.assertEqual(worker["pid"], worker_pid)
        self.assertEqual(worker["status"], "running")
        self.assertEqual(
            recovered["queue"]["events"]["evt-spawn-crash"]["run_id"],
            "run-spawn-crash",
        )

        # A further restart has no reservation to replay and cannot create a
        # second generation or queue lease.
        self._child(
            "import json,sys; from pathlib import Path; import supervisor; "
            "c=json.loads(Path(sys.argv[1]).read_text()); "
            "assert supervisor._recover_runtime_phase_reservation(c,'process_queue') is None",
            expected_returncode=0,
        )
        replayed = runtime_state.load_runtime_state(self.config)
        self.assertEqual(set(replayed["workers"]), {"run-spawn-crash"})

    def test_exact_command_runtime_entrypoint_reports_exact_live_identity(self) -> None:
        """Run the real entrypoint and verify its complete process identity."""

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.command_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        executable = str(Path(sys.executable).resolve())
        supervisor_command = [
            executable,
            "-u",
            "-B",
            str(self.command_root / ".orchestrator" / "supervisor.py"),
            "--config",
            str(self.config_path),
            "--quiet",
        ]
        self.config["ready_dispatcher"]["enabled"] = False
        for agent in self.config["agents"].values():
            agent["adapter"] = "codex"
        self.config["supervisor"] = {"poll_interval_seconds": 5}
        self.config["watchdog"] = {"supervisor_command": supervisor_command}
        self.config_path.write_text(
            json.dumps(self.config, sort_keys=True) + "\n", encoding="utf-8"
        )
        # Keep the canonical task fixture intact and disable dispatch.  The
        # entrypoint test must not evade TaskStore's nonterminal-drop gate just
        # to create an idle scheduler.
        runtime_state.save_runtime_state(self.config, runtime_state.default_state())

        environment = os.environ.copy()
        environment.update(
            {
                "PANTHEON_COMMAND_ROOT": str(self.command_root),
                "PANTHEON_COMMAND_RUNTIME_SHA": head,
                "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
                "PANTHEON_COMMAND_BASE_REF": "origin/dev",
                "PANTHEON_STATUS_ROOT": str(self.status_root),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        process = subprocess.Popen(
            supervisor_command,
            cwd=self.command_root,
            env=environment,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.processes.append(process)

        report: dict[str, object] | None = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = (process.stdout.read() if process.stdout else b"").decode(
                    "utf-8", errors="replace"
                )
                self.fail(
                    f"exact supervisor entrypoint exited {process.returncode}: {output}"
                )
            candidate = supervisor_runtime_health.evaluate_runtime_health(
                self.command_root,
                config_path_arg=self.config_path,
                max_heartbeat_age=30,
                max_cycle_elapsed=30,
                expected_command_root=self.command_root,
                expected_source_commit=head,
            )
            if (
                candidate["dimensions"]["identity"]["healthy"]
                and candidate["dimensions"]["liveness"]["healthy"]
            ):
                report = candidate
                break
            time.sleep(0.1)

        self.assertIsNotNone(report, "exact runtime identity never became healthy")
        assert report is not None
        self.assertEqual(report["identity"]["command_root"], str(self.command_root))
        self.assertEqual(report["identity"]["source_commit"], head)
        self.assertEqual(report["supervisor"]["pid"], process.pid)
        self.assertTrue(report["supervisor"]["lock_held"])

        # Exercise the legacy mutable-checkout launcher while the exact owner
        # holds the canonical lock. It must fail at entry instead of relying
        # on the incumbent lock to hide a second launch path.
        legacy = subprocess.run(
            [
                str(REPOSITORY_ROOT / "scripts" / "run-supervisor.sh"),
                "--config",
                str(self.config_path),
                "--quiet",
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(legacy.returncode, 0, legacy.stdout + legacy.stderr)
        self.assertIn("promoted live config is owned", legacy.stderr)
        self.assertIsNone(process.poll())
        pid_path = Path(self.config["paths"]["state_file"]).parent / "supervisor.pid"
        self.assertEqual(int(pid_path.read_text(encoding="utf-8").strip()), process.pid)

        # The same refusal must hold when no exact owner exists. It occurs
        # before singleton/PID/runtime-state mutation, so absence of an
        # incumbent can never promote the mutable checkout by accident.
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        pid_path.unlink(missing_ok=True)
        state_path = Path(self.config["paths"]["state_file"])
        state_before = state_path.read_bytes()
        legacy_without_owner = subprocess.run(
            [
                str(REPOSITORY_ROOT / "scripts" / "run-supervisor.sh"),
                "--config",
                str(self.config_path),
                "--quiet",
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(
            legacy_without_owner.returncode,
            0,
            legacy_without_owner.stdout + legacy_without_owner.stderr,
        )
        self.assertIn("promoted live config is owned", legacy_without_owner.stderr)
        self.assertFalse(pid_path.exists())
        self.assertEqual(state_path.read_bytes(), state_before)


if __name__ == "__main__":
    unittest.main()
