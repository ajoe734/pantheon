"""DTG-CLEAN-M1 characterization tests for the standalone status-projection
module -- not a re-test of scripts/test_ai_status.py's extensive coverage
(which already exercises this exact code through ai_status.py's re-export
and continues to pass unchanged), but proof that this module is genuinely
usable on its own: no circular import, and the lazy ai_status handback
resolves for the functions that need shared infrastructure.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import status_projection


class StatusProjectionModuleTests(unittest.TestCase):
    def test_module_imports_with_no_circular_dependency(self) -> None:
        # A second, independent interpreter-level import path: importing
        # ai_status first (which itself imports this module at its own
        # top level) must not raise, proving the dependency graph is a DAG
        # (ai_status -> status_projection -> {common, task_archive}, with
        # the reverse edge only ever taken lazily, at call time).
        import ai_status  # noqa: F401
        import importlib

        importlib.reload(status_projection)

    def test_lazy_ai_status_handback_resolves(self) -> None:
        ai_status = status_projection._ai_status_module()
        self.assertTrue(hasattr(ai_status, "canonical_agent_name"))
        self.assertTrue(hasattr(ai_status, "load_config"))
        self.assertTrue(hasattr(ai_status, "task_resolver"))

    def test_pure_rendering_helpers_work_standalone(self) -> None:
        self.assertEqual(status_projection.display_task_title({"title": "X"}), "X")
        self.assertEqual(
            status_projection.display_task_status({"status": "in_progress"}), "in_progress"
        )
        self.assertEqual(status_projection.task_status_is_nonterminal({"status": "todo"}), True)
        self.assertEqual(status_projection.task_status_is_nonterminal({"status": "done"}), False)

    def test_entry_points_are_exported(self) -> None:
        for name in (
            "write_current_work",
            "build_dashboard_bundle",
            "write_dashboard_bundle",
            "dashboard_orchestrator_state",
            "sync_docs_site",
        ):
            self.assertTrue(callable(getattr(status_projection, name)), name)


if __name__ == "__main__":
    unittest.main()
