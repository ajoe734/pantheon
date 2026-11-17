"""Contract tests for .github/workflows/dev-storage-budget.yml (RC3 WP3.3)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

TEXT = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "dev-storage-budget.yml").read_text()


class WorkflowContract(unittest.TestCase):
    def test_scheduled_daily_and_manual(self):
        self.assertRegex(TEXT, r"cron: '\d+ \d+ \* \* \*'")
        self.assertIn("workflow_dispatch:", TEXT)

    def test_read_only_no_lease_no_write(self):
        self.assertNotIn("dev_environment_lease", TEXT)
        code = "\n".join(l for l in TEXT.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("lease", code.lower())
        self.assertRegex(TEXT, r"contents: read")
        for bad in ("docker restart", "docker compose", "rm -rf", " scp", "copy-from"):
            self.assertNotIn(bad, TEXT)

    def test_report_artifact_with_retention_and_red_on_failure(self):
        self.assertIn("upload-artifact@v4", TEXT)
        self.assertRegex(TEXT, r"retention-days: \d+")
        self.assertIn("exit 1", TEXT)

    def test_raw_output_not_uploaded(self):
        self.assertIn("path: out/report.json", TEXT)
        self.assertIn("rm -f out/raw.txt", TEXT)

    def test_uses_pinned_transport(self):
        self.assertIn("scripts/dev_vm_ssh.sh prepare", TEXT)
        self.assertIsNotNone(re.search(r"measure_hosted_storage.py collect", TEXT))


if __name__ == "__main__":
    unittest.main()
