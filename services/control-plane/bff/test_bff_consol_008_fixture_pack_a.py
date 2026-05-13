from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


HEADERS = {"Authorization": "Bearer op-2:operator"}
FIXTURE_PATH = Path(__file__).resolve().parent / "data" / "fixtures_pack_a.json"

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


def _fresh_pack_a_client(td: str) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
    bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    bff_main._STRATEGY_BFF_OVERLAY.clear()
    bff_main._PERSONA_BFF_OVERLAY.clear()
    return TestClient(bff_main.app)


def _list_payload_count(payload: dict) -> int:
    rows = payload.get("data")
    if rows is None:
        rows = payload.get("items")
    assert isinstance(rows, list)
    return len(rows)


def test_fixture_pack_a_declares_all_required_families() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["policy"]["paper_canary_truth_impact"] == "none"
    for family in (
        "strategies",
        "personas",
        "capital_pools",
        "rebalances",
        "deployments",
    ):
        assert payload["families"][family]


def test_pack_a_live_lists_return_non_empty_data_counts() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            with mock.patch.dict(os.environ, SERVICE_ENV_BLANKS, clear=False):
                client = _fresh_pack_a_client(td)
                for path in (
                    "/bff/strategies",
                    "/bff/personas",
                    "/bff/capital-pools",
                    "/bff/rebalances",
                    "/bff/deployments",
                ):
                    resp = client.get(path, headers=HEADERS)
                    assert resp.status_code == 200, f"{path}: {resp.text}"
                    assert _list_payload_count(resp.json()) >= 1
        finally:
            bff_main.read_store = original


def test_pack_a_strategy_persona_and_deployment_linkages_are_queryable() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            with mock.patch.dict(os.environ, SERVICE_ENV_BLANKS, clear=False):
                client = _fresh_pack_a_client(td)

                strategy_id = "strategy-pack-a-momentum"
                for subpath in ("specs", "experiments", "artifacts", "audit"):
                    resp = client.get(f"/bff/strategies/{strategy_id}/{subpath}", headers=HEADERS)
                    assert resp.status_code == 200, f"{subpath}: {resp.text}"
                    assert _list_payload_count(resp.json()) >= 1

                lineage = client.get(f"/bff/strategies/{strategy_id}/lineage", headers=HEADERS)
                assert lineage.status_code == 200, lineage.text
                assert lineage.json()["data"]["edges"]

                persona_id = "persona-pack-a-momentum"
                route_policy = client.get(f"/bff/personas/{persona_id}/route-policy", headers=HEADERS)
                assert route_policy.status_code == 200, route_policy.text
                assert route_policy.json()["data"]["rules"]

                evaluations = client.get(f"/bff/personas/{persona_id}/evaluations", headers=HEADERS)
                assert evaluations.status_code == 200, evaluations.text
                assert _list_payload_count(evaluations.json()) >= 1

                deployment = client.get("/bff/deployments/plan-pack-a-paper-001", headers=HEADERS)
                assert deployment.status_code == 200, deployment.text
                deployment_data = deployment.json()["data"]
                assert deployment_data["approval_decision_id"] == "approval-pack-a-deploy"
                assert deployment_data["approval_decision"]["id"] == "approval-pack-a-deploy"
                assert deployment_data["stages"][0]["stage"] == "paper"
        finally:
            bff_main.read_store = original


def test_pack_a_does_not_load_when_local_snapshot_fallback_is_disabled() -> None:
    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(os.environ, SERVICE_ENV_BLANKS, clear=False):
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=False,
            )
            assert store.get_strategy_spec("strategy-pack-a-momentum") is None
            assert store.get_deployment_plan("plan-pack-a-paper-001") is None
            assert store.list_rebalances() == []
