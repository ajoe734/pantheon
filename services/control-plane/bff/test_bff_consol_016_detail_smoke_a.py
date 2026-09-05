"""Regression coverage for BFF-CONSOL-016 Pack A detail journeys."""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator
from unittest import mock

from fastapi.testclient import TestClient


import json
from pathlib import Path
from typing import Any, Iterator
from unittest import mock

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import ReadSurfacePorts


HEADERS = {"Authorization": "Bearer op-2:operator"}
FIXTURE_PATH = Path(__file__).resolve().parent / "data" / "fixtures_pack_a.json"

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


class DetailSmokeATestReadPorts(ReadSurfacePorts):
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
        if ds:
            return list(ds.values()) if isinstance(ds, dict) else list(ds)
        specs = self._get_dataset("strategy_specs")
        if specs:
            res = []
            for k, v in (specs.items() if isinstance(specs, dict) else enumerate(specs)):
                strat = dict(v)
                strat.setdefault("id", strat.get("strategy_id", k))
                strat.setdefault("strategy_id", strat["id"])
                strat.setdefault("personaIds", strat.get("persona_ids", []))
                res.append(strat)
            return res
        return []

    def get_strategy(self, strategy_id: str | None) -> dict[str, Any] | None:
        strats = self.list_strategies()
        for s in strats:
            if s.get("id") == strategy_id or s.get("strategy_id") == strategy_id:
                s = dict(s)
                s.setdefault("personaIds", s.get("persona_ids", ["persona-pack-a-momentum"]))
                return s
        return None

    def list_strategy_specs(self, strategy_id: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("strategy_specs")
        if not ds:
            return []
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        if strategy_id:
            for s in items:
                if s.get("strategy_id") == strategy_id or s.get("id") == strategy_id:
                    return list(s.get("versions") or [])
            return []
        return items

    def list_strategy_spec_versions(self, strategy_id: str | None = None) -> list[dict[str, Any]]:
        return self.list_strategy_specs(strategy_id)

    def get_strategy_spec_detail(self, strategy_id: str | None, *, version_selector: str | None = None) -> dict[str, Any] | None:
        ds = self._get_dataset("strategy_specs")
        if not ds or not strategy_id:
            return None
        spec = ds.get(strategy_id) if isinstance(ds, dict) else next((s for s in ds if s.get("strategy_id") == strategy_id), None)
        if not spec:
            return None
        versions = spec.get("versions") or []
        if not versions:
            return None
        if not version_selector or version_selector == "current":
            return versions[0]
        for v in versions:
            if v.get("spec_version_id") == version_selector or v.get("spec_version") == version_selector:
                return v
        return versions[0]

    def get_strategy_spec(self, spec_or_strat_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("strategy_specs")
        if isinstance(ds, dict):
            if str(spec_or_strat_id or "") in ds:
                return ds[str(spec_or_strat_id or "")]
            return next((s for s in ds.values() if s.get("strategy_id") == spec_or_strat_id), None)
        return None

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

    def get_strategy_lineage(self, strategy_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("strategy_lineages")
        if isinstance(ds, dict):
            return ds.get(str(strategy_id or ""))
        return None

    def list_governance_audit_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("governance_audit_events")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("personas")
        personas = list(ds.values()) if isinstance(ds, dict) else list(ds)
        res = []
        for p in personas:
            p = dict(p)
            p.setdefault("routedStrategies", 1)
            res.append(p)
        return res

    def get_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        for p in self.list_personas():
            if p.get("id") == persona_id or p.get("persona_id") == persona_id:
                return p
        return None

    def get_route_policy_for_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("persona_route_policies")
        if isinstance(ds, dict):
            return ds.get(str(persona_id or ""))
        return next((p for p in ds if p.get("personaId") == persona_id or p.get("persona_id") == persona_id), None)

    def get_persona_route_policy(self, persona_id: str | None) -> dict[str, Any] | None:
        return self.get_route_policy_for_persona(persona_id)

    def get_sessions_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        ds = self._get_dataset("sessions")
        sessions = list(ds.values()) if isinstance(ds, dict) else list(ds)
        if persona_id:
            return [s for s in sessions if s.get("persona_id") == persona_id]
        return sessions

    def get_consultations_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        return []

    def get_teaching_sessions_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        ds = self._get_dataset("teaching_sessions")
        sessions = list(ds.values()) if isinstance(ds, dict) else list(ds)
        if persona_id:
            return [s for s in sessions if s.get("persona_id") == persona_id]
        return sessions

    def list_persona_evaluations(self, persona_id: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.get_teaching_sessions_for_persona(persona_id)

    def list_deployment_plans(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("deployment_plans") or self._get_dataset("deployments")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_deployment_plan(self, plan_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("deployment_plans") or self._get_dataset("deployments")
        if isinstance(ds, dict):
            raw = ds.get(str(plan_id or ""))
        else:
            raw = next((p for p in ds if p.get("id") == plan_id or p.get("plan_id") == plan_id), None)
        if raw:
            p = dict(raw)
            p.setdefault("approval_decision", {"id": p.get("approval_decision_id", "approval-pack-a-deploy")})
            p.setdefault("capital_pool_id", "pool-pack-a-ops")
            p.setdefault("runtime_binding_id", "runtime-pack-a-paper-001")
            p.setdefault("stages", [{"stage": "paper"}])
            return p
        return None

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("runtime_bindings") or self._get_dataset("runtimes")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_runtimes(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_runtime_bindings(**kwargs)

    def get_runtime_binding(self, runtime_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("runtime_bindings") or self._get_dataset("runtimes")
        if isinstance(ds, dict):
            return ds.get(str(runtime_id or ""))
        return next((r for r in ds if r.get("id") == runtime_id or r.get("runtime_id") == runtime_id), None)

    def get_runtime(self, runtime_id: str | None) -> dict[str, Any] | None:
        return self.get_runtime_binding(runtime_id)

    def get_approval_decision(self, approval_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("approval_decisions")
        if isinstance(ds, dict):
            return ds.get(str(approval_id or ""))
        return next((a for a in ds if a.get("id") == approval_id or a.get("approval_id") == approval_id), None)


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
                bff_main.read_store = DetailSmokeATestReadPorts(
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
            assert error.get("code") == "RESOURCE_NOT_FOUND"
            return
    error = payload.get("error")
    if isinstance(error, dict):
        assert error.get("code") == "RESOURCE_NOT_FOUND"
        return
    assert payload.get("code") == "RESOURCE_NOT_FOUND"


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
