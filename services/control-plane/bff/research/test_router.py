"""Standalone contract tests for the prepared Research Experiments router.

Builds `create_research_experiments_router()` into a bare FastAPI app with
fakes for every injected dependency; does not import `main.py` and does not
touch the live `/bff/experiments*` / `/bff/research-experiments*` routes
main.py currently serves. Characterizes router.py's own behavior against
CHARACTERIZATION.md.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from research.router import create_research_experiments_router  # noqa: E402


class _FakeIdentity:
    def __init__(self, operator_id: str = "operator-1") -> None:
        self.operator_id = operator_id


class _FakeReadStore:
    """Minimal durable-store double covering exactly the functions the
    router calls, mirroring read_store.py's real behavior for
    research_experiments (list_experiments_bff/create_experiment_bff/etc.
    are thin projections over the same underlying store)."""

    def __init__(self) -> None:
        self._experiments: Dict[str, Dict[str, Any]] = {}
        self._analyses: List[Dict[str, Any]] = []
        self._next_id = 0

    def list_research_experiments(self) -> List[Dict[str, Any]]:
        return list(self._experiments.values())

    def get_research_experiment(self, experiment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._experiments.get(experiment_id) if experiment_id else None

    def get_experiment_bff(self, experiment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        exp = self.get_research_experiment(experiment_id)
        if exp is None:
            return None
        return {
            "id": exp["experiment_id"],
            "experiment_id": exp["experiment_id"],
            "name": exp.get("experiment_name", ""),
            "status": exp.get("status", "unknown"),
            "created_at": exp.get("queued_at"),
        }

    def create_experiment_bff(self, *, name, actor_id, created_at=None, params=None):
        self._next_id += 1
        experiment_id = f"exp-{self._next_id}"
        record = {
            "experiment_id": experiment_id,
            "experiment_name": name,
            "status": "queued",
            "queued_at": created_at,
            "created_by": actor_id,
        }
        self._experiments[experiment_id] = record
        return {
            "id": experiment_id,
            "experiment_id": experiment_id,
            "name": name,
            "status": "queued",
            "created_at": created_at,
        }

    def list_research_analyses(self, *, experiment_id: str) -> List[Dict[str, Any]]:
        return [a for a in self._analyses if a.get("experiment_id") == experiment_id]

    def get_experiment_logs(self, experiment_id: str) -> List[Dict[str, Any]]:
        return list(self._experiments.get(experiment_id, {}).get("logs") or [])

    def get_experiment_metrics(self, experiment_id: str) -> Dict[str, Any]:
        return dict(self._experiments.get(experiment_id, {}).get("metrics") or {})

    def get_experiment_artifacts(self, experiment_id: str) -> List[Dict[str, Any]]:
        return list(self._experiments.get(experiment_id, {}).get("artifact_links") or [])


def _bff_error(status_code, code, message, reason, **extra):
    return HTTPException(status_code=status_code, detail={"code": code.value, "message": message, "reason": reason, **extra})


def _build_app(read_store: _FakeReadStore, *, submit_experiment_action=None) -> TestClient:
    router = create_research_experiments_router(
        get_read_store=lambda: read_store,
        extract_identity=lambda authorization: _FakeIdentity(),
        require_read_role=lambda identity: None,
        require_operator_role=lambda identity: None,
        bff_error=_bff_error,
        utc_now=lambda: "2026-08-28T00:00:00Z",
        submit_experiment_action=submit_experiment_action,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_list_experiments_items_envelope_empty():
    client = _build_app(_FakeReadStore())
    resp = client.get("/bff/experiments")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["page_info"] == {"next_page_token": None}


def test_create_requires_name_and_is_visible_via_both_surfaces():
    store = _FakeReadStore()
    client = _build_app(store)

    bad = client.post("/bff/experiments", json={})
    assert bad.status_code == 422

    created = client.post("/bff/experiments", json={"name": "Exp A"})
    assert created.status_code == 201
    experiment_id = created.json()["experiment_id"]
    assert experiment_id in store._experiments

    via_experiments = client.get("/bff/experiments").json()["items"]
    via_research = client.get("/bff/research-experiments").json()
    assert [i["experiment_id"] for i in via_experiments] == [experiment_id]
    assert via_research["data"] == via_research["items"]
    assert [i["experiment_id"] for i in via_research["items"]] == [experiment_id]
    assert via_research["page_info"]["total"] == 1


def test_get_detail_404_and_data_envelope_with_analysis_links():
    store = _FakeReadStore()
    created = _build_app(store).post("/bff/experiments", json={"name": "Exp A"}).json()
    experiment_id = created["experiment_id"]
    store._analyses.append({"analysis_id": "an-1", "experiment_id": experiment_id, "ticket_id": "t-1", "status": "done"})
    client = _build_app(store)

    missing = client.get("/bff/experiments/does-not-exist")
    assert missing.status_code == 404

    found = client.get(f"/bff/experiments/{experiment_id}")
    assert found.status_code == 200
    data = found.json()["data"]
    assert data["experiment_id"] == experiment_id
    assert data["analysis_ids"] == ["an-1"]
    assert data["analysis_links"][0]["ticket_id"] == "t-1"

    # research-experiments detail resolves through the same helper.
    research_detail = client.get(f"/bff/research-experiments/{experiment_id}").json()["data"]
    assert research_detail["analysis_ids"] == ["an-1"]


def test_status_filter_is_csv_case_insensitive():
    store = _FakeReadStore()
    store._experiments["e1"] = {"experiment_id": "e1", "experiment_name": "E1", "status": "Running", "queued_at": "t1"}
    store._experiments["e2"] = {"experiment_id": "e2", "experiment_name": "E2", "status": "failed", "queued_at": "t2"}
    client = _build_app(store)

    resp = client.get("/bff/experiments", params={"status": "running,FAILED"})
    ids = {item["experiment_id"] for item in resp.json()["items"]}
    assert ids == {"e1", "e2"}

    resp = client.get("/bff/experiments", params={"status": "queued"})
    assert resp.json()["items"] == []


def test_logs_metrics_artifacts_404_on_missing_experiment():
    client = _build_app(_FakeReadStore())
    assert client.get("/bff/experiments/missing/logs").status_code == 404
    assert client.get("/bff/experiments/missing/metrics").status_code == 404
    assert client.get("/bff/experiments/missing/artifacts").status_code == 404


def test_logs_metrics_artifacts_shapes():
    store = _FakeReadStore()
    store._experiments["e1"] = {
        "experiment_id": "e1", "experiment_name": "E1", "status": "running", "queued_at": "t1",
        "logs": [{"line": "started"}], "metrics": {"sharpe": 1.2}, "artifact_links": [{"ref": "a1"}],
    }
    client = _build_app(store)

    logs = client.get("/bff/experiments/e1/logs").json()
    assert logs == {"experiment_id": "e1", "logs": [{"line": "started"}], "meta": logs["meta"]}

    metrics = client.get("/bff/experiments/e1/metrics").json()
    assert metrics["metrics"] == {"sharpe": 1.2}

    artifacts = client.get("/bff/experiments/e1/artifacts").json()
    assert artifacts["artifacts"] == [{"ref": "a1"}]


def test_action_without_injected_dispatch_is_501():
    store = _FakeReadStore()
    store._experiments["e1"] = {"experiment_id": "e1", "experiment_name": "E1", "status": "running", "queued_at": "t1"}
    client = _build_app(store, submit_experiment_action=None)

    resp = client.post("/bff/experiments/e1/actions/cancel", json={})
    assert resp.status_code == 501


def test_action_dispatches_through_injected_callable():
    store = _FakeReadStore()
    store._experiments["e1"] = {"experiment_id": "e1", "experiment_name": "E1", "status": "running", "queued_at": "t1"}

    captured = {}

    def submit_experiment_action(entity_type, entity_id, action_id, identity, payload):
        captured.update(entity_type=entity_type, entity_id=entity_id, action_id=action_id, payload=payload)
        return {"status": "accepted"}

    client = _build_app(store, submit_experiment_action=submit_experiment_action)
    resp = client.post("/bff/experiments/e1/actions/cancel", json={"reason": "budget"})
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted"}
    assert captured == {
        "entity_type": "Experiment",
        "entity_id": "e1",
        "action_id": "cancel",
        "payload": {"reason": "budget"},
    }
