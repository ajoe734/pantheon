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

    def test_git_integration_authority_path_is_not_unknown(self) -> None:
        result = component_boundary.classify_paths(
            self.manifest,
            ["scripts/git/auto_integrator.py"],
        )
        self.assertEqual(result["unknown_paths"], [])
        self.assertTrue(result["development_tooling_touched"])
        self.assertTrue(result["tooling_only"])


TOOLING_PATH = ".orchestrator/supervisor.py"
PRODUCT_PATH = "services/trade_journey/lifecycle_projector.py"
DELIVERY_PATH = ".github/workflows/branch-ci.yml"
UNKNOWN_PATH = "docs/some-note.md"
INTEGRATOR_PATH = "scripts/git/auto_integrator.py"

# (case name, paths, expected independent booleans)
DOMAIN_UNION_CASES: list[tuple[str, list[str], dict[str, bool]]] = [
    (
        "tooling_only",
        [TOOLING_PATH],
        {
            "development_tooling_touched": True,
            "product_touched": False,
            "delivery_touched": False,
            "tooling_only": True,
        },
    ),
    (
        "product_only",
        [PRODUCT_PATH],
        {
            "development_tooling_touched": False,
            "product_touched": True,
            "delivery_touched": False,
            "tooling_only": False,
        },
    ),
    (
        "delivery_only",
        [DELIVERY_PATH],
        {
            "development_tooling_touched": False,
            "product_touched": False,
            "delivery_touched": True,
            "tooling_only": True,
        },
    ),
    (
        "tooling_and_product",
        [TOOLING_PATH, PRODUCT_PATH],
        {
            "development_tooling_touched": True,
            "product_touched": True,
            "delivery_touched": False,
            "tooling_only": False,
        },
    ),
    (
        "tooling_and_delivery",
        [TOOLING_PATH, DELIVERY_PATH],
        {
            "development_tooling_touched": True,
            "product_touched": False,
            "delivery_touched": True,
            "tooling_only": True,
        },
    ),
    (
        "product_and_delivery",
        [PRODUCT_PATH, DELIVERY_PATH],
        {
            "development_tooling_touched": False,
            "product_touched": True,
            "delivery_touched": True,
            "tooling_only": False,
        },
    ),
    (
        "all_three_domains",
        [TOOLING_PATH, PRODUCT_PATH, DELIVERY_PATH],
        {
            "development_tooling_touched": True,
            "product_touched": True,
            "delivery_touched": True,
            "tooling_only": False,
        },
    ),
    (
        "docs_only",
        [UNKNOWN_PATH],
        {
            "development_tooling_touched": False,
            "product_touched": False,
            "delivery_touched": False,
            "tooling_only": True,
        },
    ),
    (
        "unknown_only",
        ["totally/unrecognized/path.bin"],
        {
            "development_tooling_touched": False,
            "product_touched": False,
            "delivery_touched": False,
            "tooling_only": True,
        },
    ),
    (
        "git_integrator_alone",
        [INTEGRATOR_PATH],
        {
            "development_tooling_touched": True,
            "product_touched": False,
            "delivery_touched": False,
            "tooling_only": True,
        },
    ),
]


class ComponentBoundaryDomainUnionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = component_boundary.load_manifest()

    def test_domain_union_matrix(self) -> None:
        for name, paths, expected in DOMAIN_UNION_CASES:
            with self.subTest(case=name):
                result = component_boundary.classify_paths(self.manifest, paths)
                for key, value in expected.items():
                    self.assertEqual(
                        result[key],
                        value,
                        msg=f"{name}: expected {key}={value}, got {result[key]} ({result})",
                    )


if __name__ == "__main__":
    unittest.main()
