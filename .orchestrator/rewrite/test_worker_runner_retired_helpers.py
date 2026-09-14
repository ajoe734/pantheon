"""Retired runner shortcuts must not replace canonical entry validation."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


class WorkerRunnerRetiredHelpersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = Path(__file__).resolve().parents[1] / "worker_runner.py"
        cls.tree = ast.parse(path.read_text(encoding="utf-8"))
        cls.functions = {
            node.name: node for node in cls.tree.body
            if isinstance(node, ast.FunctionDef)
        }

    def test_retired_shortcuts_have_no_definition_or_reference(self) -> None:
        retired = {
            "_get_task_roles", "execution_authorization_still_current",
            "ensure_execution_authorized_before_launch",
        }
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol = node.name
            elif isinstance(node, ast.Name):
                symbol = node.id
            elif isinstance(node, ast.Attribute):
                symbol = node.attr
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                symbol = node.value
            else:
                continue
            self.assertNotIn(symbol, retired, f"retired symbol at line {node.lineno}")

    def test_main_reuses_entry_binding_for_running_checks(self) -> None:
        calls = [
            node for node in ast.walk(self.functions["main"])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "validate_worker_entry_binding"
        ]
        self.assertTrue(any(
            any(keyword.arg == "entry" and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False for keyword in node.keywords)
            for node in calls
        ))
        self.assertTrue(any(
            not any(keyword.arg == "entry" for keyword in node.keywords)
            for node in calls
        ))


if __name__ == "__main__":
    unittest.main()
