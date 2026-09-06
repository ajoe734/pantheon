"""EVOLOOP-003 tests for the minimal evolvable StrategyArtifact contract."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import AuthContext

from .models import ArtifactState, ArtifactType, DeploymentStage
from .service import _register_strategy_artifact, app, get_registry_service
from .split_api import RegistryError
from .storage import get_store, reset_store
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


_TEST_CTX = AuthContext(
    actor_id="test-operator", roles=frozenset({"operator"}), claims={}, token_kind="structured",
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
    client = TestClient(app, headers={"Authorization": "Bearer test-operator:operator"})
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
    after_reset = TestClient(app, headers={"Authorization": "Bearer test-operator:operator"}).get(
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

    child = mutate_strategy_artifact(
        artifact,
        new_artifact_id="artifact-tw-session-momentum-threshold",
        new_version="1.1.0",
        parameter_updates={"momentum_threshold": 0.01},
        source_run_ids=["training-session-threshold"],
    )
    assert evaluate_strategy_action(child, [100, 101]) == "SELL"
    assert evaluate_strategy_action(artifact, [10**1000, 10**1000 + 1]) == "BUY"


def test_mutation_api_creates_real_child_delta_and_preserves_parent():
    client = TestClient(app, headers={"Authorization": "Bearer test-operator:operator"})
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


@pytest.mark.parametrize("actual_value", [True, 1.0])
def test_integer_control_validates_actual_parameter_type(actual_value):
    artifact = _artifact()
    artifact["parameters"]["integer_knob"] = actual_value
    artifact["mutation_surface"]["controls"].append(
        {
            "parameter_key": "integer_knob",
            "value_type": "integer",
            "current_value": 1,
            "allowed_range": {"min": 0, "max": 2},
            "step": 1,
        }
    )

    with pytest.raises(StrategyArtifactValidationError, match="requires an integer"):
        validate_strategy_artifact(artifact)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("lineage", {"source_run_ids": ["   "]}, "canonical non-blank ids"),
        ("algorithm_ref", {"path": " "}, "algorithm_ref.path"),
        ("binding_intent", {"observed_at": "not-a-date"}, "RFC3339"),
    ],
)
def test_semantic_validation_rejects_blank_refs_and_invalid_time(
    field,
    value,
    message,
):
    artifact = _artifact()
    artifact[field].update(value)

    with pytest.raises(StrategyArtifactValidationError, match=message):
        validate_strategy_artifact(artifact)


@pytest.mark.parametrize("wrapper_field", ["metadata", "evaluation_summary"])
def test_registration_rejects_falsy_non_object_wrappers(wrapper_field):
    registration = _registration()
    registration[wrapper_field] = []

    with pytest.raises(StrategyArtifactValidationError, match="JSON object"):
        build_strategy_artifact_registry_payload(registration)


def test_registration_rejects_non_canonical_supplemental_json():
    registration = _registration()
    registration["metadata"] = {"non_finite": float("nan")}

    with pytest.raises(StrategyArtifactValidationError, match="not canonical JSON"):
        build_strategy_artifact_registry_payload(registration)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("artifact_id", "artifact/route-break", "schema validation failed"),
        (
            "algorithm_path",
            "../outside.py",
            "normalized repository-relative path",
        ),
        (
            "logic_interpreter",
            "not-an-import-reference",
            "module:object Python reference",
        ),
    ],
)
def test_strategy_artifact_rejects_route_breaking_ids_and_unsafe_code_refs(
    field,
    value,
    message,
):
    artifact = _artifact()
    if field == "algorithm_path":
        artifact["algorithm_ref"]["path"] = value
    elif field == "logic_interpreter":
        artifact["algorithm_ref"]["logic_interpreter"] = value
    else:
        artifact[field] = value

    with pytest.raises(StrategyArtifactValidationError, match=message):
        validate_strategy_artifact(artifact)


def test_mutation_rejects_string_run_sequence_and_huge_out_of_range_integer():
    with pytest.raises(StrategyArtifactValidationError, match="not a string"):
        mutate_strategy_artifact(
            _artifact(),
            new_artifact_id="artifact-tw-session-momentum-string-runs",
            new_version="1.1.0",
            parameter_updates={"lookback_bars": 3},
            source_run_ids="run-001",
        )

    with pytest.raises(StrategyArtifactValidationError, match="outside"):
        mutate_strategy_artifact(
            _artifact(),
            new_artifact_id="artifact-tw-session-momentum-huge-lookback",
            new_version="1.1.0",
            parameter_updates={"lookback_bars": 10**1000},
            source_run_ids=["run-001"],
        )

    with pytest.raises(StrategyArtifactValidationError, match="finite number"):
        mutate_strategy_artifact(
            _artifact(),
            new_artifact_id="artifact-tw-session-momentum-digit-limit",
            new_version="1.1.0",
            parameter_updates={"lookback_bars": 10**10000},
            source_run_ids=["run-001"],
        )


def test_strategy_artifact_facade_rejects_plain_execution_bundle():
    client = TestClient(app, headers={"Authorization": "Bearer test-operator:operator"})
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


@pytest.mark.parametrize(
    "embedded",
    [
        {},
        pytest.param(
            {**_artifact(), "artifact_id": "different-artifact-id"},
            id="envelope-id-mismatch",
        ),
    ],
)
def test_strategy_artifact_facade_rejects_malformed_or_mismatched_overlay(embedded):
    client = TestClient(app, headers={"Authorization": "Bearer test-operator:operator"})
    created = client.post(
        "/api/registry/entries",
        json={
            "artifact_type": "execution_bundle",
            "strategy_id": "malformed-overlay",
            "version": "1.0.0",
            "artifact_state": "candidate",
            "lineage": {"source_run_ids": ["run-malformed"]},
            "storage_ref": {
                "backend": "inline",
                "path": "$.entry.metadata.strategy_artifact",
            },
            "checksum": "sha256:wrong",
            "metadata": {"strategy_artifact": embedded},
        },
    )
    assert created.status_code == 200, created.text
    registry_id = created.json()["entry"]["registry_id"]

    specialized = client.get(
        f"/api/registry/strategy-artifacts/{registry_id}"
    )
    assert specialized.status_code == 404, specialized.text
    listed = client.get(
        "/api/registry/strategies/malformed-overlay/strategy-artifacts"
    )
    assert listed.status_code == 200, listed.text
    assert listed.json() == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_state", "draft"),
        ("metadata", {"caller_note": "different"}),
        ("evaluation_summary", {"score": 0.5}),
        ("producer_run_id", "different-producer"),
        ("rollback_target", "artifact-prior"),
    ],
)
def test_idempotent_retry_rejects_changed_registration_envelope(field, value):
    client = TestClient(app, headers={"Authorization": "Bearer test-operator:operator"})
    registry_id = "artifact-tw-session-momentum-v1"
    seeded = client.get(f"/api/registry/strategy-artifacts/{registry_id}")
    assert seeded.status_code == 200, seeded.text

    changed = _registration()
    changed[field] = value
    response = client.post("/api/registry/strategy-artifacts", json=changed)

    assert response.status_code == 400, response.text
    assert "different content" in response.json()["detail"]


def test_same_child_id_concurrent_mutations_never_overwrite():
    parent = _artifact()
    registry_service = get_registry_service()
    child_id = "artifact-tw-session-momentum-concurrent-v2"
    children = [
        mutate_strategy_artifact(
            parent,
            new_artifact_id=child_id,
            new_version="1.1.0",
            parameter_updates={"momentum_threshold": threshold},
            source_run_ids=[f"training-session-{index}"],
        )
        for index, threshold in enumerate((0.01, 0.02), start=1)
    ]
    barrier = Barrier(2)

    def register(child):
        barrier.wait()
        try:
            view = _register_strategy_artifact(
                registry_service,
                {
                    "registry_id": child_id,
                    "artifact_state": "candidate",
                    "strategy_artifact": child,
                },
                ctx=_TEST_CTX,
            )
            return "created", view.entry.checksum
        except RegistryError as exc:
            return "collision", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(register, children))

    assert sorted(result[0] for result in results) == ["collision", "created"]
    stored = registry_service.get(child_id).entry
    assert stored.metadata["strategy_artifact"]["parameters"][
        "momentum_threshold"
    ] in {0.01, 0.02}


def test_fastapi_startup_registers_builtin_before_health_only_request():
    registry_id = "artifact-tw-session-momentum-v1"
    with TestClient(app, headers={"Authorization": "Bearer test-operator:operator"}) as client:
        health = client.get("/health")
        assert health.status_code == 200, health.text
        assert get_store().get(registry_id) is not None


def test_strategy_artifact_advance_preserves_deployment_split():
    """Checked-in built-ins are immutable via caller routes (reviewer finding
    1: "deny unauthorized builtin mutation") — this proof registers a fresh,
    non-builtin StrategyArtifact (a caller-owned entry, not a bootstrap
    artifact) and advances *that* instead of the shared built-in fixture."""
    client = TestClient(app, headers={"Authorization": "Bearer test-operator:operator"})
    artifact = copy.deepcopy(_artifact())
    artifact["artifact_id"] = "artifact-advance-split-test-v1"
    artifact["strategy_id"] = "advance-split-test"
    registered = client.post(
        "/api/registry/strategy-artifacts",
        json={"strategy_artifact": artifact},
    )
    assert registered.status_code == 200, registered.text
    registry_id = registered.json()["entry"]["registry_id"]

    approved = client.post(
        f"/api/registry/strategy-artifacts/{registry_id}/advance",
        json={
            "target_state": "approved",
            "expected_artifact_state": "candidate",
            "approver": "test-reviewer",
            "approval_decision_id": "decision-evoloop-006",
        },
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["entry"]["artifact_state"] == "approved"
    assert approved.json()["entry"]["approver"] == "test-reviewer"
    assert approved.json()["entry"]["approval_decision_id"] == (
        "decision-evoloop-006"
    )
    assert approved.json()["deployment_stage"] == DeploymentStage.NONE.value
