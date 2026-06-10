from __future__ import annotations

import copy

from services.governance.research_activation.handoff_packet import (
    BLOCKED,
    MISSING,
    PASSED,
    READY,
    SCHEMA_VERSION,
    build_research_activation_handoff_packet,
)
from services.governance.research_activation.production_data_proof import (
    SCHEMA_VERSION as PRODUCTION_DATA_PROOF_SCHEMA_VERSION,
)


def test_handoff_packet_surfaces_tier_evidence_and_admission_gate_status() -> None:
    packet = build_research_activation_handoff_packet(
        [_adapter_record("qlib")],
        as_of="2026-05-20T00:00:00Z",
    )

    assert packet.schema_version == SCHEMA_VERSION
    assert packet.summary.readiness_state == READY
    assert packet.summary.ready_adapters == 1
    assert packet.summary.tier_counts["R3"] == 1

    row = packet.adapters[0]
    assert row.adapter_id == "qlib-adapter-prod-data-proof"
    assert row.adapter_kind == "qlib"
    assert row.activation_tier == "R3"
    assert row.activation_tier_rank == 3
    assert row.proof_id == "pdp-qlib-20260520"
    assert row.target_id == "registry:qlib:model:001"
    assert row.artifact_type == "model_artifact"
    assert row.candidate_review_ready is True
    assert row.admission_gate.status == PASSED

    evidence = {item.key: item for item in row.evidence}
    assert evidence["production_data_proof"].present is True
    assert evidence["production_data_proof"].status == "present"
    assert evidence["production_data_proof"].ref == "pdp-qlib-20260520"
    assert evidence["entitlement"].present is True
    assert evidence["point_in_time"].present is True
    assert evidence["no_order_route"].present is True
    assert evidence["adapter_evidence"].present is True
    assert evidence["admission_packet"].status == PASSED

    payload = packet.to_dict()
    assert payload["read_model_kind"] == "research_activation_dashboard"
    assert payload["summary"]["evidence_present_counts"]["admission_packet"] == 1
    assert payload["adapters"][0]["admission_gate"]["errors"] == []


def test_handoff_packet_fails_closed_when_admission_packet_is_missing() -> None:
    record = _adapter_record("trl")
    record.pop("admission_packet")

    packet = build_research_activation_handoff_packet([record])
    row = packet.adapters[0]

    assert packet.summary.readiness_state == BLOCKED
    assert packet.summary.ready_adapters == 0
    assert packet.summary.missing_gate_adapters == 1
    assert row.activation_tier == "R3"
    assert row.admission_gate.status == MISSING
    assert row.candidate_review_ready is False
    assert "missing_admission_packet" in row.blocking_reasons

    evidence = {item.key: item for item in row.evidence}
    assert evidence["production_data_proof"].present is True
    assert evidence["admission_packet"].present is False
    assert packet.summary.evidence_missing_counts["admission_packet"] == 1


def test_handoff_packet_marks_invalid_proof_evidence_without_mutating_input() -> None:
    record = _adapter_record("finrl")
    original = copy.deepcopy(record)
    record["production_data_proof"]["entitlement"] = {"allowed_use": ["research"]}
    record["production_data_proof"]["pit"] = {"point_in_time": False}

    packet = build_research_activation_handoff_packet([record])
    row = packet.adapters[0]

    assert row.candidate_review_ready is False
    assert "missing_license_scope" in row.blocking_reasons
    assert "pit_not_proven" in row.blocking_reasons

    evidence = {item.key: item for item in row.evidence}
    assert evidence["production_data_proof"].status == "failed"
    assert evidence["entitlement"].status == "failed"
    assert evidence["point_in_time"].status == "failed"
    assert original["production_data_proof"]["entitlement"]["license_scope"] == "research"


def test_handoff_packet_preserves_valid_r0_r7_tier_claims_per_adapter() -> None:
    qlib = _adapter_record("qlib")
    wandb = _adapter_record("wandb")
    qlib["activation_tier"] = "R7"
    wandb["activation_tier"] = "R0"

    packet = build_research_activation_handoff_packet([wandb, qlib])

    assert [row.adapter_kind for row in packet.adapters] == ["qlib", "wandb"]
    assert [row.activation_tier for row in packet.adapters] == ["R7", "R0"]
    assert packet.summary.tier_counts["R7"] == 1
    assert packet.summary.tier_counts["R0"] == 1


def _adapter_record(adapter_kind: str) -> dict:
    proof = _proof(adapter_kind)
    target_id = f"registry:{adapter_kind}:model:001"
    candidate = {
        "registry_id": target_id,
        "artifact_type": "model_artifact",
        "strategy_id": f"{adapter_kind}-strategy",
        "version": "1.0.0",
        "artifact_state": "draft",
        "checksum": "sha256:0123456789abcdef",
        "storage_ref": {
            "backend": "object_store",
            "path": f"object://pantheon-research/{adapter_kind}/artifact/model-001",
        },
        "lineage": {
            "source_run_ids": [f"run:{adapter_kind}:20260520"],
            "source_dataset_refs": proof["source_dataset_refs"],
            "source_strategy_spec_id": f"strategy-spec:{adapter_kind}:001",
        },
    }
    admission_packet = {
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
            "artifact_type": "model_artifact",
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
    return {
        "adapter_id": proof["adapter_id"],
        "adapter_kind": adapter_kind,
        "activation_tier": "R3",
        "production_data_proof": proof,
        "admission_packet": admission_packet,
    }


def _proof(adapter_kind: str) -> dict:
    return {
        "schema_version": PRODUCTION_DATA_PROOF_SCHEMA_VERSION,
        "proof_id": f"pdp-{adapter_kind}-20260520",
        "activation_tier": "R3",
        "created_at": "2026-05-20T00:00:00Z",
        "adapter_id": f"{adapter_kind}-adapter-prod-data-proof",
        "adapter_kind": adapter_kind,
        "source_dataset_refs": [
            f"dataset:{adapter_kind}-production-research-snapshot-20260520"
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
            "as_of": "2026-05-20T00:00:00Z",
            "last_ingested_at": "2026-05-20T00:05:00Z",
            "freshness_sla_seconds": 86400,
        },
        "pit": {
            "point_in_time": True,
            "event_time_field": "event_time",
            "available_time_field": "available_time",
            "source_watermark": "2026-05-20T00:00:00Z",
        },
        "storage": {
            "backend": "postgres+object_store",
            "dataset_ref": f"dataset:{adapter_kind}-production-research-snapshot-20260520",
            "snapshot_ref": f"snapshot:{adapter_kind}-20260520",
            "path": f"object://pantheon-research/{adapter_kind}/20260520",
            "checksum": "sha256:0123456789abcdef",
            "durable": True,
        },
        "audit": {
            "audit_ref": f"audit:{adapter_kind}-prod-data-proof",
            "ingest_run_id": f"ingest:{adapter_kind}-20260520",
            "normalization_run_id": f"normalize:{adapter_kind}-20260520",
            "evidence_bundle_ref": f"evidence-bundle:{adapter_kind}-prod-data-proof",
            "rate_limit_policy_ref": f"rate-limit:{adapter_kind}-research",
        },
        "controls": {
            "no_order_route": True,
            "produced_artifact_types": ["model_artifact", "evaluation_result"],
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
