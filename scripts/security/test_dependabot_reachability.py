"""Tests for current-checkout Dependabot alert reconciliation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.security.dependabot_reachability import reconcile_alerts


def alert(
    number: int,
    *,
    severity: str,
    package: str,
    manifest: str,
    vulnerable_range: str,
    ecosystem: str = "pip",
) -> dict:
    return {
        "number": number,
        "security_advisory": {"severity": severity, "ghsa_id": f"GHSA-test-{number}"},
        "security_vulnerability": {"vulnerable_version_range": vulnerable_range},
        "dependency": {
            "package": {"ecosystem": ecosystem, "name": package},
            "manifest_path": manifest,
        },
    }


class DependabotReachabilityTests(unittest.TestCase):
    def test_fixed_candidate_pin_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest = root / "requirements.txt"
            manifest.write_text("mlflow==3.11.1\n", encoding="utf-8")
            results = reconcile_alerts(
                [
                    alert(
                        1,
                        severity="critical",
                        package="mlflow",
                        manifest="requirements.txt",
                        vulnerable_range="< 3.11.0",
                    )
                ],
                root,
                {"critical", "high"},
            )
        self.assertEqual(results[0].disposition, "candidate_fixed")
        self.assertFalse(results[0].violation)

    def test_reachable_vulnerable_pin_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "requirements.txt").write_text("ray[rllib]==2.9.3\n", encoding="utf-8")
            results = reconcile_alerts(
                [
                    alert(
                        2,
                        severity="critical",
                        package="ray",
                        manifest="requirements.txt",
                        vulnerable_range="<= 2.52.0",
                    )
                ],
                root,
                {"critical", "high"},
            )
        self.assertEqual(results[0].disposition, "reachable_vulnerable")
        self.assertTrue(results[0].violation)

    def test_ray_2_55_1_is_fixed_for_cve_2026_41486(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "requirements.txt").write_text(
                "ray[rllib]==2.55.1\nray[tune]==2.55.1\n",
                encoding="utf-8",
            )
            results = reconcile_alerts(
                [
                    alert(
                        38,
                        severity="high",
                        package="ray",
                        manifest="requirements.txt",
                        vulnerable_range=">= 2.49.0, < 2.55.0",
                    )
                ],
                root,
                {"critical", "high"},
            )
        self.assertEqual(results[0].current_version, "2.55.1")
        self.assertEqual(results[0].disposition, "candidate_fixed")
        self.assertFalse(results[0].violation)

    def test_deleted_manifest_is_inventory_not_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            results = reconcile_alerts(
                [
                    alert(
                        3,
                        severity="high",
                        package="vite",
                        manifest="execute-plans/package-lock.json",
                        vulnerable_range="<= 6.4.2",
                        ecosystem="npm",
                    )
                ],
                Path(tmpdir),
                {"critical", "high"},
            )
        self.assertEqual(results[0].disposition, "deleted_manifest")
        self.assertFalse(results[0].violation)

    def test_npm_lock_pin_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lock = root / "package-lock.json"
            lock.write_text(
                json.dumps({"packages": {"node_modules/vite": {"version": "6.4.3"}}}),
                encoding="utf-8",
            )
            results = reconcile_alerts(
                [
                    alert(
                        4,
                        severity="high",
                        package="vite",
                        manifest="package-lock.json",
                        vulnerable_range="<= 6.4.2",
                        ecosystem="npm",
                    )
                ],
                root,
                {"critical", "high"},
            )
        self.assertEqual(results[0].current_version, "6.4.3")
        self.assertFalse(results[0].violation)


if __name__ == "__main__":
    unittest.main()
