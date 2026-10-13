"""Lifecycle regression tests for the original Redis proof fixtures.

These Docker/Redis doubles test cleanup boundaries, not Redis correctness.
The real Redis proof assertions remain in the two owner test modules.
"""
from __future__ import annotations

import builtins
import importlib
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
MODULES = (
    "services.paper_fleet_reconciler.test_paper_fleet_reconciler",
    "services.execution.lean_runtime.test_signal_isolation",
)


class RedisFixtureCleanupTests(unittest.TestCase):
    def fixtures(self):
        for name in MODULES:
            module = importlib.import_module(name)
            fixture = type("OwnedCleanupFixture", (module._RealRedisDockerTestCase,), {})
            yield module, fixture

    def environment(self, module, redis, commands, *, start_error=None):
        def docker(argv, **kwargs):
            commands.append(argv)
            if argv[:2] == ["docker", "run"] and start_error is not None:
                raise start_error
            return subprocess.CompletedProcess(argv, 0, stdout="owned-container", stderr="")

        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.dict(sys.modules, {"redis": redis}))
        stack.enter_context(patch.object(module.shutil, "which", return_value="docker"))
        stack.enter_context(patch.object(module.subprocess, "run", side_effect=docker))
        return stack

    def assert_owned_cleanup(self, commands, fixture):
        starts = [c for c in commands if c[:2] == ["docker", "run"]]
        self.assertEqual(len(starts), 1)
        name = starts[0][starts[0].index("--name") + 1]
        self.assertTrue(name.startswith(("l12-cap-leader-", "l12-cap-redis-")))
        self.assertEqual([c for c in commands if c[:2] == ["docker", "rm"]],
                         [["docker", "rm", "-f", "-v", name]])
        self.assertEqual(fixture._container_name, "")

    def test_missing_dependency_skips_before_any_docker_or_socket(self):
        original_import = builtins.__import__

        def missing(name, *args, **kwargs):
            if name == "redis":
                raise ModuleNotFoundError("No module named 'redis'", name="redis")
            return original_import(name, *args, **kwargs)

        for module, fixture in self.fixtures():
            with self.subTest(module=module.__name__), patch("builtins.__import__", side_effect=missing), \
                    patch.object(module.subprocess, "run") as docker, \
                    patch.object(module.socket, "socket") as socket:
                with self.assertRaisesRegex(unittest.SkipTest, "redis package"):
                    fixture.setUpClass()
                docker.assert_not_called()
                socket.assert_not_called()

    def test_client_constructor_failure_cleans_before_propagation(self):
        for module, fixture in self.fixtures():
            commands = []
            sentinel = ValueError("owned client-construction failure")
            redis = SimpleNamespace(Redis=SimpleNamespace(from_url=Mock(side_effect=sentinel)))
            with self.subTest(module=module.__name__), self.environment(module, redis, commands):
                with self.assertRaises(ValueError) as caught:
                    fixture.setUpClass()
                self.assertIs(caught.exception, sentinel)
                self.assert_owned_cleanup(commands, fixture)

    def test_base_exception_after_start_also_cleans(self):
        for module, fixture in self.fixtures():
            commands = []
            redis = SimpleNamespace(Redis=SimpleNamespace(from_url=Mock(side_effect=KeyboardInterrupt)))
            with self.subTest(module=module.__name__), self.environment(module, redis, commands):
                with self.assertRaises(KeyboardInterrupt):
                    fixture.setUpClass()
                self.assert_owned_cleanup(commands, fixture)

    def test_ambiguous_docker_start_cleans_owned_name(self):
        for module, fixture in self.fixtures():
            commands = []
            redis = SimpleNamespace(Redis=SimpleNamespace(from_url=Mock()))
            sentinel = OSError("owned start acknowledgment lost")
            with self.subTest(module=module.__name__), self.environment(module, redis, commands, start_error=sentinel):
                with self.assertRaises(OSError):
                    fixture.setUpClass()
                self.assert_owned_cleanup(commands, fixture)
                redis.Redis.from_url.assert_not_called()

    def test_original_ten_second_readiness_bound_cleans(self):
        for module, fixture in self.fixtures():
            commands = []
            redis = SimpleNamespace(Redis=SimpleNamespace(from_url=Mock(return_value=SimpleNamespace(ping=lambda: False))))
            with self.subTest(module=module.__name__), self.environment(module, redis, commands), \
                    patch.object(module.time, "monotonic", side_effect=[0, 1, 11]), \
                    patch.object(module.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                    fixture.setUpClass()
                self.assert_owned_cleanup(commands, fixture)

    def test_ready_control_and_repeated_cleanup_remove_only_once(self):
        for module, fixture in self.fixtures():
            commands = []
            redis = SimpleNamespace(Redis=SimpleNamespace(from_url=Mock(return_value=SimpleNamespace(ping=lambda: True))))
            with self.subTest(module=module.__name__), self.environment(module, redis, commands):
                fixture.setUpClass()
                self.assertFalse(any(c[:2] == ["docker", "rm"] for c in commands))
                fixture.tearDownClass()
                fixture.doClassCleanups()
                self.assert_owned_cleanup(commands, fixture)

    def test_subclass_failure_after_ready_runs_registered_class_cleanup(self):
        for module, base in self.fixtures():
            class FailingSubclass(base):
                @classmethod
                def setUpClass(cls):
                    super().setUpClass()
                    raise RuntimeError("owned subclass setup failure")

                def test_never_runs(self):
                    raise AssertionError("failed setup must prevent test execution")

            commands = []
            redis = SimpleNamespace(Redis=SimpleNamespace(from_url=Mock(return_value=SimpleNamespace(ping=lambda: True))))
            with self.subTest(module=module.__name__), self.environment(module, redis, commands):
                result = unittest.TestResult()
                unittest.TestSuite([FailingSubclass("test_never_runs")]).run(result)
                self.assertEqual(result.testsRun, 0)
                self.assertEqual(len(result.errors), 1)
                self.assertIn("owned subclass setup failure", result.errors[0][1])
                self.assert_owned_cleanup(commands, FailingSubclass)


if __name__ == "__main__":
    unittest.main()
