"""Tests for the fail-closed external source management hosted acceptance verifier (SD-SRCM-08)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

import pytest

from scripts.verify_external_source_management_acceptance import (
    AcceptanceConfig,
    ExternalSourceManagementHostedAcceptanceVerifier,
    SourceManagementAcceptanceError,
    generate_canonical_evidence_bundle,
    main,
    EXPECTED_BFF_SHA,
    EXPECTED_FE_SHA,
    EXPECTED_SOURCE_DEFINITIONS_SHA,
    HOSTED_JOURNEY_IDS,
    NEGATIVE_CONTROL_KEYS,
    MIGRATION_REQUIREMENT_KEYS,
)


def _setup_valid_bundle(tmp_path: Path) -> Path:
    generate_canonical_evidence_bundle(tmp_path)
    return tmp_path


def _make_mock_transport(
    *,
    fe_status: int = 200,
    fe_sha: str = EXPECTED_FE_SHA,
    fe_real_writes: str = "false",
    bff_status: int = 200,
    bff_sha: str = EXPECTED_BFF_SHA,
    bff_auth_mode: str = "strict",
    bff_auth_stub: bool = False,
    source_defs_status: int = 200,
    source_defs_sha: str = EXPECTED_SOURCE_DEFINITIONS_SHA,
    unauth_status: int = 401,
    dev_login_bad_status: int = 401,
) -> Any:
    def transport(
        url: str,
        method: str = "GET",
        headers: Optional[Mapping[str, str]] = None,
        body: Optional[bytes] = None,
        timeout_seconds: float = 15.0,
    ) -> Tuple[int, Mapping[str, Any]]:
        if "deployment.json" in url:
            if fe_status != 200:
                return fe_status, {"error": "fe_error"}
            return 200, {
                "commit": fe_sha,
                "frontendSha": fe_sha,
                "bffCommit": "40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0",
                "buildMode": {
                    "VITE_BFF_MODE": "live",
                    "VITE_BFF_FALLBACK": "strict",
                    "VITE_BFF_REAL_WRITES": fe_real_writes,
                },
            }
        elif "/bff/version" in url:
            if bff_status != 200:
                return bff_status, {"error": "bff_error"}
            return 200, {
                "source_commit_sha": bff_sha,
                "commit": bff_sha,
                "environment": "dev",
                "config_posture": {
                    "auth_mode": bff_auth_mode,
                    "auth_stub": bff_auth_stub,
                    "dev_login_enabled": True,
                },
            }
        elif "/api/source-ingest/management/connector-definitions" in url or "/bff/management/data-sources/catalog" in url:
            if source_defs_status != 200:
                return source_defs_status, {"error": "defs_error"}
            return 200, {
                "definitions": [
                    {
                        "definition_id": "tw_official_market_daily",
                        "adapter_token": "tw_official",
                        "adapter_version": "1.0.0",
                        "provider": "TWSE",
                        "deployment_sha": source_defs_sha,
                        "fingerprint": "a" * 64,
                    }
                ]
            }
        elif "/bff/management/data-sources" in url:
            return unauth_status, {
                "error": {"code": "AUTH_REQUIRED", "message": "Unauthorized: missing Bearer token"},
            }
        elif "/bff/auth/dev-login" in url:
            return dev_login_bad_status, {
                "error": {"code": "AUTH_REQUIRED", "message": "Invalid dev login client credentials"},
            }
        return 200, {"status": "ok"}

    return transport


def test_verify_full_success_run_offline(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)
    result = verifier.run()

    assert result.passed is True
    assert result.task_id == "SRCM-P1-HOSTED-ACCEPTANCE-20260824"
    assert result.program_id == "SRCM-PHASE1-20260824"
    assert result.exact_pair["backend_sha"] == EXPECTED_BFF_SHA
    assert result.exact_pair["frontend_sha"] == EXPECTED_FE_SHA
    assert result.exact_pair["source_definitions_sha"] == EXPECTED_SOURCE_DEFINITIONS_SHA
    assert result.journeys["passed_count"] == len(HOSTED_JOURNEY_IDS)
    assert result.negative_controls["status"] == "passed"
    assert result.migration_rollout["status"] == "passed"
    assert result.browser_evidence["status"] == "passed"
    assert result.openclaw_boundary["openclaw_phase2_excluded"] is True
    assert len(result.artifact_checksums) >= 7


def test_verify_full_success_run_live_mock(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=False)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        config,
        transport=_make_mock_transport(),
    )
    result = verifier.run()

    assert result.passed is True
    assert result.exact_pair["backend_sha"] == EXPECTED_BFF_SHA
    assert result.exact_pair["frontend_sha"] == EXPECTED_FE_SHA
    assert result.exact_pair["source_definitions_sha"] == EXPECTED_SOURCE_DEFINITIONS_SHA
    assert result.journeys["passed_count"] == len(HOSTED_JOURNEY_IDS)


def test_live_verify_fe_unreachable_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=False)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        config,
        transport=_make_mock_transport(fe_status=500),
    )

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "live.fe_unreachable"


def test_live_verify_bff_unreachable_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=False)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        config,
        transport=_make_mock_transport(bff_status=503),
    )

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "live.bff_unreachable"


def test_live_verify_fe_sha_mismatch_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=False)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        config,
        transport=_make_mock_transport(fe_sha="1111111111111111111111111111111111111111"),
    )

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "live.fe_sha_mismatch"


def test_live_verify_bff_sha_mismatch_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=False)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        config,
        transport=_make_mock_transport(bff_sha="2222222222222222222222222222222222222222"),
    )

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "live.bff_sha_mismatch"


def test_live_verify_source_def_sha_mismatch_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=False)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        config,
        transport=_make_mock_transport(source_defs_sha="3333333333333333333333333333333333333333"),
    )

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "live.source_def_sha_mismatch"


def test_live_verify_unsafe_fe_write_defaults_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=False)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        config,
        transport=_make_mock_transport(fe_real_writes="true"),
    )

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "live.unsafe_fe_write_defaults"


def test_live_verify_insecure_bff_auth_posture_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=False)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        config,
        transport=_make_mock_transport(bff_auth_stub=True),
    )

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "live.insecure_bff_auth_posture"


def test_live_verify_negative_control_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=False)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        config,
        transport=_make_mock_transport(unauth_status=200),
    )

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "live.negative_control_failed"


def test_verify_missing_directory(tmp_path: Path) -> None:
    non_existent = tmp_path / "does_not_exist"
    config = AcceptanceConfig(evidence_dir=non_existent, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "evidence.missing_directory"


def test_verify_missing_file(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    (bundle_dir / "journey-receipts.json").unlink()

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "evidence.missing_file"


def test_verify_receipt_hash_tamper_fails_closed(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    receipts_file = bundle_dir / "journey-receipts.json"
    with receipts_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Tamper with the resulting revision without updating receipt_hash
    data["receipts"][0]["resulting_revision"] = 999
    with receipts_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "journeys.receipt_hash_mismatch"


def test_verify_network_exchange_removed_fails_closed(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    receipts_file = bundle_dir / "journey-receipts.json"
    with receipts_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    del data["receipts"][0]["observed_network_exchange"]
    with receipts_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code in ("journeys.missing_network_exchange", "journeys.receipt_hash_mismatch")


def test_verify_disproven_route_fails_closed(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    receipts_file = bundle_dir / "journey-receipts.json"
    with receipts_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Inject disproven route into journey 03 URL
    data["receipts"][2]["observed_network_exchange"]["request"]["url"] = "http://127.0.0.1:8001/bff/knowledge/search"
    # Recompute receipt hash so it passes hash check but fails disproven route check
    from scripts.verify_external_source_management_acceptance import _calculate_receipt_hash
    data["receipts"][2]["receipt_hash"] = _calculate_receipt_hash(data["receipts"][2])
    # Also update summary so cross-file matches
    summary_file = bundle_dir / "hosted-acceptance-summary.json"
    with summary_file.open("r", encoding="utf-8") as f:
        sdata = json.load(f)
    sdata["journeys"][2]["receipt_hash"] = data["receipts"][2]["receipt_hash"]
    sdata["journeys"][2]["observed_network_exchange"] = data["receipts"][2]["observed_network_exchange"]
    with summary_file.open("w", encoding="utf-8") as f:
        json.dump(sdata, f)
    with receipts_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "journeys.disproven_route"


def test_verify_action_status_not_202_fails_closed(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    receipts_file = bundle_dir / "journey-receipts.json"
    with receipts_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Change status of action journey 01 from 202 to 200
    data["receipts"][0]["observed_network_exchange"]["response"]["http_status"] = 200
    from scripts.verify_external_source_management_acceptance import _calculate_receipt_hash
    data["receipts"][0]["receipt_hash"] = _calculate_receipt_hash(data["receipts"][0])
    summary_file = bundle_dir / "hosted-acceptance-summary.json"
    with summary_file.open("r", encoding="utf-8") as f:
        sdata = json.load(f)
    sdata["journeys"][0]["receipt_hash"] = data["receipts"][0]["receipt_hash"]
    sdata["journeys"][0]["observed_network_exchange"] = data["receipts"][0]["observed_network_exchange"]
    with summary_file.open("w", encoding="utf-8") as f:
        json.dump(sdata, f)
    with receipts_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "journeys.invalid_http_status"


def test_verify_invalid_backend_sha(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    deploy_file = bundle_dir / "deployment.json"
    with deploy_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["exact_pair"]["backend_sha"] = "invalid_sha_short"
    with deploy_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
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

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
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

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
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

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
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

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
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

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "journeys.route_mocked"


def test_verify_journey_no_order_assertion_missing(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    summary_file = bundle_dir / "hosted-acceptance-summary.json"
    with summary_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["journeys"][0]["no_order_capital_route"] = False
    with summary_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "journeys.no_order_assertion_missing"


def test_verify_negative_controls_missing(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    neg_file = bundle_dir / "negative-controls.json"
    with neg_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    del data["negative_controls"]["unauthorized_mutation_rejected"]
    with neg_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
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

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
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
    from scripts.verify_external_source_management_acceptance import _calculate_receipt_hash
    data["receipts"][0]["receipt_hash"] = _calculate_receipt_hash(data["receipts"][0])
    summary_file = bundle_dir / "hosted-acceptance-summary.json"
    with summary_file.open("r", encoding="utf-8") as f:
        sdata = json.load(f)
    sdata["journeys"][0]["receipt_hash"] = data["receipts"][0]["receipt_hash"]
    with summary_file.open("w", encoding="utf-8") as f:
        json.dump(sdata, f)
    with receipts_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "security.raw_secret_leak"


def test_verify_browser_evidence_missing_journey(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    browser_file = bundle_dir / "browser-evidence.json"
    with browser_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["browser_journeys"] = [b for b in data["browser_journeys"] if b["journey_id"] != "journey_01_public_source_create_disabled"]
    with browser_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "browser_evidence.missing_journey"


def test_cli_main_success_and_output(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    out_file = tmp_path / "out.json"
    code = main(["--evidence-dir", str(bundle_dir), "--offline-only", "--output", str(out_file)])
    assert code == 0
    assert out_file.is_file()
    with out_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["passed"] is True


def test_cli_main_failure(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    code = main(["--evidence-dir", str(empty_dir), "--offline-only"])
    assert code == 1
