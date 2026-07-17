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


def _write_status(path: Path) -> None:
    (path / "ai-status.json").write_text(
        json.dumps({"tasks": [], "agents": [], "handoffs": [], "blockers": []}) + "\n",
        encoding="utf-8",
    )


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
            worktree = root / "task-worktree"
            _init_repo(central)
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

    def test_child_command_runs_in_task_worktree_with_central_status_root(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-cwd-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            worktree = root / "task-worktree"
            _init_repo(central)
            _init_repo(worktree)
            _write_status(central)
            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"
            env = os.environ.copy()
            env.update(
                {
                    "PANTHEON_STATUS_ROOT": str(central),
                    "PANTHEON_WORKTREE_ROOT": str(worktree),
                    "ORCH_WORKSPACE_PATH": str(worktree),
                }
            )
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

    def test_child_command_failure_writes_failed_status(self):
        with tempfile.TemporaryDirectory(prefix="worker-runner-failure-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            worktree = root / "task-worktree"
            _init_repo(central)
            _init_repo(worktree)
            _write_status(central)
            heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"
            env = os.environ.copy()
            env.update(
                {
                    "PANTHEON_STATUS_ROOT": str(central),
                    "PANTHEON_WORKTREE_ROOT": str(worktree),
                    "ORCH_WORKSPACE_PATH": str(worktree),
                }
            )
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
            worktree = root / "task-worktree"
            _init_repo(central)
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
            worktree = root / "task-worktree"
            _init_repo(central)
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
                                        self.assertEqual(exit_code, 128 + 9) # 128 + SIGKILL
                                        mock_killpg.assert_any_call(88888, 9) # SIGKILL
            finally:
                os.chdir(orig_cwd)

    @mock.patch("time.sleep")
    def test_sigterm_grandchild_cleanup_when_direct_child_exits_first(self, mock_sleep):
        with tempfile.TemporaryDirectory(prefix="worker-runner-grandchild-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            worktree = root / "task-worktree"
            _init_repo(central)
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


if __name__ == "__main__":
    unittest.main()
