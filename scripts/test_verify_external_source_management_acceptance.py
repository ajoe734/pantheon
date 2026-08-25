"""Tests for the fail-closed external source management hosted acceptance verifier (SD-SRCM-08)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from scripts.verify_external_source_management_acceptance import (
    AcceptanceConfig,
    ExternalSourceManagementHostedAcceptanceVerifier,
    SourceManagementAcceptanceError,
    generate_canonical_evidence_bundle,
    main,
    HOSTED_JOURNEY_IDS,
    NEGATIVE_CONTROL_KEYS,
    MIGRATION_REQUIREMENT_KEYS,
)


def _setup_valid_bundle(tmp_path: Path) -> Path:
    generate_canonical_evidence_bundle(tmp_path)
    return tmp_path


def test_verify_full_success_run(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)
    result = verifier.run()

    assert result.passed is True
    assert result.task_id == "SRCM-P1-HOSTED-ACCEPTANCE-20260824"
    assert result.program_id == "SRCM-PHASE1-20260824"
    assert result.exact_pair["backend_sha"] == "40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0"
    assert result.exact_pair["frontend_sha"] == "5447d2a09b5c83a4f9ee2d405f57c642913e0055"
    assert result.journeys["passed_count"] == len(HOSTED_JOURNEY_IDS)
    assert result.negative_controls["status"] == "passed"
    assert result.migration_rollout["status"] == "passed"
    assert result.openclaw_boundary["openclaw_phase2_excluded"] is True
    assert len(result.artifact_checksums) >= 6


def test_verify_missing_directory(tmp_path: Path) -> None:
    non_existent = tmp_path / "does_not_exist"
    config = AcceptanceConfig(evidence_dir=non_existent)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "evidence.missing_directory"


def test_verify_missing_file(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    (bundle_dir / "journey-receipts.json").unlink()

    config = AcceptanceConfig(evidence_dir=bundle_dir)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "evidence.missing_file"


def test_verify_invalid_backend_sha(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    deploy_file = bundle_dir / "deployment.json"
    with deploy_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["exact_pair"]["backend_sha"] = "invalid_sha_short"
    with deploy_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "identity.invalid_backend_sha"


def test_verify_invalid_frontend_sha(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    deploy_file = bundle_dir / "deployment.json"
    with deploy_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["exact_pair"]["frontend_sha"] = "invalid_frontend_sha"
    with deploy_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "identity.invalid_frontend_sha"


def test_verify_invalid_store_backend(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    deploy_file = bundle_dir / "deployment.json"
    with deploy_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["feature_posture"]["SOURCE_MANAGEMENT_STORE_BACKEND"] = "invalid_backend"
    with deploy_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "posture.invalid_store_backend"


def test_verify_journey_missing(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    summary_file = bundle_dir / "hosted-acceptance-summary.json"
    with summary_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["journeys"] = [j for j in data["journeys"] if j["journey_id"] != "journey_01_public_source_create_disabled"]
    with summary_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "journeys.missing_journey"


def test_verify_journey_failed(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    summary_file = bundle_dir / "hosted-acceptance-summary.json"
    with summary_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["journeys"][0]["status"] = "failed"
    with summary_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "journeys.failed_journey"


def test_verify_journey_route_mocked_rejected(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    summary_file = bundle_dir / "hosted-acceptance-summary.json"
    with summary_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["journeys"][0]["route_mocked"] = True
    with summary_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "journeys.route_mocked"


def test_verify_negative_controls_missing(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    neg_file = bundle_dir / "negative-controls.json"
    with neg_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    del data["negative_controls"]["unauthorized_mutation_rejected"]
    with neg_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "negative_controls.missing_key"


def test_verify_migration_requirement_failed(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    mig_file = bundle_dir / "migration-rollout-rollback.json"
    with mig_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["requirements"]["idempotent_table_creation"]["status"] = "failed"
    with mig_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "migration.requirement_failed"


def test_verify_raw_secret_leak_fails_closed(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    receipts_file = bundle_dir / "journey-receipts.json"
    with receipts_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["receipts"][0]["leaked_api_key"] = "sk-live-raw-secret-1234567890"
    with receipts_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "security.raw_secret_leak"


def test_cli_main_success_and_output(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    out_file = tmp_path / "out.json"
    code = main(["--evidence-dir", str(bundle_dir), "--output", str(out_file)])
    assert code == 0
    assert out_file.is_file()
    with out_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["passed"] is True


def test_cli_main_failure(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    code = main(["--evidence-dir", str(empty_dir)])
    assert code == 1
