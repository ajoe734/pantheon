from __future__ import annotations

import unittest
from unittest import mock

from scripts import component_boundary


class ComponentBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = component_boundary.load_manifest()

    def test_tooling_paths_do_not_select_product_runtime(self) -> None:
        result = component_boundary.classify_paths(
            self.manifest,
            [".orchestrator/supervisor.py", "scripts/ai_status.py"],
        )
        self.assertTrue(result["tooling_only"])
        self.assertEqual(result["domains"], ["development_tooling"])

    def test_product_path_selects_product_runtime(self) -> None:
        result = component_boundary.classify_paths(
            self.manifest,
            ["services/trade_journey/lifecycle_projector.py"],
        )
        self.assertFalse(result["tooling_only"])
        self.assertEqual(result["domains"], ["product_runtime"])

    def test_mixed_change_is_not_tooling_only(self) -> None:
        result = component_boundary.classify_paths(
            self.manifest,
            [".orchestrator/supervisor.py", "services/trade_journey/lifecycle_projector.py"],
        )
        self.assertFalse(result["tooling_only"])
        self.assertEqual(result["domains"], ["development_tooling", "product_runtime"])

    def test_unknown_path_is_reported_without_becoming_product_runtime(self) -> None:
        result = component_boundary.classify_paths(self.manifest, ["README.md"])
        self.assertTrue(result["tooling_only"])
        self.assertEqual(result["unknown_paths"], ["README.md"])

    def test_main_rejects_missing_selectors(self) -> None:
        with self.assertRaises(SystemExit):
            component_boundary.main([])

    def test_main_accepts_base_and_head_that_diff_to_no_files(self) -> None:
        # A no-op push (e.g. a commit-message-only amend) legitimately diffs to
        # zero changed files; that must not be treated the same as the caller
        # forgetting to supply --path/--base/--head.
        with mock.patch.object(component_boundary, "changed_paths", return_value=[]):
            exit_code = component_boundary.main(["--base", "aaa", "--head", "bbb", "--json"])
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
