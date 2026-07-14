"""EVOLOOP-003 tests for the minimal evolvable StrategyArtifact contract."""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from .models import ArtifactState, ArtifactType, DeploymentStage
from .service import app
from .storage import reset_store
from .strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS,
    StrategyArtifactValidationError,
    build_strategy_artifact_registry_payload,
    evaluate_strategy_action,
    load_strategy_artifact_registration,
    mutate_strategy_artifact,
    strategy_artifact_checksum,
    validate_strategy_artifact,
)


@pytest.fixture(autouse=True)
def clean_store():
    reset_store()
    yield
    reset_store()


def _registration() -> dict:
    return load_strategy_artifact_registration(
        BUILTIN_STRATEGY_ARTIFACT_PATHS[0]
    )


def _artifact() -> dict:
    return _registration()["strategy_artifact"]


def test_builtin_v1_is_schema_valid_and_maps_to_execution_bundle():
    registration = _registration()
    artifact = registration["strategy_artifact"]

    validate_strategy_artifact(artifact)
    registry_id, payload = build_strategy_artifact_registry_payload(registration)

    assert registry_id == artifact["artifact_id"]
    assert artifact["strategy_id"] == "tw_session_momentum"
    assert payload.artifact_type == ArtifactType.EXECUTION_BUNDLE
    assert payload.artifact_state == ArtifactState.CANDIDATE
    assert payload.storage_ref.path == "$.entry.metadata.strategy_artifact"
    assert payload.checksum == strategy_artifact_checksum(artifact)
    assert payload.metadata["strategy_artifact"] == artifact
    assert payload.lineage.source_run_ids == ["EVOLOOP-003"]
    assert artifact["binding_intent"]["persona_capital_binding_id"] == (
        "binding-tw-equity-paper"
    )
    assert artifact["binding_intent"]["observed_placeholder_artifact_id"] == (
        "artifact-tw-equity-session-v1"
    )


def test_builtin_v1_is_registered_idempotently_after_store_reset():
    client = TestClient(app)
    registry_id = "artifact-tw-session-momentum-v1"

    first = client.get(f"/api/registry/strategy-artifacts/{registry_id}")
    assert first.status_code == 200, first.text
    entry = first.json()["entry"]
    assert entry["registry_id"] == registry_id
    assert entry["strategy_id"] == "tw_session_momentum"
    assert entry["artifact_state"] == "candidate"
    assert first.json()["deployment_stage"] == "none"

    retry = client.post(
        "/api/registry/strategy-artifacts",
        json=_registration(),
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["entry"]["checksum"] == entry["checksum"]

    listed = client.get(
        "/api/registry/strategies/tw_session_momentum/strategy-artifacts"
    )
    assert listed.status_code == 200, listed.text
    assert [item["entry"]["registry_id"] for item in listed.json()] == [
        registry_id
    ]

    reset_store()
    after_reset = TestClient(app).get(
        f"/api/registry/strategy-artifacts/{registry_id}"
    )
    assert after_reset.status_code == 200, after_reset.text


def test_v1_logic_interpreter_uses_declared_parameters():
    artifact = _artifact()

    assert evaluate_strategy_action(artifact, [100.0, 101.0]) == "BUY"
    assert evaluate_strategy_action(artifact, [100.0, 100.0]) == "SELL"
    assert evaluate_strategy_action(artifact, [100.0, 99.0]) == "SELL"

    with pytest.raises(StrategyArtifactValidationError, match="at least 2 closes"):
        evaluate_strategy_action(artifact, [100.0])


def test_mutation_api_creates_real_child_delta_and_preserves_parent():
    client = TestClient(app)
    parent_id = "artifact-tw-session-momentum-v1"
    child_id = "artifact-tw-session-momentum-v2"

    mutated = client.post(
        f"/api/registry/strategy-artifacts/{parent_id}/mutate",
        json={
            "new_artifact_id": child_id,
            "new_version": "1.1.0",
            "parameter_updates": {"momentum_threshold": 0.01},
            "source_run_ids": [
                "decision-evoloop-004",
                "work-item-evoloop-004",
                "training-session-evoloop-004",
            ],
        },
    )

    assert mutated.status_code == 200, mutated.text
    data = mutated.json()
    entry = data["entry"]
    child = entry["metadata"]["strategy_artifact"]
    assert entry["registry_id"] == child_id
    assert entry["artifact_state"] == "candidate"
    assert data["deployment_stage"] == "none"
    assert child["version"] == "1.1.0"
    assert child["strategy_id"] == "tw_session_momentum"
    assert child["parameters"]["momentum_threshold"] == 0.01
    assert child["lineage"]["parent_registry_ids"] == [parent_id]
    assert child["lineage"]["source_run_ids"][-1] == (
        "training-session-evoloop-004"
    )
    assert evaluate_strategy_action(child, [100.0, 100.5]) == "SELL"

    parent = client.get(
        f"/api/registry/strategy-artifacts/{parent_id}"
    ).json()["entry"]["metadata"]["strategy_artifact"]
    assert parent["version"] == "1.0.0"
    assert parent["parameters"]["momentum_threshold"] == 0.0


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"unknown_parameter": 1}, "unknown parameter"),
        ({"symbols": ["2330.TW"]}, "immutable"),
        ({"order_quantity": 2}, "immutable"),
        ({"lookback_bars": 2.5}, "requires an integer"),
        ({"momentum_threshold": -0.001}, "outside"),
        ({"momentum_threshold": 0.0105}, "not aligned to step"),
        ({"momentum_threshold": 0.0}, "must change"),
    ],
)
def test_mutation_fails_closed_for_invalid_parameter_updates(updates, message):
    with pytest.raises(StrategyArtifactValidationError, match=message):
        mutate_strategy_artifact(
            _artifact(),
            new_artifact_id="artifact-tw-session-momentum-invalid",
            new_version="1.1.0",
            parameter_updates=updates,
            source_run_ids=["training-session-invalid"],
        )


def test_mutation_is_pure_and_requires_greater_version_and_run_lineage():
    parent = _artifact()
    original = copy.deepcopy(parent)

    child = mutate_strategy_artifact(
        parent,
        new_artifact_id="artifact-tw-session-momentum-v2",
        new_version="1.1.0",
        parameter_updates={"lookback_bars": 3},
        source_run_ids=["training-session-001"],
    )

    assert parent == original
    assert child is not parent
    assert child["parameters"]["lookback_bars"] == 3
    assert child["mutation_surface"]["controls"][0]["current_value"] == 3

    with pytest.raises(StrategyArtifactValidationError, match="greater"):
        mutate_strategy_artifact(
            parent,
            new_artifact_id="artifact-tw-session-momentum-v2",
            new_version="1.0.0",
            parameter_updates={"lookback_bars": 3},
            source_run_ids=["training-session-001"],
        )
    with pytest.raises(StrategyArtifactValidationError, match="source_run_id"):
        mutate_strategy_artifact(
            parent,
            new_artifact_id="artifact-tw-session-momentum-v2",
            new_version="1.1.0",
            parameter_updates={"lookback_bars": 3},
            source_run_ids=[],
        )


def test_semantic_validation_requires_total_parameter_partition():
    artifact = _artifact()
    artifact["mutation_surface"]["immutable_parameters"].remove("order_quantity")

    with pytest.raises(StrategyArtifactValidationError, match="missing: order_quantity"):
        validate_strategy_artifact(artifact)


def test_strategy_artifact_facade_rejects_plain_execution_bundle():
    client = TestClient(app)
    created = client.post(
        "/api/registry/entries",
        json={
            "artifact_type": "execution_bundle",
            "strategy_id": "plain-execution",
            "version": "1.0.0",
            "artifact_state": "candidate",
            "lineage": {"source_run_ids": ["run-plain"]},
            "storage_ref": {
                "backend": "object_store",
                "path": "s3://bucket/plain.tar.gz",
            },
            "checksum": "sha256:plain",
        },
    )
    assert created.status_code == 200, created.text
    registry_id = created.json()["entry"]["registry_id"]

    specialized = client.get(
        f"/api/registry/strategy-artifacts/{registry_id}"
    )
    assert specialized.status_code == 404, specialized.text


def test_strategy_artifact_advance_preserves_deployment_split():
    client = TestClient(app)
    registry_id = "artifact-tw-session-momentum-v1"

    approved = client.post(
        f"/api/registry/strategy-artifacts/{registry_id}/advance",
        json={"target_state": "approved", "approver": "test-reviewer"},
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["entry"]["artifact_state"] == "approved"
    assert approved.json()["entry"]["approver"] == "test-reviewer"
    assert approved.json()["deployment_stage"] == DeploymentStage.NONE.value
