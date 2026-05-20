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


ROOT = Path(__file__).resolve().parents[2]
DATASET_MANIFEST_PATH = ROOT / "support/evidence/MGMT-QLIB-001/dataset_manifest.json"
ADMISSION_PACKET_PATH = ROOT / "support/evidence/OSS-STAT-V2-001/admission_packet.json"


def test_statsmodels_cointegration_production_evidence_maps_to_r3_schema() -> None:
    proof = validate_production_data_proof(_statsmodels_production_data_proof())
    payload = proof.to_dict()

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["activation_tier"] == PRODUCTION_DATA_TIER
    assert payload["adapter_kind"] == "statsmodels"
    assert payload["source_dataset_refs"] == [
        "dataset:tw-equity-ohlcv-top50-2024-daily"
    ]
    assert payload["entitlement"]["entitlement_ref"] == (
        "ENT-TWSE-OPENAPI-RESEARCH-2024-001"
    )
    assert payload["point_in_time"]["event_time_field"] == "date"
    assert payload["point_in_time"]["available_time_field"] == "ingestion_timestamp"
    assert payload["storage"]["durable"] is True
    assert payload["storage"]["checksum"].startswith("sha256:")
    assert payload["no_order_route"]["produced_artifact_types"] == [
        "signal_snapshot",
        "registry_admission_packet",
        "candidate_packet",
    ]
    assert payload["adapter_evidence"][0]["metadata"]["pair_count"] == 10
    assert payload["adapter_evidence"][0]["metadata"]["cointegrated_pair_count"] == 10
    assert payload["adapter_evidence"][0]["metadata"]["best_pair_id"] == (
        "TWSE_0004/TWSE_0044"
    )


def test_statsmodels_production_evidence_fails_closed_for_order_route_output() -> None:
    payload = _statsmodels_production_data_proof()
    payload["controls"]["produced_artifact_types"].append("broker_order_route")
    payload["controls"]["execution_targets"].append("live")

    result = ProductionDataProof.from_mapping(payload).validate()

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert "forbidden_adapter_output" in codes
    assert "order_capable_execution_target" in codes
    with pytest.raises(ProductionDataProofError, match="forbidden_adapter_output"):
        validate_production_data_proof(payload)


def test_statsmodels_admission_packet_enters_candidate_review_only() -> None:
    packet = _research_admission_packet()
    result = require_candidate_admission(packet)

    assert result.passed is True
    assert result.target_id == (
        "statsmodels-production-cointegration-tw-cross-sectional-equity-alpha-2.0.0"
    )
    assert result.artifact_type == "signal_snapshot"
    assert result.proof_id == "pdp-statsmodels-cointegration-twse-20260105"
    assert result.production_data_tier == PRODUCTION_DATA_TIER

    source_packet = _load_json(ADMISSION_PACKET_PATH)
    assert source_packet["schema_version"] == "PromotionReadinessPacket.v1"
    assert source_packet["missing_evidence"] == []
    assert source_packet["can_proceed"] is True
    assert source_packet["cointegration_summary"]["pair_count"] == 10
    assert source_packet["cointegration_summary"]["cointegrated_pair_count"] == 10
    assert source_packet["cointegration_summary"]["best_p_value"] == 0.0055868352
    assert source_packet["registry_request"]["requested_transition"] == "draft_to_candidate"
    assert source_packet["registry_request"]["registry_write_performed"] is False
    assert source_packet["downstream_scope"]["order_route"] == "none"
    assert source_packet["safety_assertions"]["no_order_route"] is True


def test_statsmodels_admission_packet_fails_closed_for_deployment_or_order_scope() -> None:
    packet = _research_admission_packet()
    packet["registry_request"]["deployment_stage"] = "paper"
    packet["downstream_scope"]["order_route"] = "broker"
    packet["safety_assertions"]["no_order_route"] = False

    result = evaluate_candidate_admission(packet)

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert "deployment_stage_not_none" in codes
    assert "order_route_not_none" in codes
    assert "no_order_route_not_asserted" in codes
    with pytest.raises(AdmissionGateError, match="deployment_stage_not_none"):
        require_candidate_admission(packet)


def test_statsmodels_proof_documents_exist_and_cite_reviewed_evidence() -> None:
    production_doc = (
        ROOT / "integrations/statsmodels/cointegration_production_evidence.md"
    ).read_text(encoding="utf-8")
    admission_doc = (ROOT / "integrations/statsmodels/admission_proof.md").read_text(
        encoding="utf-8"
    )

    assert "ProductionDataProof.v1" in production_doc
    assert "support/evidence/OSS-STAT-V2-001/admission_packet.json" in production_doc
    assert "support/evidence/MGMT-QLIB-001/dataset_manifest.json" in production_doc
    assert "ENT-TWSE-OPENAPI-RESEARCH-2024-001" in production_doc
    assert "sha256:7f7049632dc13a004e88dfd484832389495c3a2c2172d2035b29ef89d94a0a7b" in production_doc
    assert "TWSE_0004/TWSE_0044" in production_doc
    assert "PromotionReadinessPacket.v1" in admission_doc
    assert "draft_to_candidate" in admission_doc
    assert "registry_write_performed=false" in admission_doc
    assert "artifact_state_request_limited_to_candidate" in admission_doc


def _research_admission_packet() -> dict:
    packet = copy.deepcopy(_load_json(ADMISSION_PACKET_PATH))
    packet["production_data_proof"] = _statsmodels_production_data_proof()
    return packet


def _statsmodels_production_data_proof() -> dict:
    manifest = _load_json(DATASET_MANIFEST_PATH)
    admission_packet = _load_json(ADMISSION_PACKET_PATH)
    manifest_proof = manifest["production_dataset_proof"]
    dataset = admission_packet["production_dataset"]
    candidate = admission_packet["candidate_artifact"]
    signal_ref = admission_packet["signal_snapshot_ref"]

    return {
        "schema_version": SCHEMA_VERSION,
        "proof_id": "pdp-statsmodels-cointegration-twse-20260105",
        "activation_tier": PRODUCTION_DATA_TIER,
        "created_at": admission_packet["generated_at"],
        "adapter_id": "statsmodels-cointegration-production-evidence-20260105",
        "adapter_kind": "statsmodels",
        "source_dataset_refs": admission_packet["production_dataset"]["source_dataset_refs"],
        "provider": {
            "name": manifest_proof["provider"]["name"],
            "source_class": manifest_proof["provider"]["source_class"],
            "dataset_id": manifest_proof["provider"]["dataset_id"],
        },
        "entitlement": copy.deepcopy(manifest_proof["entitlement"]),
        "freshness": copy.deepcopy(manifest_proof["freshness"]),
        "point_in_time": copy.deepcopy(manifest_proof["pit"]),
        "storage": copy.deepcopy(manifest_proof["storage"]),
        "audit": {
            "audit_ref": "support/evidence/OSS-STAT-V2-001/admission_packet.json",
            "ingest_run_id": manifest_proof["audit"]["ingest_run_id"],
            "normalization_run_id": manifest_proof["audit"]["normalization_run_id"],
            "evidence_bundle_ref": manifest_proof["audit"]["evidence_bundle_ref"],
            "rate_limit_policy_ref": manifest_proof["audit"]["rate_limit_policy_ref"],
        },
        "controls": {
            "no_order_route": True,
            "produced_artifact_types": [
                "signal_snapshot",
                "registry_admission_packet",
                "candidate_packet",
            ],
            "execution_targets": ["research", "registry_review"],
            "attempted_mutation_types": [],
        },
        "adapter_evidence": [
            {
                "adapter_id": "statsmodels-cointegration-production-evidence-20260105",
                "adapter_kind": "statsmodels",
                "backend": "statsmodels_production_cointegration",
                "evidence_refs": [
                    {
                        "key": "dataset_manifest",
                        "ref_type": "artifact",
                        "path": "support/evidence/MGMT-QLIB-001/dataset_manifest.json",
                        "status": "passed",
                    },
                    {
                        "key": "statsmodels_admission_packet",
                        "ref_type": "artifact",
                        "path": "support/evidence/OSS-STAT-V2-001/admission_packet.json",
                        "checksum": candidate["checksum"],
                        "status": "passed",
                    },
                    {
                        "key": "production_cointegration_runner",
                        "ref_type": "source",
                        "path": "services/research/statsmodels/production_cointegration.py",
                        "status": "provided",
                    },
                ],
                "metadata": {
                    "manifest_id": manifest["manifest_id"],
                    "num_instruments": dataset["num_instruments"],
                    "history_years": dataset["history_years"],
                    "min_periods_per_instrument": dataset["min_periods_per_instrument"],
                    "rolling_window": admission_packet["signal_snapshot_summary"][
                        "rolling_window"
                    ],
                    "pair_count": admission_packet["cointegration_summary"]["pair_count"],
                    "cointegrated_pair_count": admission_packet["cointegration_summary"][
                        "cointegrated_pair_count"
                    ],
                    "best_pair_id": admission_packet["cointegration_summary"][
                        "best_pair_id"
                    ],
                    "best_p_value": admission_packet["cointegration_summary"][
                        "best_p_value"
                    ],
                    "candidate_artifact_type": signal_ref["artifact_type"],
                    "candidate_checksum": candidate["checksum"],
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
