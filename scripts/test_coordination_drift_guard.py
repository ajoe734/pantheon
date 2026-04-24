#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coordination_drift_guard


class CoordinationDriftGuardTests(unittest.TestCase):
    def _write_fixture_tree(self, root: Path, *, stale_front_overview: bool) -> tuple[Path, Path]:
        pantheon = root / "pantheon"
        front = root / "front-ai-trading-system"

        (pantheon / "docs" / "pantheon-handoffs").mkdir(parents=True, exist_ok=True)
        (pantheon / ".coordination" / "responses").mkdir(parents=True, exist_ok=True)
        (front / ".coordination" / "responses").mkdir(parents=True, exist_ok=True)
        (front / "src").mkdir(parents=True, exist_ok=True)

        (pantheon / "WORKBENCH_DELIVERY_BACKLOG.md").write_text(
            "\n".join(
                [
                    "| Module | Current state | Pantheon-owned gap | Next truthful gate |",
                    "|---|---|---|---|",
                    "| `KW-04 Insight Cards` | route-live — insight list/detail routes implemented | live route | activate UI |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (pantheon / "docs" / "pantheon-handoffs" / "LOVABLE_MASTER_SA.md").write_text(
            "| `KW-04 Insight Cards` | route-live — handoff bundle published |\n",
            encoding="utf-8",
        )
        (pantheon / "docs" / "pantheon-handoffs" / "KW-04-insight-cards").mkdir(parents=True, exist_ok=True)
        (pantheon / "docs" / "pantheon-handoffs" / "KW-04-insight-cards" / "FRONTEND_CHANGE_SPEC.md").write_text(
            "# KW-04\n",
            encoding="utf-8",
        )

        pantheon_packet = (
            "feature_id: PKT-knowledge-workbench\n"
            "bff_caveats:\n"
            "  - this packet is an overview surface only\n"
            "  - KW-04 now has a published frontend handoff bundle\n"
        )
        front_packet = (
            "feature_id: PKT-knowledge-workbench\n"
            "bff_caveats:\n"
            "  - this packet is an overview surface only\n"
            "  - KW-01 to KW-05 remain blocked on net-new BFF routes\n"
            if stale_front_overview
            else "feature_id: PKT-knowledge-workbench\nbff_caveats:\n  - this packet is an overview surface only\n"
        )
        (pantheon / ".coordination" / "responses" / "PKT-knowledge-workbench-contract-ready.yaml").write_text(
            pantheon_packet,
            encoding="utf-8",
        )
        (front / ".coordination" / "responses" / "PKT-knowledge-workbench-contract-ready.yaml").write_text(
            front_packet,
            encoding="utf-8",
        )
        (front / "src" / "App.tsx").write_text(
            '<Route path="/knowledge" element={<KnowledgeWorkbench />} />\n',
            encoding="utf-8",
        )
        return pantheon, front

    def test_guard_fails_when_front_overview_still_claims_blocked_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pantheon, front = self._write_fixture_tree(Path(tmp), stale_front_overview=True)
            with (
                mock.patch.object(coordination_drift_guard, "ROOT", pantheon),
                mock.patch.object(coordination_drift_guard, "DEFAULT_FRONT_REPO", front),
                mock.patch.object(coordination_drift_guard, "BACKLOG_PATH", pantheon / "WORKBENCH_DELIVERY_BACKLOG.md"),
                mock.patch.object(coordination_drift_guard, "LOVABLE_MASTER_SA", pantheon / "docs" / "pantheon-handoffs" / "LOVABLE_MASTER_SA.md"),
                mock.patch.object(sys, "argv", ["coordination_drift_guard.py", "--front-repo", str(front)]),
            ):
                self.assertEqual(coordination_drift_guard.main(), 1)

    def test_guard_passes_when_live_side_overview_is_truthful(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pantheon, front = self._write_fixture_tree(Path(tmp), stale_front_overview=False)
            with (
                mock.patch.object(coordination_drift_guard, "ROOT", pantheon),
                mock.patch.object(coordination_drift_guard, "DEFAULT_FRONT_REPO", front),
                mock.patch.object(coordination_drift_guard, "BACKLOG_PATH", pantheon / "WORKBENCH_DELIVERY_BACKLOG.md"),
                mock.patch.object(coordination_drift_guard, "LOVABLE_MASTER_SA", pantheon / "docs" / "pantheon-handoffs" / "LOVABLE_MASTER_SA.md"),
                mock.patch.object(sys, "argv", ["coordination_drift_guard.py", "--front-repo", str(front)]),
            ):
                self.assertEqual(coordination_drift_guard.main(), 0)


if __name__ == "__main__":
    unittest.main()
