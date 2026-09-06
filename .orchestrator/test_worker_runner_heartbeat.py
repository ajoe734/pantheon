"""Tests for worker_runner heartbeat agent/task-id derivation (observability fix)."""
import importlib.util, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

_P = os.path.join(os.path.dirname(__file__), "worker_runner.py")
_spec = importlib.util.spec_from_file_location("worker_runner", _P)
wr = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(wr)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/ajoe734/pantheon.git"], cwd=path, check=True)
    (path / ".gitkeep").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=path, check=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/dev", "HEAD"], cwd=path, check=True)


import shutil
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from common import canonical_task_state_identity_for_paths, status_command_runtime_record_from_env
from rewrite.task_state_store import append_state_commit, load_snapshot


def _run_fixture_worker(argv, *, env, timeout=20, task=None, mutate_receipt=None,
                        mutate_journal=None, during_run=None, local_stub=False,
                        publish_receipt=True, mutate_store=None, receipt_in_phase=False,
                        revoke_during_sandbox=False,
                        **_kwargs):
    """Publish an isolated supervisor receipt for one actual wrapper process.

    The local stub replaces only bubblewrap, retaining the real entry barrier,
    authoritative journal, child Popen, heartbeat and termination paths.
    """
    env = dict(env)
    central = Path(env["PANTHEON_STATUS_ROOT"])
    for key in ("PANTHEON_TASK_STATE_STORE_MODE", "PANTHEON_TASK_STATE_EVENT_LOG",
                "PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON"):
        env.pop(key, None)
    journal = central.parent / "test-task-store" / "events.jsonl"
    projected = json.loads((central / "ai-status.json").read_text())
    if task is None:
        task = deepcopy((projected.get("tasks") or [{"id": "RUNNER-FIXTURE", "owner": "Codex",
                                                   "reviewer": "Claude", "status": "in_progress"}])[0])
        task.setdefault("generation", 1)
        task.setdefault("owner", "Codex")
        task.setdefault("reviewer", "Claude")
        task.setdefault("status", "in_progress")
    state = {"tasks": [deepcopy(task)], "agents": [], "handoffs": [], "blockers": []}
    append_state_commit(journal, state, source="isolated-worker-test-fixture")
    identity = canonical_task_state_identity_for_paths(status_root=central, event_log=journal)
    env.update({"PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
                "PANTHEON_TASK_STATE_EVENT_LOG": str(journal),
                "PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON": json.dumps(identity)})
    command = argv[argv.index("--") + 1:]
    run_id = argv[argv.index("--run-id") + 1]
    heartbeat = argv[argv.index("--heartbeat-path") + 1]
    runner_status = argv[argv.index("--status-path") + 1]
    actual_argv = argv
    if local_stub:
        harness = (
            "import sys; sys.path.insert(0, sys.argv[1]); import worker_runner as wr\n"
            "def sandbox(command, **kwargs):\n"
            + ("    import os\n"
               "    from rewrite.task_state_store import load_snapshot, append_state_commit\n"
               "    journal=os.environ['PANTHEON_TASK_STATE_EVENT_LOG']\n"
               "    state=load_snapshot(journal)['state']\n"
               "    state['tasks'][0]['execution_authorization']['state']='revoked'\n"
               "    append_state_commit(journal,state,source='isolated-pre-Popen-revocation')\n"
               if revoke_during_sandbox else "")
            + "    return command\n"
            "wr.bind_worker_sandbox=sandbox\n"
            "sys.exit(wr.main(sys.argv[2:]))"
        )
        actual_argv = [sys.executable, "-c", harness, str(Path(_P).resolve().parent), *argv[2:]]
    proc = subprocess.Popen(actual_argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stat_payload = Path(f"/proc/{proc.pid}/stat").read_text()
        ticks = int(stat_payload[stat_payload.rfind(")") + 2:].split()[19])
        worker = {
            "run_id": run_id, "task_id": task["id"], "queue_event_id": "fixture-dispatch",
            "pid": proc.pid, "pid_start_ticks": ticks,
            "process_generation": wr.worker_process_generation_id(
                task_id=task["id"], worker_run_id=run_id, queue_event_id="fixture-dispatch",
                pid=proc.pid, pid_start_ticks=ticks),
            "status": "running", "lease_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
            "command": command, "workspace_path": env.get("PANTHEON_WORKTREE_ROOT"),
            "workspace_source_root": str(central.parent / "shared-pantheon")
            if (central.parent / "shared-pantheon").exists() else str(central),
            "heartbeat_path": heartbeat, "runner_status_path": runner_status,
            "status_command_runtime": status_command_runtime_record_from_env(env),
            "task_state_identity": identity,
            "task_generation": wr.canonical_task_generation(task), "agent_id": task["owner"].lower(),
            "logical_agent_id": task["owner"].lower(),
            "request_snapshot": {"task_id": task["id"], "task_generation": wr.canonical_task_generation(task),
                                 "agent_id": task["owner"].lower(), "reason": "owned_in_progress_dispatch",
                                 "metadata": {"execution_authorization_run_id": "fixture-attempt-1"}},
        }
        if mutate_receipt:
            mutate_receipt(worker)
        if mutate_journal:
            mutate_journal(state)
            append_state_commit(journal, state, source="isolated-authoritative-mutation")
        if mutate_store:
            mutate_store(journal)
        if publish_receipt:
            runtime_state = ({"supervisor": {"runtime_phase_reservations": {
                "delivery": {"launch_receipt": {"worker": worker}}}}}
                if receipt_in_phase else {"workers": {run_id: worker}})
            wr.write_json(central / ".orchestrator" / "state.json", runtime_state)
        if during_run:
            during_run(proc, journal, state)
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=5)


_CLEANUP_BINDING = {"agent": "codex", "task_id": "CLEANUP-FIXTURE", "role": "owner",
                    "owner": "Codex", "reviewer": "Claude", "authorization_run_id": "",
                    "read_only_worktree": False, "source_readonly_roots": []}

def _probe_bwrap() -> str | None:
    bwrap_path = shutil.which("bwrap")
    if not bwrap_path:
        return None
    try:
        res = subprocess.run(
            [bwrap_path, "--die-with-parent", "--unshare-pid", "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev", "--proc", "/proc", "--", "/bin/true"],
            capture_output=True,
            check=False,
            timeout=5,
        )
        return bwrap_path if res.returncode == 0 else None
    except Exception:
        return None

_FUNCTIONAL_BWRAP = _probe_bwrap()


def _command_runtime_env(root: Path) -> dict[str, str]:
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    return {
        "PANTHEON_COMMAND_ROOT": str(root),
        "PANTHEON_COMMAND_RUNTIME_SHA": sha,
        "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
        "PANTHEON_COMMAND_BASE_REF": "origin/dev",
    }


def _write_status(path: Path) -> None:
    (path / "ai-status.json").write_text(
        json.dumps({"tasks": [], "agents": [], "handoffs": [], "blockers": []}) + "\n",
        encoding="utf-8",
    )
    (path / ".orchestrator").mkdir(parents=True, exist_ok=True)
    (path / ".orchestrator" / "state.json").write_text("{}", encoding="utf-8")
    (path / ".orchestrator" / "approval-queue.json").write_text("[]", encoding="utf-8")
    (path / ".orchestrator" / "config.json").write_text("{}", encoding="utf-8")


class TestDeriveAgent(unittest.TestCase):
    def test_claude_slot(self):
        self.assertEqual(wr.derive_agent("claude-1-20260615T143930Z-43181d1a"), "claude-1")
    def test_claude2_slot(self):
        self.assertEqual(wr.derive_agent("claude2-1-20260615T143931Z-7a1dc2d9"), "claude2-1")
    def test_codex(self):
        self.assertEqual(wr.derive_agent("codex-20260615T141726Z-f8f71327"), "codex")
    def test_empty(self):
        self.assertEqual(wr.derive_agent(""), "")


class TestDeriveTaskId(unittest.TestCase):
    def test_parses_task_id(self):
        cmd = ["claude", "-p", "...prompt... Task ID: CONSOLE-DATA-EVOLUTION 原因: owned_ready_dispatch"]
        self.assertEqual(wr.derive_task_id(cmd), "CONSOLE-DATA-EVOLUTION")
    def test_none_when_absent(self):
        self.assertIsNone(wr.derive_task_id(["claude", "-p", "no task here"]))


class TestCoordinationRootValidation(unittest.TestCase):
    def test_requires_status_root_when_workspace_isolated(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "PANTHEON_STATUS_ROOT is required"):
                wr.validate_coordination_root(Path("/tmp/task-worktree"))

    def test_rejects_relative_status_root(self):
        with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": "relative-root"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "absolute path"):
                wr.validate_coordination_root(Path("/tmp/task-worktree"))

    def test_rejects_symlink_status_root(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-status-root-") as temp_dir:
            root = Path(temp_dir)
            target = root / "central"
            _init_repo(target)
            _write_status(target)
            link = root / "central-link"
            link.symlink_to(target, target_is_directory=True)
            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(link)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "symlink component"):
                    wr.validate_coordination_root(root / "task-worktree")

    def test_rejects_symlink_component_in_status_root(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-symlink-component-") as temp_dir:
            root = Path(temp_dir)
            real_parent = root / "real-parent"
            real_parent.mkdir()
            target = real_parent / "central"
            _init_repo(target)
            _write_status(target)
            link_parent = root / "linked-parent"
            link_parent.symlink_to(real_parent, target_is_directory=True)
            status_root = link_parent / "central"
            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(status_root)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "symlink component"):
                    wr.validate_coordination_root(root / "task-worktree")

    def test_rejects_status_root_equal_to_workspace(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-mismatched-root-") as temp_dir:
            worktree = Path(temp_dir) / "task-worktree"
            _init_repo(worktree)
            _write_status(worktree)
            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(worktree)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "not the isolated task worktree"):
                    wr.validate_coordination_root(worktree)

    def test_rejects_status_root_mismatched_with_runner_paths(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-second-root-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            other = root / "other"
            worktree = root / "task-worktree"
            for repo in (central, other, worktree):
                _init_repo(repo)
            _write_status(central)
            _write_status(other)
            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"
            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(other)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    wr.validate_coordination_root(
                        worktree,
                        heartbeat_path=heartbeat,
                        status_path=status,
                    )

    def test_rejects_symlinked_leaves_and_mirror_children(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-symlinks-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "task-worktree"
            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)
            _write_status(central)

            # 1. Test dangling symlink in central root
            dangling = central / "current-work.md"
            dangling.symlink_to(root / "nonexistent-target")

            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(central)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "cannot be a symlink"):
                    wr.validate_coordination_root(worktree)

            # Clean up dangling symlink
            dangling.unlink()

            # 2. Test mirror child symlink (e.g. docs-site/ai-status.json)
            docs_site = central / "docs-site"
            docs_site.mkdir(parents=True, exist_ok=True)
            mirror_child = docs_site / "ai-status.json"
            mirror_child.symlink_to(root / "nonexistent-target-2")

            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(central)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "cannot be a symlink"):
                    wr.validate_coordination_root(worktree)

            # Clean up mirror child
            mirror_child.unlink()

            # 3. Test symlink in ai-task-archive/tasks
            tasks_dir = central / "ai-task-archive" / "tasks"
            tasks_dir.mkdir(parents=True, exist_ok=True)
            archive_leaf = tasks_dir / "bad-leaf.json"
            archive_leaf.symlink_to(root / "nonexistent-target-3")

            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(central)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "cannot be a symlink"):
                    wr.validate_coordination_root(worktree)

            archive_leaf.unlink()

            # 4. Test symlink in archive/logs
            logs_dir = central / "archive" / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_archive_leaf = logs_dir / "bad-log.gz"
            log_archive_leaf.symlink_to(root / "nonexistent-target-4")

            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(central)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "cannot be a symlink"):
                    wr.validate_coordination_root(worktree)

            log_archive_leaf.unlink()

            # 5. Test symlink in .orchestrator/worker-runtime
            runtime_dir = central / ".orchestrator" / "worker-runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            runtime_leaf = runtime_dir / "bad-leaf.json"
            runtime_leaf.symlink_to(root / "nonexistent-target-5")

            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(central)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "cannot be a symlink"):
                    wr.validate_coordination_root(worktree)

            runtime_leaf.unlink()

    def test_validate_coordination_root_creates_regular_derived_views_lock(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-derived-lock-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            worktree = root / "task-worktree"
            _init_repo(central)
            _init_repo(worktree)
            _write_status(central)

            lock_path = central / ".orchestrator" / "status-derived-views.lock"
            with mock.patch.dict(
                os.environ,
                {"PANTHEON_STATUS_ROOT": str(central)},
                clear=True,
            ):
                self.assertEqual(
                    wr.validate_coordination_root(worktree), central.resolve()
                )

            self.assertTrue(lock_path.is_file())
            self.assertFalse(lock_path.is_symlink())

            lock_path.unlink()
            lock_path.symlink_to(root / "outside.lock")
            with mock.patch.dict(
                os.environ,
                {"PANTHEON_STATUS_ROOT": str(central)},
                clear=True,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "derived status views lock cannot contain a symlink"
                ):
                    wr.validate_coordination_root(worktree)

    @unittest.skipUnless(_FUNCTIONAL_BWRAP, "Functional bubblewrap with user namespace support is required for sandbox execution tests")
    def test_child_command_runs_in_task_worktree_with_central_status_root(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-cwd-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "task-worktree"
            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)
            _write_status(central)
            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"
            env = os.environ.copy()
            for key in list(env.keys()):
                if key.startswith("PANTHEON_COMMAND_"):
                    env.pop(key)
            env.update(
                {
                    "PANTHEON_STATUS_ROOT": str(central),
                    "PANTHEON_WORKTREE_ROOT": str(worktree),
                    "ORCH_WORKSPACE_PATH": str(worktree),
                }
            )
            env.update(_command_runtime_env(command_root))
            proc = _run_fixture_worker(
                [
                    sys.executable,
                    _P,
                    "--run-id",
                    "codex-20260716T000000Z-test",
                    "--heartbeat-path",
                    str(heartbeat),
                    "--status-path",
                    str(status),
                    "--",
                    sys.executable,
                    "-c",
                    (
                        "import os, subprocess; "
                        "print(os.getcwd()); "
                        "subprocess.run(['git', 'rev-parse', '--show-toplevel'], check=True)"
                    ),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertIn(str(worktree), proc.stdout)
            runner_status = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(runner_status["status"], "completed")
            self.assertEqual(runner_status["status_command_runtime"]["command_root"], str(command_root.resolve()))
            heartbeat_payload = json.loads(heartbeat.read_text(encoding="utf-8"))
            self.assertEqual(heartbeat_payload["status_command_runtime"]["command_root"], str(command_root.resolve()))

    @unittest.skipUnless(_FUNCTIONAL_BWRAP, "Functional bubblewrap with user namespace support is required for sandbox execution tests")
    def test_provider_cannot_chmod_or_write_command_runtime(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-readonly-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "task-worktree"
            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)
            _write_status(central)

            guarded = command_root / "scripts" / "guarded.py"
            guarded.parent.mkdir(parents=True)
            guarded.write_text("guarded = True\n", encoding="utf-8")
            subprocess.run(["git", "add", "scripts/guarded.py"], cwd=command_root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "add guarded runtime file"],
                cwd=command_root,
                check=True,
            )
            subprocess.run(
                ["git", "update-ref", "refs/remotes/origin/dev", "HEAD"],
                cwd=command_root,
                check=True,
            )
            guarded.chmod(0o444)

            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"
            env = os.environ.copy()
            for key in list(env):
                if key.startswith("PANTHEON_COMMAND_"):
                    env.pop(key)
            env.update(
                {
                    "PANTHEON_STATUS_ROOT": str(central),
                    "PANTHEON_WORKTREE_ROOT": str(worktree),
                    "ORCH_WORKSPACE_PATH": str(worktree),
                }
            )
            env.update(_command_runtime_env(command_root))
            program = (
                "import pathlib, sys; "
                "guarded=pathlib.Path(sys.argv[1]); "
                "worktree=pathlib.Path(sys.argv[2]); "
                "status_root=pathlib.Path(sys.argv[3]); "
                "denied=0; "
                "\ntry: guarded.chmod(0o644)\nexcept OSError: denied += 1\n"
                "try: guarded.write_text('mutated\\n')\nexcept OSError: denied += 1\n"
                "(worktree / 'provider-write-ok').write_text('ok\\n'); "
                "(status_root / 'provider-status-write-ok').write_text('ok\\n'); "
                "sys.exit(0 if denied == 2 and guarded.read_text() == 'guarded = True\\n' else 41)"
            )

            proc = _run_fixture_worker(
                [
                    sys.executable,
                    _P,
                    "--run-id",
                    "codex-20260821T000000Z-readonly",
                    "--heartbeat-path",
                    str(heartbeat),
                    "--status-path",
                    str(status),
                    "--",
                    sys.executable,
                    "-c",
                    program,
                    str(guarded),
                    str(worktree),
                    str(central),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertEqual(guarded.read_text(encoding="utf-8"), "guarded = True\n")
            self.assertEqual(guarded.stat().st_mode & 0o777, 0o444)
            self.assertEqual((worktree / "provider-write-ok").read_text(), "ok\n")
            self.assertEqual((central / "provider-status-write-ok").read_text(), "ok\n")

    @unittest.skipUnless(_FUNCTIONAL_BWRAP, "Functional bubblewrap with user namespace support is required for sandbox execution tests")
    def test_relative_provider_command_is_bound_to_command_runtime(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-command-root-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "execute-plans-worktree"
            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)

            provider = command_root / ".orchestrator" / "bin" / "test-provider"
            provider.parent.mkdir(parents=True, exist_ok=True)
            provider.write_text("#!/bin/sh\necho provider-cwd=$PWD\n", encoding="utf-8")
            provider.chmod(0o755)
            subprocess.run(["git", "add", ".orchestrator/bin/test-provider"], cwd=command_root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "add provider fixture"],
                cwd=command_root,
                check=True,
            )
            subprocess.run(
                ["git", "update-ref", "refs/remotes/origin/dev", "HEAD"],
                cwd=command_root,
                check=True,
            )
            _write_status(central)

            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"
            env = os.environ.copy()
            for key in list(env.keys()):
                if key.startswith("PANTHEON_COMMAND_"):
                    env.pop(key)
            env.update(
                {
                    "PANTHEON_STATUS_ROOT": str(central),
                    "PANTHEON_WORKTREE_ROOT": str(worktree),
                    "ORCH_WORKSPACE_PATH": str(worktree),
                }
            )
            env.update(_command_runtime_env(command_root))

            proc = _run_fixture_worker(
                [
                    sys.executable,
                    _P,
                    "--run-id",
                    "antigravity-20260814T000000Z-test",
                    "--heartbeat-path",
                    str(heartbeat),
                    "--status-path",
                    str(status),
                    "--",
                    ".orchestrator/bin/test-provider",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertIn(f"provider-cwd={worktree}", proc.stdout)
            runner_status = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(runner_status["status"], "completed")
            self.assertEqual(runner_status["command"][0], str(provider.resolve()))

    def test_relative_provider_command_cannot_escape_command_runtime(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-command-escape-") as temp_dir:
            root = Path(temp_dir)
            command_root = root / "command-root"
            command_root.mkdir()

            with self.assertRaisesRegex(RuntimeError, "parent directory references"):
                wr.bind_relative_command_to_runtime(
                    ["../outside-provider", "--prompt", "test"],
                    command_root,
                )

    @unittest.skipUnless(_FUNCTIONAL_BWRAP, "Functional bubblewrap with user namespace support is required for sandbox execution tests")
    def test_heartbeat_binds_task_roles_and_run_id(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-roles-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "task-worktree"
            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)

            # Write ai-status.json with a task having Codex as owner and Claude as reviewer
            task_id = "OPS-TEST-001"
            status_data = {
                "tasks": [
                    {
                        "id": task_id,
                        "owner": "Codex",
                        "reviewer": "Claude",
                        "status": "in_progress",
                    }
                ],
                "agents": [],
                "handoffs": [],
                "blockers": []
            }
            (central / "ai-status.json").write_text(json.dumps(status_data) + "\n", encoding="utf-8")
            (central / ".orchestrator").mkdir(parents=True, exist_ok=True)
            (central / ".orchestrator" / "state.json").write_text("{}", encoding="utf-8")
            (central / ".orchestrator" / "approval-queue.json").write_text("[]", encoding="utf-8")
            (central / ".orchestrator" / "config.json").write_text("{}", encoding="utf-8")

            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"
            env = os.environ.copy()
            for key in list(env.keys()):
                if key.startswith("PANTHEON_COMMAND_"):
                    env.pop(key)
            env.update(
                {
                    "PANTHEON_STATUS_ROOT": str(central),
                    "PANTHEON_WORKTREE_ROOT": str(worktree),
                    "ORCH_WORKSPACE_PATH": str(worktree),
                }
            )
            env.update(_command_runtime_env(command_root))
            proc = _run_fixture_worker(
                [
                    sys.executable,
                    _P,
                    "--run-id",
                    "codex-20260716T000000Z-test",
                    "--heartbeat-path",
                    str(heartbeat),
                    "--status-path",
                    str(status),
                    "--",
                    sys.executable,
                    "-c",
                    f"print('Task ID: {task_id}')",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

            runner_status = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(runner_status["role"], "owner")
            self.assertEqual(runner_status["owner"], "Codex")
            self.assertEqual(runner_status["reviewer"], "Claude")
            self.assertEqual(runner_status["run_id"], "codex-20260716T000000Z-test")

            heartbeat_payload = json.loads(heartbeat.read_text(encoding="utf-8"))
            self.assertEqual(heartbeat_payload["role"], "owner")
            self.assertEqual(heartbeat_payload["owner"], "Codex")
            self.assertEqual(heartbeat_payload["reviewer"], "Claude")
            self.assertEqual(heartbeat_payload["run_id"], "codex-20260716T000000Z-test")

    @unittest.skipUnless(_FUNCTIONAL_BWRAP, "Functional bubblewrap with user namespace support is required for sandbox execution tests")
    def test_child_command_failure_writes_failed_status(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-failure-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "task-worktree"
            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)
            _write_status(central)
            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"
            env = os.environ.copy()
            for key in list(env.keys()):
                if key.startswith("PANTHEON_COMMAND_"):
                    env.pop(key)
            env.update(
                {
                    "PANTHEON_STATUS_ROOT": str(central),
                    "PANTHEON_WORKTREE_ROOT": str(worktree),
                    "ORCH_WORKSPACE_PATH": str(worktree),
                }
            )
            env.update(_command_runtime_env(command_root))
            # Run a failing command (exit code 1)
            proc = _run_fixture_worker(
                [
                    sys.executable,
                    _P,
                    "--run-id",
                    "codex-20260716T000000Z-test",
                    "--heartbeat-path",
                    str(heartbeat),
                    "--status-path",
                    str(status),
                    "--",
                    sys.executable,
                    "-c",
                    "import sys; sys.exit(1)",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
            runner_status = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(runner_status["status"], "failed")
            self.assertEqual(runner_status["exit_code"], 1)

    @mock.patch("time.sleep")
    def test_child_cleanup_on_exception(self, mock_sleep):
        mock_sleep.side_effect = KeyboardInterrupt()

        with tempfile.TemporaryDirectory(prefix="worker-runner-cleanup-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "task-worktree"
            _init_repo(central)
            command_root.mkdir()
            _init_repo(worktree)
            _write_status(central)

            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"

            test_args = [
                "worker_runner.py",
                "--run-id", "test-run",
                "--heartbeat-path", str(heartbeat),
                "--status-path", str(status),
                "--heartbeat-interval-seconds", "0.1",
                "--", "python", "-c", "pass"
            ]

            mock_child = mock.Mock()
            mock_child.pid = 99999
            mock_child.poll.return_value = None

            orig_cwd = os.getcwd()
            try:
                with mock.patch.object(wr, "_git_toplevel", return_value=central), mock.patch.object(wr, "validate_worker_entry_binding", return_value=_CLEANUP_BINDING):
                    with mock.patch.object(wr, "validate_status_command_runtime", return_value={"command_root": str(command_root)}):
                        with mock.patch("subprocess.Popen", return_value=mock_child):
                            with mock.patch("sys.argv", test_args):
                                with mock.patch.dict(os.environ, {
                                    "PANTHEON_STATUS_ROOT": str(central),
                                    "PANTHEON_WORKTREE_ROOT": str(worktree),
                                    "ORCH_WORKSPACE_PATH": str(worktree),
                                }, clear=True):
                                    with self.assertRaises(KeyboardInterrupt):
                                        wr.main()

                mock_child.terminate.assert_called_once()
            finally:
                os.chdir(orig_cwd)

    @mock.patch("time.sleep")
    def test_sigterm_deadline_force_kills_group(self, mock_sleep):
        with tempfile.TemporaryDirectory(prefix="worker-runner-sig-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "task-worktree"
            _init_repo(central)
            command_root.mkdir()
            _init_repo(worktree)
            _write_status(central)

            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"

            test_args = [
                "worker_runner.py",
                "--run-id", "test-run",
                "--heartbeat-path", str(heartbeat),
                "--status-path", str(status),
                "--heartbeat-interval-seconds", "1.0",
                "--", "python", "-c", "pass"
            ]

            mock_child = mock.Mock()
            mock_child.pid = 88888
            mock_child.poll.return_value = None

            orig_cwd = os.getcwd()
            try:
                with mock.patch.object(wr, "_git_toplevel", return_value=central), mock.patch.object(wr, "validate_worker_entry_binding", return_value=_CLEANUP_BINDING):
                    with mock.patch.object(wr, "validate_status_command_runtime", return_value={"command_root": str(command_root)}):
                        with mock.patch("subprocess.Popen", return_value=mock_child):
                            with mock.patch("sys.argv", test_args):
                                with mock.patch.dict(os.environ, {
                                    "PANTHEON_STATUS_ROOT": str(central),
                                    "PANTHEON_WORKTREE_ROOT": str(worktree),
                                    "ORCH_WORKSPACE_PATH": str(worktree),
                                }, clear=True):
                                    current_time = [100.0]
                                    def mock_monotonic():
                                        t = current_time[0]
                                        current_time[0] += 0.1
                                        return t

                                    with mock.patch("time.monotonic", side_effect=mock_monotonic):
                                        with mock.patch("os.killpg") as mock_killpg:
                                            def side_effect_sleep(*args, **kwargs):
                                                import signal
                                                os.kill(os.getpid(), signal.SIGTERM)
                                                current_time[0] += 6.0
                                            mock_sleep.side_effect = side_effect_sleep

                                            exit_code = wr.main()
                                        self.assertEqual(exit_code, 128 + 15) # 128 + SIGTERM
                                        mock_killpg.assert_any_call(88888, 9) # SIGKILL
            finally:
                os.chdir(orig_cwd)

    @mock.patch("time.sleep")
    def test_sigterm_grandchild_cleanup_when_direct_child_exits_first(self, mock_sleep):
        with tempfile.TemporaryDirectory(prefix="worker-runner-grandchild-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "task-worktree"
            _init_repo(central)
            command_root.mkdir()
            _init_repo(worktree)
            _write_status(central)

            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"

            test_args = [
                "worker_runner.py",
                "--run-id", "test-run",
                "--heartbeat-path", str(heartbeat),
                "--status-path", str(status),
                "--heartbeat-interval-seconds", "15.0",
                "--", "python", "-c", "pass"
            ]

            mock_child = mock.Mock()
            mock_child.pid = 99999
            mock_child.poll.side_effect = [None, -15, -15, -15]

            orig_cwd = os.getcwd()
            try:
                with mock.patch.object(wr, "_git_toplevel", return_value=central), mock.patch.object(wr, "validate_worker_entry_binding", return_value=_CLEANUP_BINDING):
                    with mock.patch.object(wr, "validate_status_command_runtime", return_value={"command_root": str(command_root)}):
                        with mock.patch("subprocess.Popen", return_value=mock_child):
                            with mock.patch("sys.argv", test_args):
                                with mock.patch.dict(os.environ, {
                                    "PANTHEON_STATUS_ROOT": str(central),
                                    "PANTHEON_WORKTREE_ROOT": str(worktree),
                                    "ORCH_WORKSPACE_PATH": str(worktree),
                                }, clear=True):
                                    current_time = [100.0]
                                    def mock_monotonic():
                                        t = current_time[0]
                                        current_time[0] += 0.1
                                        return t

                                    with mock.patch("time.monotonic", side_effect=mock_monotonic):
                                        with mock.patch("os.killpg") as mock_killpg:
                                            killpg_calls = []
                                            def side_effect_killpg(pgid, sig):
                                                killpg_calls.append((pgid, sig))
                                                if sig == 0:
                                                    if not any(c[1] == 9 for c in killpg_calls):
                                                        return None
                                                    raise OSError("No such process")
                                                return None
                                            mock_killpg.side_effect = side_effect_killpg

                                            def side_effect_sleep(*args, **kwargs):
                                                import signal
                                                handler = signal.getsignal(signal.SIGTERM)
                                                if callable(handler):
                                                    handler(signal.SIGTERM, None)
                                                current_time[0] += 6.0
                                            mock_sleep.side_effect = side_effect_sleep

                                            exit_code = wr.main()
                                        self.assertEqual(exit_code, 128 + 15)
                                        mock_killpg.assert_any_call(99999, 9)
            finally:
                os.chdir(orig_cwd)

    def test_rejects_parent_directory_reference_in_marker_paths(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-dotdot-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            worktree = root / "task-worktree"
            _init_repo(central)
            _init_repo(worktree)
            _write_status(central)

            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / ".." / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"

            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(central)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "parent directory references"):
                    wr.validate_coordination_root(
                        worktree,
                        heartbeat_path=heartbeat,
                        status_path=status,
                    )

    def test_coordination_root_requires_supervisor_marker_files(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-markers-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            worktree = root / "task-worktree"
            _init_repo(central)
            _init_repo(worktree)
            # Only write status, do not write the other supervisor markers
            (central / "ai-status.json").write_text(
                json.dumps({"tasks": [], "agents": [], "handoffs": [], "blockers": []}) + "\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(central)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "missing required supervisor marker"):
                    wr.validate_coordination_root(worktree)

    def test_conflicting_worktree_roots_rejected(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-conflict-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            worktree1 = root / "worktree-1"
            worktree2 = root / "worktree-2"
            _init_repo(central)
            _init_repo(worktree1)
            _init_repo(worktree2)
            _write_status(central)

            env = {
                "PANTHEON_STATUS_ROOT": str(central),
                "PANTHEON_WORKTREE_ROOT": str(worktree1),
                "ORCH_WORKSPACE_PATH": str(worktree2),
            }
            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"
            test_args = [
                "worker_runner.py",
                "--run-id", "test-run",
                "--heartbeat-path", str(heartbeat),
                "--status-path", str(status),
                "--", "python", "-c", "pass"
            ]
            with mock.patch.object(wr, "_git_toplevel", return_value=central), mock.patch.object(wr, "validate_worker_entry_binding", return_value=_CLEANUP_BINDING):
                with mock.patch.object(wr, "validate_status_command_runtime", return_value={"command_root": str(central)}):
                    with mock.patch("sys.argv", test_args):
                        with mock.patch.dict(os.environ, env, clear=True):
                            with self.assertRaisesRegex(RuntimeError, "Conflicting workspace roots"):
                                wr.main()

    def test_command_runtime_rejects_dirty_executable_import_files(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-dirty-") as temp_dir:
            root = Path(temp_dir)
            _init_repo(root)
            _write_status(root)
            
            # Create a dirty script file in the repo
            dirty_file = root / "scripts" / "malicious.py"
            dirty_file.parent.mkdir(parents=True, exist_ok=True)
            dirty_file.write_text("print('malicious')", encoding="utf-8")
            
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            env = {
                "PANTHEON_COMMAND_ROOT": str(root),
                "PANTHEON_COMMAND_RUNTIME_SHA": sha,
                "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
                "PANTHEON_COMMAND_BASE_REF": "origin/dev",
            }
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(RuntimeError, "contains dirty executable/import file"):
                    wr.validate_status_command_runtime()


class TestCrossRepoLeasedWorktreeWriteBoundary(unittest.TestCase):
    """Test suite for leased-worktree write boundary and cross-repo isolation."""

    def test_bind_worker_sandbox_argument_structure(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-sandbox-struct-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "execute-plans-worktree"
            shared_pantheon = root / "shared-pantheon"
            shared_ep = root / "code" / "execute-plans"
            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)
            _init_repo(shared_pantheon)
            _init_repo(shared_ep)
            _write_status(central)

            runtime_admission_lock = central / ".orchestrator" / "runtime-admission.lock"
            runtime_admission_lock.touch()

            # Create code subdirectories in central
            (central / "services").mkdir(parents=True, exist_ok=True)
            (central / "services" / "api.py").write_text("API=1\n")
            (central / "scripts").mkdir(parents=True, exist_ok=True)

            cmd = ["python3", "-c", "print('hello')"]
            sandbox_args = wr.bind_worker_sandbox(
                cmd,
                command_root=command_root,
                workspace_path=worktree,
                coordination_root=central,
                extra_readonly_roots=[shared_pantheon, shared_ep],
                sandbox_binary="/usr/bin/bwrap",
            )

            import inspect
            sig = inspect.signature(wr.bind_worker_sandbox)
            self.assertNotIn("extra_writable_roots", sig.parameters)

            self.assertIn("--die-with-parent", sandbox_args)
            self.assertIn("--unshare-pid", sandbox_args)

            # 1. Verify host root is mounted --ro-bind / / (default read-only)
            ro_root_idx = sandbox_args.index("/")
            self.assertEqual(sandbox_args[ro_root_idx - 1], "--ro-bind")
            self.assertEqual(sandbox_args[ro_root_idx + 1], "/")

            # 2. Verify --bind / / is NEVER present
            for i, arg in enumerate(sandbox_args):
                if arg == "--bind" and i + 2 < len(sandbox_args):
                    self.assertNotEqual((sandbox_args[i + 1], sandbox_args[i + 2]), ("/", "/"))

            # 3. Verify workspace_path is mounted --bind (read-write)
            ws_idx = sandbox_args.index(str(worktree.resolve()))
            self.assertEqual(sandbox_args[ws_idx - 1], "--bind")

            # 4. The coordination parent is writable so ai-status.json can be
            # replaced atomically through a same-directory temporary file.
            central_indices = [
                index
                for index, value in enumerate(sandbox_args)
                if value == str(central.resolve())
            ]
            self.assertEqual(len(central_indices), 2)
            self.assertEqual(sandbox_args[central_indices[0] - 1], "--bind")
            self.assertEqual(
                sandbox_args[central_indices[0] + 1], str(central.resolve())
            )

            # Existing non-governed source remains a protected mountpoint.
            source_idx = sandbox_args.index(str((central / "services").resolve()))
            self.assertEqual(sandbox_args[source_idx - 1], "--ro-bind")

            # 5. Runtime admission is an existing governed state interface,
            # not a reason to make the coordination source tree writable.
            runtime_lock_idx = sandbox_args.index(str(runtime_admission_lock.resolve()))
            self.assertEqual(sandbox_args[runtime_lock_idx - 1], "--bind")

            # 6. Verify shared repos and command runtime are NOT mounted --bind
            bound_rw_paths = [
                sandbox_args[i + 1]
                for i, arg in enumerate(sandbox_args)
                if arg == "--bind" and i + 1 < len(sandbox_args)
            ]
            self.assertIn(str(central.resolve()), bound_rw_paths)
            self.assertNotIn(str(shared_pantheon.resolve()), bound_rw_paths)
            self.assertNotIn(str(shared_ep.resolve()), bound_rw_paths)
            self.assertNotIn(str(command_root.resolve()), bound_rw_paths)

    def test_bind_worker_sandbox_mounts_atomic_task_store_parent_only(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-task-store-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "execute-plans-worktree"
            runtime = root / "runtime"
            for repository in (central, command_root, worktree):
                _init_repo(repository)
            _write_status(central)
            runtime.mkdir()
            event_log = runtime / "task-state-events-v2.jsonl"
            event_log.write_text("event\n", encoding="utf-8")
            (runtime / f"{event_log.name}.head.json").write_text("{}\n", encoding="utf-8")
            (runtime / f"{event_log.name}.lock").touch()
            live_config = runtime / "live-supervisor.json"
            live_config.write_text("{}\n", encoding="utf-8")

            with mock.patch.dict(
                os.environ,
                {"PANTHEON_TASK_STATE_EVENT_LOG": str(event_log)},
                clear=False,
            ):
                sandbox_args = wr.bind_worker_sandbox(
                    ["python3", "-c", "pass"],
                    command_root=command_root,
                    workspace_path=worktree,
                    coordination_root=central,
                    sandbox_binary="/usr/bin/bwrap",
                )

            runtime_indices = [
                index
                for index, value in enumerate(sandbox_args)
                if value == str(runtime.resolve())
            ]
            self.assertEqual(len(runtime_indices), 2)
            self.assertEqual(sandbox_args[runtime_indices[0] - 1], "--bind")
            self.assertEqual(sandbox_args[runtime_indices[0] + 1], str(runtime.resolve()))
            config_idx = sandbox_args.index(str(live_config.resolve()))
            self.assertEqual(sandbox_args[config_idx - 1], "--ro-bind")
            self.assertNotIn(str(event_log.resolve()), sandbox_args)

    def test_bind_worker_sandbox_reopens_only_selected_linked_gitdir(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-linked-git-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            source = root / "execute-plans"
            worktree = root / "task-worktree"
            for repository in (central, command_root, source):
                _init_repo(repository)
            _write_status(central)
            subprocess.run(
                ["git", "worktree", "add", "-q", "-b", "task/BOUNDARY", str(worktree), "HEAD"],
                cwd=source,
                check=True,
            )

            sandbox_args = wr.bind_worker_sandbox(
                ["python3", "-c", "pass"],
                command_root=command_root,
                workspace_path=worktree,
                coordination_root=central,
                sandbox_binary="/usr/bin/bwrap",
            )

            common_dir = Path(
                subprocess.check_output(
                    ["git", "rev-parse", "--git-common-dir"],
                    cwd=worktree,
                    text=True,
                ).strip()
            ).resolve()
            git_dir = Path(
                subprocess.check_output(
                    ["git", "rev-parse", "--absolute-git-dir"],
                    cwd=worktree,
                    text=True,
                ).strip()
            ).resolve()
            common_idx = sandbox_args.index(str(common_dir))
            self.assertEqual(sandbox_args[common_idx - 1], "--bind")
            source_head_idx = sandbox_args.index(str(common_dir / "HEAD"))
            self.assertEqual(sandbox_args[source_head_idx - 1], "--ro-bind")
            all_worktrees_idx = sandbox_args.index(str(common_dir / "worktrees"))
            self.assertEqual(sandbox_args[all_worktrees_idx - 1], "--ro-bind")
            selected_idx = sandbox_args.index(str(git_dir))
            self.assertEqual(sandbox_args[selected_idx - 1], "--bind")
            self.assertGreater(selected_idx, all_worktrees_idx)

    @unittest.skipUnless(
        _FUNCTIONAL_BWRAP,
        "Functional bubblewrap with user namespace support is required for sandbox execution tests",
    )
    def test_atomic_task_store_write_succeeds_and_runtime_sibling_stays_read_only(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-task-store-live-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "execute-plans-worktree"
            runtime = root / "runtime"
            for repository in (central, command_root, worktree):
                _init_repo(repository)
            _write_status(central)
            runtime.mkdir()
            event_log = runtime / "task-state-events-v2.jsonl"
            head = runtime / f"{event_log.name}.head.json"
            lock = runtime / f"{event_log.name}.lock"
            live_config = runtime / "live-supervisor.json"
            event_log.write_text("event-1\n", encoding="utf-8")
            head.write_text('{"sequence":1}\n', encoding="utf-8")
            lock.touch()
            live_config.write_text('{"protected":true}\n', encoding="utf-8")
            program = (
                "import os, pathlib, sys\n"
                "event, head, lock, protected = map(pathlib.Path, sys.argv[1:])\n"
                "with event.open('a') as handle: handle.write('event-2\\n')\n"
                "with lock.open('r+'): pass\n"
                "temporary = head.with_name(head.name + '.worker.tmp')\n"
                "temporary.write_text('{\\\"sequence\\\":2}\\n')\n"
                "os.replace(temporary, head)\n"
                "blocked = False\n"
                "try: protected.write_text('mutated\\n')\n"
                "except OSError: blocked = True\n"
                "sys.exit(0 if blocked else 41)\n"
            )
            with mock.patch.dict(
                os.environ,
                {"PANTHEON_TASK_STATE_EVENT_LOG": str(event_log)},
                clear=False,
            ):
                sandbox_args = wr.bind_worker_sandbox(
                    [
                        sys.executable,
                        "-c",
                        program,
                        str(event_log),
                        str(head),
                        str(lock),
                        str(live_config),
                    ],
                    command_root=command_root,
                    workspace_path=worktree,
                    coordination_root=central,
                    sandbox_binary=_FUNCTIONAL_BWRAP,
                )
            proc = subprocess.run(sandbox_args, cwd=worktree, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(head.read_text(encoding="utf-8"), '{"sequence":2}\n')
            self.assertIn("event-2", event_log.read_text(encoding="utf-8"))
            self.assertEqual(live_config.read_text(encoding="utf-8"), '{"protected":true}\n')

    @unittest.skipUnless(
        _FUNCTIONAL_BWRAP,
        "Functional bubblewrap with user namespace support is required for sandbox execution tests",
    )
    def test_linked_task_worktree_can_rebase_without_mutating_source_checkout(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-linked-git-live-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            source = root / "execute-plans"
            worktree = root / "task-worktree"
            for repository in (central, command_root, source):
                _init_repo(repository)
            _write_status(central)
            subprocess.run(["git", "branch", "-M", "dev"], cwd=source, check=True)
            subprocess.run(
                ["git", "worktree", "add", "-q", "-b", "task/BOUNDARY", str(worktree), "HEAD"],
                cwd=source,
                check=True,
            )
            (source / "base.txt").write_text("new base\n", encoding="utf-8")
            subprocess.run(["git", "add", "base.txt"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "advance dev"], cwd=source, check=True)
            source_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
            source_branch = subprocess.check_output(
                ["git", "branch", "--show-current"], cwd=source, text=True
            ).strip()
            program = (
                "set -eu\n"
                "git rebase dev\n"
                "printf 'worker\\n' > worker.txt\n"
                "git add worker.txt\n"
                "git commit -q -m 'task commit'\n"
                f"if git -C {source} checkout --detach HEAD~1; then exit 42; fi\n"
            )
            sandbox_args = wr.bind_worker_sandbox(
                ["/bin/bash", "-lc", program],
                command_root=command_root,
                workspace_path=worktree,
                coordination_root=central,
                sandbox_binary=_FUNCTIONAL_BWRAP,
            )
            proc = subprocess.run(sandbox_args, cwd=worktree, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((worktree / "worker.txt").is_file())
            self.assertEqual(
                subprocess.check_output(["git", "rev-parse", "dev"], cwd=worktree, text=True).strip(),
                source_head,
            )
            self.assertEqual(
                subprocess.check_output(["git", "branch", "--show-current"], cwd=source, text=True).strip(),
                source_branch,
            )
            self.assertEqual(
                subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip(),
                source_head,
            )

    def test_bind_worker_sandbox_protects_worktrees_under_tmp(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-sandbox-tmp-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "worktrees" / "pantheon" / "task-1"
            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)
            _write_status(central)

            cmd = ["python3", "-c", "pass"]
            sandbox_args = wr.bind_worker_sandbox(
                cmd,
                command_root=command_root,
                workspace_path=worktree,
                coordination_root=central,
                sandbox_binary="/usr/bin/bwrap",
            )

            if Path("/tmp").exists():
                tmp_idx = sandbox_args.index("/tmp")
                self.assertEqual(sandbox_args[tmp_idx - 1], "--bind-try")

            if Path("/tmp/pantheon-worker-worktrees").exists():
                wt_idx = sandbox_args.index(str(Path("/tmp/pantheon-worker-worktrees").resolve()))
                self.assertEqual(sandbox_args[wt_idx - 1], "--ro-bind-try")
                ws_idx = sandbox_args.index(str(worktree.resolve()))
                self.assertGreater(ws_idx, wt_idx)

    def test_bind_worker_sandbox_missing_binary_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-sandbox-nobin-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            _init_repo(central)
            _init_repo(command_root)
            _write_status(central)

            with mock.patch("shutil.which", return_value=None):
                with mock.patch.dict(os.environ, {}, clear=True):
                    with self.assertRaisesRegex(RuntimeError, "bubblewrap.*required"):
                        wr.bind_worker_sandbox(
                            ["echo", "hi"],
                            command_root=command_root,
                            sandbox_binary=None,
                        )

    def test_bind_worker_sandbox_claude_config_dir_and_narrowed_local_dirs(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-sandbox-user-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "execute-plans-worktree"
            fake_home = root / "fake-home"
            fake_local = fake_home / ".local"
            local_state = fake_local / "state"
            local_share = fake_local / "share"
            local_cache = fake_local / "cache"
            local_bin = fake_local / "bin"
            custom_claude_dir = root / "custom-claude-config"

            for d in (local_state, local_share, local_cache, local_bin, custom_claude_dir):
                d.mkdir(parents=True, exist_ok=True)

            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)
            _write_status(central)

            cmd = ["python3", "-c", "pass"]
            env = {
                "CLAUDE_CONFIG_DIR": str(custom_claude_dir),
            }
            with (
                mock.patch.object(Path, "home", return_value=fake_home),
                mock.patch.dict(os.environ, env, clear=False),
            ):
                sandbox_args = wr.bind_worker_sandbox(
                    cmd,
                    command_root=command_root,
                    workspace_path=worktree,
                    coordination_root=central,
                    sandbox_binary="/usr/bin/bwrap",
                )

            # 1. CLAUDE_CONFIG_DIR is discovered and mounted writable with --bind-try
            self.assertIn(str(custom_claude_dir.resolve()), sandbox_args)
            claude_idx = sandbox_args.index(str(custom_claude_dir.resolve()))
            self.assertEqual(sandbox_args[claude_idx - 1], "--bind-try")

            # 2. Narrowed ~/.local subdirectories (state, share, cache) are mounted writable with --bind-try
            for sub_dir in (local_state, local_share, local_cache):
                self.assertIn(str(sub_dir.resolve()), sandbox_args)
                idx = sandbox_args.index(str(sub_dir.resolve()))
                self.assertEqual(sandbox_args[idx - 1], "--bind-try")

            # 3. Whole ~/.local and ~/.local/bin are NOT mounted writable
            bound_rw_paths = [
                sandbox_args[i + 1]
                for i, arg in enumerate(sandbox_args)
                if arg in ("--bind", "--bind-try") and i + 1 < len(sandbox_args)
            ]
            self.assertNotIn(str(fake_local.resolve()), bound_rw_paths)
            self.assertNotIn(str(local_bin.resolve()), bound_rw_paths)

    @unittest.skipUnless(_FUNCTIONAL_BWRAP, "Functional bubblewrap with user namespace support is required for sandbox execution tests")
    def test_reproduce_and_prevent_20260827_reviewer_checkout_mutation(self):
        """Reproduce and prove fix for the 2026-08-27 reviewer checkout mutation incident.

        Incident: worker dispatched for execute-plans review ran `git checkout origin/dev`
        in shared pantheon checkout, detaching the shared HEAD.
        Fix: shared checkouts are mounted read-only, git checkout fails, HEAD unchanged.
        """
        with tempfile.TemporaryDirectory(prefix="worker-runner-incident-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "execute-plans-worktree"
            shared_pantheon = root / "shared-pantheon"

            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)
            _init_repo(shared_pantheon)
            _write_status(central)

            # Ensure shared pantheon is on branch dev
            subprocess.run(["git", "checkout", "-q", "-b", "dev"], cwd=shared_pantheon, check=True)
            initial_branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=shared_pantheon, text=True).strip()
            self.assertEqual(initial_branch, "dev")
            initial_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=shared_pantheon, text=True).strip()

            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"

            env = os.environ.copy()
            for key in list(env):
                if key.startswith("PANTHEON_COMMAND_"):
                    env.pop(key)
            env.update({
                "PANTHEON_STATUS_ROOT": str(central),
                "PANTHEON_WORKTREE_ROOT": str(worktree),
                "ORCH_WORKSPACE_PATH": str(worktree),
            })
            env.update(_command_runtime_env(command_root))

            # Worker attempts to run git checkout in shared pantheon checkout
            program = (
                "import subprocess, sys, pathlib\n"
                "shared = pathlib.Path(sys.argv[1])\n"
                "worktree = pathlib.Path(sys.argv[2])\n"
                "mutation_prevented = False\n"
                "# 1. Attempt git checkout in shared pantheon checkout\n"
                "try:\n"
                "    p = subprocess.run(['git', 'checkout', 'origin/dev'], cwd=shared, capture_output=True, text=True, check=True)\n"
                "except (subprocess.CalledProcessError, OSError):\n"
                "    mutation_prevented = True\n"
                "# 2. Writing in own worktree succeeds\n"
                "(worktree / 'delivery-result.txt').write_text('delivered\\n')\n"
                "sys.exit(0 if mutation_prevented else 42)\n"
            )

            proc = _run_fixture_worker(
                [
                    sys.executable,
                    _P,
                    "--run-id",
                    "antigravity-20260827T171213Z-boundary-test",
                    "--heartbeat-path",
                    str(heartbeat),
                    "--status-path",
                    str(status),
                    "--",
                    sys.executable,
                    "-c",
                    program,
                    str(shared_pantheon),
                    str(worktree),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            # Verify shared pantheon checkout was NOT mutated or detached
            current_branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=shared_pantheon, text=True).strip()
            self.assertEqual(current_branch, "dev")
            current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=shared_pantheon, text=True).strip()
            self.assertEqual(current_head, initial_head)

            # Verify delivery worktree write succeeded
            self.assertEqual((worktree / "delivery-result.txt").read_text(), "delivered\n")

    @unittest.skipUnless(_FUNCTIONAL_BWRAP, "Functional bubblewrap with user namespace support is required for sandbox execution tests")
    def test_prevent_cross_repo_file_mutations(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-file-mut-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "execute-plans-worktree"
            shared_pantheon = root / "shared-pantheon"

            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)
            _init_repo(shared_pantheon)
            _write_status(central)

            (shared_pantheon / "services").mkdir(parents=True, exist_ok=True)
            (shared_pantheon / "services" / "app.py").write_text("original\n")

            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"

            env = os.environ.copy()
            for key in list(env):
                if key.startswith("PANTHEON_COMMAND_"):
                    env.pop(key)
            env.update({
                "PANTHEON_STATUS_ROOT": str(central),
                "PANTHEON_WORKTREE_ROOT": str(worktree),
                "ORCH_WORKSPACE_PATH": str(worktree),
            })
            env.update(_command_runtime_env(command_root))

            program = (
                "import sys, pathlib\n"
                "shared_svc = pathlib.Path(sys.argv[1]) / 'services'\n"
                "denied = 0\n"
                "# 1. Attempt to create new file in shared repo\n"
                "try:\n"
                "    (shared_svc / 'evil.py').write_text('evil')\n"
                "except OSError:\n"
                "    denied += 1\n"
                "# 2. Attempt to overwrite existing file in shared repo\n"
                "try:\n"
                "    (shared_svc / 'app.py').write_text('mutated')\n"
                "except OSError:\n"
                "    denied += 1\n"
                "sys.exit(0 if denied == 2 else 43)\n"
            )

            proc = _run_fixture_worker(
                [
                    sys.executable,
                    _P,
                    "--run-id",
                    "codex-20260827T000000Z-file-mut-test",
                    "--heartbeat-path",
                    str(heartbeat),
                    "--status-path",
                    str(status),
                    "--",
                    sys.executable,
                    "-c",
                    program,
                    str(shared_pantheon),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertFalse((shared_pantheon / "services" / "evil.py").exists())
            self.assertEqual((shared_pantheon / "services" / "app.py").read_text(), "original\n")

    @unittest.skipUnless(_FUNCTIONAL_BWRAP, "Functional bubblewrap with user namespace support is required for sandbox execution tests")
    def test_symlink_and_path_traversal_cannot_escape_leased_worktree(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-traversal-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "execute-plans-worktree"
            shared_pantheon = root / "shared-pantheon"

            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)
            _init_repo(shared_pantheon)
            _write_status(central)

            (shared_pantheon / "services").mkdir(parents=True, exist_ok=True)
            target = shared_pantheon / "services" / "target.py"
            target.write_text("safe\n")

            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"

            env = os.environ.copy()
            for key in list(env):
                if key.startswith("PANTHEON_COMMAND_"):
                    env.pop(key)
            env.update({
                "PANTHEON_STATUS_ROOT": str(central),
                "PANTHEON_WORKTREE_ROOT": str(worktree),
                "ORCH_WORKSPACE_PATH": str(worktree),
            })
            env.update(_command_runtime_env(command_root))

            program = (
                "import os, sys, pathlib\n"
                "target = pathlib.Path(sys.argv[1])\n"
                "worktree = pathlib.Path(sys.argv[2])\n"
                "denied = 0\n"
                "# 1. Symlink pointing outside leased worktree\n"
                "symlink = worktree / 'symlink_to_shared'\n"
                "symlink.symlink_to(target)\n"
                "try:\n"
                "    symlink.write_text('mutated via symlink')\n"
                "except OSError:\n"
                "    denied += 1\n"
                "# 2. Path traversal escaping worktree\n"
                "relative_escape = worktree / '..' / 'shared-pantheon' / 'services' / 'target.py'\n"
                "try:\n"
                "    relative_escape.write_text('mutated via dotdot')\n"
                "except OSError:\n"
                "    denied += 1\n"
                "sys.exit(0 if denied == 2 else 44)\n"
            )

            proc = _run_fixture_worker(
                [
                    sys.executable,
                    _P,
                    "--run-id",
                    "claude-1-20260827T000000Z-traversal-test",
                    "--heartbeat-path",
                    str(heartbeat),
                    "--status-path",
                    str(status),
                    "--",
                    sys.executable,
                    "-c",
                    program,
                    str(target),
                    str(worktree),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertEqual(target.read_text(), "safe\n")

    @unittest.skipUnless(_FUNCTIONAL_BWRAP, "Functional bubblewrap with user namespace support is required for sandbox execution tests")
    def test_governed_ai_status_writes_succeed_under_boundary(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-status-write-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "execute-plans-worktree"

            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)
            _write_status(central)

            # Create governance status files
            (central / "ai-status.json").write_text(
                json.dumps({"tasks": [{"id": "TASK-001", "status": "in_progress"}], "agents": [], "handoffs": []}) + "\n"
            )
            (central / "ai-activity-log.jsonl").write_text('{"event": "start"}\n')
            (central / "current-work.md").write_text("# Current Work\n")
            (central / "dashboard-bundle.json").write_text(
                json.dumps({"status": "current"}) + "\n"
            )

            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"

            env = os.environ.copy()
            for key in list(env):
                if key.startswith("PANTHEON_COMMAND_"):
                    env.pop(key)
            env.update({
                "PANTHEON_STATUS_ROOT": str(central),
                "PANTHEON_WORKTREE_ROOT": str(worktree),
                "ORCH_WORKSPACE_PATH": str(worktree),
            })
            env.update(_command_runtime_env(command_root))

            program = (
                "import fcntl, json, os, sys, pathlib, tempfile\n"
                "central = pathlib.Path(sys.argv[1])\n"
                "# 1. Atomically replace ai-status.json through its parent.\n"
                "status_path = central / 'ai-status.json'\n"
                "data = json.loads(status_path.read_text())\n"
                "data['tasks'][0]['status'] = 'completed'\n"
                "with tempfile.NamedTemporaryFile('w', dir=central, delete=False) as handle:\n"
                "    handle.write(json.dumps(data) + '\\n')\n"
                "    handle.flush()\n"
                "    os.fsync(handle.fileno())\n"
                "    temp_path = pathlib.Path(handle.name)\n"
                "os.replace(temp_path, status_path)\n"
                "# 2. Append to ai-activity-log.jsonl\n"
                "log_path = central / 'ai-activity-log.jsonl'\n"
                "with log_path.open('a') as f:\n"
                "    f.write(json.dumps({'event': 'done'}) + '\\n')\n"
                "# 3. Update current-work.md\n"
                "(central / 'current-work.md').write_text('# Done\\n')\n"
                "# 4. Update the top-level dashboard projection.\n"
                "dashboard_path = central / 'dashboard-bundle.json'\n"
                "dashboard_path.write_text(json.dumps({'status': 'updated'}) + '\\n')\n"
                "# 5. The derived projection lock is a governed writable file.\n"
                "lock_path = central / '.orchestrator' / 'status-derived-views.lock'\n"
                "descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)\n"
                "fcntl.flock(descriptor, fcntl.LOCK_EX)\n"
                "fcntl.flock(descriptor, fcntl.LOCK_UN)\n"
                "os.close(descriptor)\n"
                "sys.exit(0)\n"
            )

            proc = _run_fixture_worker(
                [
                    sys.executable,
                    _P,
                    "--run-id",
                    "antigravity-20260827T000000Z-status-test",
                    "--heartbeat-path",
                    str(heartbeat),
                    "--status-path",
                    str(status),
                    "--",
                    sys.executable,
                    "-c",
                    program,
                    str(central),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            updated_status = json.loads((central / "ai-status.json").read_text())
            self.assertEqual(updated_status["tasks"][0]["status"], "completed")
            self.assertIn('{"event": "done"}', (central / "ai-activity-log.jsonl").read_text())
            self.assertEqual((central / "current-work.md").read_text(), "# Done\n")
            self.assertEqual(
                json.loads((central / "dashboard-bundle.json").read_text()),
                {"status": "updated"},
            )

    @unittest.skipUnless(_FUNCTIONAL_BWRAP, "Functional bubblewrap with user namespace support is required for sandbox execution tests")
    def test_coordination_source_stays_read_only_with_atomic_status_parent(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-status-source-ro-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            command_root = root / "command-runtime"
            worktree = root / "execute-plans-worktree"
            _init_repo(central)
            _init_repo(command_root)
            _init_repo(worktree)
            _write_status(central)
            source = central / "services" / "api.py"
            source.parent.mkdir(parents=True)
            source.write_text("API = 1\n", encoding="utf-8")

            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"
            env = os.environ.copy()
            for key in list(env):
                if key.startswith("PANTHEON_COMMAND_"):
                    env.pop(key)
            env.update({
                "PANTHEON_STATUS_ROOT": str(central),
                "PANTHEON_WORKTREE_ROOT": str(worktree),
                "ORCH_WORKSPACE_PATH": str(worktree),
            })
            env.update(_command_runtime_env(command_root))

            proc = _run_fixture_worker(
                [
                    sys.executable,
                    _P,
                    "--run-id",
                    "antigravity-20260827T000000Z-source-ro-test",
                    "--heartbeat-path",
                    str(heartbeat),
                    "--status-path",
                    str(status),
                    "--",
                    sys.executable,
                    "-c",
                    "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('API = 2\\n')",
                    str(source),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertEqual(source.read_text(encoding="utf-8"), "API = 1\n")


class TestCanonicalWorkerEntryProcess(unittest.TestCase):
    """Real local wrapper/provider processes; no bubblewrap-dependent skips."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="worker-entry-process-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.central = self.root / "central"
        self.command_root = self.root / "command-runtime"
        self.workspace = self.root / "task-worktree"
        for root in (self.central, self.command_root, self.workspace):
            _init_repo(root)
        _write_status(self.central)
        self.marker = self.workspace / "provider-effect"
        self.heartbeat = self.central / ".orchestrator/worker-runtime/heartbeats/run.json"
        self.runner_status = self.central / ".orchestrator/worker-runtime/status/run.json"
        self.env = {**os.environ, **_command_runtime_env(self.command_root),
                    "PANTHEON_STATUS_ROOT": str(self.central),
                    "PANTHEON_WORKTREE_ROOT": str(self.workspace),
                    "ORCH_WORKSPACE_PATH": str(self.workspace)}
        from test_execution_authorization import ExecutionAuthorizationTestCase
        fixture = ExecutionAuthorizationTestCase()
        fixture.setUp()
        fixture.now = datetime.now(timezone.utc)
        self.task = fixture._granted_task()
        self.task["generation"] = 1
        self.task["status"] = "in_progress"
        grant = fixture._grant(generation=1)
        # Exercise a genuinely verified isolated synthetic issuer assertion.
        wr.execution_authorization.verify_execution_grant(
            grant, policy=fixture.policy, task_id=self.task["id"],
            generation=self.task["generation"], trusted_issuers=fixture.trusted_issuers,
            now=fixture.now)
        self.task["execution_authorization"] = wr.execution_authorization.build_granted_authorization(
            policy=fixture.policy, grant=grant, task=self.task)
        self.task["execution_authorization"] = wr.execution_authorization.reserve_execution_authorization(
            self.task, run_id="fixture-attempt-1", now=fixture.now)

    def run_worker(self, *, task=None, code=None, **kwargs):
        command = [sys.executable, "-c", code or (
            "from pathlib import Path; Path('provider-effect').write_text('authorized fixture')"
        )]
        argv = [sys.executable, _P, "--run-id", "codex-20260906T000000Z-fixture",
                "--heartbeat-path", str(self.heartbeat), "--status-path", str(self.runner_status),
                "--heartbeat-interval-seconds", "1", "--", *command]
        return _run_fixture_worker(argv, env=self.env, task=task or self.task,
                                   local_stub=kwargs.pop("local_stub", True), **kwargs)

    def assert_denied(self, proc, reason):
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(reason, proc.stderr)
        self.assertFalse(self.marker.exists(), "unauthorized provider child produced an effect")
        self.assertFalse(self.heartbeat.exists(), "entry published a starting marker before authorization")
        self.assertFalse(self.runner_status.exists(), "entry published status before authorization")

    def test_exact_canonical_receipt_and_reservation_launch_once(self):
        proc = self.run_worker()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.marker.exists())
        status = json.loads(self.runner_status.read_text())
        # Codex2's provider run-id happens to start with "codex"; it is not identity.
        self.assertEqual(status["agent"], "codex2")
        self.assertEqual(status["task_id"], self.task["id"])
        self.assertEqual(status["role"], "owner")

    def test_ordinary_functional_task_launches_without_authorization_record(self):
        task = {"id": "FUNCTIONAL", "owner": "Codex", "reviewer": "Codex2",
                "generation": 1, "status": "in_progress"}
        proc = self.run_worker(task=task)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.marker.exists())

    def test_legacy_functional_task_without_generation_uses_canonical_one(self):
        task = {"id": "LEGACY-FUNCTIONAL", "owner": "Codex", "reviewer": "Codex2",
                "status": "in_progress"}
        proc = self.run_worker(task=task)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.marker.exists())

    def test_explicit_malformed_generation_has_zero_launch(self):
        task = {"id": "MALFORMED-GENERATION", "owner": "Codex", "reviewer": "Codex2",
                "status": "in_progress", "generation": "1"}
        self.assert_denied(self.run_worker(task=task), "generation or dispatch identity mismatch")

    def test_durable_phase_launch_receipt_authorizes_exact_process(self):
        proc = self.run_worker(receipt_in_phase=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.marker.exists())

    def test_direct_runner_without_canonical_receipt_has_zero_launch(self):
        self.assert_denied(self.run_worker(publish_receipt=False), "launch receipt is missing")

    def test_process_identity_replay_has_zero_launch(self):
        self.assert_denied(self.run_worker(mutate_receipt=lambda worker: worker.update(pid=1)),
                           "dispatch/run/process identity mismatch")

    def test_forged_review_purpose_has_zero_launch(self):
        self.env.update({"ORCH_AGENT_ID": "codex", "ORCH_REASON": "review_ready_dispatch"})
        def fake_review(worker):
            worker["request_snapshot"]["reason"] = "review_ready_dispatch"
        self.assert_denied(self.run_worker(mutate_receipt=fake_review), "purpose/assignment is not current")

    def test_forged_task_in_prompt_cannot_replace_canonical_task(self):
        def pending(state):
            state["tasks"][0]["execution_authorization"]["state"] = "pending_authorization"
        self.assert_denied(self.run_worker(code="print('Task ID: FUNCTIONAL'); open('provider-effect','w').write('bad')",
                                           mutate_journal=pending), "not currently execution-authorized")

    def test_revocation_in_journal_overrides_stale_projection(self):
        (self.central / "ai-status.json").write_text(json.dumps({"tasks": [self.task]}))
        def revoke(state):
            state["tasks"][0]["execution_authorization"]["state"] = "revoked"
        self.assert_denied(self.run_worker(mutate_journal=revoke), "not currently execution-authorized")

    def test_missing_authorization_fails_closed_even_with_matching_receipt(self):
        def remove(state):
            state["tasks"][0].pop("execution_authorization")
        self.assert_denied(self.run_worker(mutate_journal=remove), "not currently execution-authorized")

    def test_corrupt_authoritative_head_has_zero_launch(self):
        self.assert_denied(self.run_worker(mutate_store=lambda journal: Path(str(journal) + ".head.json").write_text("{broken")),
                           "task-state")

    def test_missing_task_has_zero_launch(self):
        def missing(worker):
            worker["task_id"] = "MISSING"
            worker["process_generation"] = wr.worker_process_generation_id(
                task_id="MISSING", worker_run_id=worker["run_id"], queue_event_id=worker["queue_event_id"],
                pid=worker["pid"], pid_start_ticks=worker["pid_start_ticks"])
        self.assert_denied(self.run_worker(mutate_receipt=missing), "canonical task is missing")

    def test_stale_generation_has_zero_launch(self):
        self.assert_denied(self.run_worker(mutate_receipt=lambda worker: worker.update(task_generation=99)),
                           "generation or dispatch identity mismatch")

    def test_expired_lease_has_zero_launch(self):
        self.assert_denied(self.run_worker(mutate_receipt=lambda worker: worker.update(lease_expires_at="2000-01-01T00:00:00Z")),
                           "lease is expired")

    def test_changed_command_has_zero_launch(self):
        self.assert_denied(self.run_worker(mutate_receipt=lambda worker: worker.update(command=["other-command"])),
                           "command/workspace/runtime")

    def test_changed_authoritative_store_binding_has_zero_launch(self):
        self.assert_denied(self.run_worker(mutate_receipt=lambda worker: worker.update(task_state_identity={})),
                           "TaskStore receipt binding mismatch")

    def test_canonical_review_does_not_spend_pending_authorization(self):
        self.task["status"] = "review"
        self.task["execution_authorization"]["state"] = "pending_authorization"
        def review(worker):
            worker["agent_id"] = worker["logical_agent_id"] = "codex"
            worker["request_snapshot"].update(agent_id="codex", reason="review_ready_dispatch")
        proc = self.run_worker(mutate_receipt=review, code="print('read-only review fixture')")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.marker.exists())
        journal = self.root / "test-task-store/events.jsonl"
        self.assertEqual(load_snapshot(journal)["state"]["tasks"][0]["execution_authorization"]["state"], "pending_authorization")
        self.assertEqual(json.loads(self.runner_status.read_text())["role"], "reviewer")

    def test_privileged_readonly_sandbox_does_not_reopen_worktree_git(self):
        with mock.patch.object(wr, "_append_leased_git_metadata_mounts") as git_mounts:
            command = wr.bind_worker_sandbox(
                [sys.executable, "-c", "pass"], command_root=self.command_root,
                workspace_path=self.workspace, sandbox_binary="/bin/true",
                read_only_worktree=True)
        git_mounts.assert_not_called()
        mounts = [command[index:index + 3] for index in range(len(command) - 2)]
        self.assertIn(["--ro-bind", str(self.workspace), str(self.workspace)], mounts)
        self.assertNotIn(["--bind", str(self.workspace), str(self.workspace)], mounts)

    def test_privileged_review_real_sandbox_denies_worktree_write(self):
        self.task["status"] = "review"
        self.task["execution_authorization"]["state"] = "pending_authorization"
        def review(worker):
            worker["agent_id"] = worker["logical_agent_id"] = "codex"
            worker["request_snapshot"].update(agent_id="codex", reason="review_ready_dispatch")
        proc = self.run_worker(mutate_receipt=review, local_stub=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Read-only file system", proc.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual(json.loads(self.runner_status.read_text())["role"], "reviewer")

    def test_canonical_owner_finalize_keeps_pending_workspace_readonly(self):
        self.task["status"] = "review_approved"
        self.task["execution_authorization"]["state"] = "pending_authorization"
        def finalize(worker):
            worker["request_snapshot"]["reason"] = "owned_finalize_dispatch"
        proc = self.run_worker(mutate_receipt=finalize, local_stub=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Read-only file system", proc.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual(json.loads(self.runner_status.read_text())["role"], "owner_finalize")
        journal = self.root / "test-task-store/events.jsonl"
        self.assertEqual(load_snapshot(journal)["state"]["tasks"][0]["execution_authorization"]["state"], "pending_authorization")

    def test_revocation_after_first_binding_before_popen_has_zero_effect(self):
        proc = self.run_worker(revoke_during_sandbox=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not currently execution-authorized", proc.stderr)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.heartbeat.exists())
        status = json.loads(self.runner_status.read_text())
        self.assertIsNone(status["child_pid"])
        self.assertEqual(status["status"], "failed")

    def test_active_revocation_stops_child_before_next_effect(self):
        ready = self.workspace / "ready"
        def revoke_after_start(proc, journal, state):
            deadline = time.monotonic() + 10
            while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(ready.exists(), "child never reached its authorized initial boundary")
            state["tasks"][0]["execution_authorization"]["state"] = "revoked"
            append_state_commit(journal, state, source="isolated-test-revoke-active-run")
        proc = self.run_worker(code="from pathlib import Path; import time; Path('ready').write_text('ready'); time.sleep(4); Path('provider-effect').write_text('unauthorized')",
                               during_run=revoke_after_start)
        self.assertEqual(proc.returncode, 143, proc.stderr)
        self.assertFalse(self.marker.exists())
        self.assertTrue(json.loads(self.runner_status.read_text())["execution_authorization_revoked"])


if __name__ == "__main__":
    unittest.main()
