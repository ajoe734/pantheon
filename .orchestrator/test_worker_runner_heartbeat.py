"""Tests for worker_runner heartbeat agent/task-id derivation (observability fix)."""
import importlib.util, os, unittest
_P = os.path.join(os.path.dirname(__file__), "worker_runner.py")
_spec = importlib.util.spec_from_file_location("worker_runner", _P)
wr = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(wr)


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


if __name__ == "__main__":
    unittest.main()
