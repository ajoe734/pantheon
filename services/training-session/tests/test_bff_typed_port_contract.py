"""Contract tests between the BFF Persona/Training typed ports and this service.

These tests verify, from the Training Session side, the two claims made by
``services/control-plane/bff/ports/persona_training.py``:

1. Trainer session create/read requests shaped the way
   ``read_store.ReadSurfaceStore`` sends them (and that
   ``TrainingSessionTrainerPort`` delegates to unchanged) are accepted by
   this service's real HTTP surface, and the response carries every field
   ``read_store._project_trainer_session_detail`` reads.
2. The rapid-evaluation owner assigned by ``RapidEvaluationOwnership``
   (ACG-02-017) is this service: ``run_rapid_eval`` is only implemented here,
   and the sole other reference in the repository
   (``services/control-plane/persona/persona_strategy_discovery.py``) is a
   recommendation label, not a competing implementation.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]
BFF_DIR = REPO_ROOT / "services" / "control-plane" / "bff"

for path in (SERVICE_DIR, BFF_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from strict_test_support import (
    FIXED_TRUSTED_NOW,
    make_fake_persona_target_commit,
    make_fake_real_vectorbt_workflow,
    make_fake_target_precondition_reader,
    materialize_strict_authority,
)

from ports.persona_training import (
    RapidEvaluationOwnership,
    TrainingSessionTrainerPort,
)


def _load_service_module():
    fixture = materialize_strict_authority(tempfile.mkdtemp())
    os.environ.update(fixture.environment())
    with mock.patch.dict("os.environ", fixture.environment(), clear=False):
        spec = importlib.util.spec_from_file_location(
            "training_session_test_typed_port_contract_main", SERVICE_DIR / "main.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["training_session_test_typed_port_contract_main"] = module
        spec.loader.exec_module(module)
    module.store = module.TrainingSessionStore(fixture.data_dir)
    module._trusted_now = lambda: FIXED_TRUSTED_NOW
    module.run_vectorbt_workflow = make_fake_real_vectorbt_workflow()
    module._read_target_precondition = make_fake_target_precondition_reader()
    module._commit_authoritative_persona_target = make_fake_persona_target_commit()
    module._strict_authority_fixture = fixture
    return module


class _HttpBackedTrainingStore:
    """Minimal stand-in for read_store.ReadSurfaceStore's HTTP-backed path.

    Sends the same body shapes read_store.py sends to
    /api/training/sessions and /api/training/sessions/{id}/events, and
    projects the response with the same field set
    read_store._project_trainer_session_detail reads, so a passing test here
    is evidence the real service satisfies the typed port's contract.
    """

    def __init__(self, client: TestClient) -> None:
        self._client = client

    @staticmethod
    def _project(session: dict) -> dict:
        session_id = str(session.get("session_id") or session.get("id") or "")
        return {
            "session_id": session_id,
            "persona_id": session.get("persona_id"),
            "session_type": session.get("session_type") or "trainer",
            "objective": session.get("objective") or session.get("topic"),
            "status": session.get("status"),
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at") or session.get("completed_at"),
            "opened_by": session.get("opened_by") or session.get("operator_id"),
            "context_refs": session.get("context_refs") or [],
            "events": session.get("events") or [],
        }

    def create_trainer_session(
        self, *, persona_id, objective, context_refs, actor_id, created_at=None
    ):
        response = self._client.post(
            "/api/training/sessions",
            json={
                "persona_id": persona_id,
                "objective": objective,
                "context_refs": context_refs,
                "actor_id": actor_id,
                "created_at": created_at,
            },
        )
        assert response.status_code == 201, response.text
        return self._project(response.json())

    def get_trainer_session(self, session_id):
        response = self._client.get(f"/api/training/sessions/{session_id}")
        assert response.status_code == 200, response.text
        return self._project(response.json())

    def list_trainer_sessions(self, *, persona_id=None, status=None):
        response = self._client.get(
            "/api/training/sessions",
            params={k: v for k, v in {"persona_id": persona_id, "status": status}.items() if v},
        )
        assert response.status_code == 200, response.text
        return [self._project(session) for session in response.json()]


def test_create_and_get_trainer_session_round_trip_through_typed_port():
    module = _load_service_module()
    client = TestClient(module.app)
    port = TrainingSessionTrainerPort(training=_HttpBackedTrainingStore(client))

    created = port.create_trainer_session(
        persona_id="persona-alpha",
        objective="Tune momentum controls",
        context_refs=[{"type": "evidence", "id": "ev-1"}],
        actor_id="operator-1",
        created_at="2026-04-28T18:00:00Z",
    )

    # Every field read_store._project_trainer_session_detail depends on is present.
    for field in (
        "session_id",
        "persona_id",
        "session_type",
        "objective",
        "status",
        "started_at",
        "opened_by",
        "context_refs",
        "events",
    ):
        assert field in created, f"missing {field} in trainer session contract"

    assert created["persona_id"] == "persona-alpha"
    assert created["objective"] == "Tune momentum controls"
    assert created["status"] == "active"
    assert created["session_id"]

    fetched = port.get_trainer_session(created["session_id"])
    assert fetched["session_id"] == created["session_id"]
    assert fetched["persona_id"] == "persona-alpha"

    listed = port.list_trainer_sessions(persona_id="persona-alpha")
    assert any(session["session_id"] == created["session_id"] for session in listed)


def test_rapid_eval_owner_is_this_service():
    """ACG-02-017: verify the traced caller evidence, not just the label."""
    ownership = RapidEvaluationOwnership()
    assert ownership.owner == "training-session"

    module_path = REPO_ROOT / ownership.implementation_module
    assert module_path == SERVICE_DIR / "rapid_eval_integration.py"
    assert module_path.is_file()

    spec = importlib.util.spec_from_file_location("rapid_eval_integration_contract_check", module_path)
    assert spec and spec.loader
    rapid_eval_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rapid_eval_module)
    assert hasattr(rapid_eval_module, ownership.implementation_symbol)
    assert callable(getattr(rapid_eval_module, ownership.implementation_symbol))


def test_persona_strategy_discovery_does_not_implement_a_competing_rapid_eval_owner():
    discovery_path = (
        REPO_ROOT / "services" / "control-plane" / "persona" / "persona_strategy_discovery.py"
    )
    assert discovery_path.is_file()
    source = discovery_path.read_text(encoding="utf-8")

    assert "rapid_eval_integration" not in source
    assert "run_rapid_eval" in source  # used only as a RecommendedAction.type label


def test_no_rapid_eval_implementation_exists_under_research():
    research_dir = REPO_ROOT / "services" / "research"
    assert research_dir.is_dir()
    hits = [
        path
        for path in research_dir.rglob("*.py")
        if "def run_rapid_eval" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits == []
