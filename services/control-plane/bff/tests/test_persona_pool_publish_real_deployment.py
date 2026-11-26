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


def test_multi_symbol_pool_publishes_and_declares_every_symbol(real_world, monkeypatch, tmp_path):
    world = real_world
    client = route._client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5), world.member("artifact-b", 0.5)], 0)
    response = client.post(route.PATH, json={}, headers=route.H)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "published"
    plan = world.transport.real_plans.plan_store.get(world.transport.real_plan_calls[-1])
    assert len(plan.metadata["symbols"]) > 1 and "symbol" not in plan.metadata

# ---------------------------------------------------------------------------
# Hosted-shaped end to end: real publisher -> real Governance candidate check
# (real PaperRegistryReader over HTTP) -> real deployment planner.
# No fake approval and no fake Registry reader sit between them.
# ---------------------------------------------------------------------------
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.governance.paper_approval_scope import (
    PaperCandidateInvalid,
    PaperRegistryReader,
    verify_paper_candidate_for_decision,
)
from services.registry.paper_strategy_spec import build_strategy_spec


class GovernanceVerifyingTransport(RealDeploymentTransport):
    """Governance review/decide run the real candidate verification."""

    registry_reader: PaperRegistryReader | None = None
    verified: list[dict[str, Any]]
    refuse_verification = False

    def _apply_post(self, owner, path, body):
        decision = (self.objects.get(("governance", path.rsplit("/", 1)[0]))
                    if owner == "governance" and path.endswith(("/review", "/decide")) else None)
        if decision is not None and str(decision.get("target_id", "")).startswith("artifact-persona-pool-"):
            if self.refuse_verification:
                raise ValueError("HTTP 400: Bad Request")
            try:
                report = verify_paper_candidate_for_decision(decision, reader=self.registry_reader)
            except PaperCandidateInvalid as exc:
                raise ValueError(f"HTTP 400: {exc}") from exc
            self.verified.append(report)
        return super()._apply_post(owner, path, body)


def _registry_http(transport):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            registry_id = self.path.rsplit("/", 1)[-1]
            view = transport.objects.get(("registry", f"/api/registry/strategy-artifacts/{registry_id}"))
            self.send_response(200 if view else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(view or {}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _hosted_member(world, name: str = "a") -> None:
    """Make artifact-<name> a provisioned paper bundle: approved spec lineage."""
    entry = route._member_entry(name, "2330.TW")
    artifact = entry["metadata"]["strategy_artifact"]
    spec = build_strategy_spec()
    spec["strategy_id"] = artifact["strategy_id"]
    spec["metadata"] = {
        "tenant_id": route.TENANT, "persona_id": route.PERSONA,
        "capital_pool_id": world.ids.capital_pool_id, "execution_context": "paper",
        "capital_scale_pct": 0,
    }
    from services.governance import paper_approval_scope as scope

    spec_id = f"spec-{name}"
    artifact["lineage"] = {"source_strategy_spec_id": spec_id}
    artifact["binding_intent"] = {**artifact.get("binding_intent", {}), "persona_id": route.PERSONA} \
        if artifact.get("binding_intent") else artifact.get("binding_intent")
    if artifact["binding_intent"] is None:
        artifact.pop("binding_intent")
    entry["checksum"] = route.strategy_artifact_checksum(artifact)
    entry["lineage"] = {"source_strategy_spec_id": spec_id}
    entry["metadata"].update(
        execution_context="paper", capital_scale_pct=0, persona_id=route.PERSONA,
        capital_pool_id=world.ids.capital_pool_id,
    )
    spec_entry = {
        "registry_id": spec_id, "artifact_type": "strategy_spec", "strategy_id": spec["strategy_id"],
        "version": entry["version"], "artifact_state": "approved", "owner_tenant": route.TENANT,
        "checksum": scope._canonical_sha256(spec), "lineage": {},
        "metadata": {"execution_context": "paper", "capital_scale_pct": 0, "persona_id": route.PERSONA,
                     "capital_pool_id": world.ids.capital_pool_id, "strategy_spec": spec},
    }
    world.entries[f"artifact-{name}"] = entry
    for rid, body in ((f"artifact-{name}", entry), (spec_id, spec_entry)):
        world.transport.objects[("registry", f"/api/registry/strategy-artifacts/{rid}")] = {
            "entry": copy.deepcopy(body), "deployment_stage": "none",
        }


@pytest.fixture
def hosted_world(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(route._PERSONA_DIR)

    def make(runtime, **kw):
        transport = GovernanceVerifyingTransport(runtime, tmp_path / "plans.json", **kw)
        transport.verified = []
        return transport

    monkeypatch.setattr(route, "PublishOwnerTransport", make)
    world = route.World()
    # Hosted publishes approve as the dedicated dev paper principal, so the
    # real candidate verification takes the paper (not research-card) path.
    from services.governance.paper_approval_scope import DEV_PAPER_PROVISIONER_SUBJECT

    monkeypatch.setenv("GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    world.coordinator.governance_actor_id = DEV_PAPER_PROVISIONER_SUBJECT
    world.transport.governance_subject = DEV_PAPER_PROVISIONER_SUBJECT
    server = _registry_http(world.transport)
    world.transport.registry_reader = PaperRegistryReader(
        base_url=f"http://127.0.0.1:{server.server_port}", service_token="registry-read"
    )
    _hosted_member(world)
    yield world
    server.shutdown()
    server.server_close()


def test_one_member_pool_envelope_publishes_through_real_governance_and_deployment(
    hosted_world, monkeypatch, tmp_path
):
    world = hosted_world
    client = route._client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 1.0)], 0)
    response = client.post(route.PATH, json={}, headers=route.H)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "published"
    assert data["envelope_artifact_id"].startswith("artifact-persona-pool-")
    reports = world.transport.verified
    assert reports and all("source_strategy_spec_id" not in r for r in reports)
    assert reports[0]["pool_members"][0]["artifact_id"] == "artifact-a"
    assert world.transport.real_plan_calls, "real deployment planner must create the plan"
    assert route._live(world)[0]["artifact_id"] == data["envelope_artifact_id"]
    approvals = [p for (o, p) in world.transport.objects if o == "governance" and "apv-persona-pool-" in p]
    assert len(approvals) == 1


def test_retry_after_failed_review_reuses_the_proposed_approval(hosted_world, monkeypatch, tmp_path):
    """Hosted left apv-persona-pool-<x>-v2 in proposed; the retry reviews it, no duplicate."""
    world = hosted_world
    client = route._client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 1.0)], 0)
    world.transport.refuse_verification = True
    first = client.post(route.PATH, json={}, headers=route.H)
    assert first.status_code >= 400
    proposals = [(p, v) for (o, p), v in world.transport.objects.items()
                 if o == "governance" and "apv-persona-pool-" in p]
    assert len(proposals) == 1 and proposals[0][1]["decision_state"] == "proposed"
    world.transport.refuse_verification = False
    second = client.post(route.PATH, json={}, headers=route.H)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["status"] == "published"
    approvals = [p for (o, p) in world.transport.objects if o == "governance" and "apv-persona-pool-" in p]
    assert approvals == [proposals[0][0]]
    proposes = [c for c in world.transport.calls if c[0] == "POST" and c[2] == "/api/governance/approvals"
                and str((c[3] or {}).get("decision_id", "")).startswith("apv-persona-pool-")]
    assert len(proposes) == 1


def test_unapproved_member_is_refused_by_governance_before_deployment(hosted_world, monkeypatch, tmp_path):
    world = hosted_world
    client = route._client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 1.0)], 0)
    spec_key = ("registry", "/api/registry/strategy-artifacts/spec-a")
    world.transport.objects[spec_key]["entry"]["artifact_state"] = "candidate"
    response = client.post(route.PATH, json={}, headers=route.H)
    assert response.status_code >= 400
    assert "approved strategy_spec" in response.text or "ApprovalDecision" in response.text
    assert not [p for p in world.transport.real_plan_calls if "pool" in p]
