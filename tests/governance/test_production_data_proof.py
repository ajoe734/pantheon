from __future__ import annotations

import pytest

from services.governance.research_activation.production_data_proof import (
    PRODUCTION_DATA_TIER,
    SCHEMA_VERSION,
    ProductionDataProof,
    ProductionDataProofError,
    validate_production_data_proof,
)


def test_production_data_proof_happy_path_serializes_r3_schema() -> None:
    proof = validate_production_data_proof(_proof("qlib"))
    payload = proof.to_dict()

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["activation_tier"] == PRODUCTION_DATA_TIER
    assert payload["adapter_kind"] == "qlib"
    assert payload["no_order_route"]["produced_artifact_types"] == [
        "model_artifact",
        "evaluation_result",
        "candidate_packet",
    ]
    assert payload["adapter_evidence"][0]["evidence_refs"][0]["key"] == "production_data_manifest"
    assert proof.validate().to_dict()["passed"] is True


@pytest.mark.parametrize(
    ("adapter_kind", "expected_kind"),
    [
        ("qlib", "qlib"),
        ("trl", "trl"),
        ("finrl", "finrl"),
        ("wandb", "wandb"),
        ("W&B", "wandb"),
    ],
)
def test_production_data_proof_is_generic_across_adapter_instances(
    adapter_kind: str,
    expected_kind: str,
) -> None:
    proof = ProductionDataProof.from_mapping(_proof(adapter_kind))
    result = proof.validate()

    assert result.passed is True
    assert proof.adapter_kind == expected_kind
    assert proof.adapter_evidence[0].adapter_kind == expected_kind


def test_production_data_proof_fails_closed_for_runtime_binding_output() -> None:
    payload = _proof("finrl")
    payload["controls"]["produced_artifact_types"] = [
        "model_artifact",
        "RuntimeBinding",
    ]

    proof = ProductionDataProof.from_mapping(payload)
    result = proof.validate()

    assert result.passed is False
    assert "forbidden_adapter_output" in {issue.code for issue in result.errors}
    with pytest.raises(ProductionDataProofError, match="forbidden_adapter_output"):
        validate_production_data_proof(payload)


def test_production_data_proof_fails_closed_without_license_and_pit() -> None:
    payload = _proof("trl")
    payload["entitlement"] = {"allowed_use": ["research"]}
    payload["pit"] = {"point_in_time": False}

    result = ProductionDataProof.from_mapping(payload).validate()

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert "missing_entitlement_ref" in codes
    assert "missing_license_scope" in codes
    assert "pit_not_proven" in codes


def _proof(adapter_kind: str) -> dict:
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
            "produced_artifact_types": [
                "model_artifact",
                "evaluation_result",
                "candidate_packet",
            ],
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
