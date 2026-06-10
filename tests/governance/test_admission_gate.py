from __future__ import annotations

import copy

import pytest

from services.governance.research_activation.admission_gate import (
    AdmissionGateError,
    evaluate_candidate_admission,
    require_candidate_admission,
)
from services.governance.research_activation.production_data_proof import SCHEMA_VERSION


def test_candidate_admission_gate_passes_for_valid_production_data_proof() -> None:
    result = require_candidate_admission(_packet("qlib", artifact_type="model_artifact"))

    assert result.passed is True
    assert result.target_id == "registry:qlib:model:001"
    assert result.artifact_type == "model_artifact"
    assert result.proof_id == "pdp-qlib-20260519"
    assert result.production_data_tier == "R3"
    assert result.to_dict()["errors"] == []


def test_candidate_admission_gate_fails_closed_without_license_pit_and_audit() -> None:
    packet = _packet("trl", artifact_type="model_artifact")
    proof = copy.deepcopy(packet["production_data_proof"])
    proof["entitlement"] = {"allowed_use": ["research"]}
    proof["pit"] = {"point_in_time": False}
    proof["audit"] = {"audit_ref": "audit:trl-prod-data-proof"}
    packet["production_data_proof"] = proof

    result = evaluate_candidate_admission(packet)

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert "missing_entitlement_ref" in codes
    assert "missing_license_scope" in codes
    assert "pit_not_proven" in codes
    assert "missing_evidence_bundle_ref" in codes
    with pytest.raises(AdmissionGateError, match="missing_license_scope"):
        require_candidate_admission(packet)


def test_candidate_admission_gate_fails_closed_for_deployment_or_order_route_request() -> None:
    packet = _packet("finrl", artifact_type="model_artifact")
    packet["registry_request"]["requested_artifact_state"] = "approved"
    packet["registry_request"]["deployment_stage"] = "paper"
    packet["safety_assertions"]["no_order_route"] = False
    packet["downstream_scope"]["order_route"] = "broker"

    result = evaluate_candidate_admission(packet)

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert "requested_state_not_candidate" in codes
    assert "deployment_stage_not_none" in codes
    assert "no_order_route_not_asserted" in codes
    assert "order_route_not_none" in codes


def test_candidate_admission_gate_requires_lineage_dataset_to_match_proof() -> None:
    packet = _packet("wandb", artifact_type="model_artifact")
    packet["candidate_artifact"]["lineage"]["source_dataset_refs"] = [
        "dataset:unrelated-snapshot"
    ]

    result = evaluate_candidate_admission(packet)

    assert result.passed is False
    assert "lineage_dataset_not_in_proof" in {issue.code for issue in result.errors}


def _packet(adapter_kind: str, *, artifact_type: str) -> dict:
    target_id = f"registry:{adapter_kind}:model:001"
    proof = _proof(adapter_kind, artifact_type=artifact_type)
    dataset_ref = proof["source_dataset_refs"][0]
    candidate = {
        "registry_id": target_id,
        "artifact_type": artifact_type,
        "strategy_id": f"{adapter_kind}-strategy",
        "version": "1.0.0",
        "artifact_state": "draft",
        "checksum": "sha256:0123456789abcdef",
        "storage_ref": {
            "backend": "object_store",
            "path": f"object://pantheon-research/{adapter_kind}/artifact/model-001",
        },
        "lineage": {
            "source_run_ids": [f"run:{adapter_kind}:20260519"],
            "source_dataset_refs": [dataset_ref],
            "source_strategy_spec_id": f"strategy-spec:{adapter_kind}:001",
        },
    }
    return {
        "packet_id": f"admission-{adapter_kind}-001",
        "target_type": "artifact",
        "target_id": target_id,
        "environment": "paper",
        "can_proceed": True,
        "required_evidence": ["production_data_proof"],
        "provided_evidence": [
            {
                "key": "production_data_proof",
                "ref_type": "proof",
                "status": "passed",
                "ref_id": proof["proof_id"],
            }
        ],
        "missing_evidence": [],
        "gate_results": [
            {
                "gate": "production_data_proof",
                "status": "passed",
                "source_ref": proof["proof_id"],
            }
        ],
        "registry_request": {
            "request_type": "artifact_state_transition",
            "artifact_type": artifact_type,
            "registry_id": target_id,
            "current_artifact_state": "draft",
            "requested_artifact_state": "candidate",
            "requested_transition": "draft_to_candidate",
            "deployment_stage": "none",
            "approval_scope": "candidate_admission_review_only",
            "registry_write_authority": "registry_service_only",
            "registry_write_performed": False,
        },
        "candidate_artifact": candidate,
        "production_data_proof": proof,
        "downstream_scope": {
            "registry_admission_packet_only": True,
            "registry_write_authority": "registry_service_only",
            "registry_write_performed": False,
            "deployment_stage": "none",
            "broker_session_opened": False,
            "order_route": "none",
            "capital_binding": "none",
            "execution_targets": ["research", "registry_review"],
        },
        "safety_assertions": {
            "no_order_route": True,
            "no_broker_session": True,
            "no_capital_binding": True,
            "deployment_stage_remains_none": True,
            "artifact_state_request_limited_to_candidate": True,
        },
    }


def _proof(adapter_kind: str, *, artifact_type: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "proof_id": f"pdp-{adapter_kind}-20260519",
        "activation_tier": "R3",
        "created_at": "2026-05-19T00:00:00Z",
        "adapter_id": f"{adapter_kind}-adapter-prod-data-proof",
        "adapter_kind": adapter_kind,
        "source_dataset_refs": [
            f"dataset:{adapter_kind}-production-research-snapshot-20260519"
        ],
        "provider": {
            "name": "Pantheon governed research data fixture",
            "source_class": "production_research",
            "dataset_id": f"{adapter_kind}-prod-research",
        },
        "entitlement": {
            "entitlement_ref": f"entitlement:{adapter_kind}-research",
            "entitlement_tags": [f"{adapter_kind}-research"],
            "license_scope": "research",
            "allowed_use": ["research", "model_training", "evaluation"],
        },
        "freshness": {
            "status": "fresh",
            "as_of": "2026-05-19T00:00:00Z",
            "last_ingested_at": "2026-05-19T00:05:00Z",
            "freshness_sla_seconds": 86400,
        },
        "pit": {
            "point_in_time": True,
            "event_time_field": "event_time",
            "available_time_field": "available_time",
            "source_watermark": "2026-05-19T00:00:00Z",
        },
        "storage": {
            "backend": "postgres+object_store",
            "dataset_ref": f"dataset:{adapter_kind}-production-research-snapshot-20260519",
            "snapshot_ref": f"snapshot:{adapter_kind}-20260519",
            "path": f"object://pantheon-research/{adapter_kind}/20260519",
            "checksum": "sha256:0123456789abcdef",
            "durable": True,
        },
        "audit": {
            "audit_ref": f"audit:{adapter_kind}-prod-data-proof",
            "ingest_run_id": f"ingest:{adapter_kind}-20260519",
            "normalization_run_id": f"normalize:{adapter_kind}-20260519",
            "evidence_bundle_ref": f"evidence-bundle:{adapter_kind}-prod-data-proof",
            "rate_limit_policy_ref": f"rate-limit:{adapter_kind}-research",
        },
        "controls": {
            "no_order_route": True,
            "produced_artifact_types": [artifact_type, "evaluation_result"],
            "execution_targets": ["research", "registry_review"],
            "attempted_mutation_types": [],
        },
        "adapter_evidence": [
            {
                "adapter_id": f"{adapter_kind}-adapter-prod-data-proof",
                "adapter_kind": adapter_kind,
                "backend": "offline_governed",
                "evidence_refs": [
                    {
                        "key": "production_data_manifest",
                        "ref_type": "artifact",
                        "path": f"support/evidence/{adapter_kind}/production_data_manifest.json",
                        "checksum": "sha256:abcdef0123456789",
                        "status": "provided",
                    }
                ],
            }
        ],
    }
