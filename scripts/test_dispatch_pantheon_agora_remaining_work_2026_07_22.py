from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


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
        self.assertIn('_run_governed_status_command(command', source)

    def test_mutation_validation_refuses_archived_task_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "ai-status.json").write_text('{"tasks":[]}\n', encoding="utf-8")
            archive_dir = root / "ai-task-archive" / "tasks"
            archive_dir.mkdir(parents=True)
            (archive_dir / "OPS-DISPATCH-LEASE-SYNC-001.json").write_text(
                '{"task_id":"OPS-DISPATCH-LEASE-SYNC-001"}\n',
                encoding="utf-8",
            )

            errors = MODULE.validate_specs(root, refuse_archived_reuse=True)

        self.assertIn(
            "OPS-DISPATCH-LEASE-SYNC-001: archived task ID cannot be reused by bulk dispatch",
            errors,
        )

    def test_register_refuses_terminal_task_before_archive_projection(self) -> None:
        task = MODULE.TASKS[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "ai-status.json").write_text(
                json.dumps({"tasks": [{"id": task["id"], "status": "done"}]}),
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {MODULE.STATUS_ROOT_ENV: str(root)}),
                mock.patch.object(MODULE, "_run_status") as run_status,
            ):
                with self.assertRaisesRegex(RuntimeError, "terminal task status done"):
                    MODULE._register(task)

        run_status.assert_not_called()

    def test_run_status_uses_pinned_governed_command_runtime(self) -> None:
        task = MODULE.TASKS[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            script = root / "command-root" / "scripts" / "ai_status.py"
            script.parent.mkdir(parents=True)
            script.write_text("# governed runtime\n", encoding="utf-8")
            status_root = root / "status-root"
            status_root.mkdir()
            context_env = {"AI_NAME": "Human/Ops", MODULE.STATUS_ROOT_ENV: str(status_root)}
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            with (
                mock.patch.object(
                    MODULE,
                    "_governed_status_context",
                    return_value=(script, status_root, context_env),
                ),
                mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run,
            ):
                MODULE._run_status(task, "note", task["id"], "checkpoint")

        self.assertEqual(
            run.call_args.args[0],
            [sys.executable, str(script), "note", task["id"], "checkpoint"],
        )
        self.assertEqual(run.call_args.kwargs["cwd"], status_root)
        self.assertEqual(run.call_args.kwargs["env"]["AI_NAME"], "Human/Ops")
        self.assertEqual(run.call_args.kwargs["env"][MODULE.STATUS_ROOT_ENV], str(status_root))

    def test_governed_context_requires_authoritative_store(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "PANTHEON_TASK_STATE_STORE_MODE=authoritative"):
                MODULE._governed_status_context()

    def test_required_absolute_path_rejects_symlink_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "target"
            target.mkdir()
            link = root / "linked"
            link.symlink_to(target, target_is_directory=True)
            with mock.patch.dict(os.environ, {MODULE.STATUS_ROOT_ENV: str(link)}):
                with self.assertRaisesRegex(RuntimeError, "cannot include a symlink component"):
                    MODULE._required_absolute_path(MODULE.STATUS_ROOT_ENV)

    def test_governed_context_rejects_dirty_runtime_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            command_root = root / "command-root"
            script = command_root / "scripts" / "ai_status.py"
            script.parent.mkdir(parents=True)
            script.write_text("# governed runtime\n", encoding="utf-8")
            status_root = root / "status-root"
            status_root.mkdir()
            event_log = root / "events.jsonl"
            event_log.write_text("", encoding="utf-8")
            env = {
                MODULE.TASK_STATE_STORE_MODE_ENV: "authoritative",
                MODULE.TASK_STATE_EVENT_LOG_ENV: str(event_log),
                MODULE.STATUS_ROOT_ENV: str(status_root),
                MODULE.COMMAND_ROOT_ENV: str(command_root),
                MODULE.COMMAND_SHA_ENV: "a" * 40,
            }
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(
                    MODULE,
                    "validate_status_command_runtime",
                    return_value={"root": str(command_root)},
                ),
                mock.patch.object(
                    MODULE,
                    "_dirty_command_runtime_files",
                    return_value=["scripts/ai_status.py"],
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "dirty executable/import files"):
                    MODULE._governed_status_context()


if __name__ == "__main__":
    unittest.main()
