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

WORKFLOW_CONTRACT_STEP_NAME = "Verify workflow contract"
EXPECTED_CONTRACT_TEST_COMMAND = "python3 -m unittest scripts.test_branch_ci_gate_selection"
EXPECTED_SMOKE_JOB_TIMEOUT_MINUTES = 30


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _load_smoke_step_conditions() -> dict[str, str]:
    workflow = _load_workflow()
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
        cls.workflow = _load_workflow()
        cls.smoke_job = cls.workflow["jobs"]["smoke"]
        cls.smoke_steps = cls.smoke_job["steps"]
        cls.conditions = _load_smoke_step_conditions()

    def test_smoke_job_timeout_minutes_is_30(self) -> None:
        self.assertEqual(
            self.smoke_job.get("timeout-minutes"),
            EXPECTED_SMOKE_JOB_TIMEOUT_MINUTES,
        )
        self.assertNotIn("continue-on-error", self.smoke_job)

    def test_workflow_contract_step_present_and_unconditional(self) -> None:
        contract_step = next(
            (s for s in self.smoke_steps if s.get("name") == WORKFLOW_CONTRACT_STEP_NAME),
            None,
        )
        self.assertIsNotNone(
            contract_step,
            f"Step {WORKFLOW_CONTRACT_STEP_NAME!r} not found in smoke job steps",
        )
        self.assertNotIn(
            "if",
            contract_step,
            f"Step {WORKFLOW_CONTRACT_STEP_NAME!r} must execute unconditionally without 'if'",
        )
        self.assertFalse(
            contract_step.get("continue-on-error", False),
            f"Step {WORKFLOW_CONTRACT_STEP_NAME!r} must not have continue-on-error",
        )

    def test_workflow_contract_step_exact_command(self) -> None:
        contract_step = next(
            (s for s in self.smoke_steps if s.get("name") == WORKFLOW_CONTRACT_STEP_NAME),
            None,
        )
        self.assertIsNotNone(
            contract_step,
            f"Step {WORKFLOW_CONTRACT_STEP_NAME!r} not found in smoke job steps",
        )
        command = contract_step.get("run", "").strip()
        self.assertEqual(command, EXPECTED_CONTRACT_TEST_COMMAND)

    def test_workflow_contract_step_placed_after_dependency_installation(self) -> None:
        names = [s.get("name") for s in self.smoke_steps]
        self.assertIn("Install deps (best-effort)", names)
        self.assertIn(WORKFLOW_CONTRACT_STEP_NAME, names)
        deps_index = names.index("Install deps (best-effort)")
        contract_index = names.index(WORKFLOW_CONTRACT_STEP_NAME)
        self.assertGreater(contract_index, deps_index)

    def test_existing_step_timeouts_preserved(self) -> None:
        step_by_name = {s.get("name"): s for s in self.smoke_steps if s.get("name")}
        self.assertEqual(
            step_by_name.get("Provision real worker sandbox", {}).get("timeout-minutes"),
            5,
        )
        self.assertEqual(
            step_by_name.get("Verify real worker sandbox boundaries", {}).get("timeout-minutes"),
            3,
        )

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
