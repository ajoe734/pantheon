from __future__ import annotations

import pytest

from services.governance.research_activation.pit_license_freshness import (
    ALLOWED_ADMISSION_ARTIFACT_STATES,
    REQUIRED_DEPLOYMENT_STAGE,
    SCHEMA_VERSION,
    ResearchArtifactAdmissionGateError,
    assert_research_artifact_admissible,
    validate_research_artifact_admission_gate,
)
from services.registry.models import (
    ArtifactState,
    ArtifactType,
    DeploymentStage,
    Lineage,
    RegistryEntryCreate,
    StorageBackend,
    StorageRef,
)
from services.registry.split_api import RegistryService
from services.registry.storage import RegistryStore


def test_admission_gate_accepts_draft_and_candidate_artifacts_with_none_stage() -> None:
    for artifact_state in sorted(ALLOWED_ADMISSION_ARTIFACT_STATES):
        result = validate_research_artifact_admission_gate(
            {
                "artifact_state": artifact_state,
                "deployment_summary": {"current_stage": "none"},
                "metadata": {"registry_write_authority": "registry_service_only"},
            }
        )

        assert result.passed is True
        assert result.schema_version == SCHEMA_VERSION
        assert result.artifact_state == artifact_state
        assert result.deployment_stage == REQUIRED_DEPLOYMENT_STAGE
        assert result.to_dict()["errors"] == []


def test_admission_gate_accepts_registry_view_with_derived_none_stage() -> None:
    service = RegistryService(RegistryStore())
    view = service.register(
        RegistryEntryCreate(
            artifact_type=ArtifactType.MODEL_ARTIFACT,
            strategy_id="res-act-002",
            version="1.0.0",
            artifact_state=ArtifactState.CANDIDATE,
            lineage=Lineage(source_run_ids=["run-res-act-002"]),
            storage_ref=StorageRef(
                backend=StorageBackend.OBJECT_STORE,
                path="object://research/res-act-002/model.pkl",
            ),
            checksum="sha256:resact002",
        ),
        "reg-res-act-002",
    )

    result = assert_research_artifact_admissible(view)

    assert result.passed is True
    assert result.artifact_state == "candidate"
    assert result.deployment_stage == DeploymentStage.NONE.value


@pytest.mark.parametrize("artifact_state", ["approved", "retired", "paper", "live"])
def test_admission_gate_rejects_non_draft_candidate_states(artifact_state: str) -> None:
    result = validate_research_artifact_admission_gate(
        {
            "artifact_state": artifact_state,
            "deployment_stage": "none",
        }
    )

    assert result.passed is False
    assert "forbidden_artifact_state" in {issue.code for issue in result.errors}
    with pytest.raises(ResearchArtifactAdmissionGateError, match="forbidden_artifact_state"):
        result.assert_passed()


@pytest.mark.parametrize("deployment_stage", ["paper", "canary", "live", "frozen"])
def test_admission_gate_rejects_any_deployed_stage(deployment_stage: str) -> None:
    result = validate_research_artifact_admission_gate(
        {
            "artifact_state": "candidate",
            "deployment_summary": {"current_stage": deployment_stage},
        }
    )

    assert result.passed is False
    assert "deployment_stage_not_none" in {issue.code for issue in result.errors}


def test_admission_gate_fails_closed_when_deployment_stage_is_missing() -> None:
    result = validate_research_artifact_admission_gate({"artifact_state": "draft"})

    assert result.passed is False
    assert "missing_deployment_stage" in {issue.code for issue in result.errors}


def test_admission_gate_rejects_conflicting_stage_projections() -> None:
    result = validate_research_artifact_admission_gate(
        {
            "artifact_state": "candidate",
            "deployment_stage": "none",
            "deployment_summary": {"current_stage": "paper"},
        }
    )

    codes = {issue.code for issue in result.errors}
    assert result.passed is False
    assert "conflicting_deployment_stage" in codes
    assert "deployment_stage_not_none" in codes
