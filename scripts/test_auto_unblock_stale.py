from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("auto_unblock_stale.py")
SPEC = importlib.util.spec_from_file_location("auto_unblock_stale", MODULE_PATH)
assert SPEC and SPEC.loader
auto_unblock = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auto_unblock)


class AutoUnblockStaleTests(unittest.TestCase):
    def _run(self, payload: dict) -> tuple[int, mock.Mock]:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            state_file = root / ".orchestrator" / "auto-unblock-state.json"
            state_file.parent.mkdir(parents=True)
            status_file.write_text(json.dumps(payload), encoding="utf-8")
            run = mock.Mock(return_value=mock.Mock(returncode=0, stderr="", stdout=""))
            with (
                mock.patch.object(auto_unblock, "ROOT", root),
                mock.patch.object(auto_unblock, "STATUS_FILE", status_file),
                mock.patch.object(auto_unblock, "ARCHIVE_DIR", root / "archive"),
                mock.patch.object(auto_unblock, "STATE_FILE", state_file),
                mock.patch.object(auto_unblock, "AI_STATUS_CLI", root / "scripts" / "ai_status.py"),
                mock.patch.object(auto_unblock, "DRY_RUN", False),
                mock.patch.object(auto_unblock, "_archived_ids", return_value=set()),
                mock.patch.object(auto_unblock, "_running_task_ids", return_value=set()),
                mock.patch.object(auto_unblock.subprocess, "run", run),
            ):
                result = auto_unblock.main()
            return result, run

    @staticmethod
    def _blocked_task(**overrides: object) -> dict:
        task = {
            "id": "OPS-STALE-001",
            "status": "blocked",
            "owner": "Codex",
            "depends_on": [],
            "last_update": "2020-01-01T00:00:00Z",
        }
        task.update(overrides)
        return task

    def test_keeps_actor_waiting_gate_blocked(self) -> None:
        result, run = self._run(
            {"tasks": [self._blocked_task(waiting_for="Claude")], "blockers": []}
        )

        self.assertEqual(result, 0)
        run.assert_not_called()

    def test_keeps_task_with_open_blocker_blocked(self) -> None:
        result, run = self._run(
            {
                "tasks": [self._blocked_task()],
                "blockers": [
                    {"task_id": "OPS-STALE-001", "status": "open", "waiting_for": "Human/Ops"}
                ],
            }
        )

        self.assertEqual(result, 0)
        run.assert_not_called()

    def test_reopens_after_waited_task_and_formal_dependencies_are_done(self) -> None:
        result, run = self._run(
            {
                "tasks": [
                    {"id": "OPS-DEP-001", "status": "done"},
                    self._blocked_task(
                        depends_on=["OPS-DEP-001"],
                        waiting_for="OPS-DEP-001",
                    ),
                ],
                "blockers": [],
            }
        )

        self.assertEqual(result, 0)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][-3:-1], ["reopen", "OPS-STALE-001"])


if __name__ == "__main__":
    unittest.main()
