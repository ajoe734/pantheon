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

import json
from pathlib import Path
from typing import Any, Iterator
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
from ports import ReadSurfacePorts


HEADERS = {"Authorization": "Bearer op-2:operator"}
FIXTURE_PATH = Path(__file__).resolve().parent / "data" / "fixtures_pack_b.json"

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


class DetailSmokeBTestReadPorts(ReadSurfacePorts):
    def __init__(self, *, allow_local_snapshot_fallback: bool = True) -> None:
        super().__init__()
        self._allow_fallback = allow_local_snapshot_fallback
        self._data: dict[str, Any] = {}
        if allow_local_snapshot_fallback and FIXTURE_PATH.exists():
            payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
            self._data = payload.get("datasets", {})

    def dataset_source(self, dataset: str) -> str:
        return "local_snapshot" if self._allow_fallback else "missing"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        src = self.dataset_source(dataset)
        status = "unavailable" if src == "missing" else "ok"
        return {"status": status, "source": src, "snapshot_at": snapshot_at}

    def _get_dataset(self, name: str) -> dict[str, Any] | list[Any]:
        return self._data.get(name, {})

    def list_evolution_programs(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("evolution_programs")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_evolution_program(self, program_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("evolution_programs")
        if isinstance(ds, dict):
            return ds.get(str(program_id or ""))
        return next((p for p in ds if p.get("id") == program_id or p.get("program_id") == program_id), None)

    def list_research_experiments(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("research_experiments")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_research_experiment(self, experiment_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("research_experiments")
        if isinstance(ds, dict):
            return ds.get(str(experiment_id or ""))
        return next((e for e in ds if e.get("id") == experiment_id or e.get("experiment_id") == experiment_id), None)

    def get_experiment_bff(self, experiment_id: str | None) -> dict[str, Any] | None:
        return self.get_research_experiment(experiment_id)

    def list_research_analyses(self, experiment_id: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("research_analyses")
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        if experiment_id:
            return [a for a in items if a.get("experiment_id") == experiment_id]
        return items

    def get_research_analysis(self, analysis_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("research_analyses")
        if isinstance(ds, dict):
            return ds.get(str(analysis_id or ""))
        return next((a for a in ds if a.get("id") == analysis_id or a.get("analysis_id") == analysis_id), None)

    def list_v5_interventions(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("v5_interventions")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_v5_intervention(self, intv_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("v5_interventions")
        if isinstance(ds, dict):
            return ds.get(str(intv_id or ""))
        return next((i for i in ds if i.get("id") == intv_id or i.get("intervention_id") == intv_id), None)

    def list_agora_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("agora_sessions")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_agora_session(self, session_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("agora_sessions")
        if isinstance(ds, dict):
            return ds.get(str(session_id or ""))
        return next((s for s in ds if s.get("sessionId") == session_id or s.get("session_id") == session_id or s.get("id") == session_id), None)

    def list_agora_session_messages(self, session_id: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("agora_sessions")
        if isinstance(ds, dict) and session_id and str(session_id) in ds:
            return list(ds[str(session_id)].get("messages") or [])
        session = self.get_agora_session(session_id)
        if session and isinstance(session.get("messages"), list):
            return list(session["messages"])
        return []

    def list_agora_messages(self, session_id: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_agora_session_messages(session_id, **kwargs)

    def list_research_artifacts(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("research_artifacts")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_research_artifact(self, artifact_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("research_artifacts")
        if isinstance(ds, dict):
            return ds.get(str(artifact_id or ""))
        return next((a for a in ds if a.get("id") == artifact_id or a.get("artifact_id") == artifact_id), None)

    def list_lineage_edges(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("lineage_edges")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def artifact_exists(self, artifact_id: str | None) -> bool:
        return self.get_research_artifact(artifact_id) is not None

    def get_inspiration_graph(self, artifact_id: str | None) -> dict[str, Any] | None:
        if not self.artifact_exists(artifact_id):
            return None
        edges = self.list_lineage_edges()
        filtered = [e for e in edges if e.get("to_artifact_id") == artifact_id or e.get("from_artifact_id") == artifact_id or e.get("artifact_id") == artifact_id]
        return {
            "artifact_id": artifact_id,
            "inspiration_edges": [
                {
                    "lineage_edge_id": e.get("id") or e.get("lineage_edge_id") or "lineage-pack-b-001",
                    "from_artifact_id": e.get("from_artifact_id"),
                    "to_artifact_id": e.get("to_artifact_id"),
                    "edge_type": e.get("edge_type"),
                }
                for e in (filtered or edges)
            ],
        }

    def get_inspiration_lineage(self, artifact_id: str | None) -> dict[str, Any]:
        return self.get_inspiration_graph(artifact_id) or {"artifact_id": artifact_id, "inspiration_edges": []}


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
                bff_main.read_store = DetailSmokeBTestReadPorts(
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
            assert error.get("code") == "RESOURCE_NOT_FOUND"
            return
    error = payload.get("error")
    if isinstance(error, dict):
        assert error.get("code") == "RESOURCE_NOT_FOUND"
        return
    assert payload.get("code") == "RESOURCE_NOT_FOUND"


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
