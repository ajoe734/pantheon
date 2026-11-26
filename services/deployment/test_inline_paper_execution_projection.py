"""Approved inline artifact survives the real plan store and runner loader."""
import json
import sys
from pathlib import Path

import pytest

from services.deployment.test_service import _plan_payload, client  # noqa: F401
from services.execution.artifact_loader import ArtifactLoader
from services.governance.test_approval_authority import approval_snapshot
from services.registry.strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS, load_strategy_artifact_registration, strategy_artifact_checksum,
)


def _inline_registry(test_client, governance_dir):
    entries = json.loads(test_client.registry_snapshot.read_text())
    entry = entries["reg-strat-001-1.2.0"]
    artifact = load_strategy_artifact_registration(BUILTIN_STRATEGY_ARTIFACT_PATHS[0])["strategy_artifact"]
    artifact.update(artifact_id=entry["registry_id"], strategy_id=entry["strategy_id"], version=entry["version"])
    artifact["parameters"]["symbols"] = ["2330.TW"]
    artifact["provenance_refs"] = ["simulation://unit/策略"]
    entry.update(artifact_type="execution_bundle", checksum=strategy_artifact_checksum(artifact), metadata={"strategy_artifact": artifact})
    test_client.registry_snapshot.write_text(json.dumps(entries))
    approval = approval_snapshot(
        decision_id="approval-001", target_id=entry["registry_id"], target_version=entry["version"],
        target_type="registry_entry", candidate_digest=entry["checksum"],
        capital_pool_id="pool-001", persona_id="persona-ops", tenant_id="tenant-deployment-test",
    )
    (governance_dir / "approval_decisions.json").write_text(json.dumps({"approval-001": approval}))
    return artifact, entry


def test_inline_paper_projection_is_durable_and_exactly_loadable(client):
    test_client, governance_dir = client
    artifact, entry = _inline_registry(test_client, governance_dir)
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-inline-paper"))
    assert response.status_code == 201, response.text
    module = sys.modules["services.deployment.service"]
    reloaded = module.DeploymentPlanStore(str(governance_dir / "deployment_plans.json")).get("plan-inline-paper")
    assert reloaded is not None
    metadata = reloaded.metadata
    assert metadata["symbol"] == artifact["parameters"]["symbols"][0]
    assert metadata["market_data_policy"]["minimum_closes"] == artifact["parameters"][artifact["strategy_logic"]["lookback_parameter"]]
    loaded = ArtifactLoader(metadata["object_store"]).load_exact(
        registry_id=entry["registry_id"], strategy_id=entry["strategy_id"], version=entry["version"],
        execution_mode="paper", expected_checksum=entry["checksum"],
    )
    assert json.loads(loaded.payload) == artifact
    assert metadata["artifact_checksum"] == entry["checksum"]
    assert "market_input" not in metadata  # projection is configuration, never fabricated market evidence


@pytest.mark.parametrize("defect", ["null", "identity", "checksum"])
def test_invalid_inline_artifact_is_not_persisted(client, defect):
    test_client, governance_dir = client
    _inline_registry(test_client, governance_dir)
    entries = json.loads(test_client.registry_snapshot.read_text())
    artifact = entries["reg-strat-001-1.2.0"]["metadata"]["strategy_artifact"]
    if defect == "null":
        entries["reg-strat-001-1.2.0"]["metadata"]["strategy_artifact"] = None
    elif defect == "identity":
        artifact["artifact_id"] = "different-artifact"
    else:
        artifact["provenance_refs"] = ["simulation://changed-after-approval"]
    test_client.registry_snapshot.write_text(json.dumps(entries))
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-invalid-inline"))
    assert response.status_code == 422, response.text
    assert test_client.get("/api/deployment/plans/plan-invalid-inline").status_code == 404


def test_existing_execution_policy_and_other_store_entries_are_preserved(client):
    test_client, governance_dir = client
    _inline_registry(test_client, governance_dir)
    request = _plan_payload(plan_id="plan-explicit-policy")
    policy = {"owner": "source-ingest", "contract": "latest_stored_normalized", "minimum_closes": 50, "max_age_seconds": 600}
    request["metadata"] = {"symbol": "2330.TW", "market_data_policy": policy, "object_store": {"unrelated/config": "keep"}}
    response = test_client.post("/api/deployment/plans", json=request)
    assert response.status_code == 201, response.text
    metadata = response.json()["metadata"]
    assert metadata["market_data_policy"] == policy
    assert metadata["symbol"] == "2330.TW"
    assert metadata["object_store"]["unrelated/config"] == "keep"


def test_momentum_plan_market_data_policy_is_byte_identical_to_legacy_derivation(client):
    """The shared helper must reproduce the pre-change momentum policy exactly."""
    test_client, governance_dir = client
    artifact, _ = _inline_registry(test_client, governance_dir)
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-byte-identical"))
    assert response.status_code == 201, response.text
    legacy = {
        "owner": "source-ingest",
        "contract": "latest_stored_normalized",
        "minimum_closes": artifact["parameters"][artifact["strategy_logic"]["lookback_parameter"]],
        "max_age_seconds": 86400,
    }
    actual = response.json()["metadata"]["market_data_policy"]
    assert json.dumps(actual, sort_keys=True) == json.dumps(legacy, sort_keys=True)


def _set_lookback(test_client, governance_dir, lookback):
    artifact, entry = _inline_registry(test_client, governance_dir)
    key = artifact["strategy_logic"]["lookback_parameter"]
    artifact["parameters"][key] = lookback
    for control in artifact["mutation_surface"]["controls"]:
        if control["parameter_key"] == key:
            control["current_value"] = lookback
            control["allowed_range"]["max"] = max(control["allowed_range"]["max"], lookback)
    entries = json.loads(test_client.registry_snapshot.read_text())
    entries[entry["registry_id"]]["checksum"] = strategy_artifact_checksum(artifact)
    entries[entry["registry_id"]]["metadata"] = {"strategy_artifact": artifact}
    test_client.registry_snapshot.write_text(json.dumps(entries))
    approval = approval_snapshot(
        decision_id="approval-001", target_id=entry["registry_id"], target_version=entry["version"],
        target_type="registry_entry", candidate_digest=strategy_artifact_checksum(artifact),
        capital_pool_id="pool-001", persona_id="persona-ops", tenant_id="tenant-deployment-test",
    )
    (governance_dir / "approval_decisions.json").write_text(json.dumps({"approval-001": approval}))


def test_plan_needing_more_closes_than_snapshot_serves_is_refused(client):
    test_client, governance_dir = client
    _set_lookback(test_client, governance_dir, 120)
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-too-long"))
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "close_to_close_momentum" in detail and "120" in detail and "60" in detail
    assert test_client.get("/api/deployment/plans/plan-too-long").status_code == 404


def test_plan_needing_exactly_served_closes_is_built(client):
    test_client, governance_dir = client
    _set_lookback(test_client, governance_dir, 60)
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-at-limit"))
    assert response.status_code == 201, response.text
    assert response.json()["metadata"]["market_data_policy"]["minimum_closes"] == 60


def test_explicit_market_data_policy_skips_history_check(client):
    test_client, governance_dir = client
    _set_lookback(test_client, governance_dir, 120)
    request = _plan_payload(plan_id="plan-explicit-long")
    policy = {"owner": "source-ingest", "contract": "latest_stored_normalized", "minimum_closes": 120, "max_age_seconds": 600}
    request["metadata"] = {"symbol": "2330.TW", "market_data_policy": policy}
    response = test_client.post("/api/deployment/plans", json=request)
    assert response.status_code == 201, response.text
    assert response.json()["metadata"]["market_data_policy"] == policy


def _install_factor_rule(test_client, governance_dir, z_window):
    from services.registry.test_factor_rule import _sample_factor_rule_artifact

    _, entry = _inline_registry(test_client, governance_dir)
    artifact = _sample_factor_rule_artifact()
    artifact.update(artifact_id=entry["registry_id"], strategy_id=entry["strategy_id"], version=entry["version"])
    artifact["parameters"]["symbols"] = ["2330.TW"]
    artifact["parameters"]["z_window"] = z_window
    for control in artifact["mutation_surface"]["controls"]:
        if control["parameter_key"] == "z_window":
            control["current_value"] = z_window
            control["allowed_range"]["max"] = max(control["allowed_range"]["max"], z_window)
    checksum = strategy_artifact_checksum(artifact)
    entries = json.loads(test_client.registry_snapshot.read_text())
    entries[entry["registry_id"]]["checksum"] = checksum
    entries[entry["registry_id"]]["metadata"] = {"strategy_artifact": artifact}
    test_client.registry_snapshot.write_text(json.dumps(entries))
    approval = approval_snapshot(
        decision_id="approval-001", target_id=entry["registry_id"], target_version=entry["version"],
        target_type="registry_entry", candidate_digest=checksum,
        capital_pool_id="pool-001", persona_id="persona-ops", tenant_id="tenant-deployment-test",
    )
    (governance_dir / "approval_decisions.json").write_text(json.dumps({"approval-001": approval}))


def test_factor_rule_plan_needing_504_bars_is_refused(client):
    test_client, governance_dir = client
    _install_factor_rule(test_client, governance_dir, 504)
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-factor-long"))
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "factor_rule_v1" in detail and "504" in detail and "60" in detail
    assert test_client.get("/api/deployment/plans/plan-factor-long").status_code == 404


def test_factor_rule_plan_within_snapshot_is_built(client):
    test_client, governance_dir = client
    _install_factor_rule(test_client, governance_dir, 24)
    response = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-factor-ok"))
    assert response.status_code == 201, response.text
    assert response.json()["metadata"]["market_data_policy"]["minimum_closes"] == 24
