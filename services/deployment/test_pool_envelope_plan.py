"""create_plan accepts pool envelopes for paper; multi-symbol is refused by name."""
import copy
import json

import pytest

from services.deployment.test_inline_paper_execution_projection import _inline_registry
from services.deployment.test_service import _plan_payload, client  # noqa: F401
from services.registry.portfolio_envelope import build_portfolio_envelope
from services.registry.strategy_artifact import strategy_artifact_checksum
from services.governance.test_approval_authority import approval_snapshot


def _install_envelope(test_client, governance_dir, member_specs):
    """member_specs: list of (symbols, lookback, weight) built from the real momentum artifact."""
    base, entry = _inline_registry(test_client, governance_dir)
    members = []
    for i, (symbols, lookback, weight) in enumerate(member_specs):
        child = copy.deepcopy(base)
        child.update(artifact_id=f"child-{i}", strategy_id=f"child-s-{i}")
        child["parameters"]["symbols"] = symbols
        key = child["strategy_logic"]["lookback_parameter"]
        child["parameters"][key] = lookback
        for control in child["mutation_surface"]["controls"]:
            if control["parameter_key"] == key:
                control["current_value"] = lookback
        members.append({"member_id": f"m{i}", "artifact": child, "risk_weight": weight})
    env = build_portfolio_envelope(
        artifact_id=entry["registry_id"], strategy_id=entry["strategy_id"],
        version=entry["version"], members=members)
    entries = json.loads(test_client.registry_snapshot.read_text())
    reg = entries[entry["registry_id"]]
    reg["checksum"] = strategy_artifact_checksum(env)
    reg["metadata"] = {"strategy_artifact": env}
    test_client.registry_snapshot.write_text(json.dumps(entries))
    approval = approval_snapshot(
        decision_id="approval-001", target_id=reg["registry_id"], target_version=reg["version"],
        target_type="registry_entry", candidate_digest=reg["checksum"],
        capital_pool_id="pool-001", persona_id="persona-ops", tenant_id="tenant-deployment-test",
    )
    (governance_dir / "approval_decisions.json").write_text(json.dumps({"approval-001": approval}))
    return env


def test_singleton_envelope_plan_is_created(client):
    test_client, governance_dir = client
    env = _install_envelope(test_client, governance_dir, [(["2330.TW"], 5, 1)])
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-env-1"))
    assert response.status_code == 201, response.text
    md = response.json()["metadata"]
    assert md["symbol"] == "2330.TW"
    assert md["market_data_policy"]["minimum_closes"] == 5
    assert md["artifact_checksum"] == strategy_artifact_checksum(env)


def test_two_member_same_symbol_envelope_uses_member_maximum(client):
    test_client, governance_dir = client
    _install_envelope(test_client, governance_dir, [(["2330.TW"], 5, 0.5), (["2330.TW"], 12, 0.5)])
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-env-2"))
    assert response.status_code == 201, response.text
    assert response.json()["metadata"]["market_data_policy"]["minimum_closes"] == 12


def test_multi_symbol_envelope_is_refused_with_named_reason_and_not_persisted(client):
    test_client, governance_dir = client
    _install_envelope(test_client, governance_dir, [(["2330.TW"], 5, 0.5), (["2317.TW"], 5, 0.5)])
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-env-3"))
    assert response.status_code == 422, response.text
    assert "multi_symbol_paper_plan_unsupported" in response.text
    assert test_client.get("/api/deployment/plans/plan-env-3").status_code == 404
