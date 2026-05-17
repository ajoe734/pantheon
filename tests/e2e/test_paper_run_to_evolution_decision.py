"""OODA-E2E-006: paper run telemetry → IncidentCase → Postmortem → EvolutionDecisionProposal.

Proves the OODA Learn stage end-to-end: a paper run anomaly telemetry event
triggers IncidentCase creation, a Postmortem is authored referencing the
incident, and the POST-EVO-BRIDGE emits an EvolutionDecisionProposal.

Proposal-only invariants verified:
  - proposed_action in {rollback, freeze, retrain}
  - bridge does NOT write to the governance store
  - no live runtime mutation occurs (pure function, evolution_controller role)

Dependencies: TEL-001, INC-001-RB, POST-001, POST-EVO-BRIDGE.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_INCIDENT_DIR = REPO_ROOT / "services" / "incident"
if str(_INCIDENT_DIR) not in sys.path:
    sys.path.insert(0, str(_INCIDENT_DIR))

_EVOLUTION_DIR = REPO_ROOT / "services" / "evolution"
if str(_EVOLUTION_DIR) not in sys.path:
    sys.path.insert(0, str(_EVOLUTION_DIR))

from incident import IncidentCase, IncidentStore, Postmortem, validate_incident_case  # type: ignore
from postmortem_bridge import on_postmortem_published  # type: ignore

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "synthetic_incident_telemetry.json"

VALID_PROPOSED_ACTIONS = {"rollback", "freeze", "retrain"}


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _load_fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _make_incident(telemetry: Dict[str, Any]) -> IncidentCase:
    """Build an IncidentCase from synthetic telemetry data, simulating ingest."""
    return IncidentCase(
        incident_id="inc-ooda-e2e-006-001",
        title="Paper run high-severity drawdown anomaly",
        status="open",
        severity=telemetry["severity"],
        created_at="2026-05-17T00:00:01Z",
        binding_id=telemetry["runtime_binding_id"],
        deployment_stage=telemetry["deployment_stage"],
        deployment_plan_id=telemetry["deployment_plan_id"],
        capital_pool_id=telemetry["capital_pool_id"],
        persona_capital_binding_id=telemetry["persona_capital_binding_id"],
        artifact_id=telemetry["artifact_id"],
        artifact_version=telemetry["artifact_version"],
        runtime_id=telemetry["runtime_id"],
        trace_id=telemetry["trace_id"],
        telemetry_event_ids=[telemetry["event_id"]],
        evidence_summary=telemetry.get("description"),
    )


def _make_postmortem(incident: IncidentCase) -> Postmortem:
    """Build a published Postmortem referencing an IncidentCase."""
    return Postmortem(
        postmortem_id="pm-ooda-e2e-006-001",
        title=f"Postmortem: paper run anomaly {incident.incident_id}",
        status="published",
        created_at="2026-05-17T00:01:00Z",
        incident_id=incident.incident_id,
        binding_id=incident.binding_id,
        deployment_stage=incident.deployment_stage,
        deployment_plan_id=incident.deployment_plan_id,
        capital_pool_id=incident.capital_pool_id,
        persona_capital_binding_id=incident.persona_capital_binding_id,
        artifact_id=incident.artifact_id,
        artifact_version=incident.artifact_version,
        runtime_id=incident.runtime_id,
        trace_id=incident.trace_id,
        root_cause=(
            "High drawdown anomaly detected during paper run; "
            "severity=high threshold breached per §7.5 policy."
        ),
        contributing_factors=["market_volatility", "model_drift"],
        action_items=["retrain_model", "review_risk_params"],
        author_ids=["evolution-controller"],
        published_at="2026-05-17T00:02:00Z",
    )


def _build_bridge_input(postmortem: Postmortem, incident: IncidentCase) -> Dict[str, Any]:
    """Build bridge input dict: postmortem fields + severity propagated from incident."""
    pm_dict = postmortem.to_dict()
    pm_dict["severity"] = incident.severity
    return pm_dict


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_fixture_has_required_telemetry_fields() -> None:
    """Fixture telemetry event carries severity=high and required binding/lineage fields."""
    telemetry = _load_fixture()
    assert telemetry["severity"] == "high"
    assert telemetry["deployment_stage"] == "paper"
    for field in ("runtime_binding_id", "artifact_id", "artifact_version",
                  "deployment_plan_id", "capital_pool_id", "runtime_id", "trace_id"):
        assert field in telemetry, f"fixture missing required field: {field}"


def test_ingest_creates_incident_case_from_telemetry() -> None:
    """High-severity telemetry creates IncidentCase with correct runtime_binding_id and artifact_id."""
    telemetry = _load_fixture()
    incident = _make_incident(telemetry)

    assert incident.binding_id == telemetry["runtime_binding_id"], (
        f"IncidentCase.binding_id must reference telemetry runtime_binding_id; "
        f"got {incident.binding_id!r}"
    )
    assert incident.artifact_id == telemetry["artifact_id"]
    assert incident.artifact_version == telemetry["artifact_version"]
    assert incident.severity == "high"
    assert telemetry["event_id"] in incident.telemetry_event_ids


def test_incident_case_validates_cleanly() -> None:
    """IncidentCase created from telemetry fixture passes semantic validation."""
    telemetry = _load_fixture()
    incident = _make_incident(telemetry)
    errors = validate_incident_case(incident)
    assert errors == [], f"IncidentCase validation errors: {errors}"


def test_postmortem_creation_references_incident() -> None:
    """Postmortem created via IncidentStore carries the incident lineage edge and binding reference."""
    telemetry = _load_fixture()
    store = IncidentStore()
    incident = store.create_incident(_make_incident(telemetry))
    postmortem = store.create_postmortem(_make_postmortem(incident))

    assert postmortem.incident_id == incident.incident_id
    assert postmortem.binding_id == incident.binding_id
    assert postmortem.artifact_id == incident.artifact_id
    assert postmortem.artifact_version == incident.artifact_version


def test_postmortem_bridge_emits_evolution_proposal() -> None:
    """POST-EVO-BRIDGE emits EvolutionDecisionProposal with proposed_action in (rollback freeze retrain)."""
    telemetry = _load_fixture()
    incident = _make_incident(telemetry)
    postmortem = _make_postmortem(incident)

    proposal = on_postmortem_published(_build_bridge_input(postmortem, incident))

    assert proposal is not None, (
        "Bridge must emit a proposal for severity=high; returned None"
    )
    assert proposal["proposed_action"] in VALID_PROPOSED_ACTIONS, (
        f"proposed_action must be one of {VALID_PROPOSED_ACTIONS}; "
        f"got {proposal['proposed_action']!r}"
    )
    assert proposal["source_postmortem_id"] == postmortem.postmortem_id


def test_bridge_is_proposal_only_no_governance_store_write() -> None:
    """Bridge result is proposal-only: governance_store_written must be absent or False."""
    telemetry = _load_fixture()
    incident = _make_incident(telemetry)
    postmortem = _make_postmortem(incident)

    proposal = on_postmortem_published(_build_bridge_input(postmortem, incident))

    assert proposal is not None
    assert proposal.get("governance_store_written", False) is False, (
        "Bridge must not write to the governance store; "
        f"governance_store_written={proposal.get('governance_store_written')!r}"
    )


def test_no_live_runtime_mutation_in_proposal() -> None:
    """Bridge proposal is created by evolution_controller role; no live runtime actor involved."""
    telemetry = _load_fixture()
    incident = _make_incident(telemetry)
    postmortem = _make_postmortem(incident)

    proposal = on_postmortem_published(_build_bridge_input(postmortem, incident))

    assert proposal is not None
    assert isinstance(proposal, dict), "Bridge result must be a plain dict (not a live mutation object)"
    assert proposal.get("created_by_role") == "evolution_controller", (
        "Proposal must be created by evolution_controller role to stay in governance plane; "
        f"got {proposal.get('created_by_role')!r}"
    )


def test_bridge_high_severity_maps_to_rollback() -> None:
    """severity=high maps to proposed_action=rollback per POST-EVO-BRIDGE §7.5 contract."""
    telemetry = _load_fixture()
    assert telemetry["severity"] == "high", "Fixture must use severity=high for this test"

    incident = _make_incident(telemetry)
    postmortem = _make_postmortem(incident)

    proposal = on_postmortem_published(_build_bridge_input(postmortem, incident))

    assert proposal is not None
    assert proposal["proposed_action"] == "rollback", (
        f"severity=high must map to proposed_action=rollback; "
        f"got {proposal['proposed_action']!r}"
    )
