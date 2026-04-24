from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from read_store import ReadSurfaceStore


OPERATOR_AUTH = "Bearer test-operator:operator"
REVIEWER_AUTH = "Bearer test-reviewer:reviewer"


@contextmanager
def _seeded_client():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store


def test_cw03_list_contract_returns_committee_projection() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/committees?consensus_state=sponsor_required",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["page_info"]["total"] == 1
        assert payload["data"][0] == {
            "committee_id": "committee-regime-risk-20260419-081",
            "committee_ref": "committee-regime-risk-20260419-081",
            "escalation_reason": {
                "trigger_rule": "macro_regime_shift",
                "forbidden_solo_action": "approve_live_deployment",
                "escalation_path": "committee_override",
            },
            "quorum_state": "quorum_met",
            "consensus_state": "sponsor_required",
            "linked_request_id": "cr-20260419-014",
            "started_at": "2026-04-19T17:07:00Z",
            "surface_state": "ok",
            "route_href": "/consultation/committees/committee-regime-risk-20260419-081",
        }
        assert payload["meta"]["surfaces"]["committee_board"] in {"degraded", "ok", "stale"}


def test_cw03_detail_contract_returns_synthesis_and_allowed_actions() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/committees/committee-regime-risk-20260419-081",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["committee_id"] == "committee-regime-risk-20260419-081"
        assert payload["linked_request_id"] == "cr-20260419-014"
        assert payload["quorum_state"] == "quorum_met"
        assert payload["consensus_state"] == "sponsor_required"
        assert payload["sponsor_assignment"]["persona_id"] == "p-compliance-sponsor"
        assert payload["participant_roster"][0]["persona_label"] == "Macro Observer"
        assert payload["synthesis_summary"] == {
            "outcome": "pending",
            "rationale_ref": "workspace://consultation-rationales/cs-20260419-081",
            "evidence_refs": [
                "telemetry-vol-spike-20260419",
                "dp-20260419-014",
            ],
            "dissent_refs": [
                "workspace://consultation-dissent/cs-20260419-081/execution-lead"
            ],
        }
        assert payload["allowedActions"] == {
            "canRecordSponsorDecision": True,
        }
        assert payload["linked_evidence"][0] == {
            "id": "telemetry-vol-spike-20260419",
            "type": "evidence_link",
            "evidence_type": "telemetry",
            "artifact_ref": "artifact-042",
            "description": "Volatility spike - 2026-04-19",
            "link": "/telemetry/events/telemetry-vol-spike-20260419",
        }
        assert payload["meta"]["surfaces"]["committee_board"] in {"degraded", "ok", "stale"}


def test_cw03_record_sponsor_decision_executes_and_updates_projection() -> None:
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/operator/commands",
            headers={"Authorization": OPERATOR_AUTH},
            json={
                "command_type": "RecordSponsorDecision",
                "committee_id": "committee-regime-risk-20260419-081",
                "sponsor_decision": "approved",
                "rationale_ref": "workspace://committee-rationales/committee-regime-risk-20260419-081/final",
                "note": "Compliance sponsor approves the risk review outcome",
            },
        )
        assert response.status_code == 202, response.text
        receipt = response.json()
        command_id = receipt["receipt_id"]

        status = client.get(
            f"/api/v1/operator/commands/{command_id}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert status.status_code == 200, status.text
        payload = status.json()
        assert payload["status"] in {"submitted", "processing", "executed"}

        detail = client.get(
            "/api/v1/committees/committee-regime-risk-20260419-081",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert detail.status_code == 200, detail.text
        projection = detail.json()
        assert projection["sponsor_decision"] == "approved"
        assert projection["consensus_state"] == "reached"
        assert projection["synthesis_summary"]["rationale_ref"] == (
            "workspace://committee-rationales/committee-regime-risk-20260419-081/final"
        )
        assert projection["allowedActions"] == {
            "canRecordSponsorDecision": False,
        }


def test_cw03_detail_hides_record_sponsor_decision_for_reviewer_only() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/committees/committee-regime-risk-20260419-081",
            headers={"Authorization": REVIEWER_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["allowedActions"] == {
            "canRecordSponsorDecision": False,
        }


def test_cw03_detail_hides_record_sponsor_decision_without_sponsor_assignment() -> None:
    with _seeded_client() as client:
        consult = (
            bff_main.read_store._data["consultation_sessions"]["cs-20260419-081"]["metadata"]["consultation"]
        )
        consult["sponsor_session_id"] = None
        bff_main.read_store._save()

        response = client.get(
            "/api/v1/committees/committee-regime-risk-20260419-081",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["sponsor_assignment"] == {}
        assert payload["allowedActions"] == {
            "canRecordSponsorDecision": False,
        }


def test_cw03_record_sponsor_decision_persists_to_service_store() -> None:
    with tempfile.TemporaryDirectory() as td:
        seed_store = ReadSurfaceStore(
            os.path.join(td, "seed-read-surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        consultation_store = os.path.join(td, "consultation_sessions.json")
        with open(consultation_store, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    seed_store._data["consultation_sessions"],
                    indent=2,
                    ensure_ascii=True,
                )
            )

        with patch.dict(
            os.environ,
            {"PANTHEON_BFF_CONSULTATION_SESSION_STORE": consultation_store},
            clear=False,
        ):
            service_store = ReadSurfaceStore(
                os.path.join(td, "service-read-surfaces.json"),
                allow_local_snapshot_fallback=False,
            )
            updated = service_store.record_sponsor_decision(
                "committee-regime-risk-20260419-081",
                sponsor_decision="conditional",
                rationale_ref="workspace://committee-rationales/committee-regime-risk-20260419-081/service",
                actor_id="operator-service-path",
                recorded_at="2026-04-20T06:10:00Z",
            )
            assert updated is not None
            assert updated["sponsor_decision"] == "conditional"

            reloaded = ReadSurfaceStore(
                os.path.join(td, "service-read-surfaces.json"),
                allow_local_snapshot_fallback=False,
            )
            persisted = reloaded.get_committee("committee-regime-risk-20260419-081")
            assert persisted is not None
            assert persisted["sponsor_decision"] == "conditional"
            assert persisted["sponsor_decided_by"] == "operator-service-path"
            assert persisted["synthesis_summary"]["rationale_ref"] == (
                "workspace://committee-rationales/committee-regime-risk-20260419-081/service"
            )
