"""
Tests for PostmortemEvidenceCollector — P1-EVO-001

Closes PM-GAP-001: verifies that the evidence collector assembles all required
telemetry/runtime-binding/deployment/artifact/capital evidence into a validated
IncidentCase.

Run:
    python3 -m pytest services/incident/test_incident_evidence_collector.py -v
    python3 services/incident/test_incident_evidence_collector.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.incident.evidence_collector import (
    EvidenceBundle,
    EvidenceCollectionError,
    PostmortemEvidenceCollector,
    RuntimeBindingEvidence,
    TelemetryEvidence,
    build_evidence_bundle,
)
from services.incident.incident import (
    IncidentSeverity,
    IncidentStatus,
    validate_incident_case,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _rb(**overrides) -> RuntimeBindingEvidence:
    defaults = dict(
        binding_id="binding-abc",
        runtime_id="runtime-001",
        deployment_stage="paper",
        deployment_plan_id="plan-xyz",
        capital_pool_id="pool-001",
        persona_capital_binding_id="pcb-001",
        artifact_id="artifact-001",
        artifact_version="1.2.3",
        trace_id="trace-aaa",
    )
    return RuntimeBindingEvidence(**{**defaults, **overrides})


def _tel(**overrides) -> TelemetryEvidence:
    defaults = dict(
        telemetry_event_ids=["evt-001", "evt-002"],
        heartbeat_lag_ms=12.5,
        order_rejection_refs=["evt-003"],
    )
    return TelemetryEvidence(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# EvidenceBundle assembly
# ---------------------------------------------------------------------------

class TestEvidenceBundleAssembly(unittest.TestCase):

    def setUp(self):
        self.collector = PostmortemEvidenceCollector()

    def test_assemble_bundle_succeeds_with_valid_inputs(self):
        bundle = self.collector.assemble_bundle(runtime_binding=_rb(), telemetry=_tel())
        self.assertIsInstance(bundle, EvidenceBundle)
        self.assertEqual(bundle.runtime_binding.binding_id, "binding-abc")
        self.assertEqual(bundle.lineage_ref, "artifact-001@1.2.3")

    def test_bundle_evidence_summary_contains_key_identifiers(self):
        bundle = self.collector.assemble_bundle(runtime_binding=_rb(), telemetry=_tel())
        self.assertIn("binding=binding-abc", bundle.evidence_summary)
        self.assertIn("stage=paper", bundle.evidence_summary)
        self.assertIn("artifact=artifact-001@1.2.3", bundle.evidence_summary)
        self.assertIn("pool=pool-001", bundle.evidence_summary)

    def test_bundle_all_event_ids_are_deduplicated(self):
        tel = TelemetryEvidence(
            telemetry_event_ids=["evt-001", "evt-002"],
            order_rejection_refs=["evt-002", "evt-003"],
        )
        bundle = self.collector.assemble_bundle(runtime_binding=_rb(), telemetry=tel)
        all_ids = bundle.telemetry.all_event_ids()
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertIn("evt-001", all_ids)
        self.assertIn("evt-003", all_ids)

    def test_extra_notes_appear_in_evidence_summary(self):
        bundle = self.collector.assemble_bundle(
            runtime_binding=_rb(),
            telemetry=_tel(),
            extra_notes="drawdown_multiple=1.30",
        )
        self.assertIn("drawdown_multiple=1.30", bundle.evidence_summary)

    def test_heartbeat_lag_appears_in_summary(self):
        bundle = self.collector.assemble_bundle(
            runtime_binding=_rb(),
            telemetry=TelemetryEvidence(
                telemetry_event_ids=["evt-001"],
                heartbeat_lag_ms=250.0,
            ),
        )
        self.assertIn("heartbeat_lag_ms=250.0", bundle.evidence_summary)


# ---------------------------------------------------------------------------
# RuntimeBindingEvidence validation
# ---------------------------------------------------------------------------

class TestRuntimeBindingValidation(unittest.TestCase):

    def setUp(self):
        self.collector = PostmortemEvidenceCollector()

    def _assemble(self, **rb_overrides):
        return self.collector.assemble_bundle(
            runtime_binding=_rb(**rb_overrides),
            telemetry=_tel(),
        )

    def test_missing_binding_id_raises(self):
        with self.assertRaises(EvidenceCollectionError) as ctx:
            self._assemble(binding_id="")
        self.assertIn("binding_id", str(ctx.exception))

    def test_missing_runtime_id_raises(self):
        with self.assertRaises(EvidenceCollectionError) as ctx:
            self._assemble(runtime_id="")
        self.assertIn("runtime_id", str(ctx.exception))

    def test_missing_deployment_plan_id_raises(self):
        with self.assertRaises(EvidenceCollectionError) as ctx:
            self._assemble(deployment_plan_id="")
        self.assertIn("deployment_plan_id", str(ctx.exception))

    def test_missing_capital_pool_id_raises(self):
        with self.assertRaises(EvidenceCollectionError) as ctx:
            self._assemble(capital_pool_id="")
        self.assertIn("capital_pool_id", str(ctx.exception))

    def test_missing_persona_capital_binding_id_raises(self):
        with self.assertRaises(EvidenceCollectionError) as ctx:
            self._assemble(persona_capital_binding_id="")
        self.assertIn("persona_capital_binding_id", str(ctx.exception))

    def test_missing_artifact_id_raises(self):
        with self.assertRaises(EvidenceCollectionError) as ctx:
            self._assemble(artifact_id="")
        self.assertIn("artifact_id", str(ctx.exception))

    def test_missing_artifact_version_raises(self):
        with self.assertRaises(EvidenceCollectionError) as ctx:
            self._assemble(artifact_version="")
        self.assertIn("artifact_version", str(ctx.exception))

    def test_missing_trace_id_raises(self):
        with self.assertRaises(EvidenceCollectionError) as ctx:
            self._assemble(trace_id="")
        self.assertIn("trace_id", str(ctx.exception))

    def test_invalid_deployment_stage_raises(self):
        with self.assertRaises(EvidenceCollectionError) as ctx:
            self._assemble(deployment_stage="production")
        self.assertIn("deployment_stage", str(ctx.exception))

    def test_valid_stages_accepted(self):
        for stage in ("paper", "canary", "live", "frozen"):
            bundle = self._assemble(deployment_stage=stage)
            self.assertEqual(bundle.runtime_binding.deployment_stage, stage)


# ---------------------------------------------------------------------------
# TelemetryEvidence validation
# ---------------------------------------------------------------------------

class TestTelemetryEvidenceValidation(unittest.TestCase):

    def setUp(self):
        self.collector = PostmortemEvidenceCollector()

    def test_empty_event_ids_raises(self):
        with self.assertRaises(EvidenceCollectionError) as ctx:
            self.collector.assemble_bundle(
                runtime_binding=_rb(),
                telemetry=TelemetryEvidence(telemetry_event_ids=[]),
            )
        self.assertIn("telemetry_event_id", str(ctx.exception))

    def test_event_ids_via_order_rejection_refs_accepted(self):
        tel = TelemetryEvidence(
            telemetry_event_ids=[],
            order_rejection_refs=["evt-order-001"],
        )
        # Should not raise because all_event_ids() returns ["evt-order-001"]
        bundle = self.collector.assemble_bundle(runtime_binding=_rb(), telemetry=tel)
        self.assertIn("evt-order-001", bundle.telemetry.all_event_ids())


# ---------------------------------------------------------------------------
# IncidentCase creation from EvidenceBundle
# ---------------------------------------------------------------------------

class TestIncidentCaseCreation(unittest.TestCase):

    def setUp(self):
        self.collector = PostmortemEvidenceCollector()
        self.bundle = self.collector.assemble_bundle(runtime_binding=_rb(), telemetry=_tel())

    def test_create_incident_produces_valid_incident_case(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Drawdown breach on paper runtime",
            severity=IncidentSeverity.HIGH,
        )
        errors = validate_incident_case(incident)
        self.assertEqual(errors, [], f"Unexpected validation errors: {errors}")

    def test_incident_carries_binding_id(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity=IncidentSeverity.LOW,
        )
        self.assertEqual(incident.binding_id, "binding-abc")

    def test_incident_carries_deployment_stage(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity=IncidentSeverity.LOW,
        )
        self.assertEqual(incident.deployment_stage, "paper")

    def test_incident_carries_capital_pool_id(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity=IncidentSeverity.LOW,
        )
        self.assertEqual(incident.capital_pool_id, "pool-001")

    def test_incident_carries_persona_capital_binding_id(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity=IncidentSeverity.LOW,
        )
        self.assertEqual(incident.persona_capital_binding_id, "pcb-001")

    def test_incident_carries_artifact_id_and_version(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity=IncidentSeverity.LOW,
        )
        self.assertEqual(incident.artifact_id, "artifact-001")
        self.assertEqual(incident.artifact_version, "1.2.3")

    def test_incident_carries_runtime_id(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity=IncidentSeverity.LOW,
        )
        self.assertEqual(incident.runtime_id, "runtime-001")

    def test_incident_carries_trace_id(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity=IncidentSeverity.LOW,
        )
        self.assertEqual(incident.trace_id, "trace-aaa")

    def test_incident_carries_telemetry_event_ids(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity=IncidentSeverity.HIGH,
        )
        # Should include events from both telemetry_event_ids and order_rejection_refs
        self.assertIn("evt-001", incident.telemetry_event_ids)
        self.assertIn("evt-002", incident.telemetry_event_ids)
        self.assertIn("evt-003", incident.telemetry_event_ids)

    def test_incident_carries_evidence_summary(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity=IncidentSeverity.HIGH,
        )
        self.assertIsNotNone(incident.evidence_summary)
        self.assertIn("binding=binding-abc", incident.evidence_summary)

    def test_incident_carries_lineage_ref(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity=IncidentSeverity.HIGH,
        )
        self.assertEqual(incident.lineage_ref, "artifact-001@1.2.3")

    def test_incident_default_status_is_open(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity=IncidentSeverity.MEDIUM,
        )
        self.assertEqual(incident.status, IncidentStatus.OPEN.value)

    def test_explicit_incident_id_is_preserved(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity=IncidentSeverity.CRITICAL,
            incident_id="inc-custom-001",
        )
        self.assertEqual(incident.incident_id, "inc-custom-001")

    def test_auto_generated_incident_id_has_correct_prefix(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity=IncidentSeverity.HIGH,
        )
        self.assertTrue(incident.incident_id.startswith("inc-"))

    def test_severity_string_value_accepted(self):
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Test",
            severity="critical",
        )
        self.assertEqual(incident.severity, "critical")

    def test_all_required_evidence_fields_present(self):
        """All required evidence fields must be non-empty on the created incident."""
        incident = self.collector.create_incident(
            bundle=self.bundle,
            title="Full evidence test",
            severity=IncidentSeverity.HIGH,
        )
        required_fields = [
            "binding_id",
            "deployment_stage",
            "deployment_plan_id",
            "capital_pool_id",
            "persona_capital_binding_id",
            "artifact_id",
            "artifact_version",
            "runtime_id",
            "trace_id",
        ]
        for field_name in required_fields:
            value = getattr(incident, field_name)
            self.assertTrue(
                bool(value),
                f"Required evidence field {field_name!r} is empty on IncidentCase",
            )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

class TestBuildEvidenceBundle(unittest.TestCase):

    def test_build_evidence_bundle_convenience_factory(self):
        bundle = build_evidence_bundle(
            binding_id="binding-xyz",
            runtime_id="runtime-002",
            deployment_stage="canary",
            deployment_plan_id="plan-002",
            capital_pool_id="pool-002",
            persona_capital_binding_id="pcb-002",
            artifact_id="artifact-002",
            artifact_version="2.0.0",
            trace_id="trace-bbb",
            telemetry_event_ids=["evt-100"],
        )
        self.assertEqual(bundle.runtime_binding.binding_id, "binding-xyz")
        self.assertEqual(bundle.runtime_binding.deployment_stage, "canary")
        self.assertIn("evt-100", bundle.telemetry.all_event_ids())

    def test_build_evidence_bundle_propagates_extra_notes(self):
        bundle = build_evidence_bundle(
            binding_id="b",
            runtime_id="r",
            deployment_stage="paper",
            deployment_plan_id="p",
            capital_pool_id="c",
            persona_capital_binding_id="pcb",
            artifact_id="a",
            artifact_version="1.0",
            trace_id="t",
            telemetry_event_ids=["e1"],
            extra_notes="psi=0.32",
        )
        self.assertIn("psi=0.32", bundle.evidence_summary)


if __name__ == "__main__":
    unittest.main()
