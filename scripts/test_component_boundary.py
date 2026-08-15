from __future__ import annotations

import unittest

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

    def test_unknown_path_is_not_tooling_only(self) -> None:
        result = component_boundary.classify_paths(self.manifest, ["README.md"])
        self.assertFalse(result["tooling_only"])
        self.assertTrue(result["product_touched"])
        self.assertEqual(result["unknown_paths"], ["README.md"])

    def test_unclassified_service_path_is_not_tooling_only(self) -> None:
        result = component_boundary.classify_paths(
            self.manifest,
            ["services/control-plane/router/main.py"],
        )
        self.assertFalse(result["tooling_only"])
        self.assertTrue(result["product_touched"])


if __name__ == "__main__":
    unittest.main()
