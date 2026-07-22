"""
Unit tests for INC-001 — IncidentCase and Postmortem backbone objects.

Coverage targets
----------------
- IncidentCase construction with all required fields
- IncidentCase enum validation (severity, status, deployment_stage)
- validate_incident_case() catches all missing required evidence fields
- Postmortem construction with all required fields
- Postmortem enum validation (status)
- validate_postmortem() catches missing fields
- Status transition helpers (is_open, is_published)
- IncidentStore create / read / update / find operations
- IncidentStore referential integrity (postmortem requires incident)
- IncidentStore update_incident_status auto-sets resolved_at
- IncidentStore link_evolution_decision
- Persistence round-trip (to_dict / from_dict, to_json / from_json)
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Allow running directly: python -m pytest or python test_incident.py
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.incident.incident import (
    IncidentCase,
    IncidentError,
    IncidentSeverity,
    IncidentStatus,
    IncidentStore,
    Postmortem,
    PostmortemStatus,
    validate_incident_case,
    validate_postmortem,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_incident(**overrides) -> IncidentCase:
    defaults = dict(
        incident_id="inc-001",
        title="Drawdown threshold breached on live binding",
        status=IncidentStatus.OPEN.value,
        severity=IncidentSeverity.HIGH.value,
        created_at="2026-04-10T14:00:00Z",
        binding_id="binding-abc",
        deployment_stage="live",
        deployment_plan_id="plan-xyz",
        capital_pool_id="pool-001",
        persona_capital_binding_id="pcb-001",
        artifact_id="artifact-001",
        artifact_version="1.2.3",
        runtime_id="runtime-001",
        trace_id="trace-abc",
    )
    defaults.update(overrides)
    return IncidentCase(**defaults)


def _make_postmortem(**overrides) -> Postmortem:
    defaults = dict(
        postmortem_id="pm-001",
        title="Drawdown breach postmortem",
        status=PostmortemStatus.DRAFT.value,
        created_at="2026-04-10T16:00:00Z",
        incident_id="inc-001",
        binding_id="binding-abc",
        deployment_stage="live",
        deployment_plan_id="plan-xyz",
        capital_pool_id="pool-001",
        persona_capital_binding_id="pcb-001",
        artifact_id="artifact-001",
        artifact_version="1.2.3",
        runtime_id="runtime-001",
        trace_id="trace-abc",
        root_cause="Position sizing model failed to account for intraday vol spike.",
    )
    defaults.update(overrides)
    return Postmortem(**defaults)


# ---------------------------------------------------------------------------
# IncidentCase construction
# ---------------------------------------------------------------------------

class TestIncidentCaseConstruction(unittest.TestCase):

    def test_valid_incident_creates_successfully(self):
        inc = _make_incident()
        self.assertEqual(inc.incident_id, "inc-001")
        self.assertEqual(inc.binding_id, "binding-abc")
        self.assertEqual(inc.deployment_stage, "live")
        self.assertEqual(inc.capital_pool_id, "pool-001")

    def test_invalid_severity_raises(self):
        with self.assertRaises(IncidentError):
            _make_incident(severity="unknown")

    def test_invalid_status_raises(self):
        with self.assertRaises(IncidentError):
            _make_incident(status="pending")

    def test_invalid_deployment_stage_raises(self):
        with self.assertRaises(IncidentError):
            _make_incident(deployment_stage="staging")

    def test_all_valid_stages_accepted(self):
        for stage in ("paper", "canary", "live", "frozen"):
            inc = _make_incident(deployment_stage=stage)
            self.assertEqual(inc.deployment_stage, stage)

    def test_all_valid_severities_accepted(self):
        for sev in ("critical", "high", "medium", "low"):
            inc = _make_incident(severity=sev)
            self.assertEqual(inc.severity, sev)

    def test_all_valid_statuses_accepted(self):
        for st in ("open", "investigating", "resolved", "closed"):
            inc = _make_incident(status=st, resolved_at="2026-04-10T15:00:00Z" if st in ("resolved", "closed") else None)
            self.assertEqual(inc.status, st)

    def test_is_open_for_open_status(self):
        inc = _make_incident(status="open")
        self.assertTrue(inc.is_open())

    def test_is_open_for_investigating_status(self):
        inc = _make_incident(status="investigating")
        self.assertTrue(inc.is_open())

    def test_is_open_false_for_resolved(self):
        inc = _make_incident(status="resolved", resolved_at="2026-04-10T15:00:00Z")
        self.assertFalse(inc.is_open())

    def test_optional_fields_default_to_none_or_empty(self):
        inc = _make_incident()
        self.assertIsNone(inc.resolved_at)
        self.assertEqual(inc.telemetry_event_ids, [])
        self.assertIsNone(inc.evidence_summary)
        self.assertIsNone(inc.lineage_ref)

    def test_telemetry_event_ids_stored(self):
        inc = _make_incident(telemetry_event_ids=["evt-001", "evt-002"])
        self.assertEqual(inc.telemetry_event_ids, ["evt-001", "evt-002"])

    def test_lineage_ref_stored(self):
        inc = _make_incident(lineage_ref="artifact-001@1.2.3")
        self.assertEqual(inc.lineage_ref, "artifact-001@1.2.3")


# ---------------------------------------------------------------------------
# validate_incident_case
# ---------------------------------------------------------------------------

class TestValidateIncidentCase(unittest.TestCase):

    def test_valid_incident_has_no_errors(self):
        inc = _make_incident()
        self.assertEqual(validate_incident_case(inc), [])

    def test_missing_binding_id_raises_error(self):
        # binding_id is required; work around frozen dataclass by patching via object.__setattr__
        inc = _make_incident()
        patched = IncidentCase(**{**inc.to_dict(), "binding_id": ""})
        errors = validate_incident_case(patched)
        self.assertTrue(any("binding_id" in e for e in errors))

    def test_missing_deployment_plan_id_raises_error(self):
        inc = _make_incident()
        patched = IncidentCase(**{**inc.to_dict(), "deployment_plan_id": ""})
        errors = validate_incident_case(patched)
        self.assertTrue(any("deployment_plan_id" in e for e in errors))

    def test_missing_persona_capital_binding_id_raises_error(self):
        inc = _make_incident()
        patched = IncidentCase(**{**inc.to_dict(), "persona_capital_binding_id": ""})
        errors = validate_incident_case(patched)
        self.assertTrue(any("persona_capital_binding_id" in e for e in errors))

    def test_missing_trace_id_raises_error(self):
        inc = _make_incident()
        patched = IncidentCase(**{**inc.to_dict(), "trace_id": ""})
        errors = validate_incident_case(patched)
        self.assertTrue(any("trace_id" in e for e in errors))

    def test_missing_artifact_id_raises_error(self):
        inc = _make_incident()
        patched = IncidentCase(**{**inc.to_dict(), "artifact_id": ""})
        errors = validate_incident_case(patched)
        self.assertTrue(any("artifact_id" in e for e in errors))

    def test_missing_artifact_version_raises_error(self):
        inc = _make_incident()
        patched = IncidentCase(**{**inc.to_dict(), "artifact_version": ""})
        errors = validate_incident_case(patched)
        self.assertTrue(any("artifact_version" in e for e in errors))

    def test_missing_runtime_id_raises_error(self):
        inc = _make_incident()
        patched = IncidentCase(**{**inc.to_dict(), "runtime_id": ""})
        errors = validate_incident_case(patched)
        self.assertTrue(any("runtime_id" in e for e in errors))

    def test_resolved_without_resolved_at_is_error(self):
        inc = _make_incident(status="resolved")
        errors = validate_incident_case(inc)
        self.assertTrue(any("resolved_at" in e for e in errors))

    def test_resolved_with_resolved_at_is_valid(self):
        inc = _make_incident(status="resolved", resolved_at="2026-04-10T15:00:00Z")
        self.assertEqual(validate_incident_case(inc), [])

    def test_closed_without_resolved_at_is_error(self):
        inc = _make_incident(status="closed")
        errors = validate_incident_case(inc)
        self.assertTrue(any("resolved_at" in e for e in errors))


# ---------------------------------------------------------------------------
# Postmortem construction
# ---------------------------------------------------------------------------

class TestPostmortemConstruction(unittest.TestCase):

    def test_valid_postmortem_creates_successfully(self):
        pm = _make_postmortem()
        self.assertEqual(pm.postmortem_id, "pm-001")
        self.assertEqual(pm.incident_id, "inc-001")
        self.assertEqual(pm.binding_id, "binding-abc")
        self.assertEqual(pm.deployment_stage, "live")
        self.assertEqual(pm.deployment_plan_id, "plan-xyz")
        self.assertEqual(pm.persona_capital_binding_id, "pcb-001")
        self.assertEqual(pm.runtime_id, "runtime-001")

    def test_invalid_status_raises(self):
        with self.assertRaises(IncidentError):
            _make_postmortem(status="pending")

    def test_all_valid_statuses_accepted(self):
        for st in ("draft", "review", "approved", "published"):
            pm = _make_postmortem(
                status=st,
                published_at="2026-04-10T20:00:00Z" if st == "published" else None,
            )
            self.assertEqual(pm.status, st)

    def test_is_published_false_by_default(self):
        pm = _make_postmortem()
        self.assertFalse(pm.is_published())

    def test_is_published_true_when_published(self):
        pm = _make_postmortem(status="published", published_at="2026-04-10T20:00:00Z")
        self.assertTrue(pm.is_published())

    def test_optional_fields_default_to_none_or_empty(self):
        pm = _make_postmortem()
        self.assertIsNone(pm.published_at)
        self.assertIsNone(pm.linked_evolution_decision_id)
        self.assertIsNone(pm.incident_cluster_id)
        self.assertIsNone(pm.incident_evidence_summary)
        self.assertIsNone(pm.lineage_ref)
        self.assertEqual(pm.contributing_factors, [])
        self.assertEqual(pm.timeline, [])
        self.assertEqual(pm.action_items, [])
        self.assertEqual(pm.telemetry_event_ids, [])
        self.assertEqual(pm.reconciliation_ids, [])

    def test_contributing_factors_stored(self):
        pm = _make_postmortem(contributing_factors=["High vol regime", "Oversized position"])
        self.assertEqual(len(pm.contributing_factors), 2)

    def test_action_items_stored(self):
        pm = _make_postmortem(action_items=["Reduce max_position_size", "Add vol regime guard"])
        self.assertEqual(len(pm.action_items), 2)

    def test_timeline_stored(self):
        pm = _make_postmortem(timeline=[
            {"ts": "2026-04-10T13:55:00Z", "description": "Vol spike detected"},
            {"ts": "2026-04-10T14:00:00Z", "description": "Drawdown threshold breached"},
        ])
        self.assertEqual(len(pm.timeline), 2)

    def test_incident_evidence_refs_stored(self):
        pm = _make_postmortem(
            telemetry_event_ids=["evt-001"],
            reconciliation_ids=["drift-report-001", "recon-run-001"],
            incident_cluster_id="drift:rolling_drawdown_multiple",
            incident_evidence_summary="threshold breach evidence",
            lineage_ref="artifact-001@1.2.3",
        )
        self.assertEqual(pm.telemetry_event_ids, ["evt-001"])
        self.assertEqual(pm.reconciliation_ids, ["drift-report-001", "recon-run-001"])
        self.assertEqual(pm.incident_cluster_id, "drift:rolling_drawdown_multiple")
        self.assertEqual(pm.incident_evidence_summary, "threshold breach evidence")
        self.assertEqual(pm.lineage_ref, "artifact-001@1.2.3")


# ---------------------------------------------------------------------------
# validate_postmortem
# ---------------------------------------------------------------------------

class TestValidatePostmortem(unittest.TestCase):

    def test_valid_postmortem_has_no_errors(self):
        pm = _make_postmortem()
        self.assertEqual(validate_postmortem(pm), [])

    def test_missing_incident_id_raises_error(self):
        pm = _make_postmortem()
        patched = Postmortem(**{**pm.to_dict(), "incident_id": ""})
        errors = validate_postmortem(patched)
        self.assertTrue(any("incident_id" in e for e in errors))

    def test_missing_binding_id_raises_error(self):
        pm = _make_postmortem()
        patched = Postmortem(**{**pm.to_dict(), "binding_id": ""})
        errors = validate_postmortem(patched)
        self.assertTrue(any("binding_id" in e for e in errors))

    def test_missing_deployment_plan_id_raises_error(self):
        pm = _make_postmortem()
        patched = Postmortem(**{**pm.to_dict(), "deployment_plan_id": ""})
        errors = validate_postmortem(patched)
        self.assertTrue(any("deployment_plan_id" in e for e in errors))

    def test_missing_persona_capital_binding_id_raises_error(self):
        pm = _make_postmortem()
        patched = Postmortem(**{**pm.to_dict(), "persona_capital_binding_id": ""})
        errors = validate_postmortem(patched)
        self.assertTrue(any("persona_capital_binding_id" in e for e in errors))

    def test_missing_root_cause_raises_error(self):
        pm = _make_postmortem()
        patched = Postmortem(**{**pm.to_dict(), "root_cause": ""})
        errors = validate_postmortem(patched)
        self.assertTrue(any("root_cause" in e for e in errors))

    def test_missing_trace_id_raises_error(self):
        pm = _make_postmortem()
        patched = Postmortem(**{**pm.to_dict(), "trace_id": ""})
        errors = validate_postmortem(patched)
        self.assertTrue(any("trace_id" in e for e in errors))

    def test_missing_runtime_id_raises_error(self):
        pm = _make_postmortem()
        patched = Postmortem(**{**pm.to_dict(), "runtime_id": ""})
        errors = validate_postmortem(patched)
        self.assertTrue(any("runtime_id" in e for e in errors))

    def test_missing_capital_pool_id_raises_error(self):
        pm = _make_postmortem()
        patched = Postmortem(**{**pm.to_dict(), "capital_pool_id": ""})
        errors = validate_postmortem(patched)
        self.assertTrue(any("capital_pool_id" in e for e in errors))

    def test_published_without_published_at_is_error(self):
        pm = _make_postmortem(status="published")
        errors = validate_postmortem(pm)
        self.assertTrue(any("published_at" in e for e in errors))

    def test_published_with_published_at_is_valid(self):
        pm = _make_postmortem(status="published", published_at="2026-04-10T20:00:00Z")
        self.assertEqual(validate_postmortem(pm), [])


# ---------------------------------------------------------------------------
# IncidentStore
# ---------------------------------------------------------------------------

class TestIncidentStore(unittest.TestCase):

    def setUp(self):
        self.store = IncidentStore()

    def test_create_and_retrieve_incident(self):
        inc = _make_incident()
        self.store.create_incident(inc)
        retrieved = self.store.require_incident("inc-001")
        self.assertEqual(retrieved.incident_id, "inc-001")

    def test_create_duplicate_incident_raises(self):
        inc = _make_incident()
        self.store.create_incident(inc)
        with self.assertRaises(IncidentError):
            self.store.create_incident(inc)

    def test_create_invalid_incident_raises(self):
        inc = _make_incident()
        bad = IncidentCase(**{**inc.to_dict(), "binding_id": ""})
        with self.assertRaises(IncidentError):
            self.store.create_incident(bad)

    def test_require_missing_incident_raises(self):
        with self.assertRaises(IncidentError):
            self.store.require_incident("nonexistent")

    def test_get_returns_none_for_missing(self):
        self.assertIsNone(self.store.get_incident("nonexistent"))

    def test_list_incidents_returns_all(self):
        self.store.create_incident(_make_incident(incident_id="inc-001"))
        self.store.create_incident(_make_incident(incident_id="inc-002"))
        self.assertEqual(len(self.store.list_incidents()), 2)

    def test_find_incidents_by_binding(self):
        self.store.create_incident(_make_incident(incident_id="inc-001", binding_id="binding-A"))
        self.store.create_incident(_make_incident(incident_id="inc-002", binding_id="binding-B"))
        result = self.store.find_incidents_by_binding("binding-A")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].incident_id, "inc-001")

    def test_find_incidents_by_pool(self):
        self.store.create_incident(_make_incident(incident_id="inc-001", capital_pool_id="pool-X"))
        self.store.create_incident(_make_incident(incident_id="inc-002", capital_pool_id="pool-Y"))
        result = self.store.find_incidents_by_pool("pool-X")
        self.assertEqual(len(result), 1)

    def test_find_open_incidents(self):
        self.store.create_incident(_make_incident(incident_id="inc-001", status="open"))
        self.store.create_incident(_make_incident(incident_id="inc-002", status="investigating"))
        self.store.create_incident(_make_incident(
            incident_id="inc-003", status="resolved", resolved_at="2026-04-10T15:00:00Z"
        ))
        open_ones = self.store.find_open_incidents()
        self.assertEqual(len(open_ones), 2)

    def test_update_incident_status_to_resolved_sets_resolved_at(self):
        self.store.create_incident(_make_incident())
        updated = self.store.update_incident_status("inc-001", "resolved")
        self.assertIsNotNone(updated.resolved_at)
        self.assertEqual(updated.status, "resolved")

    def test_update_incident_status_to_investigating(self):
        self.store.create_incident(_make_incident())
        updated = self.store.update_incident_status("inc-001", "investigating")
        self.assertEqual(updated.status, "investigating")

    def test_update_incident_status_invalid_raises(self):
        self.store.create_incident(_make_incident())
        with self.assertRaises(IncidentError):
            self.store.update_incident_status("inc-001", "pending")

    def test_create_postmortem_requires_existing_incident(self):
        pm = _make_postmortem()
        with self.assertRaises(IncidentError):
            self.store.create_postmortem(pm)

    def test_create_postmortem_after_creating_incident(self):
        self.store.create_incident(_make_incident())
        pm = _make_postmortem()
        self.store.create_postmortem(pm)
        retrieved = self.store.require_postmortem("pm-001")
        self.assertEqual(retrieved.incident_id, "inc-001")

    def test_create_postmortem_rejects_mismatched_propagated_evidence(self):
        self.store.create_incident(_make_incident())
        mismatched = _make_postmortem(
            binding_id="binding-other",
            deployment_plan_id="plan-other",
            persona_capital_binding_id="pcb-other",
            artifact_id="artifact-other",
            runtime_id="runtime-other",
            trace_id="trace-other",
        )
        with self.assertRaises(IncidentError):
            self.store.create_postmortem(mismatched)

    def test_create_duplicate_postmortem_raises(self):
        self.store.create_incident(_make_incident())
        pm = _make_postmortem()
        self.store.create_postmortem(pm)
        with self.assertRaises(IncidentError):
            self.store.create_postmortem(pm)

    def test_find_postmortem_for_incident(self):
        self.store.create_incident(_make_incident())
        self.store.create_postmortem(_make_postmortem())
        pm = self.store.find_postmortem_for_incident("inc-001")
        self.assertIsNotNone(pm)
        self.assertEqual(pm.postmortem_id, "pm-001")

    def test_find_postmortem_for_incident_returns_none_when_absent(self):
        result = self.store.find_postmortem_for_incident("inc-999")
        self.assertIsNone(result)

    def test_update_postmortem_status_to_published_sets_published_at(self):
        self.store.create_incident(_make_incident())
        self.store.create_postmortem(_make_postmortem())
        updated = self.store.update_postmortem_status("pm-001", "published")
        self.assertIsNotNone(updated.published_at)
        self.assertEqual(updated.status, "published")

    def test_link_evolution_decision(self):
        self.store.create_incident(_make_incident())
        self.store.create_postmortem(_make_postmortem())
        updated = self.store.link_evolution_decision("pm-001", "evo-dec-001")
        self.assertEqual(updated.linked_evolution_decision_id, "evo-dec-001")

    def test_update_postmortem_draft_refreshes_evidence_refs(self):
        self.store.create_incident(_make_incident(telemetry_event_ids=["evt-001"]))
        self.store.create_postmortem(_make_postmortem())
        updated = self.store.update_postmortem_draft(
            _make_postmortem(
                telemetry_event_ids=["evt-001"],
                reconciliation_ids=["recon-001"],
                incident_evidence_summary="refreshed incident evidence",
            )
        )
        self.assertEqual(updated.telemetry_event_ids, ["evt-001"])
        self.assertEqual(updated.reconciliation_ids, ["recon-001"])
        self.assertEqual(updated.incident_evidence_summary, "refreshed incident evidence")


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

class TestPersistenceRoundTrip(unittest.TestCase):

    def test_incident_to_dict_from_dict(self):
        inc = _make_incident(telemetry_event_ids=["evt-1"], lineage_ref="artifact-001@1.2.3")
        d = inc.to_dict()
        restored = IncidentCase.from_dict(d)
        self.assertEqual(restored.incident_id, inc.incident_id)
        self.assertEqual(restored.binding_id, inc.binding_id)
        self.assertEqual(restored.telemetry_event_ids, ["evt-1"])
        self.assertEqual(restored.lineage_ref, "artifact-001@1.2.3")

    def test_incident_to_json_from_json(self):
        inc = _make_incident()
        restored = IncidentCase.from_json(inc.to_json())
        self.assertEqual(restored.incident_id, inc.incident_id)
        self.assertEqual(restored.deployment_stage, inc.deployment_stage)

    def test_postmortem_to_dict_from_dict(self):
        pm = _make_postmortem(
            contributing_factors=["vol spike"],
            action_items=["reduce size"],
            telemetry_event_ids=["evt-1"],
            reconciliation_ids=["recon-1"],
            incident_evidence_summary="incident summary",
        )
        d = pm.to_dict()
        restored = Postmortem.from_dict(d)
        self.assertEqual(restored.postmortem_id, pm.postmortem_id)
        self.assertEqual(restored.contributing_factors, ["vol spike"])
        self.assertEqual(restored.action_items, ["reduce size"])
        self.assertEqual(restored.telemetry_event_ids, ["evt-1"])
        self.assertEqual(restored.reconciliation_ids, ["recon-1"])
        self.assertEqual(restored.incident_evidence_summary, "incident summary")

    def test_postmortem_to_json_from_json(self):
        pm = _make_postmortem()
        restored = Postmortem.from_json(pm.to_json())
        self.assertEqual(restored.postmortem_id, pm.postmortem_id)
        self.assertEqual(restored.incident_id, pm.incident_id)

    def test_store_persistence_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "incident_store.json"
            store = IncidentStore(path=path)
            store.create_incident(_make_incident())
            store.create_postmortem(_make_postmortem())

            # Load fresh store from same file
            store2 = IncidentStore(path=path)
            self.assertIsNotNone(store2.get_incident("inc-001"))
            self.assertIsNotNone(store2.get_postmortem("pm-001"))
            self.assertEqual(store2.get_incident("inc-001").binding_id, "binding-abc")

    def test_to_dict_excludes_none_and_empty(self):
        inc = _make_incident()
        d = inc.to_dict()
        self.assertNotIn("resolved_at", d)
        self.assertNotIn("evidence_summary", d)
        self.assertNotIn("telemetry_event_ids", d)  # default empty list excluded


# ---------------------------------------------------------------------------
# Lineage edge coverage
# ---------------------------------------------------------------------------

class TestLineageEdgeCoverage(unittest.TestCase):
    """Verify that all normalized lineage edges defined in LIN-001 are carried."""

    def test_incident_carries_incident_case_runtime_binding_edge(self):
        inc = _make_incident()
        # Edge: incident_case.runtime_binding
        self.assertTrue(hasattr(inc, "binding_id"))
        self.assertEqual(inc.binding_id, "binding-abc")

    def test_incident_carries_deployment_evidence(self):
        inc = _make_incident()
        self.assertEqual(inc.deployment_stage, "live")
        self.assertEqual(inc.deployment_plan_id, "plan-xyz")
        self.assertEqual(inc.capital_pool_id, "pool-001")
        self.assertEqual(inc.persona_capital_binding_id, "pcb-001")
        self.assertEqual(inc.artifact_id, "artifact-001")
        self.assertEqual(inc.artifact_version, "1.2.3")
        self.assertEqual(inc.runtime_id, "runtime-001")
        self.assertEqual(inc.trace_id, "trace-abc")

    def test_postmortem_carries_postmortem_incident_case_edge(self):
        pm = _make_postmortem()
        # Edge: postmortem.incident_case
        self.assertTrue(hasattr(pm, "incident_id"))
        self.assertEqual(pm.incident_id, "inc-001")

    def test_postmortem_propagates_lineage_evidence(self):
        pm = _make_postmortem()
        self.assertEqual(pm.binding_id, "binding-abc")
        self.assertEqual(pm.deployment_stage, "live")
        self.assertEqual(pm.capital_pool_id, "pool-001")
        self.assertEqual(pm.artifact_id, "artifact-001")
        self.assertEqual(pm.artifact_version, "1.2.3")
        self.assertEqual(pm.trace_id, "trace-abc")

    def test_postmortem_has_evolution_decision_link_slot(self):
        pm = _make_postmortem()
        # evolution_decision.postmortem edge: EvolutionDecision → Postmortem
        self.assertTrue(hasattr(pm, "linked_evolution_decision_id"))
        self.assertIsNone(pm.linked_evolution_decision_id)


if __name__ == "__main__":
    unittest.main()
