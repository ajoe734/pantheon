from __future__ import annotations

import json
import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_task_targets


class TaskTargetScanTests(unittest.TestCase):
    def scan_one(self, filename: str, content: str) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            paths = check_task_targets.resolve_scan_paths(root, [filename])
            return check_task_targets.scan_paths(
                root,
                paths,
                check_task_targets.DEFAULT_REQUIRED_ADR_OVERRIDE,
            )

    def test_json_p0_wrong_repo_target_fails_without_override(self) -> None:
        report = self.scan_one(
            "tasks.json",
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "P0-BOOT-001",
                            "phase": "Pantheon P0 Paper Loop",
                            "target_repo": "ajoe734/lean-platform",
                        }
                    ]
                }
            ),
        )

        self.assertFalse(report["ok"])
        self.assertEqual(len(report["violations"]), 1)

    def test_json_p0_wrong_repo_target_passes_with_migration_override(self) -> None:
        report = self.scan_one(
            "tasks.json",
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "P0-MIGRATION-001",
                            "phase": "Pantheon P0 Paper Loop",
                            "target_repo": "ajoe734/lean-platform",
                            "migration_only": True,
                            "adr_override": "ADR-EXEC-001-revision",
                        }
                    ]
                }
            ),
        )

        self.assertTrue(report["ok"])

    def test_markdown_p0_repo_line_fails_without_override(self) -> None:
        report = self.scan_one(
            "brief.md",
            "\n".join(
                [
                    "# Task Brief",
                    "task_id: P0-LEAN-001",
                    "phase: Pantheon P0 Paper Loop",
                    "repo: ajoe734/lean-platform",
                ]
            ),
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["violations"][0]["line"], 4)

    def test_narrative_lean_platform_guard_text_is_not_a_target(self) -> None:
        report = self.scan_one(
            "brief.md",
            "\n".join(
                [
                    "# Task Brief",
                    "task_id: P0-CI-BRIDGE-001",
                    "Fail if a P0 execution task targets lean-platform without override.",
                    "acceptance: P0 lean-platform target fails without migration_only and ADR override",
                ]
            ),
        )

        self.assertTrue(report["ok"])


if __name__ == "__main__":
    unittest.main()
