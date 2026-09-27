"""Regression coverage for BFF-CONTROL-LOOPS-EVIDENCE-REDACTION-SWEEP-001.

Exercises the real ``create_control_loops_router`` composition (identical
wiring to ``core/app_factory.py``: canonical ``redact_evidence_refs`` from
``models.py`` plus ``capabilities_for_identity`` from ``auth/policy.py``)
against the 9 previously-discarded-identity control-loops handlers that were
repaired to apply fail-closed evidence-ref redaction: OODA packets
(list/detail), v5 interventions (list/detail), the sentinel finding detail
route, and the aggregate control-room read model. It also exercises the
real ``create_governance_router`` composition against the committee detail
route's previously-unredacted ``linked_evidence`` field.

Loop inventory and downstream-health are covered with populated,
production-shaped baselines that document -- rather than redact -- because
neither read model carries a capability-gated evidence-ref field: loop
inventory projects only static registry/catalog metadata
(``loop_inventory.py::_project_loop``) and downstream health's SQL-backed
probe/incident/replay rows have no evidence column
(``downstream_health_monitor.py``).
"""
from __future__ import annotations

import copy
import inspect
import json
import tempfile
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, Tuple

import os

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.control_loops.router import create_control_loops_router
from services.control_plane.bff.core.app_factory import create_settings_router
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.models import redact_evidence_refs, utc_now
from services.control_plane.bff.settings_store import DEFAULT_SETTINGS_BUNDLE, SettingsStore


def _call_with_supported_kwargs(fn: Callable[..., Any], **kwargs: Any) -> Any:
    """Call ``fn`` with only the kwargs its current signature accepts.

    Lets this file run against an unmodified base checkout whose router
    constructors predate the ``redact_evidence_refs`` /
    ``capabilities_for_identity`` (and similar) kwargs added by this sweep:
    on base those kwargs are silently dropped so the router builds without
    redaction wiring and the leak assertions fail for real, instead of the
    whole test erroring out on a constructor ``TypeError``.
    """
    supported = set(inspect.signature(fn).parameters)
    return fn(**{key: value for key, value in kwargs.items() if key in supported})


LOW_CAPABILITY_TOKEN = "Bearer op-clc-1:operator"
FULL_CAPABILITY_TOKEN = "Bearer admin-clc-1:admin"

_ALERT_REF = {"ref_id": "ref-alert-clc", "type": "alert"}
_METRIC_REF = {"ref_id": "ref-metric-clc", "type": "metric"}
_JOB_REF = {"ref_id": "ref-job-clc", "type": "job"}
_MIXED_REFS = [copy.deepcopy(_ALERT_REF), copy.deepcopy(_METRIC_REF), copy.deepcopy(_JOB_REF)]


def _assert_redacted(ref: Dict[str, Any], *, ref_id: str, required_capability: str) -> None:
    assert ref["redacted"] is True
    assert ref["ref_id"] == ref_id
    assert ref["required_capability"] == required_capability
    assert ref["reason"] == "insufficient_capability"


def _assert_mixed_refs_redacted_for_low_capability(refs: List[Dict[str, Any]]) -> None:
    assert len(refs) == 3
    # operator role holds risk.alert.read: alert ref passes through unchanged
    assert refs[0] == _ALERT_REF
    # operator role lacks metric.read and job.read: both are withheld
    _assert_redacted(refs[1], ref_id="ref-metric-clc", required_capability="metric.read")
    _assert_redacted(refs[2], ref_id="ref-job-clc", required_capability="job.read")


@contextmanager
def _stub_auth_env():
    tracked = {
        "PANTHEON_BFF_AUTH_STUB": os.environ.get("PANTHEON_BFF_AUTH_STUB"),
        "PANTHEON_BFF_AUTH_MODE": os.environ.get("PANTHEON_BFF_AUTH_MODE"),
    }
    os.environ["PANTHEON_BFF_AUTH_STUB"] = "1"
    os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
    try:
        yield
    finally:
        for key, value in tracked.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# --- Populated production-shaped fixtures -----------------------------------

_OODA_PACKET_1: Dict[str, Any] = {
    "packet_id": "ooda-1",
    "loop_type": "incident_response",
    "status": "acted",
    "stage": "act",
    "environment": "dev",
    "strategy_id": "strategy-1",
    "runtime_id": "runtime-1",
    "created_at": "2026-08-30T12:00:00Z",
    "updated_at": "2026-08-30T12:05:00Z",
    "observe": {
        "trigger": "loop_anomaly",
        "source_refs": ["src-clc-1"],
        "telemetry_refs": ["telem-clc-1"],
        "signal_refs": [],
        "market_refs": [],
        "incident_refs": ["ref-incident-clc"],
        "human_feedback_refs": [],
    },
    "orient": {
        "assessment": "risk_within_bounds",
        "regime_state_ref": None,
        "universe_selection_ref": None,
        "signal_inference_refs": [],
        "allocation_proposal_refs": [],
        "risk_adjudication_ref": None,
        "persona_proposal_refs": [],
        "evidence_bundle_refs": [
            "support/evidence/MGMT-PAPER-002-paper-approval-decision.json",
            "support/evidence/MGMT-PAPER-005-paper-telemetry-packet.json",
        ],
    },
    "decide": {
        "approval_decision_id": "appr-clc-1",
        "deployment_plan_id": None,
        "evolution_decision_id": None,
        "sponsor_persona_id": None,
        "decision_rationale_ref": None,
        "policy_decision_refs": [],
    },
    "act": {
        "runtime_binding_id": "runtime-1",
        "command_receipt_refs": [],
        "broker_evidence_refs": [
            "paper-broker://logged-only-order/review-order",
        ],
        "rollback_refs": [],
        "safe_mode_refs": [],
        "live_capital_side_effects": False,
    },
    "learn": {
        "telemetry_refs": [],
        "postmortem_refs": ["ref-postmortem-clc"],
        "evolution_followthrough_refs": [],
        "trainer_refs": [],
        "retrain_refs": [],
        "observation_window": None,
    },
    "audit_refs": ["ref-audit-clc"],
    "evidence_refs": copy.deepcopy(_MIXED_REFS),
}
_OODA_PACKET_2: Dict[str, Any] = {
    "packet_id": "ooda-2",
    "loop_type": "paper_strategy",
    "status": "closed",
    "stage": "learn",
    "environment": "dev",
    "created_at": "2026-08-30T13:00:00Z",
    "updated_at": "2026-08-30T13:05:00Z",
    "observe": {
        "source_refs": [],
        "telemetry_refs": [],
        "signal_refs": [],
        "market_refs": [],
        "incident_refs": [],
        "human_feedback_refs": [],
    },
    "orient": {
        "regime_state_ref": None,
        "universe_selection_ref": None,
        "signal_inference_refs": [],
        "allocation_proposal_refs": [],
        "risk_adjudication_ref": None,
        "persona_proposal_refs": [],
        "evidence_bundle_refs": [],
    },
    "decide": {
        "approval_decision_id": None,
        "deployment_plan_id": None,
        "evolution_decision_id": None,
        "sponsor_persona_id": None,
        "decision_rationale_ref": None,
        "policy_decision_refs": [],
    },
    "act": {
        "runtime_binding_id": None,
        "command_receipt_refs": [],
        "broker_evidence_refs": [],
        "rollback_refs": [],
        "safe_mode_refs": [],
        "live_capital_side_effects": False,
    },
    "learn": {
        "telemetry_refs": [],
        "postmortem_refs": [],
        "evolution_followthrough_refs": [],
        "trainer_refs": [],
        "retrain_refs": [],
        "observation_window": None,
    },
    "audit_refs": [],
    "evidence_refs": [],
}

_INTERVENTION_1: Dict[str, Any] = {
    "intervention_id": "intv-clc-1",
    "kind": "hiq_sentinel",
    "status": "pending",
    "target_type": "Runtime",
    "target_id": "rt-clc-1",
    "triggered_at": "2026-08-30T12:00:00Z",
    "triggered_by": "sentinel",
    "description": "HIQ Sentinel detected anomalous loop behavior",
    "evidence_refs": copy.deepcopy(_MIXED_REFS),
}
_INTERVENTION_2: Dict[str, Any] = {
    "intervention_id": "intv-clc-2",
    "kind": "risk_breach",
    "status": "remediated",
    "target_type": "Runtime",
    "target_id": "rt-clc-2",
    "triggered_at": "2026-08-30T12:10:00Z",
    "evidence_refs": [],
}

_SENTINEL_FINDING_1: Dict[str, Any] = {
    "finding_id": "sf-clc-1",
    "id": "sf-clc-1",
    "kind": "risk_breach",
    "status": "open",
    "severity": "high",
    "created_at": "2026-08-30T12:00:00Z",
    "incident_id": "inc-clc-1",
    "details": "Risk breach detected on runtime rt-clc-1",
    "evidence_refs": copy.deepcopy(_MIXED_REFS),
}


class _ControlLoopsSweepStore:
    """Minimal read-store double exposing only what the router/service calls."""

    def dataset_source(self, _dataset: str) -> str:
        return "service_store"

    # OODA packets --------------------------------------------------------
    def list_ooda_packets(self, **_: Any) -> List[Dict[str, Any]]:
        return [copy.deepcopy(_OODA_PACKET_1), copy.deepcopy(_OODA_PACKET_2)]

    def get_ooda_packet(self, packet_id: str) -> Optional[Dict[str, Any]]:
        for item in (_OODA_PACKET_1, _OODA_PACKET_2):
            if item["packet_id"] == packet_id:
                return copy.deepcopy(item)
        return None

    # v5 interventions ------------------------------------------------------
    def list_v5_interventions(self, **_: Any) -> List[Dict[str, Any]]:
        return [copy.deepcopy(_INTERVENTION_1), copy.deepcopy(_INTERVENTION_2)]

    def get_intervention(self, intervention_id: str) -> Optional[Dict[str, Any]]:
        for item in (_INTERVENTION_1, _INTERVENTION_2):
            if item["intervention_id"] == intervention_id:
                return copy.deepcopy(item)
        return None

    # Sentinel findings -----------------------------------------------------
    def list_sentinel_findings(self, **_: Any) -> Tuple[bool, List[Dict[str, Any]]]:
        return True, [copy.deepcopy(_SENTINEL_FINDING_1)]

    def get_sentinel_finding(self, finding_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        if finding_id == _SENTINEL_FINDING_1["finding_id"]:
            return True, copy.deepcopy(_SENTINEL_FINDING_1)
        return True, None

    # Loop runs (aggregated into control-room only; not a fixed surface) ----
    def list_loop_runs(self, **_: Any) -> Tuple[bool, List[Dict[str, Any]]]:
        return True, [{"loop_run_id": "run-1", "loop_id": "loop-1", "status": "completed"}]


def _build_control_loops_app(
    store: Optional[_ControlLoopsSweepStore] = None,
    *,
    capabilities_for_identity: Any = None,
    downstream_health_monitor: Any = None,
) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        _call_with_supported_kwargs(
            create_control_loops_router,
            read_surface=store or _ControlLoopsSweepStore(),
            downstream_health_monitor=downstream_health_monitor,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now_fn=utc_now,
            redact_evidence_refs=redact_evidence_refs,
            capabilities_for_identity=capabilities_for_identity or auth_policy.capabilities_for_identity,
        )
    )
    return app


# --- OODA packets (list + detail) -------------------------------------------


def test_ooda_packets_list_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        response = client.get(
            "/bff/ooda/packets",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        by_id = {item["packet_id"]: item for item in payload["data"]}
        _assert_mixed_refs_redacted_for_low_capability(by_id["ooda-1"]["evidence_refs"])
        assert by_id["ooda-2"]["evidence_refs"] == []
        _assert_redacted(by_id["ooda-1"]["audit_refs"][0], ref_id="ref-audit-clc", required_capability="audit.read")
        _assert_redacted(
            by_id["ooda-1"]["learn"]["postmortem_refs"][0],
            ref_id="ref-postmortem-clc",
            required_capability="postmortem.read",
        )
        _assert_redacted(
            by_id["ooda-1"]["orient"]["evidence_bundle_refs"][0],
            ref_id="support/evidence/MGMT-PAPER-002-paper-approval-decision.json",
            required_capability="approval.read",
        )
        assert (
            by_id["ooda-1"]["orient"]["evidence_bundle_refs"][1]
            == "support/evidence/MGMT-PAPER-005-paper-telemetry-packet.json"
        )
        _assert_redacted(
            by_id["ooda-1"]["act"]["broker_evidence_refs"][0],
            ref_id="paper-broker://logged-only-order/review-order",
            required_capability="audit.read",
        )
        assert by_id["ooda-1"]["observe"]["incident_refs"] == ["ref-incident-clc"]
        assert payload["meta"]["redacted_evidence_count"] == 6
        # populated production-shaped baseline: non-evidence stage data stays intact
        assert by_id["ooda-1"]["act"]["runtime_binding_id"] == "runtime-1"


def test_ooda_packets_list_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        response = client.get(
            "/bff/ooda/packets",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        by_id = {item["packet_id"]: item for item in payload["data"]}
        assert by_id["ooda-1"]["evidence_refs"] == _MIXED_REFS
        assert by_id["ooda-1"]["audit_refs"] == ["ref-audit-clc"]
        assert by_id["ooda-1"]["learn"]["postmortem_refs"] == ["ref-postmortem-clc"]
        assert by_id["ooda-1"]["orient"]["evidence_bundle_refs"] == [
            "support/evidence/MGMT-PAPER-002-paper-approval-decision.json",
            "support/evidence/MGMT-PAPER-005-paper-telemetry-packet.json",
        ]
        assert by_id["ooda-1"]["act"]["broker_evidence_refs"] == [
            "paper-broker://logged-only-order/review-order",
        ]
        assert by_id["ooda-1"]["observe"]["incident_refs"] == ["ref-incident-clc"]
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_ooda_packets_list_redacted_count_scoped_to_returned_page() -> None:
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        page1 = client.get(
            "/bff/ooda/packets",
            params={"page_size": 1},
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert page1.status_code == 200, page1.text
        page1_payload = page1.json()
        assert len(page1_payload["items"]) == 1
        assert page1_payload["items"][0]["packet_id"] == "ooda-1"
        assert page1_payload["meta"]["redacted_evidence_count"] == 6

        next_token = page1_payload["page_info"]["next_page_token"]
        assert next_token
        page2 = client.get(
            "/bff/ooda/packets",
            params={"page_size": 1, "page_token": next_token},
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        page2_payload = page2.json()
        assert page2_payload["items"][0]["packet_id"] == "ooda-2"
        # page 2's packet carries no evidence_refs, so nothing withheld on this page
        assert page2_payload["meta"]["redacted_evidence_count"] == 0


def test_ooda_packet_detail_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        response = client.get(
            "/bff/ooda/packets/ooda-1",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        _assert_mixed_refs_redacted_for_low_capability(payload["data"]["evidence_refs"])
        _assert_redacted(payload["data"]["audit_refs"][0], ref_id="ref-audit-clc", required_capability="audit.read")
        _assert_redacted(
            payload["data"]["learn"]["postmortem_refs"][0],
            ref_id="ref-postmortem-clc",
            required_capability="postmortem.read",
        )
        _assert_redacted(
            payload["data"]["orient"]["evidence_bundle_refs"][0],
            ref_id="support/evidence/MGMT-PAPER-002-paper-approval-decision.json",
            required_capability="approval.read",
        )
        assert (
            payload["data"]["orient"]["evidence_bundle_refs"][1]
            == "support/evidence/MGMT-PAPER-005-paper-telemetry-packet.json"
        )
        _assert_redacted(
            payload["data"]["act"]["broker_evidence_refs"][0],
            ref_id="paper-broker://logged-only-order/review-order",
            required_capability="audit.read",
        )
        assert payload["data"]["observe"]["incident_refs"] == ["ref-incident-clc"]
        assert payload["meta"]["redacted_evidence_count"] == 6


def test_ooda_packet_detail_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        response = client.get(
            "/bff/ooda/packets/ooda-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["evidence_refs"] == _MIXED_REFS
        assert payload["data"]["audit_refs"] == ["ref-audit-clc"]
        assert payload["data"]["learn"]["postmortem_refs"] == ["ref-postmortem-clc"]
        assert payload["data"]["orient"]["evidence_bundle_refs"] == [
            "support/evidence/MGMT-PAPER-002-paper-approval-decision.json",
            "support/evidence/MGMT-PAPER-005-paper-telemetry-packet.json",
        ]
        assert payload["data"]["act"]["broker_evidence_refs"] == [
            "paper-broker://logged-only-order/review-order",
        ]
        assert payload["data"]["observe"]["incident_refs"] == ["ref-incident-clc"]
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_ooda_packet_detail_fails_closed_when_capabilities_unresolvable() -> None:
    def _boom(identity: Any) -> List[str]:
        raise RuntimeError("capability lookup unavailable")

    with _stub_auth_env():
        client = TestClient(_build_control_loops_app(capabilities_for_identity=_boom))
        response = client.get(
            "/bff/ooda/packets/ooda-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["data"]["evidence_refs"]) == 3
        assert all(ref["redacted"] is True for ref in payload["data"]["evidence_refs"])
        assert len(payload["data"]["audit_refs"]) == 1
        assert all(ref["redacted"] is True for ref in payload["data"]["audit_refs"])
        assert len(payload["data"]["learn"]["postmortem_refs"]) == 1
        assert all(ref["redacted"] is True for ref in payload["data"]["learn"]["postmortem_refs"])
        assert len(payload["data"]["orient"]["evidence_bundle_refs"]) == 2
        assert all(ref["redacted"] is True for ref in payload["data"]["orient"]["evidence_bundle_refs"])
        assert len(payload["data"]["act"]["broker_evidence_refs"]) == 1
        assert all(ref["redacted"] is True for ref in payload["data"]["act"]["broker_evidence_refs"])
        assert len(payload["data"]["observe"]["incident_refs"]) == 1
        assert all(ref["redacted"] is True for ref in payload["data"]["observe"]["incident_refs"])
        assert len(payload["data"]["observe"]["source_refs"]) == 1
        assert all(ref["redacted"] is True for ref in payload["data"]["observe"]["source_refs"])
        assert len(payload["data"]["observe"]["telemetry_refs"]) == 1
        assert all(ref["redacted"] is True for ref in payload["data"]["observe"]["telemetry_refs"])
        assert payload["meta"]["redacted_evidence_count"] == 11


def test_ooda_packet_detail_fails_closed_when_capabilities_returns_none() -> None:
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app(capabilities_for_identity=lambda _: None))
        response = client.get(
            "/bff/ooda/packets/ooda-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["data"]["evidence_refs"]) == 3
        assert all(ref["redacted"] is True for ref in payload["data"]["evidence_refs"])
        assert len(payload["data"]["audit_refs"]) == 1
        assert all(ref["redacted"] is True for ref in payload["data"]["audit_refs"])
        assert len(payload["data"]["learn"]["postmortem_refs"]) == 1
        assert all(ref["redacted"] is True for ref in payload["data"]["learn"]["postmortem_refs"])
        assert len(payload["data"]["orient"]["evidence_bundle_refs"]) == 2
        assert all(ref["redacted"] is True for ref in payload["data"]["orient"]["evidence_bundle_refs"])
        assert len(payload["data"]["act"]["broker_evidence_refs"]) == 1
        assert all(ref["redacted"] is True for ref in payload["data"]["act"]["broker_evidence_refs"])
        assert len(payload["data"]["observe"]["incident_refs"]) == 1
        assert all(ref["redacted"] is True for ref in payload["data"]["observe"]["incident_refs"])
        assert len(payload["data"]["observe"]["source_refs"]) == 1
        assert all(ref["redacted"] is True for ref in payload["data"]["observe"]["source_refs"])
        assert len(payload["data"]["observe"]["telemetry_refs"]) == 1
        assert all(ref["redacted"] is True for ref in payload["data"]["observe"]["telemetry_refs"])
        assert payload["meta"]["redacted_evidence_count"] == 11


# --- v5 interventions (list + detail) ---------------------------------------


def test_v5_interventions_list_baseline_response_model_strips_evidence_refs() -> None:
    """``InterventionRecord`` declares no ``evidence_refs`` field and no
    ``extra="allow"`` config, so FastAPI's response-model serialization
    always drops any such field before it reaches the wire regardless of
    identity -- there is nothing to redact on this list surface, only the
    (structurally schema-enforced) baseline to document."""
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        response = client.get(
            "/bff/v5/interventions",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        by_id = {item["intervention_id"]: item for item in payload["items"]}
        assert set(by_id) == {"intv-clc-1", "intv-clc-2"}
        assert "evidence_refs" not in by_id["intv-clc-1"]


def test_v5_intervention_detail_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        response = client.get(
            "/bff/v5/interventions/intv-clc-1",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        _assert_mixed_refs_redacted_for_low_capability(payload["data"]["evidence_refs"])
        assert payload["meta"]["redacted_evidence_count"] == 2


def test_v5_intervention_detail_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        response = client.get(
            "/bff/v5/interventions/intv-clc-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_v5_intervention_detail_fails_closed_when_capabilities_unresolvable() -> None:
    def _boom(identity: Any) -> List[str]:
        raise RuntimeError("capability lookup unavailable")

    with _stub_auth_env():
        client = TestClient(_build_control_loops_app(capabilities_for_identity=_boom))
        response = client.get(
            "/bff/v5/interventions/intv-clc-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        refs = payload["data"]["evidence_refs"]
        assert len(refs) == 3
        assert all(ref["redacted"] is True for ref in refs)
        assert payload["meta"]["redacted_evidence_count"] == 3


# --- Sentinel finding detail -------------------------------------------------


def test_sentinel_finding_detail_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        response = client.get(
            "/bff/v5/sentinel/findings/sf-clc-1",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        _assert_mixed_refs_redacted_for_low_capability(payload["data"]["evidence_refs"])
        assert payload["meta"]["redacted_evidence_count"] == 2


def test_sentinel_finding_detail_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        response = client.get(
            "/bff/v5/sentinel/findings/sf-clc-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["redacted_evidence_count"] == 0


# --- Aggregate control-room --------------------------------------------------


def test_control_room_redacts_embedded_items_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        response = client.get(
            "/bff/v5/control-room",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        interventions_by_id = {
            item["intervention_id"]: item for item in payload["interventions"]["items"]
        }
        _assert_mixed_refs_redacted_for_low_capability(
            interventions_by_id["intv-clc-1"]["evidence_refs"]
        )
        sentinel_by_id = {item["finding_id"]: item for item in payload["sentinel"]["items"]}
        _assert_mixed_refs_redacted_for_low_capability(
            sentinel_by_id["sf-clc-1"]["evidence_refs"]
        )
        assert payload["meta"]["redacted_evidence_count"] == 4


def test_control_room_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        response = client.get(
            "/bff/v5/control-room",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        interventions_by_id = {
            item["intervention_id"]: item for item in payload["interventions"]["items"]
        }
        assert interventions_by_id["intv-clc-1"]["evidence_refs"] == _MIXED_REFS
        sentinel_by_id = {item["finding_id"]: item for item in payload["sentinel"]["items"]}
        assert sentinel_by_id["sf-clc-1"]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["redacted_evidence_count"] == 0


# --- Loop inventory / downstream health: populated baselines, nothing to
# redact (no capability-gated evidence-ref field exists in either read
# model), documented rather than silently assumed. -------------------------


def test_loop_inventory_list_baseline_has_no_evidence_ref_field() -> None:
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        response = client.get(
            "/bff/v5/loop-inventory",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["data"]) > 0
        assert "evidence_refs" not in payload["data"][0]
        assert "linked_evidence" not in payload["data"][0]


def test_loop_inventory_detail_baseline_has_no_evidence_ref_field() -> None:
    """Populated detail baseline for a real catalog loop id -- not a 404.

    The prior sweep misclassified a detail route as safe from a 404
    baseline; fetch a real ``loop_id`` from the populated list surface first
    so this exercises the actual production-shaped detail record.
    """
    with _stub_auth_env():
        client = TestClient(_build_control_loops_app())
        list_response = client.get(
            "/bff/v5/loop-inventory",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert list_response.status_code == 200, list_response.text
        loop_id = list_response.json()["data"][0]["loop_id"]

        response = client.get(
            f"/bff/v5/loop-inventory/{loop_id}",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["loop_id"] == loop_id
        assert "evidence_refs" not in payload["data"]
        assert "linked_evidence" not in payload["data"]


_DOWNSTREAM_HEALTH_TARGET_PROBE: Dict[str, Any] = {
    "target_name": "risk-engine",
    "ok": True,
    "status_code": 200,
    "latency_ms": 42.5,
    "checked_at": "2026-08-30T12:00:00Z",
    "failure_reason": None,
    "consecutive_failures": 0,
    "probe_kind": "http",
    "window_started_at": "2026-08-30T11:00:00Z",
    "sample_count": 60,
    "failure_count": 0,
    "error_rate": 0.0,
    "registry": {"name": "risk-engine", "base_url": "https://risk-engine.internal", "component_kind": "service"},
}


class _PopulatedDownstreamHealthMonitor:
    """Minimal double returning a populated, production-shaped ``get_state``."""

    def get_state(self) -> Dict[str, Any]:
        return {
            "overall_ok": True,
            "targets": {"risk-engine": copy.deepcopy(_DOWNSTREAM_HEALTH_TARGET_PROBE)},
        }


def test_downstream_health_baseline_has_no_evidence_ref_field() -> None:
    with _stub_auth_env():
        client = TestClient(
            _build_control_loops_app(downstream_health_monitor=_PopulatedDownstreamHealthMonitor())
        )
        response = client.get(
            "/bff/v5/downstream-health",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["read_model"] == "downstream_health"
        assert payload["meta"]["source"] == "bff_downstream_health_monitor"
        # populated production-shaped baseline: real probe data, not the
        # empty {overall_ok: null, targets: {}} shape from an uninjected monitor
        assert payload["data"]["overall_ok"] is True
        assert payload["data"]["targets"]["risk-engine"]["ok"] is True
        assert "evidence_refs" not in payload["data"]
        assert "linked_evidence" not in payload["data"]


# --- Settings read/export: top-level evidence_refs field --------------------
#
# ``SettingsStore._validate_settings_bundle`` only requires the eight
# section dicts to be present; it accepts any additional top-level field, so
# an ``evidence_refs`` list mixed into the bundle (whether written through
# the settings API or seeded directly) previously round-tripped unredacted
# through both GET /api/v1/settings and GET /api/v1/settings/export.


def _build_settings_app(*, capabilities_for_identity: Any = None) -> tuple[FastAPI, SettingsStore]:
    tmpdir = tempfile.mkdtemp()
    store = SettingsStore(os.path.join(tmpdir, "settings.json"))
    bundle = copy.deepcopy(DEFAULT_SETTINGS_BUNDLE)
    bundle["evidence_refs"] = copy.deepcopy(_MIXED_REFS)
    bundle["linked_evidence"] = [{"ref_id": "review-metric", "type": "metric", "link": "/metrics/private"}]
    bundle["risk"]["evidence_refs"] = [{"ref_id": "review-audit", "type": "audit", "link": "/audits/private"}]
    store.replace(bundle)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        _call_with_supported_kwargs(
            create_settings_router,
            settings_store=store,
            extract_identity=auth_policy.extract_identity,
            require_admin_mfa=auth_policy.require_admin_mfa,
            redact_evidence_refs=redact_evidence_refs,
            capabilities_for_identity=capabilities_for_identity or auth_policy.capabilities_for_identity,
        )
    )
    return app, store


def test_settings_get_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        app, _ = _build_settings_app()
        client = TestClient(app)
        response = client.get(
            "/api/v1/settings",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        _assert_mixed_refs_redacted_for_low_capability(payload["evidence_refs"])
        _assert_redacted(
            payload["linked_evidence"][0],
            ref_id="review-metric",
            required_capability="metric.read",
        )
        assert "link" not in payload["linked_evidence"][0]
        _assert_redacted(
            payload["risk"]["evidence_refs"][0],
            ref_id="review-audit",
            required_capability="audit.read",
        )
        assert "link" not in payload["risk"]["evidence_refs"][0]
        assert payload["meta"]["redacted_evidence_count"] == 4
        # populated production-shaped baseline: unrelated sections stay intact
        assert payload["general"]["language"] == "zh-TW"


def test_settings_get_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        app, _ = _build_settings_app()
        client = TestClient(app)
        response = client.get(
            "/api/v1/settings",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["evidence_refs"] == _MIXED_REFS
        assert payload["linked_evidence"] == [
            {"ref_id": "review-metric", "type": "metric", "link": "/metrics/private"}
        ]
        assert payload["risk"]["evidence_refs"] == [
            {"ref_id": "review-audit", "type": "audit", "link": "/audits/private"}
        ]
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_settings_get_fails_closed_when_capabilities_unresolvable() -> None:
    def _boom(identity: Any) -> List[str]:
        raise RuntimeError("capability lookup unavailable")

    with _stub_auth_env():
        app, _ = _build_settings_app(capabilities_for_identity=_boom)
        client = TestClient(app)
        response = client.get(
            "/api/v1/settings",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        refs = payload["evidence_refs"]
        assert len(refs) == 3
        assert all(ref["redacted"] is True for ref in refs)
        assert len(payload["linked_evidence"]) == 1
        assert payload["linked_evidence"][0]["redacted"] is True
        assert "link" not in payload["linked_evidence"][0]
        assert len(payload["risk"]["evidence_refs"]) == 1
        assert payload["risk"]["evidence_refs"][0]["redacted"] is True
        assert "link" not in payload["risk"]["evidence_refs"][0]
        assert payload["meta"]["redacted_evidence_count"] == 5


def test_settings_get_fails_closed_when_capabilities_returns_none() -> None:
    with _stub_auth_env():
        app, _ = _build_settings_app(capabilities_for_identity=lambda _: None)
        client = TestClient(app)
        response = client.get(
            "/api/v1/settings",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        refs = payload["evidence_refs"]
        assert len(refs) == 3
        assert all(ref["redacted"] is True for ref in refs)
        assert len(payload["linked_evidence"]) == 1
        assert payload["linked_evidence"][0]["redacted"] is True
        assert "link" not in payload["linked_evidence"][0]
        assert len(payload["risk"]["evidence_refs"]) == 1
        assert payload["risk"]["evidence_refs"][0]["redacted"] is True
        assert "link" not in payload["risk"]["evidence_refs"][0]
        assert payload["meta"]["redacted_evidence_count"] == 5


def test_settings_export_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        app, _ = _build_settings_app()
        client = TestClient(app)
        response = client.get(
            "/api/v1/settings/export",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        exported = json.loads(payload["jsonData"])
        _assert_mixed_refs_redacted_for_low_capability(exported["evidence_refs"])
        _assert_redacted(
            exported["linked_evidence"][0],
            ref_id="review-metric",
            required_capability="metric.read",
        )
        assert "link" not in exported["linked_evidence"][0]
        _assert_redacted(
            exported["risk"]["evidence_refs"][0],
            ref_id="review-audit",
            required_capability="audit.read",
        )
        assert "link" not in exported["risk"]["evidence_refs"][0]
        assert payload["meta"]["redacted_evidence_count"] == 4


def test_settings_export_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        app, _ = _build_settings_app()
        client = TestClient(app)
        response = client.get(
            "/api/v1/settings/export",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        exported = json.loads(payload["jsonData"])
        assert exported["evidence_refs"] == _MIXED_REFS
        assert exported["linked_evidence"] == [
            {"ref_id": "review-metric", "type": "metric", "link": "/metrics/private"}
        ]
        assert exported["risk"]["evidence_refs"] == [
            {"ref_id": "review-audit", "type": "audit", "link": "/audits/private"}
        ]
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_settings_export_fails_closed_when_capabilities_unresolvable() -> None:
    def _boom(identity: Any) -> List[str]:
        raise RuntimeError("capability lookup unavailable")

    with _stub_auth_env():
        app, _ = _build_settings_app(capabilities_for_identity=_boom)
        client = TestClient(app)
        response = client.get(
            "/api/v1/settings/export",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        exported = json.loads(payload["jsonData"])
        refs = exported["evidence_refs"]
        assert len(refs) == 3
        assert all(ref["redacted"] is True for ref in refs)
        assert len(exported["linked_evidence"]) == 1
        assert exported["linked_evidence"][0]["redacted"] is True
        assert "link" not in exported["linked_evidence"][0]
        assert len(exported["risk"]["evidence_refs"]) == 1
        assert exported["risk"]["evidence_refs"][0]["redacted"] is True
        assert "link" not in exported["risk"]["evidence_refs"][0]
        assert payload["meta"]["redacted_evidence_count"] == 5


def test_settings_export_fails_closed_when_capabilities_returns_none() -> None:
    with _stub_auth_env():
        app, _ = _build_settings_app(capabilities_for_identity=lambda _: None)
        client = TestClient(app)
        response = client.get(
            "/api/v1/settings/export",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        exported = json.loads(payload["jsonData"])
        refs = exported["evidence_refs"]
        assert len(refs) == 3
        assert all(ref["redacted"] is True for ref in refs)
        assert len(exported["linked_evidence"]) == 1
        assert exported["linked_evidence"][0]["redacted"] is True
        assert "link" not in exported["linked_evidence"][0]
        assert len(exported["risk"]["evidence_refs"]) == 1
        assert exported["risk"]["evidence_refs"][0]["redacted"] is True
        assert "link" not in exported["risk"]["evidence_refs"][0]
        assert payload["meta"]["redacted_evidence_count"] == 5


# --- Governance committee detail: linked_evidence -----------------------


_COMMITTEE_1: Dict[str, Any] = {
    "committee_id": "committee-clc-1",
    "committee_ref": "committee-clc-1",
    "linked_request_id": "consult-req-clc-1",
    "linked_session_id": "session-clc-1",
    "started_at": "2026-08-30T12:00:00Z",
    "quorum_state": "met",
    "consensus_state": "consensus_reached",
    "participant_roster": [{"participant_id": "p-1", "role": "reviewer"}],
    "sponsor_assignment": {"participant_id": "p-1"},
    "synthesis_summary": {
        "summary": "Consensus reached on rollback plan",
        "evidence_refs": ["ref-alert-clc", "ref-metric-clc"],
    },
    "linked_evidence": copy.deepcopy(_MIXED_REFS),
    "service_handoff": {
        "handoff_id": "handoff-1",
        "evidence_refs": copy.deepcopy(_MIXED_REFS),
        "audit_refs": ["ref-audit-clc"],
    },
}


class _CommitteeSweepStore:
    def dataset_source(self, _dataset: str) -> str:
        return "service_store"

    def get_committee(self, committee_id: str) -> Optional[Dict[str, Any]]:
        return copy.deepcopy(_COMMITTEE_1) if committee_id == "committee-clc-1" else None


def _build_governance_app(*, capabilities_for_identity: Any = None) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        _call_with_supported_kwargs(
            create_governance_router,
            read_surface=_CommitteeSweepStore(),
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=utc_now,
            redact_evidence_refs=redact_evidence_refs,
            capabilities_for_identity=capabilities_for_identity or auth_policy.capabilities_for_identity,
        )
    )
    return app


def test_committee_detail_redacts_linked_evidence_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_governance_app())
        response = client.get(
            "/api/v1/committees/committee-clc-1",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        _assert_mixed_refs_redacted_for_low_capability(payload["linked_evidence"])
        _assert_mixed_refs_redacted_for_low_capability(payload["service_handoff"]["evidence_refs"])
        assert payload["synthesis_summary"]["evidence_refs"][0] == "ref-alert-clc"
        _assert_redacted(
            payload["synthesis_summary"]["evidence_refs"][1],
            ref_id="ref-metric-clc",
            required_capability="metric.read",
        )
        _assert_redacted(
            payload["service_handoff"]["audit_refs"][0],
            ref_id="ref-audit-clc",
            required_capability="audit.read",
        )
        assert payload["meta"]["redacted_evidence_count"] == 6


def test_committee_detail_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_governance_app())
        response = client.get(
            "/api/v1/committees/committee-clc-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["linked_evidence"] == _MIXED_REFS
        assert payload["synthesis_summary"]["evidence_refs"] == ["ref-alert-clc", "ref-metric-clc"]
        assert payload["service_handoff"]["evidence_refs"] == _MIXED_REFS
        assert payload["service_handoff"]["audit_refs"] == ["ref-audit-clc"]
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_committee_detail_fails_closed_when_capabilities_unresolvable() -> None:
    def _boom(identity: Any) -> List[str]:
        raise RuntimeError("capability lookup unavailable")

    with _stub_auth_env():
        client = TestClient(_build_governance_app(capabilities_for_identity=_boom))
        response = client.get(
            "/api/v1/committees/committee-clc-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["linked_evidence"]) == 3
        assert all(ref["redacted"] is True for ref in payload["linked_evidence"])
        assert len(payload["synthesis_summary"]["evidence_refs"]) == 2
        assert all(ref["redacted"] is True for ref in payload["synthesis_summary"]["evidence_refs"])
        assert len(payload["service_handoff"]["evidence_refs"]) == 3
        assert all(ref["redacted"] is True for ref in payload["service_handoff"]["evidence_refs"])
        assert len(payload["service_handoff"]["audit_refs"]) == 1
        assert all(ref["redacted"] is True for ref in payload["service_handoff"]["audit_refs"])
        assert payload["meta"]["redacted_evidence_count"] == 9


def test_committee_detail_projection_direct() -> None:
    from services.control_plane.bff.governance.service import GovernanceService
    with _stub_auth_env():
        svc = GovernanceService(
            read_store=_CommitteeSweepStore(),
            capabilities_for_identity=auth_policy.capabilities_for_identity,
            redact_evidence_refs=redact_evidence_refs,
        )
        identity = auth_policy.extract_identity(LOW_CAPABILITY_TOKEN)
        projected = svc.committee_projection("committee-clc-1", identity=identity)
        assert projected is not None
        assert projected["meta"]["redacted_evidence_count"] == 6
