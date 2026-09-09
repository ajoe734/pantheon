"""Runtime authority binds a paper-scoped approval to the actual plan stage/scale."""
from __future__ import annotations

import copy
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.governance.paper_approval_scope import DEV_PAPER_AUTHORIZATION_SCOPE
from services.governance.test_approval_authority import SnapshotApprovalReader

_SPEC = importlib.util.spec_from_file_location(
    "paper_scope_deploy_authority_helpers", Path(__file__).with_name("test_deploy_authority.py")
)
assert _SPEC and _SPEC.loader
helpers = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(helpers)
authority = helpers.authority


def _scoped_facts(*, capital_scale_pct=0.0, with_scale=True):
    request, registry, approval, plan, capital_pool, persona_binding = helpers._facts()
    approval = dict(approval, authorization_scope=copy.deepcopy(DEV_PAPER_AUTHORIZATION_SCOPE))
    plan = dict(plan)
    if with_scale:
        plan["scale"] = {"capital_scale_pct": capital_scale_pct, "gross_scale_pct": 100.0, "ramp_schedule": []}
    return request, registry, approval, plan, capital_pool, persona_binding


def _verify(request, registry, approval, plan, capital_pool, persona_binding, **overrides):
    kwargs = dict(
        deployment_base_url="http://deployment:8095", registry_base_url="http://registry:8087",
        governance_base_url="http://governance:8082", capital_base_url="http://capital:8092",
        approval_reader=SnapshotApprovalReader(approval),
        registry_fetch_json=lambda url, timeout: registry,
        fetch_json=helpers._fetcher(registry, approval, plan, capital_pool, persona_binding),
        now=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
    )
    kwargs.update(overrides)
    return authority.verify_deploy_authorities(request, **kwargs)


def test_scoped_paper_zero_capital_plan_is_admitted_in_dev(monkeypatch):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    report = _verify(*_scoped_facts())
    assert report["status"] == "passed"
    assert report["approval_authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE
    assert report["deployment_plan_scale"]["capital_scale_pct"] == 0.0


@pytest.mark.parametrize("capital_scale_pct", [0.01, 5.0, 100.0])
def test_scoped_approval_denies_nonzero_capital_scale(monkeypatch, capital_scale_pct):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    with pytest.raises(authority.DeployAuthorityError, match="authorization_scope"):
        _verify(*_scoped_facts(capital_scale_pct=capital_scale_pct))


def test_scoped_approval_requires_persisted_plan_scale(monkeypatch):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    with pytest.raises(authority.DeployAuthorityError, match="scale.capital_scale_pct is required"):
        _verify(*_scoped_facts(with_scale=False))


@pytest.mark.parametrize("environment", [None, "", "prod", "sandbox"])
def test_scoped_approval_denies_non_dev_environment(monkeypatch, environment):
    if environment is None:
        monkeypatch.delenv("PANTHEON_ENV", raising=False)
    else:
        monkeypatch.setenv("PANTHEON_ENV", environment)
    with pytest.raises(authority.DeployAuthorityError, match="authorization_scope"):
        _verify(*_scoped_facts())


@pytest.mark.parametrize("target_stage", ["canary", "live"])
def test_scoped_approval_never_admits_canary_or_live_promotion(monkeypatch, target_stage):
    """The promotion verifier reuses this path with allowed_target_stages=(target,)."""
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    request, registry, approval, plan, capital_pool, persona_binding = _scoped_facts()
    request = dict(request, target_stage=target_stage, allowed_deployment_scope="live")
    plan = dict(plan, target_stage=target_stage, current_stage="paper",
                scale={"capital_scale_pct": 0.0, "gross_scale_pct": 100.0, "ramp_schedule": []})
    registry = dict(registry, deployment_stage="paper")
    persona_binding = dict(persona_binding, allowed_deployment_scope="live")
    admissibility = dict(helpers._admissibility(capital_pool, persona_binding), target_stage=target_stage)
    with pytest.raises(authority.DeployAuthorityError, match=f"does not admit target_stage '{target_stage}'"):
        _verify(request, registry, approval, plan, capital_pool, persona_binding,
                fetch_json=helpers._fetcher(registry, approval, plan, capital_pool, persona_binding,
                                            capital_admissibility=admissibility),
                allowed_target_stages=(target_stage,), allowed_registry_deployment_stages=("paper",))


def test_unscoped_approval_keeps_existing_behaviour(monkeypatch):
    monkeypatch.delenv("PANTHEON_ENV", raising=False)
    request, registry, approval, plan, capital_pool, persona_binding = helpers._facts()
    report = _verify(request, registry, approval, plan, capital_pool, persona_binding)
    assert report["status"] == "passed" and report["approval_authorization_scope"] is None
    plan = dict(plan, scale={"capital_scale_pct": 100.0, "gross_scale_pct": 100.0, "ramp_schedule": []})
    assert _verify(request, registry, approval, plan, capital_pool, persona_binding)["status"] == "passed"


def _binding_readback_with_approval_hash(stored_hash, current_hash):
    with pytest.MonkeyPatch.context() as imports:
        imports.syspath_prepend(str(Path(__file__).resolve().parents[1] / "deployment"))
        imports.syspath_prepend(str(Path(__file__).resolve().parent))
        from services.deployment.test_runtime_manager_dispatch_adapter import (
            _authority_report, _make_binding, _make_saga,
        )
    from services.deployment.runtime_manager_dispatch_adapter import validate_authoritative_readback

    binding = _make_binding()
    binding["metadata"]["authoritative_loader_attestation"]["approval_decision_sha256"] = stored_hash
    report = _authority_report()
    report["approval_decision_sha256"] = current_hash
    return validate_authoritative_readback(
        saga=_make_saga(), binding=binding, expected_binding_id=binding["binding_id"],
        expected_authority_report=report,
    )


def test_legacy_approval_digest_preserves_actual_runtime_binding_readback(monkeypatch):
    monkeypatch.delenv("PANTHEON_ENV", raising=False)
    facts = helpers._facts()
    snapshot = SnapshotApprovalReader(facts[2]).get(facts[2]["decision_id"]).model_dump()
    assert snapshot.pop("authorization_scope") is None
    legacy_hash = authority._canonical_digest(snapshot)
    report = _verify(*facts)
    assert report["approval_decision_sha256"] == legacy_hash
    assert _binding_readback_with_approval_hash(legacy_hash, report["approval_decision_sha256"]) is None


def test_nonnull_paper_scope_remains_hash_covered_and_readback_rejects_drift(monkeypatch):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    facts = _scoped_facts()
    snapshot = SnapshotApprovalReader(facts[2]).get(facts[2]["decision_id"]).model_dump()
    report = _verify(*facts)
    assert report["approval_decision_sha256"] == authority._canonical_digest(snapshot)
    snapshot.pop("authorization_scope")
    stripped_hash = authority._canonical_digest(snapshot)
    assert stripped_hash != report["approval_decision_sha256"]
    error = _binding_readback_with_approval_hash(stripped_hash, report["approval_decision_sha256"])
    assert "approval_decision_sha256" in error
