"""Prove branch-ci's smoke job selects every applicable independent gate.

DTG-CI-01: the workflow must run the tooling core gate, the tooling
integration-authority gate, and the product/mixed gate independently rather
than as a single either/or choice, so a mixed tooling+product diff runs both
tooling gates *and* product smoke instead of skipping one.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

from scripts import component_boundary

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "branch-ci.yml"

STEP_NAMES = (
    "Run tooling smoke gate",
    "Run tooling integration-authority gate",
    "Run product or mixed smoke gate",
)


def _load_smoke_step_conditions() -> dict[str, str]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    smoke_steps = workflow["jobs"]["smoke"]["steps"]
    conditions = {}
    for step in smoke_steps:
        name = step.get("name")
        if name in STEP_NAMES:
            conditions[name] = step["if"]
    return conditions


def _evaluate_condition(expr: str, outputs: dict[str, bool]) -> bool:
    """Evaluate the subset of GitHub Actions expression syntax this workflow uses."""
    py_expr = expr
    for key, value in outputs.items():
        token = f"steps.boundary.outputs.{key}"
        py_expr = py_expr.replace(token, repr(str(value).lower()))
    py_expr = py_expr.replace("||", " or ").replace("&&", " and ")
    if re.search(r"steps\.boundary\.outputs\.\w+", py_expr):
        raise AssertionError(f"unresolved output token in: {py_expr}")
    return bool(eval(py_expr))  # noqa: S307 - controlled input from our own workflow file


class BranchCiGateSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = component_boundary.load_manifest()
        cls.conditions = _load_smoke_step_conditions()

    def test_all_three_named_steps_present(self) -> None:
        self.assertEqual(set(self.conditions), set(STEP_NAMES))

    def test_mixed_tooling_and_product_diff_selects_both_tooling_gates_and_product_smoke(
        self,
    ) -> None:
        result = component_boundary.classify_paths(
            self.manifest,
            [".orchestrator/supervisor.py", "services/trade_journey/lifecycle_projector.py"],
        )
        for name in STEP_NAMES:
            with self.subTest(step=name):
                self.assertTrue(_evaluate_condition(self.conditions[name], result))

    def test_unknown_only_diff_falls_through_to_product_or_mixed_gate(self) -> None:
        result = component_boundary.classify_paths(self.manifest, ["totally/unknown/path.bin"])
        self.assertFalse(_evaluate_condition(self.conditions["Run tooling smoke gate"], result))
        self.assertFalse(
            _evaluate_condition(self.conditions["Run tooling integration-authority gate"], result)
        )
        self.assertTrue(
            _evaluate_condition(self.conditions["Run product or mixed smoke gate"], result)
        )

    def test_tooling_only_diff_skips_product_gate(self) -> None:
        result = component_boundary.classify_paths(self.manifest, [".orchestrator/supervisor.py"])
        self.assertTrue(_evaluate_condition(self.conditions["Run tooling smoke gate"], result))
        self.assertFalse(
            _evaluate_condition(self.conditions["Run product or mixed smoke gate"], result)
        )


if __name__ == "__main__":
    unittest.main()
