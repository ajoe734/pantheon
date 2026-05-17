from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import workflow_health_check


NOW = "2026-05-17T12:00:00Z"


def assert_finding_shape(testcase: unittest.TestCase, finding: dict[str, object], expected_type: str) -> None:
    for key in ("finding_id", "type", "severity", "recommended_action", "evidence_refs", "detected_at"):
        testcase.assertIn(key, finding)
    testcase.assertEqual(finding["type"], expected_type)
    testcase.assertTrue(str(finding["finding_id"]).startswith(f"workflow-health:{expected_type}:"))
    testcase.assertIsInstance(finding["evidence_refs"], list)
    testcase.assertEqual(finding["detected_at"], NOW)


class TaskPrStaleTests(unittest.TestCase):
    def test_task_pr_stale_uses_gh_api_and_emits_finding(self) -> None:
        calls: list[list[str]] = []

        def fake_gh(args: list[str]) -> object:
            calls.append(args)
            return [
                {
                    "number": 101,
                    "title": "Old task",
                    "updated_at": "2026-05-16T08:30:00Z",
                    "html_url": "https://github.example/pr/101",
                    "head": {"ref": "task/OLD-001"},
                },
                {
                    "number": 102,
                    "title": "Fresh task",
                    "updated_at": "2026-05-17T10:30:00Z",
                    "head": {"ref": "task/FRESH-001"},
                },
                {
                    "number": 103,
                    "title": "Non task",
                    "updated_at": "2026-05-16T08:30:00Z",
                    "head": {"ref": "feature/not-task"},
                },
            ]

        findings = workflow_health_check.check_task_pr_stale(
            now=NOW,
            repo="ajoe734/pantheon",
            gh=fake_gh,
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(calls[0][0], "/repos/ajoe734/pantheon/pulls")
        self.assertIn("--paginate", calls[0])
        assert_finding_shape(self, findings[0], "task_pr_stale")
        self.assertEqual(findings[0]["evidence"]["number"], 101)

    def test_task_pr_fresh_is_not_reported(self) -> None:
        findings = workflow_health_check.check_task_pr_stale(
            now=NOW,
            prs=[
                {
                    "number": 201,
                    "updated_at": "2026-05-17T00:30:00Z",
                    "head": {"ref": "task/FRESH-ONLY"},
                }
            ],
        )

        self.assertEqual(findings, [])


class DevPublishStaleTests(unittest.TestCase):
    def test_dev_publish_stale_when_dev_advanced_past_threshold(self) -> None:
        findings = workflow_health_check.check_dev_publish_stale(
            now=NOW,
            dev_latest_commit_at="2026-05-16T08:00:00Z",
            last_publish_at="2026-05-16T07:00:00Z",
        )

        self.assertEqual(len(findings), 1)
        assert_finding_shape(self, findings[0], "dev_publish_stale")
        self.assertEqual(findings[0]["evidence"]["dev_latest_commit_at"], "2026-05-16T08:00:00Z")
        self.assertEqual(findings[0]["evidence"]["last_publish_at"], "2026-05-16T07:00:00Z")

    def test_dev_publish_fresh_when_publish_covers_dev_commit(self) -> None:
        findings = workflow_health_check.check_dev_publish_stale(
            now=NOW,
            dev_latest_commit_at="2026-05-16T08:00:00Z",
            last_publish_at="2026-05-16T09:00:00Z",
        )

        self.assertEqual(findings, [])

    def test_dev_publish_fresh_when_dev_advance_is_recent(self) -> None:
        findings = workflow_health_check.check_dev_publish_stale(
            now=NOW,
            dev_latest_commit_at="2026-05-17T10:00:00Z",
            last_publish_at="2026-05-16T09:00:00Z",
        )

        self.assertEqual(findings, [])


class PublishPromoteStaleTests(unittest.TestCase):
    def test_publish_promote_stale_when_unpromoted_past_window(self) -> None:
        findings = workflow_health_check.check_publish_promote_stale(
            window_hours=3,
            now=NOW,
            last_publish_at="2026-05-17T08:00:00Z",
            master_promoted_at="2026-05-17T07:00:00Z",
            version="v2026.05.17.0",
        )

        self.assertEqual(len(findings), 1)
        assert_finding_shape(self, findings[0], "publish_promote_stale")
        self.assertEqual(findings[0]["evidence"]["version"], "v2026.05.17.0")

    def test_publish_promote_fresh_when_master_is_promoted(self) -> None:
        findings = workflow_health_check.check_publish_promote_stale(
            window_hours=3,
            now=NOW,
            last_publish_at="2026-05-17T08:00:00Z",
            master_promoted_at="2026-05-17T09:00:00Z",
            version="v2026.05.17.0",
        )

        self.assertEqual(findings, [])

    def test_publish_promote_fresh_inside_window(self) -> None:
        findings = workflow_health_check.check_publish_promote_stale(
            window_hours=6,
            now=NOW,
            last_publish_at="2026-05-17T08:00:00Z",
            master_promoted_at=None,
            version="v2026.05.17.0",
        )

        self.assertEqual(findings, [])


class ReportTests(unittest.TestCase):
    def test_build_report_can_use_offline_inputs_without_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task_pr_json = root / "prs.json"
            task_pr_json.write_text(
                """
                [
                  {
                    "number": 301,
                    "updated_at": "2026-05-16T06:00:00Z",
                    "head": {"ref": "task/STALE-301"}
                  }
                ]
                """,
                encoding="utf-8",
            )

            report = workflow_health_check.build_report(
                now=NOW,
                task_pr_json=task_pr_json,
                dev_latest_commit_at="2026-05-16T08:00:00Z",
                last_publish_at="2026-05-16T07:00:00Z",
                master_promoted_at=None,
                publish_promote_window_hours=3,
            )

        self.assertFalse(report["ok"])
        self.assertEqual(report["finding_count"], 3)
        self.assertEqual(
            [finding["type"] for finding in report["findings"]],
            ["task_pr_stale", "dev_publish_stale", "publish_promote_stale"],
        )


if __name__ == "__main__":
    unittest.main()
