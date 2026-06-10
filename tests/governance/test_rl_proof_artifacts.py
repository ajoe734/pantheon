from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from services.governance.research_activation.no_order_route_scanner import scan_paths
from services.governance.research_activation.production_data_proof import (
    PRODUCTION_DATA_TIER,
    SCHEMA_VERSION,
    ProductionDataProof,
    ProductionDataProofError,
    validate_production_data_proof,
)
from services.research.finrl.registry_admission_packet import (
    validate_admission_packet as validate_finrl_admission_packet,
)
from services.research.rllib.registry_admission_packet import (
    validate_admission_packet as validate_rllib_admission_packet,
)


ROOT = Path(__file__).resolve().parents[2]
RL_EVIDENCE_DIR = ROOT / "support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001"
FINRL_ADMISSION_PATH = ROOT / "support/evidence/OSS-FINRL-V2-001/admission_packet.json"
RLLIB_ADMISSION_PATH = ROOT / "support/evidence/OSS-RLLIB-V2-001/admission_packet.json"


def test_finrl_no_order_route_proof_maps_smoke_evidence_to_r3_schema() -> None:
    proof = validate_production_data_proof(_rl_production_data_proof("finrl"))
    payload = proof.to_dict()

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["activation_tier"] == PRODUCTION_DATA_TIER
    assert payload["adapter_id"] == "finrl-no-order-route-proof-20260502"
    assert payload["adapter_kind"] == "finrl"
    assert payload["source_dataset_refs"] == ["synthetic-ohlcv://bounded-fixture/30-periods"]
    assert payload["no_order_route"]["produced_artifact_types"] == [
        "model_artifact",
        "evaluation_result",
        "candidate_packet",
        "registry_admission_packet",
    ]
    assert payload["no_order_route"]["execution_targets"] == [
        "research",
        "registry_review",
    ]
    metadata = payload["adapter_evidence"][0]["metadata"]
    assert metadata["real_backend_status"] == "dependency_or_config_error"
    assert metadata["real_backend_cause_message"] == "No module named 'finrl'"
    assert metadata["silent_stub_fallback"] is False
    assert metadata["order_routing_enabled"] is False
    assert metadata["admission_can_proceed"] is True


def test_rllib_research_only_admission_maps_smoke_evidence_to_r3_schema() -> None:
    proof = validate_production_data_proof(_rl_production_data_proof("rllib"))
    payload = proof.to_dict()

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["activation_tier"] == PRODUCTION_DATA_TIER
    assert payload["adapter_id"] == "rllib-research-only-admission-20260502"
    assert payload["adapter_kind"] == "rllib"
    assert payload["source_dataset_refs"] == ["synthetic-ohlcv://bounded-fixture/30-periods"]
    metadata = payload["adapter_evidence"][0]["metadata"]
    assert metadata["real_backend_status"] == "dependency_or_config_error"
    assert metadata["real_backend_cause_message"] == "No module named 'ray'"
    assert metadata["silent_stub_fallback"] is False
    assert metadata["order_routing_enabled"] is False
    assert metadata["admission_can_proceed"] is False
    assert metadata["admission_missing_evidence"] == [
        "upstream_rllib_ppo_backend_confirmed"
    ]


def test_rl_proof_fails_closed_for_order_capable_output_and_target() -> None:
    payload = _rl_production_data_proof("rllib")
    payload["controls"]["produced_artifact_types"].append("order")
    payload["controls"]["execution_targets"].append("paper")

    result = ProductionDataProof.from_mapping(payload).validate()

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert "forbidden_adapter_output" in codes
    assert "order_capable_execution_target" in codes
    with pytest.raises(ProductionDataProofError, match="forbidden_adapter_output"):
        validate_production_data_proof(payload)


def test_finrl_and_rllib_admission_packets_are_research_only() -> None:
    finrl = _load_json(FINRL_ADMISSION_PATH)
    rllib = _load_json(RLLIB_ADMISSION_PATH)

    assert validate_finrl_admission_packet(finrl) == []
    assert validate_rllib_admission_packet(rllib) == []

    assert finrl["can_proceed"] is True
    assert finrl["missing_evidence"] == []
    assert rllib["can_proceed"] is False
    assert rllib["missing_evidence"] == ["upstream_rllib_ppo_backend_confirmed"]

    for packet in (finrl, rllib):
        assert packet["schema_version"] == "PromotionReadinessPacket.v1"
        assert packet["environment"] == "paper"
        assert packet["registry_request"]["current_artifact_state"] == "draft"
        assert packet["registry_request"]["requested_artifact_state"] == "candidate"
        assert packet["registry_request"]["approval_scope"] == "candidate_admission_review_only"
        assert packet["registry_request"]["registry_write_authority"] == "registry_service_only"
        assert packet["registry_request"]["registry_write_performed"] is False
        assert packet["registry_request"]["deployment_stage"] == "none"
        assert packet["candidate_artifact"]["artifact_state"] == "draft"
        assert packet["candidate_artifact"]["deployment_summary"]["current_stage"] == "none"
        assert packet["downstream_scope"]["registry_admission_packet_only"] is True
        assert packet["downstream_scope"]["registry_write_performed"] is False
        assert packet["downstream_scope"]["deployment_stage"] == "none"
        assert packet["downstream_scope"]["broker_session_opened"] is False
        assert packet["downstream_scope"]["order_route"] == "none"
        assert packet["downstream_scope"]["capital_binding"] == "none"
        assert packet["safety_assertions"]["no_order_route"] is True
        assert packet["safety_assertions"]["no_broker_session"] is True
        assert packet["safety_assertions"]["no_capital_binding"] is True
        assert packet["safety_assertions"]["no_registry_write"] is True


def test_finrl_and_rllib_static_no_order_scan_passes_adapter_roots() -> None:
    result = scan_paths(
        [
            ROOT / "services/research/finrl",
            ROOT / "services/research/rllib",
        ],
        repo_root=ROOT,
    )

    assert result.passed, result.to_dict()
    assert "services/research/finrl/engine/finrl_adapter.py" in result.checked_files
    assert "services/research/rllib/adapter/rllib_adapter.py" in result.checked_files
    assert "services/research/rllib/production_ppo_run.py" in result.checked_files


def test_rl_proof_documents_exist_and_cite_reviewed_evidence() -> None:
    finrl_doc = (ROOT / "integrations/finrl/no_order_route_proof.md").read_text(
        encoding="utf-8"
    )
    rllib_doc = (ROOT / "integrations/rllib/research_only_admission.md").read_text(
        encoding="utf-8"
    )

    assert "ProductionDataProof.v1" in finrl_doc
    assert "support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/finrl_activation_evidence_summary.json" in finrl_doc
    assert "support/evidence/OSS-FINRL-V2-001/admission_packet.json" in finrl_doc
    assert "No module named 'finrl'" in finrl_doc
    assert "`downstream_scope.registry_admission_packet_only` | true" in finrl_doc

    assert "ProductionDataProof.v1" in rllib_doc
    assert "support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/rllib_activation_evidence_summary.json" in rllib_doc
    assert "support/evidence/OSS-RLLIB-V2-001/admission_packet.json" in rllib_doc
    assert "No module named 'ray'" in rllib_doc
    assert "`missing_evidence` | `upstream_rllib_ppo_backend_confirmed`" in rllib_doc
    assert "`can_proceed` | false" in rllib_doc


def _rl_production_data_proof(adapter_kind: str) -> dict:
    summary = _load_rl_json(f"{adapter_kind}_activation_evidence_summary.json")
    artifact_bundle = _load_rl_json(f"{adapter_kind}_artifact_bundle.json")
    manifest = _load_rl_json("manifest.json")
    real_backend = _load_rl_json(f"{adapter_kind}_real_backend_attempt.json")
    registry_entry = _load_rl_json(f"{adapter_kind}_registry_entry.json")
    candidate_packet = _load_rl_json(f"{adapter_kind}_candidate_packet.json")
    admission_path = FINRL_ADMISSION_PATH if adapter_kind == "finrl" else RLLIB_ADMISSION_PATH
    admission_packet = _load_json(admission_path)

    source_ref = summary["source_ref"]
    checksum_key = f"{adapter_kind}_artifact_bundle.json"
    evidence_dir = "support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001"
    adapter_id = {
        "finrl": "finrl-no-order-route-proof-20260502",
        "rllib": "rllib-research-only-admission-20260502",
    }[adapter_kind]

    return {
        "schema_version": SCHEMA_VERSION,
        "proof_id": f"pdp-{adapter_id}",
        "activation_tier": PRODUCTION_DATA_TIER,
        "created_at": summary["created_at"],
        "adapter_id": adapter_id,
        "adapter_kind": adapter_kind,
        "source_dataset_refs": [source_ref],
        "provider": {
            "name": "Pantheon bounded RL OHLCV fixture",
            "source_class": "research_grade",
            "dataset_id": artifact_bundle["dataset_summary"]["dataset_id"],
        },
        "entitlement": {
            "entitlement_ref": f"entitlement:{adapter_kind}-bounded-rl-research",
            "entitlement_tags": [adapter_kind, "rl", "bounded-fixture"],
            "license_scope": "internal_research",
            "allowed_use": ["research", "model_training", "evaluation"],
            "restrictions": ["no_order_routing", "no_live_execution"],
        },
        "freshness": {
            "status": "fresh",
            "as_of": summary["created_at"],
            "last_ingested_at": summary["created_at"],
            "freshness_sla_seconds": 86400,
        },
        "point_in_time": {
            "point_in_time": True,
            "event_time_field": "date",
            "available_time_field": "created_at",
            "source_watermark": summary["created_at"],
        },
        "storage": {
            "backend": "object_store",
            "dataset_ref": source_ref,
            "snapshot_ref": f"{evidence_dir}/{checksum_key}",
            "path": f"{evidence_dir}/",
            "checksum": manifest["checksums"][checksum_key],
            "durable": True,
        },
        "audit": {
            "audit_ref": "support/reviews/P2-RL-UPSTREAM-RUNTIME-SMOKE-001-claude-review.md",
            "ingest_run_id": f"P2-RL-UPSTREAM-RUNTIME-SMOKE-001:{adapter_kind}_activation_smoke",
            "normalization_run_id": f"{adapter_kind}-bounded-rl-ohclv-adapter",
            "evidence_bundle_ref": f"{evidence_dir}/",
            "rate_limit_policy_ref": "not-applicable-internal-bounded-fixture",
        },
        "controls": {
            "no_order_route": True,
            "produced_artifact_types": [
                "model_artifact",
                "evaluation_result",
                "candidate_packet",
                "registry_admission_packet",
            ],
            "execution_targets": ["research", "registry_review"],
            "attempted_mutation_types": [],
        },
        "adapter_evidence": [
            {
                "adapter_id": adapter_id,
                "adapter_kind": adapter_kind,
                "backend": f"{real_backend['backend']}_attempt+{summary['handoff_backend']}_handoff",
                "evidence_refs": [
                    {
                        "key": "activation_evidence_summary",
                        "ref_type": "artifact",
                        "path": f"{evidence_dir}/{adapter_kind}_activation_evidence_summary.json",
                        "checksum": manifest["checksums"][f"{adapter_kind}_activation_evidence_summary.json"],
                        "status": "provided",
                    },
                    {
                        "key": "artifact_bundle",
                        "ref_type": "artifact",
                        "path": f"{evidence_dir}/{adapter_kind}_artifact_bundle.json",
                        "checksum": manifest["checksums"][f"{adapter_kind}_artifact_bundle.json"],
                        "status": "provided",
                    },
                    {
                        "key": "candidate_packet",
                        "ref_type": "artifact",
                        "path": f"{evidence_dir}/{adapter_kind}_candidate_packet.json",
                        "checksum": manifest["checksums"][f"{adapter_kind}_candidate_packet.json"],
                        "status": candidate_packet["gate_state"],
                    },
                    {
                        "key": "registry_entry",
                        "ref_type": "artifact",
                        "path": f"{evidence_dir}/{adapter_kind}_registry_entry.json",
                        "checksum": manifest["checksums"][f"{adapter_kind}_registry_entry.json"],
                        "status": registry_entry["artifact_state"],
                    },
                    {
                        "key": "real_backend_attempt",
                        "ref_type": "artifact",
                        "path": f"{evidence_dir}/{adapter_kind}_real_backend_attempt.json",
                        "checksum": manifest["checksums"][f"{adapter_kind}_real_backend_attempt.json"],
                        "status": real_backend["status"],
                    },
                    {
                        "key": "registry_admission_packet",
                        "ref_type": "artifact",
                        "path": str(admission_path.relative_to(ROOT)),
                        "status": "provided",
                    },
                ],
                "metadata": {
                    "handoff_backend": summary["handoff_backend"],
                    "real_backend_status": real_backend["status"],
                    "real_backend_cause_message": real_backend["cause_message"],
                    "silent_stub_fallback": real_backend["silent_stub_fallback"],
                    "gate_state": summary["governance_boundary"]["gate_state"],
                    "deployment_stage": summary["governance_boundary"]["deployment_stage"],
                    "order_routing_enabled": summary["governance_boundary"]["order_routing_enabled"],
                    "broker_session_enabled": summary["governance_boundary"]["broker_session_enabled"],
                    "capital_binding": summary["governance_boundary"]["capital_binding"],
                    "admission_can_proceed": admission_packet["can_proceed"],
                    "admission_missing_evidence": admission_packet["missing_evidence"],
                },
            }
        ],
    }


def _load_rl_json(name: str) -> dict:
    return _load_json(RL_EVIDENCE_DIR / name)


def _load_json(path: Path) -> dict:
    return copy.deepcopy(json.loads(path.read_text(encoding="utf-8")))
