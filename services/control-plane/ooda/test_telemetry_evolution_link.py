"""Tests for MGMT-EVO-001 telemetry-to-evolution packet link."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


OODA_DIR = Path(__file__).resolve().parent
REPO_ROOT = OODA_DIR.parents[2]
GOVERNANCE_DIR = REPO_ROOT / "services" / "control-plane" / "governance"
for _path in (REPO_ROOT, OODA_DIR, GOVERNANCE_DIR):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

from evolution_decision import EvolutionDecision, validate_evolution_decision  # noqa: E402
from services.evolution.models import ProposeRequest  # noqa: E402
from telemetry_evolution_link import (  # noqa: E402
    TelemetryEvolutionLinkContext,
    build_evidence_packet,
    validate_evidence_packet,
    write_evidence_packet,
)


class TestTelemetryEvolutionLink(unittest.TestCase):
    def test_packet_links_telemetry_evidence_to_proposed_evolution_decision(self) -> None:
        packet = build_evidence_packet(generated_at="2026-05-15T15:09:00Z")

        self.assertEqual(packet["validation_errors"], [])
        self.assertEqual(packet["task_id"], "MGMT-EVO-001")
        self.assertEqual(packet["milestone_id"], "MGMT-OODA-M6")
        self.assertEqual(packet["link_type"], "telemetry_to_evolution")
        self.assertEqual(packet["environment"], "paper")

        evidence_ref = packet["evidence_ref"]
        telemetry_summary = packet["telemetry_summary"]
        self.assertEqual(evidence_ref["ref_type"], "telemetry_summary")
        self.assertEqual(evidence_ref["ref_id"], telemetry_summary["packet_id"])

        proposal = ProposeRequest(**packet["proposal_request"])
        self.assertEqual(proposal.decision_id, "evolution-telemetry-link-paper-001")
        self.assertEqual(proposal.action_type, "revalidate")
        self.assertEqual(proposal.evidence_refs[0].ref_type, "telemetry_summary")
        self.assertTrue(proposal.metadata["proposal_only"])
        self.assertFalse(proposal.metadata["runtime_binding_mutation_allowed"])

        decision = EvolutionDecision.from_dict(packet["evolution_decision"])
        self.assertEqual(validate_evolution_decision(decision), [])
        self.assertEqual(decision.decision_state, "proposed")
        self.assertEqual(decision.metadata["telemetry_packet_id"], telemetry_summary["packet_id"])

        learn_patch = packet["ooda_link"]["learn_patch"]["learn"]
        self.assertEqual(
            learn_patch["telemetry_refs"],
            telemetry_summary["event_refs"],
        )
        self.assertIn(
            "evolution-decision://evolution-telemetry-link-paper-001/proposal",
            learn_patch["evolution_followthrough_refs"],
        )
        self.assertTrue(packet["safety_assertions"]["proposal_only"])
        self.assertTrue(packet["safety_assertions"]["no_runtime_binding_mutation"])

    def test_validation_rejects_unbacked_telemetry_ref(self) -> None:
        packet = build_evidence_packet(generated_at="2026-05-15T15:09:00Z")
        mutated = json.loads(json.dumps(packet))
        mutated["ooda_link"]["learn_patch"]["learn"]["telemetry_refs"].append(
            "telemetry-event://not-in-summary"
        )

        errors = validate_evidence_packet(mutated)

        self.assertTrue(
            any("not backed by telemetry_summary.event_refs" in error for error in errors),
            errors,
        )

    def test_write_evidence_packet_writes_task_artifact(self) -> None:
        packet = build_evidence_packet(
            TelemetryEvolutionLinkContext(link_id="telemetry-evolution-link-test"),
            generated_at="2026-05-15T15:09:00Z",
        )
        with tempfile.TemporaryDirectory(prefix="mgmt_evo_001_test_") as temp_dir:
            task_path = Path(temp_dir) / "packet.json"

            written = write_evidence_packet(packet, task_path=task_path)

            self.assertEqual(written["task_id"], "MGMT-EVO-001")
            self.assertTrue(task_path.exists())
            payload = json.loads(task_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["link_id"], "telemetry-evolution-link-test")
            self.assertEqual(payload["validation_errors"], [])


if __name__ == "__main__":
    unittest.main()
