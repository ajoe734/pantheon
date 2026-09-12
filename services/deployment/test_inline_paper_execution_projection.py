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
