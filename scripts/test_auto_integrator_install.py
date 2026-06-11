from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import auto_integrator_install


class AutoIntegratorInstallTests(unittest.TestCase):
    def test_render_cron_line_uses_repo_status_root_and_tag(self) -> None:
        line = auto_integrator_install.render_cron_line(
            Path("/repo/dev-root"),
            Path("/repo/status-root"),
            interval="*/5 * * * *",
        )

        self.assertTrue(line.startswith("*/5 * * * * cd /repo/dev-root"))
        self.assertIn("PANTHEON_STATUS_ROOT=/repo/status-root", line)
        self.assertIn("bash scripts/run-auto-integrator.sh", line)
        self.assertIn("/repo/status-root/.orchestrator/logs/auto-integrator-cron.log", line)
        self.assertTrue(line.endswith("# pantheon-auto-integrator"))

    def test_render_cron_line_quotes_spaces(self) -> None:
        line = auto_integrator_install.render_cron_line(
            Path("/repo/dev root"),
            Path("/repo/status root"),
        )

        self.assertIn("cd '/repo/dev root'", line)
        self.assertIn("PANTHEON_STATUS_ROOT='/repo/status root'", line)


if __name__ == "__main__":
    unittest.main()
