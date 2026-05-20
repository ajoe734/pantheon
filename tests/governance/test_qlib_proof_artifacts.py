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
from services.governance.research_activation.oos_runner import (
    SCHEMA_VERSION as OOS_SCHEMA_VERSION,
    assert_oos_no_order_route,
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
ADMISSION_PACKET_PATH = ROOT / "support/evidence/OSS-QLIB-V2-001/admission_packet.json"


def test_qlib_production_data_proof_maps_mgmt_manifest_to_r3_schema() -> None:
    proof = validate_production_data_proof(_qlib_production_data_proof())
    payload = proof.to_dict()

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["activation_tier"] == PRODUCTION_DATA_TIER
    assert payload["adapter_kind"] == "qlib"
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
        "model_artifact",
        "evaluation_result",
        "registry_admission_packet",
        "candidate_packet",
    ]
    assert payload["adapter_evidence"][0]["metadata"]["num_instruments"] == 50
    assert payload["adapter_evidence"][0]["metadata"]["history_years"] == 2.0096


def test_qlib_production_data_proof_fails_closed_for_pit_and_order_route() -> None:
    payload = _qlib_production_data_proof()
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


def test_qlib_rolling_oos_packet_enters_candidate_review_only() -> None:
    packet = _research_admission_packet()
    result = require_candidate_admission(packet)

    assert result.passed is True
    assert result.target_id == "qlib-production-rolling-tw-cross-sectional-equity-alpha-2.0.0"
    assert result.artifact_type == "model_artifact"
    assert result.proof_id == "pdp-qlib-twse-ohlcv-20260105"
    assert result.production_data_tier == PRODUCTION_DATA_TIER

    source_packet = _load_json(ADMISSION_PACKET_PATH)
    assert source_packet["schema_version"] == "PromotionReadinessPacket.v1"
    assert source_packet["missing_evidence"] == []
    assert source_packet["can_proceed"] is True
    assert source_packet["rolling_metric_summary"]["window_count"] == 457
    assert source_packet["rolling_metric_summary"]["mean_rolling_sharpe"] == 0.115705
    assert source_packet["rolling_metric_summary"]["mean_rolling_ic"] == 0.005935
    assert source_packet["downstream_scope"]["registry_write_performed"] is False
    assert source_packet["downstream_scope"]["order_route"] == "none"
    assert source_packet["safety_assertions"]["no_order_route"] is True


def test_qlib_rolling_oos_packet_fails_closed_for_deployment_or_order_scope() -> None:
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


def test_qlib_oos_no_order_route_harness_accepts_packet_replay() -> None:
    admission_packet = _load_json(ADMISSION_PACKET_PATH)

    proof = assert_oos_no_order_route(
        lambda: {
            "rolling_metric_summary": admission_packet["rolling_metric_summary"],
            "model_artifact_ref": admission_packet["model_artifact_ref"],
            "registry_request": admission_packet["registry_request"],
        },
        adapter_id="qlib-production-rolling-oos-20260517",
        adapter_kind="qlib",
        produced_artifact_types=(
            "model_artifact",
            "evaluation_result",
            "registry_admission_packet",
        ),
        adapter_paths=(ROOT / "services/research/qlib",),
        repo_root=ROOT,
        evidence_refs=(
            "support/evidence/OSS-QLIB-V2-001/admission_packet.json",
            "services/research/qlib/production_rolling_run.py",
        ),
        label="qlib_rolling_oos_packet_replay",
        metadata={"activation_tier": "R5", "source_task_id": "OSS-QLIB-V2-001"},
    )
    payload = proof.to_dict()

    assert payload["schema_version"] == OOS_SCHEMA_VERSION
    assert payload["no_order_route"] is True
    assert payload["static_scan"]["passed"] is True
    assert "services/research/qlib/production_rolling_run.py" in payload["static_scan"]["checked_files"]
    assert payload["dynamic_probe"]["passed"] is True
    assert payload["dynamic_probe"]["broker_outbox_count"] == 0
    assert payload["produced_artifact_types"] == [
        "model_artifact",
        "evaluation_result",
        "registry_admission_packet",
    ]


def test_qlib_proof_documents_exist_and_cite_reviewed_evidence() -> None:
    production_doc = (ROOT / "integrations/qlib/production_data_proof.md").read_text(
        encoding="utf-8"
    )
    rolling_doc = (ROOT / "integrations/qlib/rolling_oos_admission_packet.md").read_text(
        encoding="utf-8"
    )

    assert "ProductionDataProof.v1" in production_doc
    assert "support/evidence/MGMT-QLIB-001/dataset_manifest.json" in production_doc
    assert "ENT-TWSE-OPENAPI-RESEARCH-2024-001" in production_doc
    assert "sha256:fda828e5504eb3b4ae962bd2982c62b5c7b9075f7d2aa44ab197077f02146662" in production_doc
    assert "PromotionReadinessPacket.v1" in rolling_doc
    assert "support/evidence/OSS-QLIB-V2-001/admission_packet.json" in rolling_doc
    assert "457 windows" in rolling_doc
    assert "draft_to_candidate" in rolling_doc
    assert "registry_write_performed=false" in rolling_doc


def _research_admission_packet() -> dict:
    packet = copy.deepcopy(_load_json(ADMISSION_PACKET_PATH))
    packet["production_data_proof"] = _qlib_production_data_proof()
    return packet


def _qlib_production_data_proof() -> dict:
    manifest = _load_json(DATASET_MANIFEST_PATH)
    admission_packet = _load_json(ADMISSION_PACKET_PATH)
    manifest_proof = manifest["production_dataset_proof"]
    dataset = manifest["governed_dataset"]

    return {
        "schema_version": SCHEMA_VERSION,
        "proof_id": "pdp-qlib-twse-ohlcv-20260105",
        "activation_tier": PRODUCTION_DATA_TIER,
        "created_at": manifest["created_at"],
        "adapter_id": "qlib-production-data-proof-20260105",
        "adapter_kind": "qlib",
        "source_dataset_refs": manifest["governed_dataset"]["source_dataset_refs"],
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
            "audit_ref": "support/evidence/MGMT-QLIB-001/dataset_manifest.json",
            "ingest_run_id": manifest_proof["audit"]["ingest_run_id"],
            "normalization_run_id": manifest_proof["audit"]["normalization_run_id"],
            "evidence_bundle_ref": manifest_proof["audit"]["evidence_bundle_ref"],
            "rate_limit_policy_ref": manifest_proof["audit"]["rate_limit_policy_ref"],
        },
        "controls": {
            "no_order_route": True,
            "produced_artifact_types": [
                "model_artifact",
                "evaluation_result",
                "registry_admission_packet",
                "candidate_packet",
            ],
            "execution_targets": ["research", "registry_review"],
            "attempted_mutation_types": [],
        },
        "adapter_evidence": [
            {
                "adapter_id": "qlib-production-data-proof-20260105",
                "adapter_kind": "qlib",
                "backend": "qlib_production_rolling",
                "evidence_refs": [
                    {
                        "key": "dataset_manifest",
                        "ref_type": "artifact",
                        "path": "support/evidence/MGMT-QLIB-001/dataset_manifest.json",
                        "status": "provided",
                    },
                    {
                        "key": "rolling_oos_admission_packet",
                        "ref_type": "artifact",
                        "path": "support/evidence/OSS-QLIB-V2-001/admission_packet.json",
                        "checksum": admission_packet["model_artifact_ref"]["checksum"],
                        "status": "passed",
                    },
                ],
                "metadata": {
                    "manifest_id": manifest["manifest_id"],
                    "num_instruments": dataset["num_instruments"],
                    "history_years": dataset["history_years"],
                    "min_periods_per_instrument": admission_packet["production_dataset"][
                        "min_periods_per_instrument"
                    ],
                    "rolling_window_count": admission_packet["rolling_metric_summary"][
                        "window_count"
                    ],
                    "mean_rolling_sharpe": admission_packet["rolling_metric_summary"][
                        "mean_rolling_sharpe"
                    ],
                    "mean_rolling_ic": admission_packet["rolling_metric_summary"][
                        "mean_rolling_ic"
                    ],
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
