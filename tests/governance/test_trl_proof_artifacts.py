from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from services.governance.research_activation.disclosure_report import (
    BackendDisclosure,
    DisclosureReportError,
    ResearchBackendDisclosureReport,
    validate_disclosure_report,
)
from services.governance.research_activation.production_data_proof import (
    PRODUCTION_DATA_TIER,
    SCHEMA_VERSION,
    ProductionDataProof,
    ProductionDataProofError,
    validate_production_data_proof,
)


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = ROOT / "support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001"


def test_trl_preference_data_proof_maps_runtime_evidence_to_r3_schema() -> None:
    proof = validate_production_data_proof(_trl_production_data_proof())
    payload = proof.to_dict()

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["activation_tier"] == PRODUCTION_DATA_TIER
    assert payload["adapter_kind"] == "trl"
    assert payload["source_dataset_refs"] == ["feedback-store://bounded-fixture/240"]
    assert payload["no_order_route"]["produced_artifact_types"] == [
        "model_artifact",
        "evaluation_result",
        "candidate_packet",
    ]
    assert payload["adapter_evidence"][0]["metadata"]["preference_pair_count"] == 240
    assert payload["adapter_evidence"][0]["metadata"]["silent_stub_fallback"] is False


def test_trl_preference_data_proof_fails_closed_for_order_capable_output() -> None:
    payload = _trl_production_data_proof()
    payload["controls"]["produced_artifact_types"].append("order")
    payload["controls"]["execution_targets"].append("paper")

    result = ProductionDataProof.from_mapping(payload).validate()

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert "forbidden_adapter_output" in codes
    assert "order_capable_execution_target" in codes
    with pytest.raises(ProductionDataProofError, match="forbidden_adapter_output"):
        validate_production_data_proof(payload)


def test_trl_real_backend_attempt_records_dependency_error_without_silent_fallback() -> None:
    attempt = _load_json("real_backend_attempt.json")
    summary = _load_json("activation_evidence_summary.json")
    artifact_bundle = _load_json("artifact_bundle.json")
    candidate_packet = _load_json("candidate_packet.json")

    assert attempt["backend"] == "trl_dpo"
    assert attempt["status"] == "dependency_or_config_error"
    assert attempt["cause_type"] == "ModuleNotFoundError"
    assert attempt["cause_message"] == "No module named 'trl'"
    assert attempt["silent_stub_fallback"] is False
    assert summary["handoff_backend"] == "stub_dpo"
    assert artifact_bundle["evaluation_summary"]["backend"] == "stub_dpo"
    assert candidate_packet["candidate_registry_projection"]["metadata"]["training_backend"] == "stub_dpo"


def test_trl_backend_disclosure_fails_closed_for_silent_stub_fallback() -> None:
    bad = BackendDisclosure(
        adapter_id="trl",
        adapter_kind="trl",
        default_backend="stub_dpo",
        default_backend_kind="stub_mock",
        activation_state="real_backend_gated",
        real_backend="trl_dpo",
        real_backend_status="selectable_gated",
        stub_backend="stub_dpo",
        stub_backend_available=True,
        activation_gates=("PANTHEON_TRL_ACTIVATION_READY_ENABLED=1", "TRL_BACKEND=real"),
        evidence_refs=("services/learning/trl/activation_smoke.py",),
        silent_stub_fallback=True,
        can_route_orders=False,
    )
    report = ResearchBackendDisclosureReport(
        report_id="bad-trl-disclosure",
        generated_at="2026-05-19T00:00:00Z",
        adapters=(bad,),
    )

    result = report.validate()

    assert result.passed is False
    assert "silent_stub_fallback" in {issue.code for issue in result.errors}
    with pytest.raises(DisclosureReportError, match="silent_stub_fallback"):
        validate_disclosure_report(report)


def test_trl_proof_documents_exist_and_cite_reviewed_evidence() -> None:
    preference_doc = (ROOT / "integrations/trl/preference_data_proof.md").read_text(
        encoding="utf-8"
    )
    backend_doc = (ROOT / "integrations/trl/dpo_backend_evidence.md").read_text(
        encoding="utf-8"
    )

    assert "ProductionDataProof.v1" in preference_doc
    assert "support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/fb002_evidence_snapshot.json" in preference_doc
    assert "feedback-store://bounded-fixture/240" in preference_doc
    assert "No module named 'trl'" in backend_doc
    assert "silent_stub_fallback" in backend_doc
    assert "stub_dpo" in backend_doc


def _trl_production_data_proof() -> dict:
    summary = _load_json("activation_evidence_summary.json")
    snapshot = _load_json("fb002_evidence_snapshot.json")
    artifact_bundle = _load_json("artifact_bundle.json")
    manifest = _load_json("manifest.json")
    real_backend = _load_json("real_backend_attempt.json")
    source_ref = summary["source_ref"]
    query_window = snapshot["query_window"]

    return {
        "schema_version": SCHEMA_VERSION,
        "proof_id": "pdp-trl-preference-data-20260501",
        "activation_tier": PRODUCTION_DATA_TIER,
        "created_at": summary["created_at"],
        "adapter_id": "trl-preference-data-proof-20260501",
        "adapter_kind": "trl",
        "source_dataset_refs": [source_ref],
        "provider": {
            "name": "Pantheon governed FB-002 feedback store",
            "source_class": "production_research",
            "dataset_id": artifact_bundle["dataset_summary"]["dataset_id"],
        },
        "entitlement": {
            "entitlement_ref": "entitlement:internal-fb002-preference-learning",
            "entitlement_tags": ["fb002", "preference-learning", "operator-feedback"],
            "license_scope": "internal_research",
            "allowed_use": ["research", "model_training", "evaluation"],
            "restrictions": ["no_order_routing", "no_live_execution"],
        },
        "freshness": {
            "status": "fresh",
            "as_of": query_window["created_at_to"],
            "last_ingested_at": summary["created_at"],
            "freshness_sla_seconds": 86400,
        },
        "point_in_time": {
            "point_in_time": True,
            "event_time_field": "created_at",
            "available_time_field": "created_at",
            "source_watermark": query_window["created_at_to"],
        },
        "storage": {
            "backend": "feedback_store+object_store",
            "dataset_ref": source_ref,
            "snapshot_ref": "support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/fb002_evidence_snapshot.json",
            "path": "support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/",
            "checksum": manifest["checksum"],
            "durable": True,
        },
        "audit": {
            "audit_ref": "support/reviews/P2-TRL-RUNTIME-DATA-ACTIVATION-001-codex-review.md",
            "ingest_run_id": "P2-TRL-RUNTIME-DATA-ACTIVATION-001:activation_smoke",
            "normalization_run_id": "trl-preference-pair-adapter:fb002-runtime-data-activation-20260501",
            "evidence_bundle_ref": "support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/",
            "rate_limit_policy_ref": "not-applicable-internal-bounded-fixture",
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
                "adapter_id": "trl-preference-data-proof-20260501",
                "adapter_kind": "trl",
                "backend": "trl_dpo_attempt+stub_dpo_handoff",
                "evidence_refs": [
                    {
                        "key": "preference_dataset_snapshot",
                        "ref_type": "artifact",
                        "path": "support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/fb002_evidence_snapshot.json",
                        "status": "provided",
                    },
                    {
                        "key": "real_backend_attempt",
                        "ref_type": "artifact",
                        "path": "support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/real_backend_attempt.json",
                        "status": real_backend["status"],
                    },
                    {
                        "key": "artifact_bundle",
                        "ref_type": "artifact",
                        "path": "support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/artifact_bundle.json",
                        "checksum": manifest["checksum"],
                        "status": "provided",
                    },
                ],
                "metadata": {
                    "source_feedback_event_count": snapshot["source_feedback_event_count"],
                    "preference_pair_count": snapshot["preference_pair_count"],
                    "strategy_family_count": snapshot["strategy_family_count"],
                    "all_required_actions_present": snapshot["all_required_actions_present"],
                    "artifact_linkage_complete": snapshot["linkage"]["artifact_linkage_complete"],
                    "real_backend_status": real_backend["status"],
                    "silent_stub_fallback": real_backend["silent_stub_fallback"],
                },
            }
        ],
    }


def _load_json(name: str) -> dict:
    return copy.deepcopy(json.loads((EVIDENCE_DIR / name).read_text(encoding="utf-8")))
