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

from datetime import datetime, timezone

from services.incident.incident import (
    IncidentCase,
    IncidentError,
    IncidentSeverity,
    IncidentStatus,
    IncidentStore,
    MONITOR_AGENT_ARTIFACT_PREFIX,
    MONITOR_AGENT_AUTHORITY_ID,
    MONITOR_AGENT_BINDING_ID,
    MONITOR_AGENT_CLUSTER_PREFIX,
    MONITOR_AGENT_COMPONENT_KIND,
    MONITOR_AGENT_PLAN_ID,
    MONITOR_AGENT_POOL_ID,
    MONITOR_AGENT_RUNTIME_ID,
    NOT_REOBSERVED_RESOLUTION_REASON,
    ORPHAN_RECOVERED_RESOLUTION_REASON,
    Postmortem,
    PostmortemStatus,
    _merge_summary,
    build_incident_timeline,
    canonical_monitor_agent_finding_key,
    derive_incident_summary_fields,
    extract_incident_last_observed_at,
    extract_incident_resolution_reason,
    extract_infrastructure_incident_component,
    extract_infrastructure_incident_referenced_ids,
    is_infrastructure_health_incident,
    is_monitor_agent_finding_incident,
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


# ---------------------------------------------------------------------------
# Evidence summary bounding (MONITOR-AGENT-PROMPT-BOUND-20261009)
# ---------------------------------------------------------------------------

class TestIncidentEvidenceMergeBound(unittest.TestCase):
    def test_merge_incident_evidence_replaces_single_report_summary_and_increments_occurrences(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = IncidentStore(path=Path(tmpdir) / "incidents.json")
            inc = _make_incident(
                incident_id="inc-bound-001",
                created_at="2026-11-18T11:36:14Z",
                evidence_summary="Initial breach report",
                telemetry_event_ids=["te-0"],
                reconciliation_ids=["rec-0"],
                kind="drift",
                metric="rolling_drawdown_multiple",
                observed=1.30,
                threshold=1.25,
                comparator="gt",
                occurrence_count=1,
                first_seen_at="2026-11-18T11:36:14Z",
                last_seen_at="2026-11-18T11:36:14Z",
            )
            store.create_incident(inc)

            for i in range(1, 30):
                incoming = _make_incident(
                    incident_id="inc-bound-001",
                    created_at=f"2026-11-18T12:{i:02d}:00Z",
                    evidence_summary=f"Drift report breach #{i} with details: " + ("x" * 200),
                    telemetry_event_ids=[f"te-{i}"],
                    reconciliation_ids=[f"rec-{i}"],
                    kind="drift",
                    metric="rolling_drawdown_multiple",
                    observed=1.30 + (i * 0.1),
                    threshold=1.25,
                    comparator="gt",
                )
                updated = store.merge_incident_evidence("inc-bound-001", incoming)
                self.assertEqual(
                    updated.evidence_summary,
                    f"Drift report breach #{i} with details: " + ("x" * 200),
                )
                self.assertNotIn("Drift report breach #0", updated.evidence_summary)
                self.assertEqual(updated.occurrence_count, i + 1)

            final = store.get_incident("inc-bound-001")
            self.assertIsNotNone(final)
            self.assertEqual(
                final.evidence_summary,
                "Drift report breach #29 with details: " + ("x" * 200),
            )
            self.assertNotIn("[older entries dropped] ", final.evidence_summary)
            self.assertEqual(final.occurrence_count, 30)
            self.assertEqual(final.first_seen_at, "2026-11-18T11:36:14Z")
            self.assertEqual(final.last_seen_at, "2026-11-18T12:29:00Z")
            self.assertAlmostEqual(final.observed, 4.20)
            for i in range(30):
                self.assertIn(f"te-{i}", final.telemetry_event_ids)
                self.assertIn(f"rec-{i}", final.reconciliation_ids)
            self.assertEqual(len(final.telemetry_event_ids), 30)
            self.assertEqual(len(final.reconciliation_ids), 30)
            timeline = build_incident_timeline(final)
            self.assertEqual(len(timeline), 30)

    def test_merge_incident_evidence_reduces_existing_oversized_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = IncidentStore(path=Path(tmpdir) / "incidents.json")
            oversized_summary = "Old drift reports: " + ("drift_entry; " * 15000)
            self.assertGreater(len(oversized_summary), 100000)
            inc = _make_incident(
                incident_id="inc-bound-002",
                evidence_summary=oversized_summary,
                telemetry_event_ids=["te-old-1", "te-old-2"],
                reconciliation_ids=["rec-old-1"],
            )
            store.create_incident(inc)

            incoming = _make_incident(
                incident_id="inc-bound-002",
                evidence_summary="Newest incoming breach report 2026-10-09",
                telemetry_event_ids=["te-new-1"],
                reconciliation_ids=["rec-new-1"],
            )
            updated = store.merge_incident_evidence("inc-bound-002", incoming)
            self.assertEqual(updated.evidence_summary, "Newest incoming breach report 2026-10-09")
            self.assertEqual(updated.occurrence_count, 3)
            self.assertEqual(
                set(updated.telemetry_event_ids),
                {"te-old-1", "te-old-2", "te-new-1"},
            )
            self.assertEqual(
                set(updated.reconciliation_ids),
                {"rec-old-1", "rec-new-1"},
            )

    def test_merge_incident_evidence_bounds_single_oversized_incoming_and_dedupes_repeat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = IncidentStore(path=Path(tmpdir) / "incidents.json")
            inc = _make_incident(
                incident_id="inc-bound-003",
                evidence_summary="seed_entry=1",
            )
            store.create_incident(inc)

            for i in range(40):
                incoming = _make_incident(
                    incident_id="inc-bound-003",
                    evidence_summary=f"metric_{i}=drawdown; observed=0.{i:03d}; details_{i}=" + ("e" * 300),
                )
                store.merge_incident_evidence("inc-bound-003", incoming)

            latest = store.get_incident("inc-bound-003")
            self.assertIsNotNone(latest)
            self.assertLessEqual(len(latest.evidence_summary), 4000)
            self.assertTrue(latest.evidence_summary.startswith("metric_39=drawdown;"))
            self.assertNotIn("metric_38=", latest.evidence_summary)
            self.assertEqual(latest.occurrence_count, 41)
            self.assertAlmostEqual(latest.observed, 0.039)

            short_incoming = _make_incident(
                incident_id="inc-bound-003",
                evidence_summary="short_metric=breach",
            )
            updated_short = store.merge_incident_evidence("inc-bound-003", short_incoming)
            self.assertEqual(updated_short.evidence_summary, "short_metric=breach")
            self.assertEqual(updated_short.occurrence_count, 42)

            dup_incoming = _make_incident(
                incident_id="inc-bound-003",
                evidence_summary="short_metric=breach",
            )
            updated_dup = store.merge_incident_evidence("inc-bound-003", dup_incoming)
            self.assertEqual(updated_dup.evidence_summary, updated_short.evidence_summary)
            self.assertEqual(updated_dup.occurrence_count, 42)

    def test_merge_incident_evidence_skips_save_when_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = IncidentStore(path=Path(tmpdir) / "incidents.json")
            store.create_incident(_make_incident(
                incident_id="inc-noop-001",
                evidence_summary="drift=breach",
                telemetry_event_ids=["te-0"],
                reconciliation_ids=["rec-0"],
            ))

            def incoming(**kw):
                return _make_incident(incident_id="inc-noop-001", **kw)

            saves = []
            original_save = store._save

            def counting_save(*args, **kwargs):
                saves.append(1)
                return original_save(*args, **kwargs)

            store._save = counting_save

            same = dict(
                evidence_summary="drift=breach",
                telemetry_event_ids=["te-0"],
                reconciliation_ids=["rec-0"],
            )
            first = store.merge_incident_evidence("inc-noop-001", incoming(**same))
            second = store.merge_incident_evidence("inc-noop-001", incoming(**same))
            self.assertEqual(saves, [])
            self.assertEqual(first.to_dict(), second.to_dict())

            changed = store.merge_incident_evidence(
                "inc-noop-001",
                incoming(evidence_summary="drift=breach2", telemetry_event_ids=["te-1"], reconciliation_ids=["rec-0"]),
            )
            self.assertEqual(len(saves), 1)
            self.assertIn("te-1", changed.telemetry_event_ids)
            self.assertIn("te-1", store.get_incident("inc-noop-001").telemetry_event_ids)


def _make_agent_finding_incident(
    incident_id: str = "agent-0123456789abcdef",
    key: str = "reconciler-generation-change",
    created_at: str = "2026-11-01T10:00:00Z",
    **kw,
) -> IncidentCase:
    base = dict(
        incident_id=incident_id,
        title="Reconciler generation jumped after restart",
        status="open",
        severity="medium",
        created_at=created_at,
        binding_id=MONITOR_AGENT_BINDING_ID,
        deployment_stage="paper",
        deployment_plan_id=MONITOR_AGENT_PLAN_ID,
        capital_pool_id=MONITOR_AGENT_POOL_ID,
        persona_capital_binding_id=MONITOR_AGENT_AUTHORITY_ID,
        artifact_id=f"{MONITOR_AGENT_ARTIFACT_PREFIX}{key}",
        artifact_version="1.0.0",
        runtime_id=MONITOR_AGENT_RUNTIME_ID,
        trace_id="monitor-snapshot-20261101T100000Z-112233445566",
        telemetry_event_ids=["monitor-snapshot-20261101T100000Z-112233445566"],
        incident_cluster_id=f"{MONITOR_AGENT_CLUSTER_PREFIX}{key}",
        evidence_summary=(
            f"component_kind={MONITOR_AGENT_COMPONENT_KIND}; component=agent-finding-{key}; "
            f"fingerprint={key}; snapshot_ref=monitor-snapshot-20261101T100000Z-112233445566; "
            "rationale=observed generation change"
        ),
        lineage_ref=f"{MONITOR_AGENT_ARTIFACT_PREFIX}{key}@1.0.0",
    )
    base.update(kw)
    return IncidentCase(**base)


class TestMonitorAgentFindingDedupeAndResolve(unittest.TestCase):
    def test_is_monitor_agent_finding_incident_strict_scope(self):
        agent_inc = _make_agent_finding_incident()
        self.assertTrue(is_monitor_agent_finding_incident(agent_inc))

        runtime_inc = _make_incident(incident_id="inc-runtime-01")
        self.assertFalse(is_monitor_agent_finding_incident(runtime_inc))

        drift_inc = _make_incident(
            incident_id="inc-drift-01",
            incident_cluster_id="drift:binding-01:runtime-01:position_drift",
        )
        self.assertFalse(is_monitor_agent_finding_incident(drift_inc))

        infra_inc = _make_incident(
            incident_id="infra-health-01",
            binding_id="infra-subject-default-monitor-agent-svc",
            deployment_plan_id=MONITOR_AGENT_PLAN_ID,
            persona_capital_binding_id=MONITOR_AGENT_AUTHORITY_ID,
            runtime_id="infra-endpoint-1234567890ab",
        )
        self.assertFalse(is_monitor_agent_finding_incident(infra_inc))

    def test_canonical_monitor_agent_finding_key_normalizes_noise_families(self):
        self.assertEqual(
            canonical_monitor_agent_finding_key(
                "reconciler-generation-mismatch-gen78",
                "Reconciler generation advanced to 78",
                "fence_token jumped after restart",
            ),
            "reconciler-generation-change",
        )
        self.assertEqual(
            canonical_monitor_agent_finding_key(
                "paper-fleet-runtime-count-drift-12",
                "Paper fleet runtime count changed",
                "active_runtimes moved from 10 to 12",
            ),
            "fleet-size-change",
        )
        self.assertEqual(
            canonical_monitor_agent_finding_key(
                "telemetry-lag",
                "Telemetry lag exceeds threshold",
                "No events observed for 10m",
            ),
            "telemetry-lag",
        )

    def test_resolve_stale_monitor_agent_findings_only_resolves_expired_agent_findings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = IncidentStore(path=Path(tmpdir) / "incidents.json")
            stale_agent = _make_agent_finding_incident(
                incident_id="agent-stale000000000001",
                created_at="2026-11-01T10:00:00Z",
            )
            fresh_agent = _make_agent_finding_incident(
                incident_id="agent-fresh000000000002",
                key="fleet-size-change",
                created_at="2026-11-01T11:30:00Z",
                trace_id="monitor-snapshot-20261101T113000Z-abcdef123456",
                telemetry_event_ids=["monitor-snapshot-20261101T113000Z-abcdef123456"],
                evidence_summary=(
                    f"component_kind={MONITOR_AGENT_COMPONENT_KIND}; component=agent-finding-fleet-size-change; "
                    "observed_at=2026-11-01T11:30:00Z; snapshot_ref=monitor-snapshot-20261101T113000Z-abcdef123456"
                ),
            )
            stale_runtime = _make_incident(
                incident_id="inc-runtime-old",
                created_at="2026-10-01T00:00:00Z",
            )
            store.create_incident(stale_agent)
            store.create_incident(fresh_agent)
            store.create_incident(stale_runtime)

            now = datetime(2026, 11, 1, 12, 30, 0, tzinfo=timezone.utc)
            self.assertTrue(
                store.has_stale_monitor_agent_findings(
                    resolve_after_seconds=7200,
                    now=now,
                )
            )
            resolved = store.resolve_stale_monitor_agent_findings(
                resolve_after_seconds=7200,
                now=now,
            )
            self.assertEqual([inc.incident_id for inc in resolved], ["agent-stale000000000001"])
            self.assertFalse(
                store.has_stale_monitor_agent_findings(
                    resolve_after_seconds=7200,
                    now=now,
                )
            )

            reloaded_stale = store.require_incident("agent-stale000000000001")
            self.assertEqual(reloaded_stale.status, IncidentStatus.RESOLVED.value)
            self.assertEqual(
                extract_incident_resolution_reason(reloaded_stale),
                NOT_REOBSERVED_RESOLUTION_REASON,
            )
            self.assertIn("resolution_reason=not_reobserved", reloaded_stale.evidence_summary or "")
            self.assertIn("resolution_reason:not_reobserved", reloaded_stale.telemetry_event_ids)

            self.assertEqual(store.require_incident("agent-fresh000000000002").status, IncidentStatus.OPEN.value)
            self.assertEqual(store.require_incident("inc-runtime-old").status, IncidentStatus.OPEN.value)

    def test_reobserved_finding_updates_last_observed_at_and_stays_open(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = IncidentStore(path=Path(tmpdir) / "incidents.json")
            inc = _make_agent_finding_incident(
                incident_id="agent-reobs00000000001",
                created_at="2026-11-01T10:00:00Z",
            )
            store.create_incident(inc)
            incoming = _make_agent_finding_incident(
                incident_id="agent-reobs00000000001",
                created_at="2026-11-01T11:45:00Z",
                telemetry_event_ids=["monitor-snapshot-20261101T114500Z-999999999999"],
                evidence_summary=(
                    f"component_kind={MONITOR_AGENT_COMPONENT_KIND}; "
                    "observed_at=2026-11-01T11:45:00Z; "
                    "snapshot_ref=monitor-snapshot-20261101T114500Z-999999999999; "
                    "rationale=re-observed generation advance"
                ),
            )
            updated = store.merge_incident_evidence("agent-reobs00000000001", incoming)
            self.assertEqual(
                extract_incident_last_observed_at(updated),
                datetime(2026, 11, 1, 11, 45, 0, tzinfo=timezone.utc),
            )

            now = datetime(2026, 11, 1, 12, 30, 0, tzinfo=timezone.utc)
            resolved = store.resolve_stale_monitor_agent_findings(
                resolve_after_seconds=7200,
                now=now,
            )
            self.assertEqual(resolved, [])
            self.assertEqual(store.require_incident("agent-reobs00000000001").status, IncidentStatus.OPEN.value)


def _make_infra_health_incident(
    incident_id: str = "infra-bff-0847594c513bbe71b6aedcd3",
    component: str = "governance",
    created_at: str = "2026-10-22T08:00:00Z",
    source_event_id: str = "evt-infra-001",
    **kw,
) -> IncidentCase:
    base = dict(
        incident_id=incident_id,
        title=f"BFF downstream degradation: {component}",
        status="open",
        severity="high",
        created_at=created_at,
        binding_id=f"infra-subject-default-control-plane-bff-{component}",
        deployment_stage="paper",
        deployment_plan_id="infra-plan-control-plane-bff",
        capital_pool_id="infra-tenant-default",
        persona_capital_binding_id="infra-authority-control-plane-bff",
        artifact_id=f"infra-component-{component}",
        artifact_version="endpoint-1234567890ab",
        runtime_id="infra-endpoint-1234567890ab",
        trace_id=source_event_id,
        telemetry_event_ids=[source_event_id],
        incident_cluster_id=f"infrastructure:default:control-plane-bff:{component}",
        evidence_summary=(
            f"non_trading_infrastructure_incident=true; tenant_id=default; "
            f"producer=control-plane-bff; service_name={component}; "
            f"component_kind=http_service; endpoint=http://{component}:8080/__health__; "
            f"source_event_id={source_event_id}"
        ),
        lineage_ref=f"infra-component-{component}@endpoint-1234567890ab",
    )
    base.update(kw)
    return IncidentCase(**base)


class TestInfrastructureIncidentOrphanResolve(unittest.TestCase):
    def test_is_infrastructure_health_incident_strict_scope(self):
        infra_inc = _make_infra_health_incident()
        self.assertTrue(is_infrastructure_health_incident(infra_inc))
        self.assertEqual(extract_infrastructure_incident_component(infra_inc), "governance")

        agent_inc = _make_agent_finding_incident()
        self.assertFalse(is_infrastructure_health_incident(agent_inc))
        self.assertIsNone(extract_infrastructure_incident_component(agent_inc))

        runtime_inc = _make_incident(incident_id="inc-runtime-01")
        self.assertFalse(is_infrastructure_health_incident(runtime_inc))

        drift_inc = _make_incident(
            incident_id="inc-drift-01",
            incident_cluster_id="drift:binding-01:runtime-01:position_drift",
        )
        self.assertFalse(is_infrastructure_health_incident(drift_inc))

        deploy_inc = _make_incident(
            incident_id="inc-deploy-01",
            incident_cluster_id="deployment:plan-01:rollout_failed",
        )
        self.assertFalse(is_infrastructure_health_incident(deploy_inc))

    def test_upsert_open_infrastructure_incident_merges_second_open_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = IncidentStore(path=Path(tmpdir) / "incidents.json")
            first = _make_infra_health_incident(
                incident_id="infra-bff-0847594c513bbe71b6aedcd3",
                component="governance",
                created_at="2026-10-22T08:00:00Z",
                source_event_id="evt-gov-1022",
            )
            second = _make_infra_health_incident(
                incident_id="infra-bff-2b8541b81f8d5796101ad57e",
                component="governance",
                created_at="2026-10-27T01:32:00Z",
                source_event_id="evt-gov-1027",
            )
            inc1, created1 = store.upsert_open_infrastructure_incident(first)
            self.assertTrue(created1)
            self.assertEqual(inc1.incident_id, "infra-bff-0847594c513bbe71b6aedcd3")

            inc2, created2 = store.upsert_open_infrastructure_incident(second)
            self.assertFalse(created2)
            self.assertEqual(inc2.incident_id, "infra-bff-0847594c513bbe71b6aedcd3")
            self.assertEqual(
                len(store.find_open_infrastructure_incidents_for_component("governance")),
                1,
            )
            self.assertIn("evt-gov-1027", inc2.telemetry_event_ids)
            self.assertIn(
                "infra-bff-2b8541b81f8d5796101ad57e",
                extract_infrastructure_incident_referenced_ids(inc2),
            )
            alias_found = store.find_infrastructure_incident_by_id_or_alias(
                "infra-bff-2b8541b81f8d5796101ad57e"
            )
            self.assertIsNotNone(alias_found)
            self.assertEqual(alias_found.incident_id, "infra-bff-0847594c513bbe71b6aedcd3")

    def test_resolve_orphaned_infrastructure_incidents_records_orphan_recovered(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = IncidentStore(path=Path(tmpdir) / "incidents.json")
            orphan_gov = _make_infra_health_incident(
                incident_id="infra-bff-0847594c513bbe71b6aedcd3",
                component="governance",
                created_at="2026-10-22T08:00:00Z",
            )
            referenced_cap = _make_infra_health_incident(
                incident_id="infra-bff-cap-active",
                component="capital",
                created_at="2026-11-03T01:00:00Z",
            )
            agent_inc = _make_agent_finding_incident(incident_id="agent-keep-open-001")
            runtime_inc = _make_incident(incident_id="inc-runtime-keep-open")

            store.create_incident(orphan_gov)
            store.create_incident(referenced_cap)
            store.create_incident(agent_inc)
            store.create_incident(runtime_inc)

            self.assertTrue(
                store.has_orphaned_infrastructure_incidents(
                    healthy_components=["governance", "capital"],
                    referenced_incident_ids=["infra-bff-cap-active"],
                )
            )
            resolved = store.resolve_orphaned_infrastructure_incidents(
                healthy_components=["governance", "capital"],
                referenced_incident_ids=["infra-bff-cap-active"],
                resolved_at="2026-11-03T02:00:00Z",
            )
            self.assertEqual([i.incident_id for i in resolved], ["infra-bff-0847594c513bbe71b6aedcd3"])
            self.assertFalse(
                store.has_orphaned_infrastructure_incidents(
                    healthy_components=["governance", "capital"],
                    referenced_incident_ids=["infra-bff-cap-active"],
                )
            )
            reloaded = store.require_incident("infra-bff-0847594c513bbe71b6aedcd3")
            self.assertEqual(reloaded.status, IncidentStatus.RESOLVED.value)
            self.assertEqual(
                extract_incident_resolution_reason(reloaded),
                ORPHAN_RECOVERED_RESOLUTION_REASON,
            )
            self.assertIn("resolution_reason=orphan_recovered", reloaded.evidence_summary or "")
            self.assertIn("resolution_reason:orphan_recovered", reloaded.telemetry_event_ids)

            self.assertEqual(store.require_incident("infra-bff-cap-active").status, IncidentStatus.OPEN.value)
            self.assertEqual(store.require_incident("agent-keep-open-001").status, IncidentStatus.OPEN.value)
            self.assertEqual(store.require_incident("inc-runtime-keep-open").status, IncidentStatus.OPEN.value)


# ---------------------------------------------------------------------------
# INCIDENT-HUMAN-SUMMARY-AND-OCCURRENCES-20261120 (RC6 WP6.1)
# ---------------------------------------------------------------------------

_HOSTED_INC_DRIFT_5F668EC4C8D9_EVIDENCE_SUMMARY = (
    "[older entries dropped] DriftReport drift-76586033-ad6a-4f09-ae0d-9a2e756617a5-drawdown; "
    "cluster=drift:drawdown; reconciliation_ids=drift-76586033-ad6a-4f09-ae0d-9a2e756617a5-drawdown,"
    "recon-76586033-ad6a-4f09-ae0d-9a2e756617a5,eval-76586033-ad6a-4f09-ae0d-9a2e756617a5; "
    "telemetry_event_ids=76586033-ad6a-4f09-ae0d-9a2e756617a5; worst_metric=drawdown; "
    "threshold_metric=rolling_drawdown_multiple; observed=9.609309450237793; comparator=gt; "
    "threshold_value=1.25; threshold_window=paper-daily-sweep; "
    "policy_source=EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1; "
    "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-005-governed-baselines.md#governance-decision; "
    "DriftReport drift-0b7a8a43-5c02-403e-970f-1a637517a78a-drawdown; cluster=drift:drawdown; "
    "reconciliation_ids=drift-0b7a8a43-5c02-403e-970f-1a637517a78a-drawdown,"
    "recon-0b7a8a43-5c02-403e-970f-1a637517a78a,eval-0b7a8a43-5c02-403e-970f-1a637517a78a; "
    "telemetry_event_ids=0b7a8a43-5c02-403e-970f-1a637517a78a; worst_metric=drawdown; "
    "threshold_metric=rolling_drawdown_multiple; observed=9.609309450237793; comparator=gt; "
    "threshold_value=1.25; threshold_window=paper-daily-sweep; "
    "policy_source=EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1; "
    "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-005-governed-baselines.md#governance-decision; "
    "DriftReport drift-94f2d4bb-75e5-4f50-ae40-53124d5872e7-drawdown; cluster=drift:drawdown; "
    "reconciliation_ids=drift-94f2d4bb-75e5-4f50-ae40-53124d5872e7-drawdown,"
    "recon-94f2d4bb-75e5-4f50-ae40-53124d5872e7,eval-94f2d4bb-75e5-4f50-ae40-53124d5872e7; "
    "telemetry_event_ids=94f2d4bb-75e5-4f50-ae40-53124d5872e7; worst_metric=drawdown; "
    "threshold_metric=rolling_drawdown_multiple; observed=9.609309450237793; comparator=gt; "
    "threshold_value=1.25; threshold_window=paper-daily-sweep; "
    "policy_source=EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1; "
    "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-005-governed-baselines.md#governance-decision; "
    "DriftReport drift-bf92e54f-13a9-4a76-b7a2-875427646c5c-drawdown; cluster=drift:drawdown; "
    "reconciliation_ids=drift-bf92e54f-13a9-4a76-b7a2-875427646c5c-drawdown,"
    "recon-bf92e54f-13a9-4a76-b7a2-875427646c5c,eval-bf92e54f-13a9-4a76-b7a2-875427646c5c; "
    "telemetry_event_ids=bf92e54f-13a9-4a76-b7a2-875427646c5c; worst_metric=drawdown; "
    "threshold_metric=rolling_drawdown_multiple; observed=9.609309450237793; comparator=gt; "
    "threshold_value=1.25; threshold_window=paper-daily-sweep; "
    "policy_source=EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1; "
    "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-005-governed-baselines.md#governance-decision"
)

_CURRENT_DEV_INCIDENT_CASE_FIELDS = frozenset({
    "incident_id",
    "title",
    "status",
    "severity",
    "created_at",
    "binding_id",
    "deployment_stage",
    "deployment_plan_id",
    "capital_pool_id",
    "persona_capital_binding_id",
    "artifact_id",
    "artifact_version",
    "runtime_id",
    "trace_id",
    "resolved_at",
    "telemetry_event_ids",
    "reconciliation_ids",
    "incident_cluster_id",
    "evidence_summary",
    "lineage_ref",
    "threshold_identity",
})


class TestIncidentHumanSummaryAndOccurrences(unittest.TestCase):
    def test_reproduce_current_dev_four_drift_reports_vs_bounded_single_report_update(self):
        """Acceptance 1, 2, 3: Reproduce current dev (BFF fb8ed951d, 2026-11-20 sweep)
        where feeding 4 DriftReports for one binding (cluster drift:drawdown;
        observed 9.609309450237793; threshold 1.25) concatenated evidence_summary
        and showed that log as the inbox body, then verify the new bounded store
        behavior updates occurrence_count, last_seen_at, and observed without
        growing evidence_summary.
        """
        from services.incidents.consumer import (
            DriftReportIncidentConsumer,
            build_incident_from_drift_report,
        )

        event_ids = [
            "76586033-ad6a-4f09-ae0d-9a2e756617a5",
            "0b7a8a43-5c02-403e-970f-1a637517a78a",
            "94f2d4bb-75e5-4f50-ae40-53124d5872e7",
            "bf92e54f-13a9-4a76-b7a2-875427646c5c",
        ]
        timestamps = [
            "2026-11-18T11:36:14Z",
            "2026-11-18T12:36:14Z",
            "2026-11-18T13:36:14Z",
            "2026-11-18T14:36:14Z",
        ]
        reports = []
        for eid, ts in zip(event_ids, timestamps):
            reports.append(
                {
                    "drift_report_id": f"drift-{eid}-drawdown",
                    "recon_run_id": f"recon-{eid}",
                    "evaluation_id": f"eval-{eid}",
                    "incident_cluster_id": "drift:drawdown",
                    "drift_type": "pnl",
                    "status": "open",
                    "severity": "medium",
                    "binding_id": "rb-3823397e125e4579ba5f4174067087ed",
                    "runtime_id": "runtime-paper-persona-1",
                    "deployment_stage": "paper",
                    "deployment_plan_id": "plan-paper-persona-1",
                    "capital_pool_id": "pool-paper-persona-1",
                    "persona_capital_binding_id": "pcb-paper-persona-1",
                    "artifact_id": "artifact-paper-persona-1",
                    "artifact_version": "1.0.0",
                    "trace_id": f"trace-{eid}",
                    "telemetry_event_ids": [eid],
                    "worst_metric": "drawdown",
                    "threshold_metric": "rolling_drawdown_multiple",
                    "observed": 9.609309450237793,
                    "comparator": "gt",
                    "threshold_value": 1.25,
                    "threshold_window": "paper-daily-sweep",
                    "policy_source": (
                        "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1; "
                        "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/"
                        "EVOLOOP-005-governed-baselines.md#governance-decision"
                    ),
                    "generated_at": ts,
                }
            )

        # 1. Reproduce current-dev (fb8ed951d) behavior: _merge_summary appended each DriftReport
        # and current-dev _human_inbox_incident_item fell back to record["evidence_summary"].
        legacy_appended_summary = ""
        for rep in reports:
            built = build_incident_from_drift_report(rep)
            legacy_appended_summary = _merge_summary(legacy_appended_summary, built.evidence_summary or "")
        current_dev_inbox_body = None or legacy_appended_summary
        self.assertEqual(current_dev_inbox_body.count("DriftReport drift-"), 4)
        self.assertIn("observed=9.609309450237793", current_dev_inbox_body)
        self.assertIn("threshold_value=1.25", current_dev_inbox_body)
        self.assertIn("EVOLOOP-005-governed-baselines.md#governance-decision", current_dev_inbox_body)

        # 2. Verify new DriftReportIncidentConsumer + IncidentStore behavior on the same 4 reports
        store = IncidentStore(path=None)
        consumer = DriftReportIncidentConsumer(incident_store=store)
        results = [consumer.consume(rep) for rep in reports]
        self.assertTrue(results[0].created)
        self.assertFalse(results[1].created)
        self.assertFalse(results[2].created)
        self.assertFalse(results[3].created)

        persisted = store.require_incident(results[-1].incident.incident_id)
        self.assertEqual(persisted.kind, "drift")
        self.assertEqual(persisted.metric, "rolling_drawdown_multiple")
        self.assertAlmostEqual(persisted.observed, 9.609309450237793)
        self.assertAlmostEqual(persisted.threshold, 1.25)
        self.assertEqual(persisted.comparator, "gt")
        self.assertEqual(persisted.occurrence_count, 4)
        self.assertEqual(persisted.first_seen_at, "2026-11-18T11:36:14Z")
        self.assertEqual(persisted.last_seen_at, "2026-11-18T14:36:14Z")

        # evidence_summary holds only the latest single report, never an appended log
        self.assertEqual((persisted.evidence_summary or "").count("DriftReport drift-"), 1)
        self.assertIn(f"DriftReport drift-{event_ids[-1]}-drawdown", persisted.evidence_summary or "")
        self.assertNotIn(f"DriftReport drift-{event_ids[0]}-drawdown", persisted.evidence_summary or "")

        # Every report stays retrievable as a timeline entry and evidence reference
        self.assertEqual(persisted.telemetry_event_ids, event_ids)
        for eid in event_ids:
            self.assertIn(f"drift-{eid}-drawdown", persisted.reconciliation_ids)
        timeline = build_incident_timeline(persisted)
        self.assertEqual(len(timeline), 4)
        self.assertEqual(
            [entry["source_ref"] for entry in timeline],
            [f"drift-{eid}-drawdown" for eid in event_ids],
        )

    def test_existing_stored_incident_with_appended_evidence_summary_derives_at_read_time_without_rewrite(self):
        """Acceptance 6: Existing incidents with an appended evidence_summary get a
        summary derived from their stored fields at read time; no stored row is
        rewritten by a migration.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "incidents.json"
            legacy_raw_row = {
                "incident_id": "inc-drift-5f668ec4c8d9",
                "title": "Drift threshold breach: drift:drawdown for rb-3823397e125e4579ba5f4174067087ed",
                "status": "open",
                "severity": "medium",
                "created_at": "2026-11-18T11:36:14Z",
                "binding_id": "rb-3823397e125e4579ba5f4174067087ed",
                "deployment_stage": "paper",
                "deployment_plan_id": "plan-3823397e125e4579ba5f4174067087ed",
                "capital_pool_id": "pool-3823397e125e4579ba5f4174067087ed",
                "persona_capital_binding_id": "pcb-3823397e125e4579ba5f4174067087ed",
                "artifact_id": "artifact-3823397e125e4579ba5f4174067087ed",
                "artifact_version": "1.0.0",
                "runtime_id": "runtime-3823397e125e4579ba5f4174067087ed",
                "trace_id": "trace-76586033-ad6a-4f09-ae0d-9a2e756617a5",
                "incident_cluster_id": "drift:drawdown",
                "telemetry_event_ids": [
                    "76586033-ad6a-4f09-ae0d-9a2e756617a5",
                    "0b7a8a43-5c02-403e-970f-1a637517a78a",
                    "94f2d4bb-75e5-4f50-ae40-53124d5872e7",
                    "bf92e54f-13a9-4a76-b7a2-875427646c5c",
                ],
                "reconciliation_ids": [
                    "drift-76586033-ad6a-4f09-ae0d-9a2e756617a5-drawdown",
                    "drift-0b7a8a43-5c02-403e-970f-1a637517a78a-drawdown",
                    "drift-94f2d4bb-75e5-4f50-ae40-53124d5872e7-drawdown",
                    "drift-bf92e54f-13a9-4a76-b7a2-875427646c5c-drawdown",
                ],
                "evidence_summary": _HOSTED_INC_DRIFT_5F668EC4C8D9_EVIDENCE_SUMMARY,
            }
            path.write_text(json.dumps({"incidents": [legacy_raw_row], "postmortems": []}, indent=2))
            bytes_before = path.read_bytes()

            store = IncidentStore(path=path)
            loaded = store.require_incident("inc-drift-5f668ec4c8d9")
            derived = derive_incident_summary_fields(loaded)

            self.assertEqual(derived["kind"], "drift")
            self.assertEqual(derived["metric"], "rolling_drawdown_multiple")
            self.assertAlmostEqual(derived["observed"], 9.609309450237793)
            self.assertAlmostEqual(derived["threshold"], 1.25)
            self.assertEqual(derived["comparator"], "gt")
            self.assertEqual(derived["occurrence_count"], 4)
            self.assertEqual(derived["first_seen_at"], "2026-11-18T11:36:14Z")
            self.assertEqual(derived["last_seen_at"], "2026-11-18T11:36:14Z")
            self.assertEqual(derived["summary_key"], "incident.summary.drift_threshold_breach")
            self.assertEqual(
                derived["summary"],
                "Observed rolling drawdown multiple 9.61 (> 1.25) across 4 occurrences.",
            )
            self.assertNotIn("DriftReport", derived["summary"])
            self.assertNotIn("policy_source=", derived["summary"])
            self.assertEqual(len(derived["timeline"]), 4)
            # Stored row on disk is completely untouched
            self.assertEqual(path.read_bytes(), bytes_before)

    def test_rollback_reader_current_dev_from_dict_reads_new_rows_without_error(self):
        """Acceptance 5: Rows written by the new code are read without error by
        the current dev IncidentCase.from_dict.
        """
        from services.incidents.consumer import (
            build_incident_from_agent_finding_payload,
            build_incident_from_drift_report,
            build_incident_from_infrastructure_health_payload,
        )

        drift_inc = build_incident_from_drift_report(
            {
                "drift_report_id": "drift-rb-001",
                "recon_run_id": "recon-rb-001",
                "incident_cluster_id": "drift:drawdown",
                "status": "open",
                "severity": "high",
                "binding_id": "rb-3823397e125e4579ba5f4174067087ed",
                "runtime_id": "runtime-rb-001",
                "deployment_stage": "paper",
                "deployment_plan_id": "plan-rb-001",
                "capital_pool_id": "pool-rb-001",
                "persona_capital_binding_id": "pcb-rb-001",
                "artifact_id": "art-rb-001",
                "artifact_version": "1.0.0",
                "trace_id": "trace-rb-001",
                "telemetry_event_ids": ["evt-rb-001"],
                "threshold_metric": "rolling_drawdown_multiple",
                "observed": 9.609309450237793,
                "threshold_value": 1.25,
                "comparator": "gt",
                "generated_at": "2026-11-20T01:00:00Z",
            }
        )
        agent_inc = build_incident_from_agent_finding_payload(
            {
                "fingerprint": "drawdown:persona-a",
                "title": "Drawdown spike",
                "severity": "high",
                "rationale": "drawdown 9% with no incident",
                "snapshot_ref": "monitor-snapshot-20261120T010000Z-112233445566",
                "observed_at": "2026-11-20T01:00:00Z",
            }
        )
        infra_inc = build_incident_from_infrastructure_health_payload(
            {
                "schema_version": "pantheon.infrastructure-incident/1",
                "incident_id": "infra-bff-rollback-001",
                "tenant_id": "default",
                "producer": "control-plane-bff",
                "source_event_id": "evt-infra-rb-001",
                "title": "BFF downstream degradation: execution",
                "status": "open",
                "severity": "high",
                "created_at": "2026-11-20T01:00:00Z",
                "component": {
                    "service_name": "execution",
                    "component_kind": "http_service",
                    "endpoint": "http://execution:8007/healthz",
                },
                "evidence_summary": "health_probe observed 3/3 failures",
            }
        )

        for new_inc in (drift_inc, agent_inc, infra_inc):
            serialized = new_inc.to_dict()
            for field_name in (
                "kind",
                "metric",
                "observed",
                "threshold",
                "comparator",
                "occurrence_count",
                "first_seen_at",
                "last_seen_at",
            ):
                self.assertIn(field_name, serialized)
                self.assertIsNotNone(serialized[field_name])

            # Simulate current-dev IncidentCase.from_dict filtering by _CURRENT_DEV_INCIDENT_CASE_FIELDS
            current_dev_filtered = {
                k: v for k, v in serialized.items() if k in _CURRENT_DEV_INCIDENT_CASE_FIELDS
            }
            rollback_loaded = IncidentCase(**current_dev_filtered)
            self.assertEqual(rollback_loaded.incident_id, new_inc.incident_id)
            self.assertEqual(rollback_loaded.binding_id, new_inc.binding_id)


if __name__ == "__main__":
    unittest.main()


