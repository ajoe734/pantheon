"""DTG-CLEAN-M4 characterization tests for the standalone worker-workspace
filesystem module -- not a re-test of .orchestrator/test_supervisor.py's
extensive coverage (which already exercises this exact code through
supervisor.py's re-export and continues to pass unchanged), but proof that
this module is genuinely usable on its own: no circular import, and the
lazy supervisor handback resolves for the handful of symbols supervisor.py
still owns.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import worker_workspace


class WorkerWorkspaceModuleTests(unittest.TestCase):
    def test_module_imports_with_no_circular_dependency(self) -> None:
        # supervisor.py imports this module at its own top level; importing
        # supervisor here (a second, independent path into the same
        # dependency graph) must not raise, proving the graph is a DAG
        # (supervisor -> worker_workspace -> {common, dispatch_policy,
        # multi_repo_registry, adapters.base}, with the reverse edge only
        # ever taken lazily, at call time, via _supervisor_module()).
        import supervisor  # noqa: F401

    def test_lazy_supervisor_handback_resolves(self) -> None:
        supervisor = worker_workspace._supervisor_module()
        self.assertTrue(hasattr(supervisor, "write_activity_log"))
        self.assertTrue(hasattr(supervisor, "pid_is_alive"))
        self.assertTrue(hasattr(supervisor, "parse_runtime_timestamp"))
        self.assertTrue(hasattr(supervisor, "materialize_worker_context_files"))
        self.assertTrue(hasattr(supervisor, "bind_external_worker_context"))

    def test_pure_helpers_work_standalone(self) -> None:
        self.assertEqual(worker_workspace._task_id_slug("REG-002"), "reg-002")
        self.assertEqual(worker_workspace._task_id_slug(None), "unknown-task")
        self.assertEqual(
            worker_workspace.worker_task_branch({}, "REG-002"), "task/REG-002"
        )
        clean, dirty_paths = worker_workspace._classify_worktree_dirt("")
        self.assertEqual(clean, "clean")
        self.assertEqual(dirty_paths, [])

    def test_settings_helpers_apply_defaults(self) -> None:
        settings = worker_workspace.worktree_cleanup_settings({})
        self.assertTrue(settings["enabled"])
        self.assertTrue(settings["cleanup_inactive_leases"])
        self.assertGreater(settings["orphan_prune_interval_seconds"], 0)

    def test_entry_points_are_exported(self) -> None:
        for name in (
            "prepare_worker_workspace",
            "cleanup_inactive_worker_worktrees",
            "prune_orphan_worktrees",
            "active_worker_workspace_roots",
            "validate_worker_workspace_binding",
        ):
            self.assertTrue(callable(getattr(worker_workspace, name)), name)


if __name__ == "__main__":
    unittest.main()
