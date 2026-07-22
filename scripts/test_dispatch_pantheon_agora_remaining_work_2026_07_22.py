from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dispatch_pantheon_agora_remaining_work_2026-07-22.py"
SPEC = importlib.util.spec_from_file_location("dispatch_pantheon_agora_remaining_work", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PacketTests(unittest.TestCase):
    def test_specs_are_valid_against_repository_state(self) -> None:
        self.assertEqual(MODULE.validate_specs(ROOT), [])

    def test_reuses_only_the_two_active_closeout_ids(self) -> None:
        existing = {task["id"] for task in MODULE.TASKS if task.get("existing")}
        self.assertEqual(existing, {"PPL-ALLOC-009", "TJ-E2E-012"})
        ids = {task["id"] for task in MODULE.TASKS}
        self.assertNotIn("AG-GAP-005", ids)

    def test_new_cross_repository_work_is_split(self) -> None:
        for task in MODULE.TASKS:
            if task.get("existing"):
                continue
            self.assertIn(task.get("repository_id"), {"pantheon", "execute_plans"})
            self.assertNotEqual(task.get("target_repo"), "pantheon+execute-plans")
            if task.get("repository_id") == "execute_plans":
                self.assertTrue(
                    any(str(path).startswith("execute-plans/") for path in task["artifacts"]),
                    task["id"],
                )

    def test_bootstrap_then_frontier_fits_enabled_owner_capacity(self) -> None:
        counts = {"Codex": 0, "Codex2": 0, "Claude": 0, "Antigravity": 0}
        initial = []
        for task in MODULE.TASKS:
            if task.get("existing"):
                continue
            deps = task.get("depends_on") or []
            if not deps:
                initial.append(task["id"])
            if deps == ["OPS-DISPATCH-LEASE-SYNC-001"]:
                counts[task["owner"]] += 1
        self.assertEqual(initial, ["OPS-DISPATCH-LEASE-SYNC-001"])
        self.assertEqual(
            counts,
            {"Codex": 2, "Codex2": 2, "Claude": 2, "Antigravity": 2},
        )

    def test_ep5_live_work_is_not_dispatched(self) -> None:
        self.assertFalse(any("EP5" in task["id"] for task in MODULE.TASKS))

    def test_dispatcher_does_not_directly_write_canonical_state(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("ai-status.json\").write", source)
        self.assertNotIn("ai-activity-log.jsonl\").write", source)
        self.assertIn('"scripts/ai_status.py", command', source)


if __name__ == "__main__":
    unittest.main()
