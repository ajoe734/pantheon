"""Pool envelope publish against the REAL DeploymentPlannerService.

The route test next door fakes the Deployment owner.  Here the Deployment plan
POST runs the real ``DeploymentPlannerService.create_plan`` (real approval
verification, inline-artifact validation, minimum_closes derivation) and the
dispatch runs the real ``dispatch_to_runtime_manager`` adapter (inherited).
Registry/Governance/Capital owner state still comes from the provisioning
transport; only the Deployment owner is real here.
"""
from __future__ import annotations

import copy
from typing import Any

import pytest

from services.control_plane.bff.tests import test_persona_pool_publish_route as route
from services.deployment.service import (
    DeploymentPlanError,
    DeploymentPlanStore,
    DeploymentPlannerService,
)
from services.deployment.models import CreateDeploymentPlanRequest
from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid


class _TransportApprovalReader:
    """Reads the exact decision the Governance owner stored, then verifies it."""

    def __init__(self, transport) -> None:
        self.transport = transport

    def verify(self, decision_id, *, expected, now=None, usage_context=None):
        body = self.transport.objects.get(("governance", f"/api/governance/approvals/{decision_id}"))
        if body is None:
            raise ApprovalInvalid("Governance exact decision not found")
        evidence = ApprovalEvidence.model_validate(body)
        return evidence.require_valid(expected=expected, now=now, usage_context=usage_context)


class RealDeploymentTransport(route.PublishOwnerTransport):
    def __init__(self, runtime, plan_store_path, **kwargs: Any) -> None:
        super().__init__(runtime, **kwargs)
        self.real_plans = DeploymentPlannerService(
            plan_store=DeploymentPlanStore(str(plan_store_path)),
            registry_reader=self._owner_registry_entry,
            approval_reader=_TransportApprovalReader(self),
        )
        self.real_plan_calls: list[str] = []

    def _apply_post(self, owner, path, body):
        if owner == "deployment" and path == "/api/deployment/plans":
            request = CreateDeploymentPlanRequest(**body)
            try:
                real = self.real_plans.create_plan(
                    request, persist=True,
                    actor_id="pantheon-persona-provisioner", tenant_id=route.TENANT,
                )
            except DeploymentPlanError as exc:
                raise ValueError(f"HTTP 422: {exc}") from exc
            self.real_plan_calls.append(real.plan_id)
            response = super()._apply_post(owner, path, body)
            stored = self.objects[(owner, f"/api/deployment/plans/{body['plan_id']}")]
            # The real plan's metadata (symbol, market_data_policy, object_store)
            # is what the runtime sees; dispatch reads it from the stored plan.
            stored["metadata"] = {**stored["metadata"], **copy.deepcopy(real.metadata)}
            return response
        return super()._apply_post(owner, path, body)


@pytest.fixture
def real_world(monkeypatch, tmp_path) -> route.World:
    monkeypatch.syspath_prepend(route._PERSONA_DIR)
    monkeypatch.setattr(route, "PublishOwnerTransport",
                        lambda runtime, **kw: RealDeploymentTransport(runtime, tmp_path / "plans.json", **kw))
    return route.World()


def _same_symbol(world, name: str, lookback: int | None = None) -> None:
    entry = route._member_entry(name, "2330.TW")
    if lookback is not None:
        art = entry["metadata"]["strategy_artifact"]
        key = art["strategy_logic"]["lookback_parameter"]
        art["parameters"][key] = lookback
        for control in art["mutation_surface"]["controls"]:
            if control["parameter_key"] == key:
                control["current_value"] = lookback
        entry["checksum"] = route.strategy_artifact_checksum(art)
    world.entries[f"artifact-{name}"] = entry
    world.transport.objects[("registry", f"/api/registry/strategy-artifacts/artifact-{name}")] = {
        "entry": copy.deepcopy(entry), "deployment_stage": "none",
    }


def test_singleton_pool_publishes_through_real_deployment(real_world, monkeypatch, tmp_path):
    world = real_world
    client = route._client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 1.0)], 0)
    response = client.post(route.PATH, json={}, headers=route.H)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "published"
    assert world.transport.real_plan_calls, "production path must run the real create_plan"
    plan = world.transport.real_plans.plan_store.get(world.transport.real_plan_calls[-1])
    assert plan.metadata["symbol"] == "2330.TW"
    assert plan.metadata["market_data_policy"]["minimum_closes"] == 2
    assert route._live(world)[0]["artifact_id"] == data["envelope_artifact_id"]


def test_two_member_same_symbol_pool_publishes_with_member_maximum(real_world, monkeypatch, tmp_path):
    world = real_world
    _same_symbol(world, "a", 3)
    _same_symbol(world, "b", 9)
    client = route._client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5), world.member("artifact-b", 0.5)], 0)
    response = client.post(route.PATH, json={}, headers=route.H)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "published"
    plan = world.transport.real_plans.plan_store.get(world.transport.real_plan_calls[-1])
    assert plan.metadata["market_data_policy"]["minimum_closes"] == 9
    assert len(route._live(world)) == 1


def test_multi_symbol_pool_is_refused_with_named_reason_surfaced(real_world, monkeypatch, tmp_path):
    world = real_world
    client = route._client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5), world.member("artifact-b", 0.5)], 0)
    response = client.post(route.PATH, json={}, headers=route.H)
    assert response.status_code >= 400, response.text
    assert "multi_symbol_paper_plan_unsupported" in response.text
    assert world.runtime.calls == ["deploy"]  # previous envelope keeps running
