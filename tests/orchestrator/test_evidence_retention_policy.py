from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts import evidence_retention_policy as retention


class EvidenceRetentionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.evidence_root = self.root / "support" / "evidence"
        self.sidecars_root = self.root / "support" / "sidecars"
        self.archive_tasks_dir = self.root / "ai-task-archive" / "tasks"
        self.status_path = self.root / "ai-status.json"
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.write_text(json.dumps({"tasks": []}), encoding="utf-8")
        self.now = "2026-05-19T00:00:00Z"

    def _write_evidence_json(self, task_id: str, payload: dict) -> Path:
        packet_dir = self.evidence_root / task_id
        packet_dir.mkdir(parents=True, exist_ok=True)
        (packet_dir / "packet.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return packet_dir

    def _write_evidence_text(self, task_id: str, filename: str, text: str) -> Path:
        packet_dir = self.evidence_root / task_id
        packet_dir.mkdir(parents=True, exist_ok=True)
        (packet_dir / filename).write_text(text, encoding="utf-8")
        return packet_dir

    def _write_sidecar(self, task_id: str) -> Path:
        packet_dir = self.sidecars_root / task_id
        packet_dir.mkdir(parents=True, exist_ok=True)
        (packet_dir / f"{task_id}-SIDECAR-ACCEPTANCE.md").write_text(f"# {task_id}\n", encoding="utf-8")
        return packet_dir

    def _write_archive_snapshot(self, task_id: str, done_at: str) -> None:
        self.archive_tasks_dir.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "version": 1,
            "task_id": task_id,
            "archived_at": done_at,
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": {
                "id": task_id,
                "status": "done",
                "last_update": done_at,
            },
        }
        (self.archive_tasks_dir / f"{task_id}.json").write_text(
            json.dumps(snapshot, indent=2) + "\n",
            encoding="utf-8",
        )

    def _scan(self) -> retention.RetentionPlan:
        return retention.scan(
            evidence_root=self.evidence_root,
            sidecars_root=self.sidecars_root,
            archive_tasks_dir=self.archive_tasks_dir,
            status_path=self.status_path,
            now=self.now,
        )

    def test_classifies_part_h5_protected_evidence_signals(self) -> None:
        self._write_evidence_json(
            "LIVE-001",
            {
                "stage": "live",
                "rollback_plan": {"target": "runtime-binding-001"},
                "operator_approval": "approved",
            },
        )
        self._write_evidence_text("CANARY-001", "README.md", "canary readiness human-gate packet")
        self._write_evidence_text("POST-001", "postmortem.md", "incident postmortem root cause")
        self._write_evidence_json("PAPER-001", {"stage": "paper", "summary": "ordinary replay evidence"})

        plan = self._scan()
        by_task = {item.task_id: item for item in plan.items if item.kind == retention.KIND_EVIDENCE}

        self.assertEqual(by_task["LIVE-001"].retention_class, retention.RETENTION_PROTECTED_EVIDENCE)
        self.assertEqual(by_task["LIVE-001"].action, retention.ACTION_KEEP)
        self.assertTrue({"live", "rollback", "human_gate"}.issubset(set(by_task["LIVE-001"].signals)))
        self.assertEqual(by_task["CANARY-001"].signals, ("canary", "human_gate"))
        self.assertEqual(by_task["POST-001"].signals, ("postmortem",))
        self.assertEqual(by_task["PAPER-001"].retention_class, retention.RETENTION_STANDARD_EVIDENCE)
        self.assertEqual(by_task["PAPER-001"].action, retention.ACTION_KEEP)

    def test_execute_archives_eligible_sidecars_but_never_modifies_evidence(self) -> None:
        live_evidence = self._write_evidence_json("CANARY-LIVE-001", {"stage": "canary", "rollback": "ready"})
        stale_sidecar = self._write_sidecar("STALE-SIDECAR-001")
        old_sidecar = self._write_sidecar("OLD-SIDECAR-001")
        self._write_archive_snapshot("STALE-SIDECAR-001", "2026-04-20T00:00:00Z")
        self._write_archive_snapshot("OLD-SIDECAR-001", "2026-01-01T00:00:00Z")

        plan = self._scan()
        by_task = {item.task_id: item for item in plan.items if item.kind == retention.KIND_SIDECAR}

        self.assertEqual(by_task["STALE-SIDECAR-001"].action, retention.ACTION_ARCHIVE)
        self.assertEqual(by_task["OLD-SIDECAR-001"].action, retention.ACTION_ARCHIVE)
        self.assertFalse(any(item.action == "delete" for item in plan.items))

        result = retention.execute(plan, dry_run=False)

        self.assertIsInstance(result, retention.ExecutionResult)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(live_evidence.exists())
        self.assertFalse(stale_sidecar.exists())
        self.assertFalse(old_sidecar.exists())
        self.assertTrue((self.sidecars_root / "archived" / "STALE-SIDECAR-001").exists())
        self.assertTrue((self.sidecars_root / "archived" / "OLD-SIDECAR-001").exists())
        self.assertTrue(all(operation.action == retention.ACTION_ARCHIVE for operation in result.operations))

    def test_cli_dry_run_reports_protected_evidence_and_sidecar_archive(self) -> None:
        self._write_evidence_text("HUMAN-GATE-001", "summary.md", "human gate approval and rollback drill")
        self._write_sidecar("STALE-SIDECAR-001")
        self._write_archive_snapshot("STALE-SIDECAR-001", "2026-04-20T00:00:00Z")
        stdout = io.StringIO()

        exit_code = retention.main(
            [
                "--evidence-root",
                str(self.evidence_root),
                "--sidecars-root",
                str(self.sidecars_root),
                "--archive-tasks-dir",
                str(self.archive_tasks_dir),
                "--status-path",
                str(self.status_path),
                "--now",
                self.now,
            ],
            stdout=stdout,
        )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["plan"]["counts"][retention.RETENTION_PROTECTED_EVIDENCE], 1)
        self.assertEqual(payload["plan"]["counts"][retention.ACTION_ARCHIVE], 1)
        self.assertTrue((self.sidecars_root / "STALE-SIDECAR-001").exists())


if __name__ == "__main__":
    unittest.main()
