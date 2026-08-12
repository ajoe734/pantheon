#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.ai_status import status_worker_process_generation_id


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".orchestrator"))

from rewrite.task_state_store import append_state_commit


class StatusCommandRuntimePinTests(unittest.TestCase):
    def _init_repo(self, path: Path, *, remote: str = "https://github.com/ajoe734/pantheon.git") -> str:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=path, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=path, check=True)
        (path / ".gitkeep").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=path, check=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/dev", "HEAD"], cwd=path, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()

    def _commit_all(self, repo: Path, message: str = "commit") -> str:
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/dev", "HEAD"], cwd=repo, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    def _copy_wrapper(self, worktree: Path) -> None:
        target = worktree / "scripts" / "ai-status.sh"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / "scripts" / "ai-status.sh", target)

    def _copy_full_status_tooling(self, destination: Path) -> None:
        for rel in (
            "scripts/ai_status.py",
            "scripts/ai-status.sh",
            "scripts/loop_done_guardrail.py",
            ".orchestrator/common.py",
            ".orchestrator/canonical_mutation_assertion.py",
            ".orchestrator/runtime_state.py",
            ".orchestrator/task_archive.py",
            ".orchestrator/multi_repo_registry.py",
            ".orchestrator/rewrite/__init__.py",
            ".orchestrator/rewrite/task_machine.py",
            ".orchestrator/rewrite/task_state_store.py",
            ".orchestrator/config.json",
        ):
            source = REPO_ROOT / rel
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def _iso(self, delta_seconds: int = 3600) -> str:
        return (
            datetime.now(timezone.utc)
            + timedelta(seconds=delta_seconds)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _write_status_state(self, status_root: Path, *, task_id: str = "RUNTIME-PIN-001") -> None:
        state = {
            "project": "pantheon",
            "sprint": "runtime-pin-test",
            "objective": "Synthetic runtime pin status command fixture.",
            "agents": [],
            "tasks": [
                {
                    "id": task_id,
                    "generation": 1,
                    "title": "Runtime pin fixture",
                    "owner": "Codex2",
                    "reviewer": "Claude",
                    "status": "in_progress",
                    "next": "fixture",
                    "last_update": "2026-07-17T00:00:00Z",
                }
            ],
            "handoffs": [],
            "blockers": [],
        }
        (status_root / "ai-status.json").write_text(
            json.dumps(state, indent=2) + "\n",
            encoding="utf-8",
        )
        append_state_commit(
            self._task_state_event_log(status_root),
            state,
            source="test-fixture",
        )
        (status_root / "ai-activity-log.jsonl").write_text("", encoding="utf-8")

    @staticmethod
    def _task_state_event_log(status_root: Path) -> Path:
        return (
            status_root.parent
            / "runtime"
            / f"{status_root.name}-task-state-events-v2.jsonl"
        )

    def _runtime_record(
        self,
        *,
        run_id: str,
        task_id: str,
        worktree: Path,
        status_root: Path,
        command_root: Path,
        command_sha: str,
        workspace_task_id: str | None = None,
        status: str = "running",
        expires_delta_seconds: int = 3600,
        issued_sha: str | None = None,
    ) -> tuple[str, dict[str, object]]:
        workspace_task_id = workspace_task_id or task_id
        runtime = {
            "command_root": str(command_root),
            "source_sha": issued_sha or command_sha,
            "remote": "ajoe734/pantheon",
            "base_ref": "origin/dev",
        }
        pid = 4242
        pid_start_ticks = 987654
        queue_event_id = f"evt-{run_id}"
        worker = {
            "run_id": run_id,
            "provider": "codex",
            "agent_id": "codex2",
            "task_id": task_id,
            "task_generation": 1,
            "status": status,
            "lease_acquired_at": self._iso(-10),
            "lease_expires_at": self._iso(expires_delta_seconds),
            "queue_event_id": queue_event_id,
            "pid": pid,
            "pid_start_ticks": pid_start_ticks,
            "process_generation": status_worker_process_generation_id(
                task_id=task_id,
                worker_run_id=run_id,
                queue_event_id=queue_event_id,
                pid=pid,
                pid_start_ticks=pid_start_ticks,
            ),
            "workspace_path": str(worktree),
            "status_root": str(status_root),
            "status_command_runtime": runtime,
            "request_snapshot": {
                "metadata": {
                    "task_generation": 1,
                    "workspace_task_id": workspace_task_id,
                    "workspace_path": str(worktree),
                    "status_root": str(status_root),
                    "status_command_runtime": runtime,
                }
            },
        }
        lease = {
            "task_id": task_id,
            "workspace_task_id": workspace_task_id,
            "branch": f"task/{task_id}",
            "path": str(worktree),
            "status_root": str(status_root),
            "last_queue_event_id": f"evt-{run_id}",
            "last_target_agent": "Codex2",
            "last_used_at": self._iso(0),
        }
        return workspace_task_id, {"worker": worker, "lease": lease}

    def _write_runtime_state(
        self,
        status_root: Path,
        records: list[tuple[str, dict[str, object]]],
    ) -> None:
        workers: dict[str, object] = {}
        leases: dict[str, object] = {}
        for workspace_task_id, record in records:
            worker = record["worker"]
            assert isinstance(worker, dict)
            workers[str(worker["run_id"])] = worker
            lease = record.get("lease")
            if isinstance(lease, dict):
                leases[workspace_task_id] = lease
        state = {
            "version": 2,
            "workers": workers,
            "worker_worktrees": {"leases": leases},
            "queue": {"events": {}},
        }
        path = status_root / ".orchestrator" / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def _run_status(
        self,
        *,
        command_root: Path,
        status_root: Path,
        worktree: Path,
        command_sha: str,
        run_id: str,
        task_id: str,
        args: list[str],
    ) -> subprocess.CompletedProcess[str]:
        env = self._base_env(
            status_root=status_root,
            worktree=worktree,
            command_root=command_root,
            command_sha=command_sha,
        )
        env["ORCH_RUN_ID"] = run_id
        env["ORCH_TASK_ID"] = task_id
        env["ORCH_RUNNER_STATUS_PATH"] = str(
            status_root / ".orchestrator" / "worker-runtime" / "status" / f"{run_id}.json"
        )
        env["ORCH_HEARTBEAT_PATH"] = str(
            status_root / ".orchestrator" / "worker-runtime" / "heartbeats" / f"{run_id}.json"
        )
        return subprocess.run(
            ["bash", str(command_root / "scripts" / "ai-status.sh"), *args],
            cwd=worktree,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def _write_stub_command(self, command_root: Path) -> str:
        wrapper = command_root / "scripts" / "ai-status.sh"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / "scripts" / "ai-status.sh", wrapper)
        script = command_root / "scripts" / "ai_status.py"
        script.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import json, os, sys",
                    "print(json.dumps({",
                    "  'marker': 'installed-command-runtime',",
                    "  'argv': sys.argv[1:],",
                    "  'cwd': os.getcwd(),",
                    "  'status_root': os.environ.get('PANTHEON_STATUS_ROOT'),",
                    "  'delivery_root': os.environ.get('PANTHEON_WORKTREE_ROOT'),",
                    "  'wrapper_root': os.environ.get('PANTHEON_STATUS_COMMAND_WRAPPER_ROOT'),",
                    "}, sort_keys=True))",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return self._commit_all(command_root, "install command runtime")

    def _base_env(self, *, status_root: Path, worktree: Path, command_root: Path, command_sha: str) -> dict[str, str]:
        env = os.environ.copy()
        # Clean inherited runtime bindings so synthetic roots do not read or
        # write the live authoritative journal of the auto worker running this
        # suite.
        for key in list(env.keys()):
            if key.startswith("PANTHEON_STATUS_COMMAND_") or key.startswith(
                "PANTHEON_TASK_STATE_"
            ):
                env.pop(key)
        env.update(
            {
                "AI_NAME": "Codex2",
                "PANTHEON_STATUS_ROOT": str(status_root),
                "PANTHEON_WORKTREE_ROOT": str(worktree),
                "ORCH_WORKSPACE_PATH": str(worktree),
                "ORCH_RUN_ID": "codex-runtime-pin-test",
                "ORCH_RUNNER_STATUS_PATH": str(status_root / ".orchestrator" / "worker-runtime" / "status" / "run.json"),
                "ORCH_HEARTBEAT_PATH": str(status_root / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"),
                "PANTHEON_COMMAND_ROOT": str(command_root),
                "PANTHEON_COMMAND_RUNTIME_SHA": command_sha,
                "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
                "PANTHEON_COMMAND_BASE_REF": "origin/dev",
                "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
                "PANTHEON_TASK_STATE_EVENT_LOG": str(
                    self._task_state_event_log(status_root)
                ),
            }
        )
        return env

    def test_stale_worktree_wrapper_execs_installed_command_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="status-command-pin-") as tmpdir:
            root = Path(tmpdir)
            status_root = root / "status-root"
            command_root = root / "command-root"
            worktree = root / "task-worktree"
            self._init_repo(status_root)
            self._init_repo(command_root)
            self._init_repo(worktree)
            self._copy_wrapper(worktree)
            (worktree / "scripts" / "ai_status.py").write_text(
                "raise SystemExit('stale local ai_status.py executed')\n",
                encoding="utf-8",
            )
            command_sha = self._write_stub_command(command_root)
            env = self._base_env(
                status_root=status_root,
                worktree=worktree,
                command_root=command_root,
                command_sha=command_sha,
            )
            proc = subprocess.run(
                ["bash", str(command_root / "scripts" / "ai-status.sh"), "show", "TASK-1"],
                cwd=worktree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["marker"], "installed-command-runtime")
        self.assertEqual(payload["argv"], ["show", "TASK-1"])
        self.assertEqual(payload["delivery_root"], str(worktree))
        self.assertEqual(payload["status_root"], str(status_root))
        self.assertEqual(payload["wrapper_root"], str(command_root))

    def test_authoritative_wrapper_requires_absolute_journal_binding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="status-command-authoritative-") as tmpdir:
            root = Path(tmpdir)
            status_root = root / "status-root"
            command_root = root / "command-root"
            worktree = root / "task-worktree"
            self._init_repo(status_root)
            self._init_repo(command_root)
            self._init_repo(worktree)
            config_path = command_root / ".orchestrator" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "task_state_store": {
                            "mode": "authoritative",
                            "event_log": ".orchestrator/task-state-events.jsonl",
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            command_sha = self._write_stub_command(command_root)
            env = self._base_env(
                status_root=status_root,
                worktree=worktree,
                command_root=command_root,
                command_sha=command_sha,
            )
            env.pop("PANTHEON_TASK_STATE_STORE_MODE")
            env.pop("PANTHEON_TASK_STATE_EVENT_LOG")

            rejected = subprocess.run(
                ["bash", str(command_root / "scripts" / "ai-status.sh"), "show", "TASK-1"],
                cwd=worktree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            env["PANTHEON_TASK_STATE_STORE_MODE"] = "authoritative"
            env["PANTHEON_TASK_STATE_EVENT_LOG"] = str(root / "runtime" / "events.jsonl")
            accepted = subprocess.run(
                ["bash", str(command_root / "scripts" / "ai-status.sh"), "show", "TASK-1"],
                cwd=worktree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("PANTHEON_TASK_STATE_STORE_MODE=authoritative", rejected.stderr)
        self.assertEqual(accepted.returncode, 0, accepted.stderr + accepted.stdout)

    def test_wrapper_rejects_invalid_command_root_before_local_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="status-command-pin-invalid-") as tmpdir:
            root = Path(tmpdir)
            status_root = root / "status-root"
            command_root = root / "command-root"
            worktree = root / "task-worktree"
            wrong_remote = root / "wrong-remote"
            unmerged = root / "unmerged"
            nested_repo = root / "nested-repo"
            self._init_repo(status_root)
            self._init_repo(command_root)
            self._init_repo(worktree)
            self._init_repo(wrong_remote, remote="https://github.com/example/not-pantheon.git")
            self._init_repo(unmerged)
            nested_sha = self._init_repo(nested_repo)
            nested_subdir = nested_repo / "subdir"
            nested_subdir.mkdir()
            command_link = root / "command-link"
            command_link.symlink_to(command_root, target_is_directory=True)
            self._copy_wrapper(worktree)
            local_sentinel = worktree / "local-sentinel.txt"
            local_sentinel.write_text("unchanged\n", encoding="utf-8")
            (worktree / "scripts" / "ai_status.py").write_text(
                "from pathlib import Path\nPath('local-sentinel.txt').write_text('mutated\\n')\n",
                encoding="utf-8",
            )
            command_sha = self._write_stub_command(command_root)
            wrong_sha = self._write_stub_command(wrong_remote)
            old_unmerged_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=unmerged, text=True).strip()
            (unmerged / "later.txt").write_text("not in origin dev\n", encoding="utf-8")
            subprocess.run(["git", "add", "later.txt"], cwd=unmerged, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "unmerged"], cwd=unmerged, check=True)
            unmerged_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=unmerged, text=True).strip()
            subprocess.run(["git", "update-ref", "refs/remotes/origin/dev", old_unmerged_sha], cwd=unmerged, check=True)

            base_env = self._base_env(
                status_root=status_root,
                worktree=worktree,
                command_root=command_root,
                command_sha=command_sha,
            )
            cases = [
                ({"PANTHEON_COMMAND_ROOT": ""}, "PANTHEON_COMMAND_ROOT is required"),
                ({"PANTHEON_COMMAND_ROOT": "relative"}, "must be an absolute path"),
                ({"PANTHEON_COMMAND_ROOT": str(command_link)}, "symlink component"),
                (
                    {
                        "PANTHEON_COMMAND_ROOT": str(nested_subdir),
                        "PANTHEON_COMMAND_RUNTIME_SHA": nested_sha,
                    },
                    "must be a git repository root",
                ),
                ({"PANTHEON_COMMAND_RUNTIME_SHA": "0" * 40}, "PANTHEON_COMMAND_RUNTIME_SHA mismatch"),
                (
                    {
                        "PANTHEON_COMMAND_ROOT": str(wrong_remote),
                        "PANTHEON_COMMAND_RUNTIME_SHA": wrong_sha,
                    },
                    "remote mismatch",
                ),
                (
                    {
                        "PANTHEON_COMMAND_ROOT": str(unmerged),
                        "PANTHEON_COMMAND_RUNTIME_SHA": unmerged_sha,
                    },
                    "is not merged into origin/dev",
                ),
            ]
            for env_update, expected in cases:
                with self.subTest(expected=expected):
                    env = dict(base_env)
                    env.update(env_update)
                    proc = subprocess.run(
                        ["bash", str(worktree / "scripts" / "ai-status.sh"), "show", "TASK-1"],
                        cwd=worktree,
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertIn(expected, proc.stderr + proc.stdout)
                    self.assertEqual(local_sentinel.read_text(encoding="utf-8"), "unchanged\n")

    def test_direct_python_from_stale_worktree_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="status-command-direct-") as tmpdir:
            root = Path(tmpdir)
            status_root = root / "status-root"
            command_root = root / "command-root"
            worktree = root / "task-worktree"
            self._init_repo(status_root)
            self._init_repo(command_root)
            self._init_repo(worktree)
            command_sha = self._write_stub_command(command_root)
            self._copy_full_status_tooling(worktree)
            env = self._base_env(
                status_root=status_root,
                worktree=worktree,
                command_root=command_root,
                command_sha=command_sha,
            )

            proc = subprocess.run(
                [sys.executable, str(worktree / "scripts" / "ai_status.py"), "show", "TASK-1"],
                cwd=worktree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("must execute the installed command runtime", proc.stderr + proc.stdout)

    def test_supervisor_and_worker_status_entrypoints_leave_command_root_bytecode_free(self) -> None:
        task_id = "RUNTIME-PIN-NO-BYTECODE"
        with tempfile.TemporaryDirectory(prefix="status-command-bytecode-") as tmpdir:
            command_root = Path(tmpdir) / "command-root"
            self._init_repo(command_root)
            self._copy_full_status_tooling(command_root)
            self._write_status_state(command_root, task_id=task_id)
            command_sha = self._commit_all(
                command_root,
                "install no-bytecode status command runtime",
            )
            env = self._base_env(
                status_root=command_root,
                worktree=command_root,
                command_root=command_root,
                command_sha=command_sha,
            )
            for name in (
                "ORCH_RUN_ID",
                "ORCH_RUNNER_STATUS_PATH",
                "ORCH_HEARTBEAT_PATH",
                "PANTHEON_WORKTREE_ROOT",
                "ORCH_WORKSPACE_PATH",
            ):
                env.pop(name, None)
            env["AI_NAME"] = "Human/Ops"
            env["PYTHONDONTWRITEBYTECODE"] = "1"

            supervisor_style = subprocess.run(
                [
                    sys.executable,
                    str(command_root / "scripts" / "ai_status.py"),
                    "show",
                    task_id,
                ],
                cwd=command_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            env.pop("PYTHONDONTWRITEBYTECODE")
            worker_style = subprocess.run(
                [
                    "bash",
                    str(command_root / "scripts" / "ai-status.sh"),
                    "show",
                    task_id,
                ],
                cwd=command_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            bytecode_paths = sorted(
                str(path.relative_to(command_root))
                for path in command_root.rglob("*")
                if path.name == "__pycache__" or path.suffix == ".pyc"
            )

        self.assertEqual(
            supervisor_style.returncode,
            0,
            supervisor_style.stderr + supervisor_style.stdout,
        )
        self.assertEqual(
            worker_style.returncode,
            0,
            worker_style.stderr + worker_style.stdout,
        )
        self.assertEqual(bytecode_paths, [])

    def test_active_lease_allows_mutation_and_preserves_worktree(self) -> None:
        task_id = "RUNTIME-PIN-001"
        run_id = "codex-runtime-pin-active"
        with tempfile.TemporaryDirectory(prefix="status-command-active-") as tmpdir:
            root = Path(tmpdir)
            central = root / "central"
            worktree = root / "task-worktree"
            self._init_repo(central)
            self._init_repo(worktree)
            self._copy_full_status_tooling(central)
            self._copy_wrapper(worktree)
            (worktree / "scripts" / "ai_status.py").write_text(
                "raise SystemExit('stale worktree ai_status.py executed')\n",
                encoding="utf-8",
            )
            sentinel = worktree / "sentinel.txt"
            sentinel.write_text("unchanged\n", encoding="utf-8")
            command_sha = self._commit_all(central, "install full status command runtime")
            self._write_status_state(central, task_id=task_id)
            self._write_runtime_state(
                central,
                [
                    self._runtime_record(
                        run_id=run_id,
                        task_id=task_id,
                        worktree=worktree,
                        status_root=central,
                        command_root=central,
                        command_sha=command_sha,
                    )
                ],
            )

            proc = self._run_status(
                command_root=central,
                status_root=central,
                worktree=worktree,
                command_sha=command_sha,
                run_id=run_id,
                task_id=task_id,
                args=["progress", task_id, "lease validated progress"],
            )

            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            state = json.loads((central / "ai-status.json").read_text(encoding="utf-8"))
            [task] = [item for item in state["tasks"] if item["id"] == task_id]
            self.assertEqual(task["next"], "lease validated progress")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged\n")
            events = [
                json.loads(line)
                for line in (central / "ai-activity-log.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            progress_event = [event for event in events if event.get("type") == "progress"][-1]
            self.assertEqual(progress_event["status_command"]["command_root"], str(central.resolve()))
            self.assertEqual(progress_event["status_command"]["source_sha"], command_sha)
            self.assertEqual(progress_event["status_command"]["delivery_root"], str(worktree.resolve()))

    def test_mutations_reject_invalid_active_lease_before_canonical_writes(self) -> None:
        task_id = "RUNTIME-PIN-001"
        base_run_id = "codex-runtime-pin-invalid"
        cases = [
            ("missing_worker", {}, "lease not found"),
            (
                "terminal_worker",
                {"worker_update": {"status": "paused"}},
                "is not running",
            ),
            (
                "expired_worker",
                {"record_kwargs": {"expires_delta_seconds": -60}},
                "is expired",
            ),
            (
                "wrong_task",
                {"worker_update": {"task_id": "OTHER-TASK"}},
                "task mismatch",
            ),
            (
                "wrong_workspace",
                {"worker_update": {"workspace_path": "__OTHER__"}},
                "workspace mismatch",
            ),
            (
                "wrong_issued_sha",
                {"record_kwargs": {"issued_sha": "1" * 40}},
                "runtime SHA mismatch",
            ),
            (
                "missing_worktree_lease",
                {"drop_lease": True},
                "worktree lease not found",
            ),
        ]
        for name, setup, expected in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory(prefix=f"status-command-{name}-") as tmpdir:
                    root = Path(tmpdir)
                    central = root / "central"
                    worktree = root / "task-worktree"
                    other = root / "other-worktree"
                    self._init_repo(central)
                    self._init_repo(worktree)
                    self._init_repo(other)
                    self._copy_full_status_tooling(central)
                    command_sha = self._commit_all(central, "install full status command runtime")
                    self._write_status_state(central, task_id=task_id)
                    before_status = (central / "ai-status.json").read_bytes()
                    before_log = (central / "ai-activity-log.jsonl").read_bytes()
                    run_id = f"{base_run_id}-{name}"
                    record_kwargs = dict(setup.get("record_kwargs", {}))
                    workspace_task_id, record = self._runtime_record(
                        run_id=run_id,
                        task_id=task_id,
                        worktree=worktree,
                        status_root=central,
                        command_root=central,
                        command_sha=command_sha,
                        **record_kwargs,
                    )
                    worker = record["worker"]
                    assert isinstance(worker, dict)
                    update = dict(setup.get("worker_update", {}))
                    if update.get("workspace_path") == "__OTHER__":
                        update["workspace_path"] = str(other)
                    worker.update(update)
                    records = [] if name == "missing_worker" else [(workspace_task_id, record)]
                    if setup.get("drop_lease") and records:
                        record = dict(record)
                        record["lease"] = None
                        records = [(workspace_task_id, record)]
                    self._write_runtime_state(central, records)

                    proc = self._run_status(
                        command_root=central,
                        status_root=central,
                        worktree=worktree,
                        command_sha=command_sha,
                        run_id=run_id,
                        task_id=task_id,
                        args=["progress", task_id, f"should reject {name}"],
                    )

                    self.assertNotEqual(proc.returncode, 0, proc.stderr + proc.stdout)
                    self.assertIn(expected, proc.stderr + proc.stdout)
                    self.assertEqual((central / "ai-status.json").read_bytes(), before_status)
                    self.assertEqual((central / "ai-activity-log.jsonl").read_bytes(), before_log)

    def test_concurrent_worktree_writers_keep_final_state_and_event_ids(self) -> None:
        task_id = "RUNTIME-PIN-001"
        with tempfile.TemporaryDirectory(prefix="status-command-concurrent-") as tmpdir:
            root = Path(tmpdir)
            central = root / "central"
            worktree_a = root / "task-worktree-a"
            worktree_b = root / "task-worktree-b"
            self._init_repo(central)
            self._init_repo(worktree_a)
            self._init_repo(worktree_b)
            self._copy_full_status_tooling(central)
            command_sha = self._commit_all(central, "install full status command runtime")
            self._write_status_state(central, task_id=task_id)
            self._write_runtime_state(
                central,
                [
                    self._runtime_record(
                        run_id="codex-runtime-pin-a",
                        task_id=task_id,
                        workspace_task_id=f"{task_id}-A",
                        worktree=worktree_a,
                        status_root=central,
                        command_root=central,
                        command_sha=command_sha,
                    ),
                    self._runtime_record(
                        run_id="codex-runtime-pin-b",
                        task_id=task_id,
                        workspace_task_id=f"{task_id}-B",
                        worktree=worktree_b,
                        status_root=central,
                        command_root=central,
                        command_sha=command_sha,
                    ),
                ],
            )
            env_a = self._base_env(
                status_root=central,
                worktree=worktree_a,
                command_root=central,
                command_sha=command_sha,
            )
            env_a.update(
                {
                    "ORCH_RUN_ID": "codex-runtime-pin-a",
                    "ORCH_TASK_ID": task_id,
                    "ORCH_RUNNER_STATUS_PATH": str(central / ".orchestrator" / "worker-runtime" / "status" / "a.json"),
                    "ORCH_HEARTBEAT_PATH": str(central / ".orchestrator" / "worker-runtime" / "heartbeats" / "a.json"),
                }
            )
            env_b = self._base_env(
                status_root=central,
                worktree=worktree_b,
                command_root=central,
                command_sha=command_sha,
            )
            env_b.update(
                {
                    "ORCH_RUN_ID": "codex-runtime-pin-b",
                    "ORCH_TASK_ID": task_id,
                    "ORCH_RUNNER_STATUS_PATH": str(central / ".orchestrator" / "worker-runtime" / "status" / "b.json"),
                    "ORCH_HEARTBEAT_PATH": str(central / ".orchestrator" / "worker-runtime" / "heartbeats" / "b.json"),
                }
            )
            commands = [
                (
                    ["bash", str(central / "scripts" / "ai-status.sh"), "handoff", task_id, "Claude", "ready for review"],
                    worktree_a,
                    env_a,
                ),
                (
                    ["bash", str(central / "scripts" / "ai-status.sh"), "progress", task_id, "concurrent progress"],
                    worktree_b,
                    env_b,
                ),
            ]
            procs = [
                subprocess.Popen(command, cwd=worktree, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for command, worktree, env in commands
            ]
            results = [proc.communicate(timeout=20) + (proc.returncode,) for proc in procs]

            for stdout, stderr, returncode in results:
                self.assertEqual(returncode, 0, stderr + stdout)
            state = json.loads((central / "ai-status.json").read_text(encoding="utf-8"))
            [task] = [item for item in state["tasks"] if item["id"] == task_id]
            self.assertEqual(task["status"], "review")
            self.assertIn(task["next"], {"ready for review", "concurrent progress"})
            events = [
                json.loads(line)
                for line in (central / "ai-activity-log.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            task_events = [event for event in events if event.get("task_id") == task_id]
            event_types = {event.get("type") for event in task_events}
            self.assertTrue({"handoff", "progress"}.issubset(event_types))
            event_ids = [event.get("event_id") for event in task_events]
            self.assertEqual(len(event_ids), len(set(event_ids)))


if __name__ == "__main__":
    unittest.main()
