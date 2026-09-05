from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
from ports import ReadSurfacePorts

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


class FixturePackATestReadPorts(ReadSurfacePorts):
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

    def list_strategies(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("strategies")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_strategy(self, strategy_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("strategies")
        if isinstance(ds, dict):
            return ds.get(str(strategy_id or ""))
        return next((s for s in ds if s.get("id") == strategy_id or s.get("strategy_id") == strategy_id), None)

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("personas")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("personas")
        if isinstance(ds, dict):
            return ds.get(str(persona_id or ""))
        return next((p for p in ds if p.get("id") == persona_id or p.get("persona_id") == persona_id), None)

    def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("capital_pools")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_capital_pool(self, pool_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("capital_pools")
        if isinstance(ds, dict):
            return ds.get(str(pool_id or ""))
        return next((p for p in ds if p.get("id") == pool_id or p.get("pool_id") == pool_id), None)

    def list_rebalances(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("rebalances")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_rebalance(self, rebalance_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("rebalances")
        if isinstance(ds, dict):
            return ds.get(str(rebalance_id or ""))
        return next((r for r in ds if r.get("id") == rebalance_id or r.get("rebalance_id") == rebalance_id), None)

    def list_deployment_plans(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("deployment_plans") or self._get_dataset("deployments")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_deployment_plan(self, plan_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("deployment_plans") or self._get_dataset("deployments")
        if isinstance(ds, dict):
            return ds.get(str(plan_id or ""))
        return next((p for p in ds if p.get("id") == plan_id or p.get("plan_id") == plan_id), None)

    def get_strategy_spec(self, spec_or_strat_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("strategy_specs")
        if isinstance(ds, dict):
            if str(spec_or_strat_id or "") in ds:
                return ds[str(spec_or_strat_id or "")]
            return next((s for s in ds.values() if s.get("strategy_id") == spec_or_strat_id), None)
        return next((s for s in ds if s.get("id") == spec_or_strat_id or s.get("strategy_id") == spec_or_strat_id), None)

    def list_strategy_spec_versions(self, strategy_id: str | None = None) -> list[dict[str, Any]]:
        spec = self.get_strategy_spec(strategy_id)
        if spec and isinstance(spec.get("versions"), list):
            return spec["versions"]
        ds = self._get_dataset("strategy_specs")
        specs = list(ds.values()) if isinstance(ds, dict) else list(ds)
        res = []
        for s in specs:
            if not strategy_id or s.get("strategy_id") == strategy_id:
                if isinstance(s.get("versions"), list):
                    res.extend(s["versions"])
                else:
                    res.append(s)
        return res

    def list_strategy_specs(self, strategy_id: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_strategy_spec_versions(strategy_id)

    def list_research_experiments(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("research_experiments")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_strategy_experiments(self, strategy_id: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        raw = self.list_research_experiments()
        if strategy_id:
            return [e for e in raw if (e.get("linked_strategy_id") or e.get("strategy_id")) == strategy_id]
        return raw

    def list_research_artifacts(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("research_artifacts")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_strategy_artifacts(self, strategy_id: str | None) -> list[dict[str, Any]]:
        raw = self.list_research_artifacts()
        if strategy_id:
            return [a for a in raw if (a.get("linked_strategy_id") or a.get("strategy_id")) == strategy_id]
        return raw

    def list_lineage_edges(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("lineage_edges")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_governance_audit_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("governance_audit_events")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_strategy_lineage(self, strategy_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("strategy_lineages")
        if isinstance(ds, dict):
            if str(strategy_id or "") in ds:
                return ds[str(strategy_id or "")]
            return next((l for l in ds.values() if l.get("strategy_id") == strategy_id), None)
        return next((l for l in ds if l.get("strategy_id") == strategy_id or l.get("id") == strategy_id), None)

    def get_route_policy_for_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("persona_route_policies")
        if isinstance(ds, dict):
            if str(persona_id or "") in ds:
                return ds[str(persona_id or "")]
            return next((p for p in ds.values() if p.get("persona_id") == persona_id or p.get("personaId") == persona_id), None)
        return next((p for p in ds if p.get("persona_id") == persona_id or p.get("personaId") == persona_id or p.get("id") == persona_id), None)

    def get_persona_route_policy(self, persona_id: str | None) -> dict[str, Any] | None:
        return self.get_route_policy_for_persona(persona_id)

    def get_teaching_sessions_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        ds = self._get_dataset("teaching_sessions")
        sessions = list(ds.values()) if isinstance(ds, dict) else list(ds)
        if persona_id:
            return [s for s in sessions if s.get("persona_id") == persona_id]
        return sessions

    def list_persona_evaluations(self, persona_id: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.get_teaching_sessions_for_persona(persona_id)

    def get_approval_decision(self, approval_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("approval_decisions")
        if isinstance(ds, dict):
            return ds.get(str(approval_id or ""))
        return next((a for a in ds if a.get("id") == approval_id or a.get("approval_id") == approval_id or a.get("decision_id") == approval_id), None)


def _fresh_pack_a_client(td: str) -> TestClient:
    bff_main.read_store = FixturePackATestReadPorts(
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
            store = FixturePackATestReadPorts(
                allow_local_snapshot_fallback=False,
            )
            assert store.get_strategy_spec("strategy-pack-a-momentum") is None
            assert store.get_deployment_plan("plan-pack-a-paper-001") is None
            assert store.list_rebalances() == []
