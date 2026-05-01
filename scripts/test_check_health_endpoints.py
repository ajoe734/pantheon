from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_health_endpoints


class HealthEndpointReportTests(unittest.TestCase):
    def _write_baseline(self, root: Path) -> None:
        helper = root / "services" / "foundation" / "health.py"
        helper.parent.mkdir(parents=True)
        helper.write_text(
            "\n".join(
                [
                    "def register_fastapi_health_routes(app):",
                    "    app.add_api_route('/healthz')",
                    "    app.add_api_route('/livez')",
                    "    app.add_api_route('/readyz')",
                    "    app.add_api_route('/metrics')",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "docker-compose.yml").write_text(
            "healthcheck:\n  test: curl http://127.0.0.1:8000/readyz\n",
            encoding="utf-8",
        )

    def test_warn_mode_reports_staged_legacy_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_baseline(root)
            (root / "docker-compose.control.yml").write_text(
                "test: http://127.0.0.1:8083/__health__\n",
                encoding="utf-8",
            )
            (root / "docker-compose.exec.yml").write_text("", encoding="utf-8")

            report = check_health_endpoints.build_report(root=root, mode="warn")

        self.assertTrue(report["ok"])
        self.assertEqual(report["legacy_count"], 1)
        self.assertEqual(report["legacy_occurrences"][0]["path"], "docker-compose.control.yml")

    def test_fail_mode_fails_on_staged_legacy_occurrences(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_baseline(root)
            (root / "docker-compose.control.yml").write_text("", encoding="utf-8")
            (root / "docker-compose.exec.yml").write_text(
                "test: http://127.0.0.1:8081/__health__\n",
                encoding="utf-8",
            )

            report = check_health_endpoints.build_report(root=root, mode="fail")

        self.assertFalse(report["ok"])
        self.assertEqual(report["legacy_count"], 1)
        self.assertIn("must not use legacy", "\n".join(report["violations"]))

    def test_missing_standard_helper_is_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "docker-compose.yml").write_text(
                "healthcheck:\n  test: curl http://127.0.0.1:8000/readyz\n",
                encoding="utf-8",
            )
            (root / "docker-compose.control.yml").write_text("", encoding="utf-8")
            (root / "docker-compose.exec.yml").write_text("", encoding="utf-8")

            report = check_health_endpoints.build_report(root=root, mode="warn")

        self.assertFalse(report["ok"])
        self.assertFalse(report["standard_helper"]["exists"])


if __name__ == "__main__":
    unittest.main()
