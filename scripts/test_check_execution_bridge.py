from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_execution_bridge


class RemoteNormalizationTests(unittest.TestCase):
    def test_normalizes_common_github_remote_forms(self) -> None:
        self.assertEqual(
            check_execution_bridge.normalize_remote_url("https://github.com/ajoe734/pantheon-lean.git"),
            "ajoe734/pantheon-lean.git",
        )
        self.assertEqual(
            check_execution_bridge.normalize_remote_url("git@github.com:ajoe734/pantheon-lean.git"),
            "ajoe734/pantheon-lean.git",
        )


class BridgeReportTests(unittest.TestCase):
    def test_reports_official_bridge_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".gitmodules").write_text(
                "\n".join(
                    [
                        '[submodule "lean"]',
                        "\tpath = lean",
                        "\turl = https://github.com/ajoe734/pantheon-lean.git",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            base_path = root / "lean" / "Algorithm.Python" / "pantheon_algo" / "base.py"
            base_path.parent.mkdir(parents=True)
            base_path.write_text("class PantheonAlgoBase:\n    pass\n", encoding="utf-8")
            (root / "docker-compose.exec.yml").write_text(
                "PANTHEON_LEAN_CONFIG_PATH: /workspace/lean/Launcher/config.json\n",
                encoding="utf-8",
            )

            report = check_execution_bridge.build_report(root=root, bridge_commit="abc123")

        self.assertTrue(report["ok"])
        self.assertEqual(report["bridge_path"], "pantheon/lean")
        self.assertEqual(report["bridge_remote"], "ajoe734/pantheon-lean.git")
        self.assertEqual(report["bridge_commit"], "abc123")
        self.assertTrue(report["pantheon_algo_base_present"])
        self.assertTrue(report["compose_exec_path_valid"])

    def test_reports_wrong_remote_as_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".gitmodules").write_text(
                "\n".join(
                    [
                        '[submodule "lean"]',
                        "\tpath = lean",
                        "\turl = https://github.com/ajoe734/lean-platform.git",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "lean").mkdir()
            (root / "docker-compose.exec.yml").write_text("", encoding="utf-8")

            report = check_execution_bridge.build_report(root=root, bridge_commit="abc123")

        self.assertFalse(report["ok"])
        self.assertIn("remote must be ajoe734/pantheon-lean.git", "\n".join(report["violations"]))


if __name__ == "__main__":
    unittest.main()
