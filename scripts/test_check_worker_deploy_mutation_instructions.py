#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import check_worker_deploy_mutation_instructions as policy


class WorkerInstructionScannerTests(unittest.TestCase):
    def _scan(self, text: str) -> list[policy.Finding]:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "worker.md"
            path.write_text(text, encoding="utf-8")
            return policy.scan_paths((path,))

    def test_rejects_copy_pastable_workflow_disable_in_fence(self) -> None:
        findings = self._scan("```bash\ngh workflow disable 123 --repo owner/repo\n```\n")
        self.assertEqual(len(findings), 1)

    def test_rejects_copy_pastable_run_cancel(self) -> None:
        findings = self._scan("Run this now: gh run cancel 123 --repo owner/repo\n")
        self.assertEqual(len(findings), 1)

    def test_rejects_mutation_with_group_flag_before_action(self) -> None:
        findings = self._scan(
            "```bash\ngh workflow --repo owner/repo disable 123\ngh run -R owner/repo cancel 456\n```\n"
        )
        self.assertEqual(len(findings), 2)

    def test_rejects_shell_line_continuation(self) -> None:
        findings = self._scan(
            "```bash\ngh workflow \\\n  --repo owner/repo \\\n  disable 123\n```\n"
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].line_number, 2)

    def test_misleading_earlier_negation_does_not_exempt_command(self) -> None:
        findings = self._scan("Do not wait; run gh workflow disable 123 now.\n")
        self.assertEqual(len(findings), 1)

    def test_misleading_comma_negation_does_not_exempt_command(self) -> None:
        findings = self._scan("Do not wait, run gh workflow disable 123 now.\n")
        self.assertEqual(len(findings), 1)

    def test_rejects_raw_actions_mutation_endpoint(self) -> None:
        findings = self._scan(
            "```bash\ngh api -X POST repos/ajoe734/pantheon/actions/runs/123/force-cancel\n```\n"
        )
        self.assertEqual(len(findings), 1)

    def test_allows_explicit_negative_rule(self) -> None:
        findings = self._scan("Workers must not run `gh workflow disable` against shared infrastructure.\n")
        self.assertEqual(findings, [])

    def test_repository_worker_surfaces_are_clean(self) -> None:
        self.assertEqual(policy.scan_paths(policy.DEFAULT_PATHS), [])


if __name__ == "__main__":
    unittest.main()
