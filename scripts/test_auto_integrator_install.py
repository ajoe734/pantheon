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
            Path("/repo/runtime/live.json"),
            interval="*/5 * * * *",
        )

        self.assertTrue(line.startswith("*/5 * * * * cd /repo/dev-root"))
        self.assertIn("PANTHEON_STATUS_ROOT=/repo/status-root", line)
        self.assertIn("PANTHEON_AUTO_INTEGRATOR_CONFIG=/repo/runtime/live.json", line)
        self.assertIn("bash scripts/run-auto-integrator.sh", line)
        self.assertIn("/repo/status-root/.orchestrator/logs/auto-integrator-cron.log", line)
        self.assertTrue(line.endswith("# pantheon-auto-integrator"))

    def test_render_cron_line_quotes_spaces(self) -> None:
        line = auto_integrator_install.render_cron_line(
            Path("/repo/dev root"),
            Path("/repo/status root"),
            Path("/repo/runtime config/live.json"),
        )

        self.assertIn("cd '/repo/dev root'", line)
        self.assertIn("PANTHEON_STATUS_ROOT='/repo/status root'", line)
        self.assertIn(
            "PANTHEON_AUTO_INTEGRATOR_CONFIG='/repo/runtime config/live.json'",
            line,
        )

    def test_default_config_remains_status_root_template(self) -> None:
        line = auto_integrator_install.render_cron_line(
            Path("/repo/dev-root"),
            Path("/repo/status-root"),
        )

        self.assertIn(
            "PANTHEON_AUTO_INTEGRATOR_CONFIG=/repo/status-root/.orchestrator/config.json",
            line,
        )

    def test_render_cron_line_defaults_max_tasks_to_two(self) -> None:
        line = auto_integrator_install.render_cron_line(
            Path("/repo/dev-root"),
            Path("/repo/status-root"),
        )

        self.assertEqual(auto_integrator_install.DEFAULT_MAX_TASKS, 2)
        self.assertIn("AUTO_INTEGRATOR_MAX_TASKS=2", line)

    def test_render_cron_line_renders_explicit_max_tasks_override(self) -> None:
        line = auto_integrator_install.render_cron_line(
            Path("/repo/dev-root"),
            Path("/repo/status-root"),
            max_tasks=5,
        )

        self.assertIn("AUTO_INTEGRATOR_MAX_TASKS=5", line)

    def test_render_cron_line_rejects_non_positive_max_tasks(self) -> None:
        with self.assertRaises(ValueError):
            auto_integrator_install.render_cron_line(
                Path("/repo/dev-root"),
                Path("/repo/status-root"),
                max_tasks=0,
            )

    def test_install_cron_cli_default_persists_max_tasks_into_crontab(self) -> None:
        # Exercises the CLI parser -> install_cron -> render_cron_line path
        # end to end so the rendered AUTO_INTEGRATOR_MAX_TASKS is proven to
        # come from the one declared source (DEFAULT_MAX_TASKS), not an
        # ambient shell env var, and so it survives runtime promotion via the
        # crontab entry itself.
        import io
        import tempfile
        from contextlib import redirect_stdout
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            (repo_root / "scripts").mkdir()
            (repo_root / "scripts" / "run-auto-integrator.sh").write_text("", encoding="utf-8")

            with mock.patch.object(auto_integrator_install, "current_crontab", return_value=[]):
                with mock.patch.object(auto_integrator_install, "write_crontab") as write_crontab:
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        with mock.patch(
                            "sys.argv",
                            ["auto_integrator_install.py", "--repo", str(repo_root), "--dry-run"],
                        ):
                            exit_code = auto_integrator_install.main()
            self.assertEqual(exit_code, 0)
            write_crontab.assert_called_once()
            call_args, call_kwargs = write_crontab.call_args
            self.assertTrue(call_kwargs.get("dry_run"))
            self.assertIn("AUTO_INTEGRATOR_MAX_TASKS=2", call_args[0][0])


if __name__ == "__main__":
    unittest.main()
