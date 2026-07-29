"""Cross-service proof for the EVOLOOP-006 promote and rollback pipeline."""
from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

from services.deployment.promote_pipeline import (
    PromotePipeline,
    PromotePipelineError,
    PromoteRequest,
    ServiceResponse,
)
from services.registry.service import app as registry_app
from services.registry.storage import reset_store


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SERVICE_DIR = REPO_ROOT / "services" / "runtime-manager"
FLEET_MODULE_PATH = (
    REPO_ROOT
    / "services"
    / "execution"
    / "runtime-manager"
    / "paper_fleet_reconciler.py"
)
REGISTRATION_PATH = (
    REPO_ROOT
    / "services"
    / "registry"
    / "strategy-artifacts"
    / "tw-session-momentum-v1.registration.json"
)


def _load_runtime_main(store_path: Path):
    service_dir = str(RUNTIME_SERVICE_DIR)
    if service_dir not in sys.path:
        sys.path.insert(0, service_dir)
    os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = str(store_path)
    os.environ["PANTHEON_SINGLE_RUNTIME_ENFORCED"] = "true"
    module_name = "evoloop_006_runtime_manager_main"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name,
        RUNTIME_SERVICE_DIR / "main.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    module._svc = None
    return module


def _load_fleet_module():
    module_name = "evoloop_006_paper_fleet_reconciler"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, FLEET_MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _canonical_authority_report(request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "passed",
        "authority": "canonical_deployment_registry_governance_capital",
        "plan_id": request["plan_id"],
        "plan_status": request["plan_status"],
        "target_stage": request["target_stage"],
        "artifact_id": request["artifact_id"],
        "artifact_version": request["artifact_version"],
        "strategy_id": request["strategy_id"],
        "approval_decision_id": request["approval_decision_id"],
        "sponsor_persona_id": request["sponsor_persona_id"],
        "capital_pool_id": request["capital_pool_id"],
        "persona_capital_binding_id": request["persona_capital_binding_id"],
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": request["allowed_deployment_scope"],
        "deployment_plan_sha256": "sha256:" + "0" * 64,
        "registry_entry_sha256": "sha256:" + "1" * 64,
        "approval_decision_sha256": "sha256:" + "2" * 64,
        "capital_pool_sha256": "sha256:" + "3" * 64,
        "capital_admissibility_sha256": "sha256:" + "4" * 64,
        "persona_capital_binding_sha256": "sha256:" + "5" * 64,
    }


def _seed_runtime(runtime_client) -> dict[str, Any]:
    response = runtime_client.post(
        "/api/runtimes/deploy",
        headers={"Authorization": "Bearer test-token:operator"},
        json={
            "plan_id": "plan-rescue-paper",
            "plan_status": "approved",
            "target_stage": "paper",
            "artifact_id": "artifact-rescue-placeholder",
            "artifact_version": "1.0.0",
            "approval_decision_id": "approval-rescue-paper",
            "sponsor_persona_id": "persona-tw-equity",
            "capital_pool_id": "pool-evoloop-paper",
            "persona_capital_binding_id": "binding-evoloop-paper",
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": "paper",
            "loader_checks_passed": True,
            "runtime_id": "runtime-evoloop-paper",
            "strategy_id": "tw_session_momentum",
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _register_artifact(registry_client: TestClient, binding: Mapping[str, Any]) -> str:
    registration = json.loads(REGISTRATION_PATH.read_text(encoding="utf-8"))
    artifact = copy.deepcopy(registration["strategy_artifact"])
    registry_id = "artifact-evoloop-promote-integration"
    artifact["artifact_id"] = registry_id
    intent = artifact["binding_intent"]
    intent.update(
        {
            "observed_runtime_binding_id": binding["binding_id"],
            "observed_runtime_id": binding["runtime_id"],
            "observed_deployment_plan_id": binding["plan_id"],
            "observed_capital_pool_id": binding["capital_pool_id"],
            "observed_placeholder_artifact_id": binding["artifact_id"],
            "observed_placeholder_artifact_version": binding["artifact_version"],
            "observed_binding_status": binding["status"],
            "observed_deployment_mode": binding["deployment_mode"],
            "observed_effective_at": binding["effective_at"],
            "replacement_task_id": "EVOLOOP-006",
            "persona_capital_binding_id": binding[
                "persona_capital_binding_id"
            ],
        }
    )
    response = registry_client.post(
        "/api/registry/strategy-artifacts",
        json={
            "registry_id": registry_id,
            "artifact_state": "candidate",
            "strategy_artifact": artifact,
        },
    )
    assert response.status_code == 200, response.text
    return registry_id


class _ApiTransport:
    """Route pipeline calls through real service test clients."""

    def __init__(
        self,
        *,
        registry_client: TestClient,
        deployment_client: TestClient,
        runtime_client: Any,
        decision: Mapping[str, Any],
    ) -> None:
        self.registry_client = registry_client
        self.deployment_client = deployment_client
        self.runtime_client = runtime_client
        self.decision = dict(decision)
        self.configured_services = {
            "registry",
            "governance",
            "deployment",
            "runtime_manager",
            "fleet",
        }

    def is_configured(self, service: str) -> bool:
        return service in self.configured_services

    def request(
        self,
        service: str,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> ServiceResponse:
        if service == "governance":
            expected = f"/api/governance/approvals/{self.decision['decision_id']}"
            if method != "GET" or path != expected:
                return ServiceResponse(404, {"detail": "unknown governance route"})
            return ServiceResponse(200, dict(self.decision))

        if service == "fleet":
            if method != "GET" or path != "/api/fleet/state":
                return ServiceResponse(404, {"detail": "unknown fleet route"})
            active = self.runtime_client.get(
                "/api/runtimes/pool-evoloop-paper/active",
                headers={"Authorization": "Bearer test-token:operator"},
            )
            workers = []
            if active.status_code == 200:
                binding = active.get_json()
                workers.append(
                    {
                        "binding_id": binding["binding_id"],
                        "runtime_id": binding["runtime_id"],
                        "status": "running",
                    }
                )
            return ServiceResponse(
                200,
                {
                    "worker_count": len(workers),
                    "running_count": len(workers),
                    "workers": workers,
                },
            )

        if service == "runtime_manager":
            response = self.runtime_client.open(
                path,
                method=method,
                json=dict(json) if json is not None else None,
                query_string=dict(params) if params is not None else None,
                headers={"Authorization": "Bearer test-token:operator"},
            )
            return ServiceResponse(response.status_code, response.get_json())

        client = {
            "registry": self.registry_client,
            "deployment": self.deployment_client,
        }[service]
        response = client.request(
            method,
            path,
            json=dict(json) if json is not None else None,
            params=dict(params) if params is not None else None,
        )
        return ServiceResponse(response.status_code, response.json())


class _RaiseAfterCallTransport:
    """Inject one lost-response failure after a real service mutation."""

    def __init__(
        self,
        delegate: _ApiTransport,
        *,
        service: str,
        method: str,
        path_suffix: str,
    ) -> None:
        self.delegate = delegate
        self.service = service
        self.method = method
        self.path_suffix = path_suffix
        self.raised = False

    def is_configured(self, service: str) -> bool:
        return self.delegate.is_configured(service)

    def request(self, service: str, method: str, path: str, **kwargs):
        response = self.delegate.request(service, method, path, **kwargs)
        if (
            not self.raised
            and service == self.service
            and method == self.method
            and path.endswith(self.path_suffix)
        ):
            self.raised = True
            raise RuntimeError("injected lost service response")
        return response


@pytest.fixture()
def api_stack():
    tempdir = tempfile.TemporaryDirectory(prefix="evoloop_006_pipeline_")
    root = Path(tempdir.name)
    governance_dir = root / "governance"
    governance_dir.mkdir(parents=True)
    runtime_store = root / "runtime_bindings.json"
    registry_snapshot = root / "registry_snapshot.json"
    env_names = (
        "CAPITAL_DATA_DIR",
        "DEPLOYMENT_DATA_DIR",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "PANTHEON_RUNTIME_BINDING_STORE_PATH",
        "PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH",
        "PANTHEON_SINGLE_RUNTIME_ENFORCED",
    )
    old_env = {name: os.environ.get(name) for name in env_names}
    os.environ.update(
        {
            "CAPITAL_DATA_DIR": str(governance_dir),
            "DEPLOYMENT_DATA_DIR": str(governance_dir),
            "PANTHEON_GOVERNANCE_DATA_DIR": str(governance_dir),
            "PANTHEON_RUNTIME_BINDING_STORE_PATH": str(runtime_store),
            "PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH": str(
                registry_snapshot
            ),
            "PANTHEON_SINGLE_RUNTIME_ENFORCED": "true",
        }
    )

    reset_store()
    registry_client = TestClient(registry_app)
    runtime_main = _load_runtime_main(runtime_store)
    runtime_main.verify_deploy_authorities = (
        lambda request, **_kwargs: _canonical_authority_report(request)
    )
    runtime_client = runtime_main.app.test_client()
    old_binding = _seed_runtime(runtime_client)
    registry_id = _register_artifact(registry_client, old_binding)

    sys.modules.pop("services.deployment.service", None)
    deployment_service = importlib.import_module("services.deployment.service")
    deployment_service = importlib.reload(deployment_service)
    deployment_client = TestClient(
        deployment_service.app,
        headers={
            "Authorization": "Bearer promote-pipeline:operator,service",
            "X-Tenant-Id": "tenant-evoloop-006",
        },
    )
    decision = {
        "decision_id": "approval-evoloop-006-integration",
        "target_type": "registry_entry",
        "target_id": registry_id,
        "target_version": "1.0.0",
        "decision_state": "decided",
        "decision": "approved",
        "risk_level": "low",
        "tenant_id": "tenant-evoloop-006",
        "capital_pool_id": old_binding["capital_pool_id"],
        "persona_id": "persona-tw-equity",
    }
    transport = _ApiTransport(
        registry_client=registry_client,
        deployment_client=deployment_client,
        runtime_client=runtime_client,
        decision=decision,
    )

    try:
        yield {
            "root": root,
            "transport": transport,
            "registry": registry_client,
            "deployment": deployment_client,
            "runtime": runtime_client,
            "old_binding": old_binding,
            "registry_id": registry_id,
            "decision": decision,
        }
    finally:
        reset_store()
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        tempdir.cleanup()


def _request(stack: Mapping[str, Any], *, plan_id: str) -> PromoteRequest:
    return PromoteRequest(
        registry_id=stack["registry_id"],
        approval_decision_id=stack["decision"]["decision_id"],
        current_binding_id=stack["old_binding"]["binding_id"],
        plan_id=plan_id,
        expected_runtime_id=stack["old_binding"]["runtime_id"],
        source_task_id="EVOLOOP-006",
        created_by="Codex2",
        tenant_id="tenant-evoloop-006",
    )


def test_promote_and_rollback_through_real_service_apis(api_stack):
    pipeline = PromotePipeline(
        api_stack["transport"],
        fleet_enabled=True,
        fleet_poll_timeout_seconds=0,
        fleet_poll_interval_seconds=0,
    )

    promote_receipt = pipeline.promote(
        _request(api_stack, plan_id="plan-evoloop-006-integration")
    )

    assert promote_receipt["status"] == "promoted"
    assert promote_receipt["invariants"]["no_store_edits"] is True
    assert promote_receipt["fleet"]["converged"] is True
    promoted = promote_receipt["post"]
    assert promoted["binding_id"] != api_stack["old_binding"]["binding_id"]
    assert promoted["artifact_id"] == api_stack["registry_id"]
    assert promoted["runtime_id"] == api_stack["old_binding"]["runtime_id"]
    assert "rollback_parent" not in promoted
    assert "rollback_action_type" not in promoted

    old_readback = api_stack["runtime"].get(
        f"/api/runtime-bindings/{api_stack['old_binding']['binding_id']}",
        headers={"Authorization": "Bearer test-token:operator"},
    ).get_json()
    assert old_readback["status"] == "retired"
    plan = api_stack["deployment"].get(
        "/api/deployment/plans/plan-evoloop-006-integration"
    ).json()
    assert plan["transition_type"] == "replace"
    assert plan["runtime_action"] == "replace_binding"
    assert plan["status"] == "executed"
    registry = api_stack["registry"].get(
        f"/api/registry/strategy-artifacts/{api_stack['registry_id']}"
    ).json()["entry"]
    assert registry["artifact_state"] == "approved"
    assert registry["approval_decision_id"] == api_stack["decision"]["decision_id"]
    assert registry["deployment_summary"]["current_stage"] == "paper"
    assert registry["deployment_summary"]["runtime_binding_id"] == promoted[
        "binding_id"
    ]

    active = api_stack["runtime"].get(
        "/api/runtimes/pool-evoloop-paper/active",
        headers={"Authorization": "Bearer test-token:operator"},
    ).get_json()
    fleet_module = _load_fleet_module()
    reconciler = fleet_module.PaperFleetReconciler(
        runtime_manager_url="http://runtime-manager.test",
        performance_state_root=str(api_stack["root"] / "performance"),
    )
    worker_env = reconciler._build_worker_env(active)
    assert worker_env["PANTHEON_RUNTIME_ID"] == promoted["runtime_id"]
    assert worker_env["PANTHEON_RUNTIME_BINDING_ID"] == promoted["binding_id"]
    assert worker_env["PANTHEON_ARTIFACT_ID"] == api_stack["registry_id"]

    rollback_receipt = pipeline.rollback(promote_receipt)

    assert rollback_receipt["status"] == "rolled_back"
    assert rollback_receipt["fleet"]["converged"] is True
    restored = rollback_receipt["post"]
    assert restored["artifact_id"] == api_stack["old_binding"]["artifact_id"]
    assert restored["artifact_version"] == api_stack["old_binding"][
        "artifact_version"
    ]
    assert restored["runtime_id"] == api_stack["old_binding"]["runtime_id"]
    assert restored["rollback_parent"] == promoted["binding_id"]
    assert restored["rollback_action_type"] == "replace"
    promoted_readback = api_stack["runtime"].get(
        f"/api/runtime-bindings/{promoted['binding_id']}",
        headers={"Authorization": "Bearer test-token:operator"},
    ).get_json()
    assert promoted_readback["status"] == "retired"
    rolled_back_registry = api_stack["registry"].get(
        f"/api/registry/strategy-artifacts/{api_stack['registry_id']}"
    ).json()["entry"]
    assert rolled_back_registry["deployment_summary"]["current_stage"] == "none"
    assert rolled_back_registry["deployment_summary"].get(
        "runtime_binding_id"
    ) is None

    final_request = PromoteRequest(
        registry_id=api_stack["registry_id"],
        approval_decision_id=api_stack["decision"]["decision_id"],
        current_binding_id=restored["binding_id"],
        plan_id="plan-evoloop-006-integration-final",
        expected_runtime_id=restored["runtime_id"],
        source_task_id="EVOLOOP-006",
        created_by="Codex2",
        tenant_id="tenant-evoloop-006",
    )
    final_receipt = pipeline.promote(final_request)
    assert final_receipt["status"] == "promoted"
    assert final_receipt["post"]["artifact_id"] == api_stack["registry_id"]
    assert final_receipt["post"]["runtime_id"] == restored["runtime_id"]
    assert final_receipt["post"]["binding_id"] != restored["binding_id"]
    json.dumps(promote_receipt, allow_nan=False)
    json.dumps(rollback_receipt, allow_nan=False)
    json.dumps(final_receipt, allow_nan=False)


def test_runtime_identity_mismatch_fails_before_runtime_replace(api_stack):
    pipeline = PromotePipeline(
        api_stack["transport"],
        fleet_enabled=True,
        fleet_poll_timeout_seconds=0,
        fleet_poll_interval_seconds=0,
    )
    request = _request(api_stack, plan_id="plan-evoloop-006-runtime-mismatch")
    request = PromoteRequest(
        **{
            **request.__dict__,
            "expected_runtime_id": "runtime-wrong",
        }
    )

    with pytest.raises(PromotePipelineError, match="runtime_id"):
        pipeline.promote(request)

    active = api_stack["runtime"].get(
        "/api/runtimes/pool-evoloop-paper/active",
        headers={"Authorization": "Bearer test-token:operator"},
    ).get_json()
    assert active["binding_id"] == api_stack["old_binding"]["binding_id"]
    assert active["status"] == "active"
    bindings = api_stack["runtime"].get(
        "/api/runtime-bindings",
        headers={"Authorization": "Bearer test-token:operator"},
    ).get_json()
    assert bindings["count"] == 1


def test_approved_registry_without_decision_link_fails_closed(api_stack):
    approved = api_stack["registry"].post(
        f"/api/registry/strategy-artifacts/{api_stack['registry_id']}/advance",
        json={"target_state": "approved", "approver": "legacy-reviewer"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["entry"].get("approval_decision_id") is None
    pipeline = PromotePipeline(
        api_stack["transport"],
        fleet_enabled=True,
        fleet_poll_timeout_seconds=0,
        fleet_poll_interval_seconds=0,
    )

    with pytest.raises(PromotePipelineError, match="approval_decision_id"):
        pipeline.promote(_request(api_stack, plan_id="plan-missing-decision-link"))

    active = api_stack["runtime"].get(
        "/api/runtimes/pool-evoloop-paper/active",
        headers={"Authorization": "Bearer test-token:operator"},
    ).get_json()
    assert active["binding_id"] == api_stack["old_binding"]["binding_id"]
    assert api_stack["deployment"].get(
        "/api/deployment/plans/plan-missing-decision-link"
    ).status_code == 404


def test_partial_promotion_replay_never_synthesizes_success(api_stack):
    failing_transport = _RaiseAfterCallTransport(
        api_stack["transport"],
        service="deployment",
        method="POST",
        path_suffix="/binding-created",
    )
    request = _request(api_stack, plan_id="plan-partial-promotion")
    pipeline = PromotePipeline(
        failing_transport,
        fleet_enabled=True,
        fleet_poll_timeout_seconds=0,
        fleet_poll_interval_seconds=0,
    )
    with pytest.raises(PromotePipelineError, match="lost service response"):
        pipeline.promote(request)

    retry = PromotePipeline(
        api_stack["transport"],
        fleet_enabled=True,
        fleet_poll_timeout_seconds=0,
        fleet_poll_interval_seconds=0,
    )
    with pytest.raises(PromotePipelineError, match="completed DeploymentPlan.status"):
        retry.promote(request)

    plan = api_stack["deployment"].get(
        "/api/deployment/plans/plan-partial-promotion"
    ).json()
    assert plan["status"] == "executing"
    registry = api_stack["registry"].get(
        f"/api/registry/strategy-artifacts/{api_stack['registry_id']}"
    ).json()["entry"]
    assert registry.get("deployment_summary") in (None, {})


def test_rollback_lost_projection_response_replays_from_authoritative_state(api_stack):
    pipeline = PromotePipeline(
        api_stack["transport"],
        fleet_enabled=True,
        fleet_poll_timeout_seconds=0,
        fleet_poll_interval_seconds=0,
    )
    receipt = pipeline.promote(
        _request(api_stack, plan_id="plan-rollback-response-loss")
    )
    failing_transport = _RaiseAfterCallTransport(
        api_stack["transport"],
        service="registry",
        method="PUT",
        path_suffix="/deployment-summary",
    )
    first_rollback = PromotePipeline(
        failing_transport,
        fleet_enabled=True,
        fleet_poll_timeout_seconds=0,
        fleet_poll_interval_seconds=0,
    )
    with pytest.raises(PromotePipelineError, match="lost service response"):
        first_rollback.rollback(receipt)

    replay = pipeline.rollback(receipt)
    assert replay["status"] == "already_rolled_back"
    assert replay["replayed"] is True
    assert replay["post"]["artifact_id"] == api_stack["old_binding"]["artifact_id"]


def test_rollback_rejects_tampered_pre_artifact(api_stack):
    pipeline = PromotePipeline(
        api_stack["transport"],
        fleet_enabled=True,
        fleet_poll_timeout_seconds=0,
        fleet_poll_interval_seconds=0,
    )
    receipt = pipeline.promote(_request(api_stack, plan_id="plan-tamper-proof"))
    tampered = copy.deepcopy(receipt)
    tampered["pre"]["artifact_id"] = "artifact-attacker-selected"

    with pytest.raises(PromotePipelineError, match="artifact_id mismatch"):
        pipeline.rollback(tampered)
