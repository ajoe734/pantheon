"""Regression coverage for BFF-CONSOL-016 Pack A detail journeys."""
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

PACK_A = {
    "strategy_id": "strategy-pack-a-momentum",
    "spec_version_id": "spec-pack-a-momentum-v1",
    "experiment_id": "exp-pack-a-momentum-001",
    "artifact_id": "artifact-pack-a-momentum-v1",
    "lineage_edge_id": "lineage-pack-a-strategy-artifact",
    "audit_entry_id": "audit-pack-a-strategy-approved",
    "persona_id": "persona-pack-a-momentum",
    "evaluation_session_id": "eval-pack-a-momentum-001",
    "deployment_plan_id": "plan-pack-a-paper-001",
    "approval_id": "approval-pack-a-deploy",
    "runtime_id": "runtime-pack-a-paper-001",
    "capital_pool_id": "pool-pack-a-ops",
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
def _pack_a_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_strategy_overlay = dict(bff_main._STRATEGY_BFF_OVERLAY)
        original_persona_overlay = dict(bff_main._PERSONA_BFF_OVERLAY)
        original_idempotency = dict(bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY)
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
                bff_main._STRATEGY_BFF_OVERLAY.clear()
                bff_main._PERSONA_BFF_OVERLAY.clear()
                bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
                yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            bff_main._STRATEGY_BFF_OVERLAY.clear()
            bff_main._STRATEGY_BFF_OVERLAY.update(original_strategy_overlay)
            bff_main._PERSONA_BFF_OVERLAY.clear()
            bff_main._PERSONA_BFF_OVERLAY.update(original_persona_overlay)
            bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
            bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.update(original_idempotency)


def _get(client: TestClient, path: str):
    return client.get(path, headers=HEADERS)


def _data(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _rows(payload: dict) -> list[dict]:
    rows = payload.get("data")
    if rows is None:
        rows = payload.get("items")
    if rows is None:
        rows = payload.get("events")
    return rows if isinstance(rows, list) else []


def _row_by_id(rows: list[dict], expected_id: str, *keys: str) -> dict:
    for row in rows:
        if any(str(row.get(key) or "") == expected_id for key in keys):
            return row
    raise AssertionError(f"missing row {expected_id}")


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


def test_detail_smoke_a_pack_a_routes_resolve_acceptance_links() -> None:
    with _pack_a_client() as client:
        strategies = _get(client, "/bff/strategies")
        assert strategies.status_code == 200, strategies.text
        _row_by_id(_rows(strategies.json()), PACK_A["strategy_id"], "id", "strategy_id")

        strategy = _get(client, f"/bff/strategies/{PACK_A['strategy_id']}")
        assert strategy.status_code == 200, strategy.text
        strategy_record = _data(strategy.json())
        assert strategy_record["id"] == PACK_A["strategy_id"]
        assert PACK_A["persona_id"] in strategy_record["personaIds"]

        specs = _get(client, f"/bff/strategies/{PACK_A['strategy_id']}/specs")
        assert specs.status_code == 200, specs.text
        _row_by_id(_rows(specs.json()), PACK_A["spec_version_id"], "spec_version_id", "id")

        experiments = _get(client, f"/bff/strategies/{PACK_A['strategy_id']}/experiments")
        assert experiments.status_code == 200, experiments.text
        experiment = _row_by_id(_rows(experiments.json()), PACK_A["experiment_id"], "experiment_id", "id")
        assert PACK_A["artifact_id"] in experiment["artifact_ids"]

        artifacts = _get(client, f"/bff/strategies/{PACK_A['strategy_id']}/artifacts")
        assert artifacts.status_code == 200, artifacts.text
        artifact = _row_by_id(_rows(artifacts.json()), PACK_A["artifact_id"], "artifact_id", "id")
        assert artifact["lineage_id"]

        lineage = _get(client, f"/bff/strategies/{PACK_A['strategy_id']}/lineage")
        assert lineage.status_code == 200, lineage.text
        lineage_record = _data(lineage.json())
        _row_by_id(lineage_record["edges"], PACK_A["lineage_edge_id"], "id", "lineage_id")

        audit = _get(client, f"/bff/strategies/{PACK_A['strategy_id']}/audit")
        assert audit.status_code == 200, audit.text
        _row_by_id(_rows(audit.json()), PACK_A["audit_entry_id"], "entry_id", "id")

        personas = _get(client, "/bff/personas")
        assert personas.status_code == 200, personas.text
        _row_by_id(_rows(personas.json()), PACK_A["persona_id"], "id", "persona_id")

        persona = _get(client, f"/bff/personas/{PACK_A['persona_id']}")
        assert persona.status_code == 200, persona.text
        persona_record = _data(persona.json())
        assert persona_record["id"] == PACK_A["persona_id"]
        assert persona_record["routedStrategies"] >= 1

        route_policy = _get(client, f"/bff/personas/{PACK_A['persona_id']}/route-policy")
        assert route_policy.status_code == 200, route_policy.text
        policy_record = _data(route_policy.json())
        assert policy_record["rules"]
        assert policy_record["rules"][0]["route"] == PACK_A["strategy_id"]

        activity = _get(client, f"/bff/personas/{PACK_A['persona_id']}/activity")
        assert activity.status_code == 200, activity.text
        activity_record = _data(activity.json())
        assert activity_record["personaId"] == PACK_A["persona_id"]
        assert isinstance(activity_record["sessions"], list)
        assert isinstance(activity_record["consultations"], list)

        evaluations = _get(client, f"/bff/personas/{PACK_A['persona_id']}/evaluations")
        assert evaluations.status_code == 200, evaluations.text
        _row_by_id(_rows(evaluations.json()), PACK_A["evaluation_session_id"], "session_id", "id")

        deployments = _get(client, "/bff/deployments")
        assert deployments.status_code == 200, deployments.text
        _row_by_id(_rows(deployments.json()), PACK_A["deployment_plan_id"], "plan_id", "id")

        deployment = _get(client, f"/bff/deployments/{PACK_A['deployment_plan_id']}")
        assert deployment.status_code == 200, deployment.text
        deployment_record = _data(deployment.json())
        assert deployment_record["plan_id"] == PACK_A["deployment_plan_id"]
        assert deployment_record["approval_decision_id"] == PACK_A["approval_id"]
        assert deployment_record["approval_decision"]["id"] == PACK_A["approval_id"]
        assert deployment_record["capital_pool_id"] == PACK_A["capital_pool_id"]
        assert deployment_record["runtime_binding_id"] == PACK_A["runtime_id"]
        assert deployment_record["stages"]

        runtimes = _get(client, "/bff/runtimes")
        assert runtimes.status_code == 200, runtimes.text
        _row_by_id(_rows(runtimes.json()), PACK_A["runtime_id"], "runtime_id", "binding_id", "id")

        runtime = _get(client, f"/bff/runtimes/{PACK_A['runtime_id']}")
        assert runtime.status_code == 200, runtime.text
        runtime_record = _data(runtime.json())
        assert runtime_record["runtime_id"] == PACK_A["runtime_id"]
        assert runtime_record["plan_id"] == PACK_A["deployment_plan_id"]
        assert runtime_record["deployment_stage"] == "paper"
        assert runtime_record["capital_pool_id"] == PACK_A["capital_pool_id"]
        assert runtime_record["artifact_id"] == PACK_A["artifact_id"]


def test_detail_smoke_a_phantom_family_ids_return_typed_404() -> None:
    with _pack_a_client() as client:
        for path in (
            "/bff/strategies/phantom-id-does-not-exist",
            "/bff/personas/phantom-id-does-not-exist",
            "/bff/deployments/phantom-id-does-not-exist",
            "/bff/runtimes/phantom-id-does-not-exist",
        ):
            response = _get(client, path)
            assert response.status_code == 404, response.text
            assert "undefined" not in response.text
            _assert_typed_404(response.json())
