#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import common


class PlanningSharedFilesTests(unittest.TestCase):
    def test_planning_shared_files_follow_active_session_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            planning_dir = root / "docs" / "02-architecture" / "consensus" / "sessions" / "phase3-test"
            planning_dir.mkdir(parents=True)
            readme = planning_dir / "README.md"
            session_file = planning_dir / "planning-session.json"
            state_file = root / ".orchestrator" / "planning-state.json"
            state_file.parent.mkdir(parents=True)
            readme.write_text("# phase3\n", encoding="utf-8")
            session_file.write_text("{}", encoding="utf-8")
            state_file.write_text(
                json.dumps(
                    {
                        "status": "active",
                        "session_file": str(session_file),
                        "artifacts": {
                            "planning_readme": {
                                "path": str(readme),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(common, "PLANNING_STATE_PATH", state_file):
                files = common.planning_shared_files()

        self.assertEqual(files, [readme, session_file])


if __name__ == "__main__":
    unittest.main()
