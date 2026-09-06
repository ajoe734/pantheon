from __future__ import annotations

import copy
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from types import SimpleNamespace

import pytest
from services.governance.test_approval_authority import approval_snapshot, SnapshotApprovalReader

from services.registry.strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS,
    load_strategy_artifact_registration,
    strategy_artifact_checksum,
)


MODULE_PATH = Path(__file__).with_name("deploy_authority.py")
SPEC = importlib.util.spec_from_file_location("loop_prod_dep_deploy_authority", MODULE_PATH)
assert SPEC and SPEC.loader
authority = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(authority)


def _facts():
    registration = load_strategy_artifact_registration(
        BUILTIN_STRATEGY_ARTIFACT_PATHS[0]
    )
    artifact = registration["strategy_artifact"]
    artifact_id = artifact["artifact_id"]
    approval_id = "approval-loop-prod-dep-001"
    request = {
        "plan_id": "plan-loop-prod-dep-001",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": artifact_id,
        "artifact_version": artifact["version"],
        "strategy_id": artifact["strategy_id"],
        "approval_decision_id": approval_id,
        "capital_pool_id": "pool-loop-prod-dep-001",
        "sponsor_persona_id": "persona-loop-prod-dep-001",
        "persona_capital_binding_id": "pcb-loop-prod-dep-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "paper",
    }
    plan = {
        "plan_id": request["plan_id"],
        "status": request["plan_status"],
        "current_stage": "none",
        "target_stage": request["target_stage"],
        "artifact_id": request["artifact_id"],
        "artifact_version": request["artifact_version"],
        "strategy_id": request["strategy_id"],
        "approval_decision_id": approval_id,
        "capital_pool_id": request["capital_pool_id"],
        "sponsor_persona_id": request["sponsor_persona_id"],
    }
    registry = {
        "entry": {
            "registry_id": artifact_id,
            "artifact_type": "execution_bundle",
            "strategy_id": artifact["strategy_id"],
            "version": artifact["version"],
            "artifact_state": "approved",
            "checksum": strategy_artifact_checksum(artifact),
            "approval_decision_id": approval_id,
            "metadata": {"strategy_artifact": artifact},
        },
        "deployment_stage": "none",
    }
    approval = {
        "decision_id": approval_id,
        "decision_state": "decided",
        "decision": "approved",
        "target_type": "registry_entry",
        "target_id": artifact_id,
        "target_version": artifact["version"],
        "capital_pool_id": "pool-loop-prod-dep-001",
        "persona_id": "persona-loop-prod-dep-001",
        "actor_id": "governance-reviewer",
        "conditions": [],
        "expires_at": "2026-07-15T00:00:00Z",
        "revoked_at": None,
    }
    plan["metadata"] = {"tenant_id": "tenant-unit"}
    approval = approval_snapshot(candidate_digest=registry["entry"]["checksum"], **approval)
    capital_pool = {
        "pool_id": request["capital_pool_id"],
        "status": "active",
        "single_runtime_enforced": True,
    }
    persona_binding = {
        "binding_id": request["persona_capital_binding_id"],
        "persona_id": request["sponsor_persona_id"],
        "capital_pool_id": request["capital_pool_id"],
        "status": "active",
        "allowed_deployment_scope": "paper",
        "effective_from": "2026-07-01T00:00:00Z",
        "effective_to": "2026-08-01T00:00:00Z",
    }
    return request, registry, approval, plan, capital_pool, persona_binding


def _admissibility(capital_pool, persona_binding):
    return {
        "persona_id": persona_binding["persona_id"],
        "capital_pool_id": persona_binding["capital_pool_id"],
        "target_stage": "paper",
        "permitted": True,
        "pool_status": capital_pool["status"],
        "single_runtime_enforced": capital_pool["single_runtime_enforced"],
        "binding_id": persona_binding["binding_id"],
        "binding_status": persona_binding["status"],
        "allowed_deployment_scope": persona_binding["allowed_deployment_scope"],
    }


def _fetcher(
    registry,
    approval,
    plan,
    capital_pool,
    persona_binding,
    *,
    capital_admissibility=None,
):
    admissibility = capital_admissibility or _admissibility(
        capital_pool, persona_binding
    )

    def fetch(url: str, _timeout: float):
        if "/api/deployment/" in url:
            return plan
        if "/api/registry/" in url:
            return registry
        if "/api/governance/" in url:
            return approval
        if "/api/capital-pools/" in url:
            return capital_pool
        if "/api/bindings/admissibility" in url:
            return admissibility
        if "/api/bindings/" in url:
            return persona_binding
        raise AssertionError(url)

    return fetch


def _verify(
    request,
    registry,
    approval,
    plan,
    capital_pool,
    persona_binding,
    *,
    capital_admissibility=None,
):
    return authority.verify_deploy_authorities(
        request,
        deployment_base_url="http://deployment:8095",
        registry_base_url="http://registry:8087",
        governance_base_url="http://governance:8082",
        capital_base_url="http://capital:8092",
        approval_reader=SnapshotApprovalReader(approval),
        registry_fetch_json=lambda url, timeout: registry,
        fetch_json=_fetcher(
            registry,
            approval,
            plan,
            capital_pool,
            persona_binding,
            capital_admissibility=capital_admissibility,
        ),
        now=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
    )


def test_exact_authoritative_registry_and_approval_are_admitted():
    request, registry, approval, plan, capital_pool, persona_binding = _facts()

    report = _verify(request, registry, approval, plan, capital_pool, persona_binding)

    assert report["status"] == "passed"
    assert report["authority"] == "canonical_deployment_registry_governance_capital"
    assert report["artifact_id"] == request["artifact_id"]
    assert report["approval_decision_id"] == request["approval_decision_id"]
    assert report["registry_entry_sha256"].startswith("sha256:")
    assert report["approval_decision_sha256"].startswith("sha256:")


def test_plan_authority_digest_ignores_only_runtime_projection_fields():
    _, _, _, plan, _, _ = _facts()
    plan.update(
        {
            "current_stage": "none",
            "binding_id": None,
            "metadata": {"source_task_id": "LOOP-PROD-DEP-001"},
            "rollback": {
                "target_artifact_id": "artifact-fallback",
                "target_version": "0.9.0",
                "action_type": "replace",
            },
        }
    )
    advanced = copy.deepcopy(plan)
    advanced.update(
        {
            "status": "executing",
            "binding_id": "rb-response-loss",
        }
    )
    advanced["metadata"]["runtime_lifecycle"] = {
        "binding_id": "rb-response-loss",
        "runtime_id": "rt-response-loss",
    }

    assert authority._canonical_digest(plan) != authority._canonical_digest(advanced)
    assert authority._canonical_digest(
        authority._deployment_plan_authority_view(plan)
    ) == authority._canonical_digest(
        authority._deployment_plan_authority_view(advanced)
    )

    advanced["current_stage"] = "paper"
    assert authority._canonical_digest(
        authority._deployment_plan_authority_view(plan)
    ) != authority._canonical_digest(
        authority._deployment_plan_authority_view(advanced)
    )
    advanced["current_stage"] = "none"

    advanced["rollback"]["target_version"] = "0.8.0"
    assert authority._canonical_digest(
        authority._deployment_plan_authority_view(plan)
    ) != authority._canonical_digest(
        authority._deployment_plan_authority_view(advanced)
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("current_stage", None, "current_stage"),
        ("metadata", [], "metadata must be an object"),
        (
            "metadata",
            {"runtime_lifecycle": []},
            "runtime_lifecycle must be an object",
        ),
    ],
)
def test_plan_runtime_projection_shape_is_fail_closed(field, value, message):
    request, registry, approval, plan, capital_pool, persona_binding = _facts()
    plan[field] = value

    with pytest.raises(authority.DeployAuthorityError, match=message):
        _verify(
            request,
            registry,
            approval,
            plan,
            capital_pool,
            persona_binding,
        )


@pytest.mark.parametrize("target_stage", ["canary", "live"])
def test_new_nonpaper_binding_is_rejected_before_any_authority_read(target_stage):
    request, *_ = _facts()
    request["target_stage"] = target_stage

    with pytest.raises(authority.DeployAuthorityError, match="paper only"):
        authority.verify_deploy_authorities(
            request,
            deployment_base_url="http://deployment:8095",
            registry_base_url="http://registry:8087",
            governance_base_url="http://governance:8082",
            capital_base_url="http://capital:8092",
            fetch_json=lambda *_: pytest.fail("authority read must not run"),
        )


def test_candidate_or_unapproved_registry_entry_is_rejected():
    request, registry, approval, plan, capital_pool, persona_binding = _facts()
    registry["entry"]["artifact_state"] = "candidate"

    with pytest.raises(authority.DeployAuthorityError, match="artifact_state"):
        _verify(request, registry, approval, plan, capital_pool, persona_binding)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("plan_id", "other-plan"),
        ("status", "executing"),
        ("artifact_version", "9.9.9"),
        ("capital_pool_id", "other-pool"),
    ],
)
def test_deployment_plan_readback_must_match_the_requested_target(field, value):
    request, registry, approval, plan, capital_pool, persona_binding = _facts()
    plan[field] = value

    with pytest.raises(
        authority.DeployAuthorityError, match="deployment authority mismatch"
    ):
        _verify(request, registry, approval, plan, capital_pool, persona_binding)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_id", "other-artifact"),
        ("version", "9.9.9"),
        ("strategy_id", "other-strategy"),
    ],
)
def test_embedded_strategy_artifact_identity_must_match_registry_target(field, value):
    request, registry, approval, plan, capital_pool, persona_binding = _facts()
    registry["entry"]["metadata"]["strategy_artifact"][field] = value

    with pytest.raises(
        authority.DeployAuthorityError,
        match="embedded StrategyArtifact identity mismatch",
    ):
        _verify(request, registry, approval, plan, capital_pool, persona_binding)


def test_forged_loader_boolean_cannot_hide_artifact_checksum_mismatch():
    request, registry, approval, plan, capital_pool, persona_binding = _facts()
    request["loader_checks_passed"] = True
    registry["entry"]["checksum"] = "sha256:" + "0" * 64

    with pytest.raises(authority.DeployAuthorityError, match="checksum mismatch"):
        _verify(request, registry, approval, plan, capital_pool, persona_binding)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capital_pool_id", "other-pool"),
        ("persona_id", "other-persona"),
        ("target_version", "9.9.9"),
        ("decision_state", "under_review"),
        ("decision", "rejected"),
    ],
)
def test_governance_proof_must_be_exactly_target_bound(field, value):
    request, registry, approval, plan, capital_pool, persona_binding = _facts()
    approval[field] = value

    with pytest.raises(authority.DeployAuthorityError, match="governance authority mismatch"):
        _verify(request, registry, approval, plan, capital_pool, persona_binding)


def test_expired_or_revoked_approval_is_rejected():
    request, registry, approval, plan, capital_pool, persona_binding = _facts()
    expired = copy.deepcopy(approval)
    expired["expires_at"] = "2026-07-14T11:59:59Z"
    with pytest.raises(authority.DeployAuthorityError, match="expired"):
        _verify(request, registry, expired, plan, capital_pool, persona_binding)

    revoked = copy.deepcopy(approval)
    revoked["revoked_at"] = "2026-07-14T11:00:00Z"
    with pytest.raises(authority.DeployAuthorityError, match="revoked"):
        _verify(request, registry, revoked, plan, capital_pool, persona_binding)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("binding_id", "other-binding"),
        ("permitted", False),
        ("binding_status", "paused"),
        ("allowed_deployment_scope", "none"),
    ],
)
def test_capital_admissibility_must_match_the_exact_persona_binding(field, value):
    request, registry, approval, plan, capital_pool, persona_binding = _facts()
    capital_admissibility = _admissibility(capital_pool, persona_binding)
    capital_admissibility[field] = value

    with pytest.raises(
        authority.DeployAuthorityError,
        match="capital admissibility authority mismatch",
    ):
        _verify(
            request,
            registry,
            approval,
            plan,
            capital_pool,
            persona_binding,
            capital_admissibility=capital_admissibility,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("binding_id", "other-binding"),
        ("persona_id", "other-persona"),
        ("capital_pool_id", "other-pool"),
        ("status", "paused"),
        ("allowed_deployment_scope", "none"),
    ],
)
def test_persona_capital_binding_readback_must_match_exactly(field, value):
    request, registry, approval, plan, capital_pool, persona_binding = _facts()
    capital_admissibility = _admissibility(capital_pool, persona_binding)
    persona_binding[field] = value

    with pytest.raises(
        authority.DeployAuthorityError, match="capital binding authority mismatch"
    ):
        _verify(
            request,
            registry,
            approval,
            plan,
            capital_pool,
            persona_binding,
            capital_admissibility=capital_admissibility,
        )


@pytest.mark.parametrize(
    ("status_code", "expected_type"),
    [
        (408, authority.DeployAuthorityUnavailableError),
        (425, authority.DeployAuthorityUnavailableError),
        (404, authority.DeployAuthorityError),
    ],
)
def test_http_authority_failures_distinguish_retryable_from_rejected(
    monkeypatch, status_code, expected_type
):
    def fail_read(request, *, timeout):
        raise authority.HTTPError(
            request.full_url,
            status_code,
            "test authority response",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(authority, "build_opener", lambda *_: SimpleNamespace(open=fail_read))

    with pytest.raises(expected_type) as raised:
        authority._fetch_json("http://authority.test/resource", 1.0)

    assert type(raised.value) is expected_type


def test_deployment_authority_read_sends_service_token_and_tenant(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"plan_id":"plan-authenticated"}'

    def read(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN", "runtime:service")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_TENANT_ID", "tenant-runtime")
    monkeypatch.setattr(authority, "build_opener", lambda *_: SimpleNamespace(open=read))

    headers = authority._deployment_request_headers()
    result = authority._fetch_json(
        "http://deployment:8095/api/deployment/plans/plan-authenticated",
        2.0,
        headers=headers,
    )

    assert result == {"plan_id": "plan-authenticated"}
    assert captured["timeout"] == 2.0
    assert captured["request"].get_header("Authorization") == "Bearer runtime:service"
    assert captured["request"].get_header("X-tenant-id") == "tenant-runtime"


def test_deployment_authority_auth_configuration_fails_closed_when_partial(monkeypatch):
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN", "runtime:service")
    monkeypatch.delenv("PANTHEON_DEPLOYMENT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("PANTHEON_DEPLOYMENT_TENANT_ID", raising=False)

    with pytest.raises(
        authority.DeployAuthorityUnavailableError,
        match="token and tenant must be configured together",
    ):
        authority._deployment_request_headers()


def test_missing_authority_urls_fail_closed():
    request, *_ = _facts()

    with pytest.raises(authority.DeployAuthorityError, match="URLs are required"):
        authority.verify_deploy_authorities(
            request,
            deployment_base_url="",
            registry_base_url="",
            governance_base_url="",
            capital_base_url="",
        )
