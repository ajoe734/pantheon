from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from services.governance.research_activation.admission_gate import (
    AdmissionGateError,
    evaluate_candidate_admission,
    require_candidate_admission,
)
from services.governance.research_activation.production_data_proof import (
    PRODUCTION_DATA_TIER,
    SCHEMA_VERSION,
    ProductionDataProof,
    ProductionDataProofError,
    validate_production_data_proof,
)
from services.research.quantlib.registry_admission_packet import (
    validate_admission_packet as validate_quantlib_admission_packet,
)


ROOT = Path(__file__).resolve().parents[2]
QUANTLIB_EVIDENCE_DIR = ROOT / "support/evidence/OSS-QUANTLIB-V2-001"
PRICING_SNAPSHOT_PATH = QUANTLIB_EVIDENCE_DIR / "pricing_snapshot.json"
ADMISSION_PACKET_PATH = QUANTLIB_EVIDENCE_DIR / "admission_packet.json"


def test_quantlib_pricing_evidence_retention_maps_snapshot_to_r3_schema() -> None:
    proof = validate_production_data_proof(_quantlib_production_data_proof())
    payload = proof.to_dict()

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["activation_tier"] == PRODUCTION_DATA_TIER
    assert payload["adapter_kind"] == "quantlib"
    assert payload["source_dataset_refs"] == [
        "dataset:txo-option-chain-fixture-2026-05"
    ]
    assert payload["provider"]["dataset_id"] == "txo-option-chain-fixture-2026-05"
    assert payload["entitlement"]["entitlement_ref"] == (
        "ENT-QUANTLIB-TXO-PRICING-RESEARCH-2026-05-17"
    )
    assert payload["point_in_time"]["event_time_field"] == "inputs.chain_definition.as_of"
    assert payload["point_in_time"]["available_time_field"] == "generated_at"
    assert payload["storage"]["durable"] is True
    assert payload["storage"]["checksum"] == (
        "sha256:80b1a323b3ce1f3fa5bdb35e20b8750e7c14c3d97fe7b06c36335ea205095b59"
    )
    assert payload["no_order_route"]["produced_artifact_types"] == [
        "pricing_snapshot",
        "evaluation_result",
        "registry_admission_packet",
        "candidate_packet",
    ]
    metadata = payload["adapter_evidence"][0]["metadata"]
    assert metadata["contract_count"] == 30
    assert metadata["strike_count"] == 5
    assert metadata["expiry_count"] == 3
    assert metadata["registry_write_performed"] is False
    assert metadata["order_route"] == "none"


def test_quantlib_admission_proof_enters_candidate_review_only() -> None:
    packet = _research_admission_packet()

    assert validate_quantlib_admission_packet(packet) == []

    result = require_candidate_admission(packet)

    assert result.passed is True
    assert result.target_id == "quantlib-production-option-chain-txo-2.0.0"
    assert result.artifact_type == "pricing_snapshot"
    assert result.proof_id == "pdp-quantlib-pricing-snapshot-20260517"
    assert result.production_data_tier == PRODUCTION_DATA_TIER

    source_packet = _load_json(ADMISSION_PACKET_PATH)
    assert source_packet["schema_version"] == "PromotionReadinessPacket.v1"
    assert source_packet["missing_evidence"] == []
    assert source_packet["can_proceed"] is True
    assert source_packet["registry_request"]["requested_transition"] == "draft_to_candidate"
    assert source_packet["registry_request"]["deployment_stage"] == "none"
    assert source_packet["downstream_scope"]["registry_write_performed"] is False
    assert source_packet["downstream_scope"]["order_route"] == "none"
    assert source_packet["safety_assertions"]["no_order_route"] is True


def test_quantlib_proof_fails_closed_for_pit_and_order_route() -> None:
    payload = _quantlib_production_data_proof()
    payload["point_in_time"]["point_in_time"] = False
    payload["controls"]["produced_artifact_types"].append("broker_order_route")
    payload["controls"]["execution_targets"].append("live")

    result = ProductionDataProof.from_mapping(payload).validate()

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert "pit_not_proven" in codes
    assert "forbidden_adapter_output" in codes
    assert "order_capable_execution_target" in codes
    with pytest.raises(ProductionDataProofError, match="pit_not_proven"):
        validate_production_data_proof(payload)


def test_quantlib_admission_gate_requires_pricing_snapshot_in_proof_outputs() -> None:
    packet = _research_admission_packet()
    packet["production_data_proof"]["controls"]["produced_artifact_types"] = [
        "evaluation_result",
        "registry_admission_packet",
        "candidate_packet",
    ]

    result = evaluate_candidate_admission(packet)

    assert result.passed is False
    assert "candidate_type_not_in_proof_outputs" in {
        issue.code for issue in result.errors
    }
    with pytest.raises(AdmissionGateError, match="candidate_type_not_in_proof_outputs"):
        require_candidate_admission(packet)


def test_quantlib_proof_documents_exist_and_cite_reviewed_evidence() -> None:
    retention_doc = (
        ROOT / "integrations/quantlib/pricing_evidence_retention.md"
    ).read_text(encoding="utf-8")
    admission_doc = (ROOT / "integrations/quantlib/admission_proof.md").read_text(
        encoding="utf-8"
    )

    assert "ProductionDataProof.v1" in retention_doc
    assert "support/evidence/OSS-QUANTLIB-V2-001/pricing_snapshot.json" in retention_doc
    assert "ENT-QUANTLIB-TXO-PRICING-RESEARCH-2026-05-17" in retention_doc
    assert "pricing_snapshot`, `evaluation_result`, `registry_admission_packet`, `candidate_packet`" in retention_doc
    assert "PromotionReadinessPacket.v1" in admission_doc
    assert "support/evidence/OSS-QUANTLIB-V2-001/admission_packet.json" in admission_doc
    assert "quantlib-production-option-chain-txo-2.0.0" in admission_doc
    assert "draft_to_candidate" in admission_doc
    assert "registry_write_performed=false" in admission_doc


def _research_admission_packet() -> dict:
    packet = copy.deepcopy(_load_json(ADMISSION_PACKET_PATH))
    packet["production_data_proof"] = _quantlib_production_data_proof()
    packet["downstream_scope"]["execution_targets"] = ["research", "registry_review"]
    return packet


def _quantlib_production_data_proof() -> dict:
    snapshot = _load_json(PRICING_SNAPSHOT_PATH)
    admission_packet = _load_json(ADMISSION_PACKET_PATH)
    chain_summary = snapshot["chain_summary"]
    checksum = snapshot["checksum"]
    source_dataset_refs = snapshot["registry_entry"]["lineage"]["source_dataset_refs"]

    return {
        "schema_version": SCHEMA_VERSION,
        "proof_id": "pdp-quantlib-pricing-snapshot-20260517",
        "activation_tier": PRODUCTION_DATA_TIER,
        "created_at": snapshot["generated_at"],
        "adapter_id": "quantlib-pricing-evidence-retention-20260517",
        "adapter_kind": "quantlib",
        "source_dataset_refs": source_dataset_refs,
        "provider": {
            "name": "Pantheon governed TXO option-chain fixture",
            "source_class": "production_research_fixture",
            "dataset_id": "txo-option-chain-fixture-2026-05",
        },
        "entitlement": {
            "entitlement_ref": "ENT-QUANTLIB-TXO-PRICING-RESEARCH-2026-05-17",
            "entitlement_tags": ["quantlib", "txo", "pricing-snapshot"],
            "license_scope": "internal_research",
            "allowed_use": ["research", "evaluation", "registry_review"],
            "restrictions": [
                "no_order_routing",
                "no_live_execution",
                "no_broker_feed_claim",
            ],
        },
        "freshness": {
            "status": "fresh",
            "as_of": snapshot["generated_at"],
            "last_ingested_at": snapshot["generated_at"],
            "freshness_sla_seconds": 86400,
        },
        "point_in_time": {
            "point_in_time": True,
            "event_time_field": "inputs.chain_definition.as_of",
            "available_time_field": "generated_at",
            "source_watermark": snapshot["generated_at"],
        },
        "storage": {
            "backend": "git_json_artifact",
            "dataset_ref": source_dataset_refs[0],
            "snapshot_ref": "support/evidence/OSS-QUANTLIB-V2-001/pricing_snapshot.json",
            "path": "support/evidence/OSS-QUANTLIB-V2-001/pricing_snapshot.json",
            "checksum": checksum,
            "durable": True,
        },
        "audit": {
            "audit_ref": "support/reviews/OSS-QUANTLIB-V2-001-review-codex2.md",
            "ingest_run_id": "OSS-QUANTLIB-V2-001:emit_pricing_snapshot",
            "normalization_run_id": "quantlib-production-option-chain:2.0.0",
            "evidence_bundle_ref": "support/evidence/OSS-QUANTLIB-V2-001/",
            "rate_limit_policy_ref": "not-applicable-deterministic-fixture",
        },
        "controls": {
            "no_order_route": True,
            "produced_artifact_types": [
                "pricing_snapshot",
                "evaluation_result",
                "registry_admission_packet",
                "candidate_packet",
            ],
            "execution_targets": ["research", "registry_review"],
            "attempted_mutation_types": [],
        },
        "adapter_evidence": [
            {
                "adapter_id": "quantlib-pricing-evidence-retention-20260517",
                "adapter_kind": "quantlib",
                "backend": "quantlib_production_option_chain",
                "evidence_refs": [
                    {
                        "key": "pricing_snapshot",
                        "ref_type": "artifact",
                        "path": "support/evidence/OSS-QUANTLIB-V2-001/pricing_snapshot.json",
                        "checksum": checksum,
                        "status": "passed",
                    },
                    {
                        "key": "registry_admission_packet",
                        "ref_type": "artifact",
                        "path": "support/evidence/OSS-QUANTLIB-V2-001/admission_packet.json",
                        "checksum": admission_packet["pricing_snapshot_summary"]["checksum"],
                        "status": "passed",
                    },
                    {
                        "key": "review_approval",
                        "ref_type": "review",
                        "path": "support/reviews/OSS-QUANTLIB-V2-001-review-codex2.md",
                        "status": "approved",
                    },
                ],
                "metadata": {
                    "snapshot_id": snapshot["snapshot_id"],
                    "target_id": admission_packet["target_id"],
                    "artifact_type": admission_packet["registry_request"]["artifact_type"],
                    "contract_count": chain_summary["contract_count"],
                    "strike_count": chain_summary["strike_count"],
                    "expiry_count": chain_summary["expiry_count"],
                    "call_count": chain_summary["call_count"],
                    "put_count": chain_summary["put_count"],
                    "greeks_required": ["price", "delta", "gamma", "vega", "theta"],
                    "admission_can_proceed": admission_packet["can_proceed"],
                    "registry_write_performed": admission_packet["downstream_scope"][
                        "registry_write_performed"
                    ],
                    "order_route": admission_packet["downstream_scope"]["order_route"],
                },
            }
        ],
    }


def _load_json(path: Path) -> dict:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded
