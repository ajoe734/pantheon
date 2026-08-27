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
            proc = subprocess.run(
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

            proc = subprocess.run(
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

            proc = subprocess.run(
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
            proc = subprocess.run(
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
            proc = subprocess.run(
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
                with mock.patch.object(wr, "_git_toplevel", return_value=central):
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
                with mock.patch.object(wr, "_git_toplevel", return_value=central):
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
                with mock.patch.object(wr, "_git_toplevel", return_value=central):
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
            with mock.patch.object(wr, "_git_toplevel", return_value=central):
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

            # 4. Verify ai-status.json in central is mounted --bind (read-write)
            status_idx = sandbox_args.index(str((central / "ai-status.json").resolve()))
            self.assertEqual(sandbox_args[status_idx - 1], "--bind")

            # 5. Verify shared repos and central root are NOT mounted --bind
            bound_rw_paths = [
                sandbox_args[i + 1]
                for i, arg in enumerate(sandbox_args)
                if arg == "--bind" and i + 1 < len(sandbox_args)
            ]
            self.assertNotIn(str(central.resolve()), bound_rw_paths)
            self.assertNotIn(str(shared_pantheon.resolve()), bound_rw_paths)
            self.assertNotIn(str(shared_ep.resolve()), bound_rw_paths)
            self.assertNotIn(str(command_root.resolve()), bound_rw_paths)

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

            proc = subprocess.run(
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

            proc = subprocess.run(
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

            proc = subprocess.run(
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
                "import json, sys, pathlib\n"
                "central = pathlib.Path(sys.argv[1])\n"
                "# 1. Update ai-status.json\n"
                "status_path = central / 'ai-status.json'\n"
                "data = json.loads(status_path.read_text())\n"
                "data['tasks'][0]['status'] = 'completed'\n"
                "status_path.write_text(json.dumps(data) + '\\n')\n"
                "# 2. Append to ai-activity-log.jsonl\n"
                "log_path = central / 'ai-activity-log.jsonl'\n"
                "with log_path.open('a') as f:\n"
                "    f.write(json.dumps({'event': 'done'}) + '\\n')\n"
                "# 3. Update current-work.md\n"
                "(central / 'current-work.md').write_text('# Done\\n')\n"
                "sys.exit(0)\n"
            )

            proc = subprocess.run(
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


if __name__ == "__main__":
    unittest.main()
