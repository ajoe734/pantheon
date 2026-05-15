"""Regression coverage for BFF-CONSOL-017 Pack B detail journeys."""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


HEADERS = {"Authorization": "Bearer op-2:operator"}

PACK_B = {
    "evolution_program_id": "evoprog-pack-b-001",
    "research_ticket_id": "rt-pack-b-001",
    "research_experiment_id": "exp-pack-b-001",
    "research_analysis_id": "analysis-pack-b-001",
    "intervention_id": "intv-pack-b-001",
    "agora_session_id": "agora-session-pack-b-001",
    "artifact_id": "artifact-pack-b-001",
    "lineage_id": "lineage-pack-b-001",
}

SERVICE_ENV_BLANKS = {
    "PANTHEON_DEPLOYMENT_API_URL": "",
    "PANTHEON_DEPLOYMENT_SERVICE_URL": "",
    "PANTHEON_GOVERNANCE_APPROVAL_API_URL": "",
    "PANTHEON_GOVERNANCE_SERVICE_URL": "",
    "PANTHEON_CAPITAL_API_URL": "",
    "PANTHEON_CAPITAL_SERVICE_URL": "",
    "PANTHEON_RUNTIME_MANAGER_URL": "",
    "PANTHEON_INTERNAL_API_URL": "",
    "PANTHEON_PERSONA_API_URL": "",
    "PANTHEON_PERSONA_SERVICE_URL": "",
    "PANTHEON_LINEAGE_READ_URL": "",
    "PANTHEON_LINEAGE_API_URL": "",
}


@contextmanager
def _pack_b_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_program_overlay = dict(bff_main._GOV_BFF_EVOLUTION_PROGRAM_OVERLAY)
        original_experiment_overlay = dict(bff_main._GOV_BFF_EXPERIMENT_OVERLAY)
        original_job_overlay = dict(bff_main._GOV_BFF_JOB_OVERLAY)
        original_idempotency = dict(bff_main._GOV_BFF_IDEMPOTENCY)
        env = {
            **SERVICE_ENV_BLANKS,
            "PANTHEON_BFF_AUTH_STUB": "true",
            "PANTHEON_BFF_AUTH_MODE": "permissive",
        }
        try:
            with mock.patch.dict(os.environ, env, clear=False):
                bff_main.read_store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=True,
                )
                bff_main._GOV_BFF_EVOLUTION_PROGRAM_OVERLAY.clear()
                bff_main._GOV_BFF_EXPERIMENT_OVERLAY.clear()
                bff_main._GOV_BFF_JOB_OVERLAY.clear()
                bff_main._GOV_BFF_IDEMPOTENCY.clear()
                yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            bff_main._GOV_BFF_EVOLUTION_PROGRAM_OVERLAY.clear()
            bff_main._GOV_BFF_EVOLUTION_PROGRAM_OVERLAY.update(original_program_overlay)
            bff_main._GOV_BFF_EXPERIMENT_OVERLAY.clear()
            bff_main._GOV_BFF_EXPERIMENT_OVERLAY.update(original_experiment_overlay)
            bff_main._GOV_BFF_JOB_OVERLAY.clear()
            bff_main._GOV_BFF_JOB_OVERLAY.update(original_job_overlay)
            bff_main._GOV_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.update(original_idempotency)


def _get(client: TestClient, path: str):
    return client.get(path, headers=HEADERS)


def _data(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _assert_typed_404(payload: dict) -> None:
    detail = payload.get("detail")
    if isinstance(detail, dict):
        error = detail.get("error")
        if isinstance(error, dict):
            assert error.get("code") == "OBJECT_NOT_FOUND"
            return
    error = payload.get("error")
    if isinstance(error, dict):
        assert error.get("code") == "OBJECT_NOT_FOUND"
        return
    assert payload.get("code") == "OBJECT_NOT_FOUND"


def test_detail_smoke_b_pack_b_routes_resolve_acceptance_links() -> None:
    with _pack_b_client() as client:
        program = _get(client, f"/bff/evolution-programs/{PACK_B['evolution_program_id']}")
        assert program.status_code == 200, program.text
        program_record = _data(program.json())
        assert program_record["program_id"] == PACK_B["evolution_program_id"]

        experiment = _get(client, f"/bff/research-experiments/{PACK_B['research_experiment_id']}")
        assert experiment.status_code == 200, experiment.text
        experiment_record = _data(experiment.json())
        assert experiment_record["experiment_id"] == PACK_B["research_experiment_id"]
        assert experiment_record["ticket_id"] == PACK_B["research_ticket_id"]
        assert PACK_B["research_analysis_id"] in experiment_record["analysis_ids"]
        assert experiment_record["analysis_links"][0]["detail"] == (
            f"/bff/research-analyses/{PACK_B['research_analysis_id']}"
        )

        analysis = _get(client, f"/bff/research-analyses/{PACK_B['research_analysis_id']}")
        assert analysis.status_code == 200, analysis.text
        analysis_record = _data(analysis.json())
        assert analysis_record["analysis_id"] == PACK_B["research_analysis_id"]
        assert analysis_record["ticket_id"] == PACK_B["research_ticket_id"]
        assert analysis_record["experiment_id"] == PACK_B["research_experiment_id"]

        intervention = _get(client, f"/bff/v5/interventions/{PACK_B['intervention_id']}")
        assert intervention.status_code == 200, intervention.text
        skeleton = _data(intervention.json())["remediation_skeleton"]
        assert skeleton["two_man_rule_enforced"] is True
        assert skeleton["remediation_actions_available"]

        session = _get(client, f"/bff/agora/sessions/{PACK_B['agora_session_id']}")
        assert session.status_code == 200, session.text
        session_record = _data(session.json())
        assert session_record["topic"]
        assert session_record["participants"]

        messages = _get(client, f"/bff/agora/sessions/{PACK_B['agora_session_id']}/messages")
        assert messages.status_code == 200, messages.text
        messages_payload = messages.json()
        assert messages_payload["data"]

        artifact = _get(client, f"/bff/artifacts/{PACK_B['artifact_id']}")
        assert artifact.status_code == 200, artifact.text
        artifact_record = _data(artifact.json())
        assert artifact_record["artifact_id"] == PACK_B["artifact_id"]
        assert artifact_record["lineage_id"] == PACK_B["lineage_id"]

        lineage = _get(client, f"/api/v1/lineage/inspiration/{PACK_B['artifact_id']}")
        assert lineage.status_code == 200, lineage.text
        lineage_payload = lineage.json()
        assert lineage_payload["inspiration_edges"][0]["lineage_edge_id"] == PACK_B["lineage_id"]


def test_detail_smoke_b_phantom_family_ids_return_typed_404() -> None:
    with _pack_b_client() as client:
        for path in (
            "/bff/evolution-programs/phantom-id-does-not-exist",
            "/bff/research-experiments/phantom-id-does-not-exist",
            "/bff/v5/interventions/phantom-id-does-not-exist",
            "/bff/agora/sessions/phantom-id-does-not-exist",
            "/bff/artifacts/phantom-id-does-not-exist",
        ):
            response = _get(client, path)
            assert response.status_code == 404, response.text
            _assert_typed_404(response.json())
