import hashlib
import json
import shutil
import struct
import zlib
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

import pytest

from scripts.verify_external_source_management_acceptance import (
    AcceptanceConfig,
    ExternalSourceManagementHostedAcceptanceVerifier,
    SourceManagementAcceptanceError,
    main,
    DEFAULT_EVIDENCE_DIR,
    EXPECTED_BFF_SHA,
    EXPECTED_FE_SHA,
    EXPECTED_SOURCE_DEFINITIONS_SHA,
    UNSUPPORTED_READONLY_FE_BASELINE,
    HOSTED_JOURNEY_IDS,
    NEGATIVE_CONTROL_KEYS,
    MIGRATION_REQUIREMENT_KEYS,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    )


def _write_synthetic_screenshot(
    path: Path,
    *,
    red: int,
    green: int,
    blue: int,
    width: int = 640,
    height: int = 360,
) -> None:
    """Write a structurally valid, non-placeholder PNG used only inside tmp_path tests."""
    scanline = b"\x00" + bytes((red, green, blue)) * width
    pixels = scanline * height
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(pixels, level=9))
        + _png_chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def _bind_manifest_checksums(bundle_dir: Path) -> None:
    manifest_path = bundle_dir / "evidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "passed"
    for key, filename in {
        "deployment": "deployment.json",
        "functional_closure": "functional-closure-20260828.json",
        "hosted_summary": "hosted-acceptance-summary.json",
        "journey_receipts": "journey-receipts.json",
        "browser_evidence": "browser-evidence.json",
        "negative_controls": "negative-controls.json",
        "migration_rollout": "migration-rollout-rollback.json",
    }.items():
        manifest.setdefault("artifacts", {}).setdefault(key, {})["path"] = filename
        manifest["artifacts"][key]["sha256"] = _sha256(bundle_dir / filename)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _install_valid_browser_capture(bundle_dir: Path) -> None:
    receipts = json.loads((bundle_dir / "journey-receipts.json").read_text(encoding="utf-8"))["receipts"]
    screenshots = bundle_dir / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    har_entries = []
    browser_journeys = []

    for index, receipt in enumerate(receipts):
        journey_id = receipt["journey_id"]
        exchange = receipt["observed_network_exchange"]
        screenshot = screenshots / f"{journey_id}.png"
        _write_synthetic_screenshot(
            screenshot,
            red=(index * 31 + 17) % 256,
            green=(index * 47 + 29) % 256,
            blue=(index * 61 + 43) % 256,
        )
        har_entries.append(
            {
                "startedDateTime": f"2026-08-26T08:{20 + index:02d}:00.000Z",
                "time": 25 + index,
                "request": {
                    "method": exchange["request"]["method"],
                    "url": exchange["request"]["url"],
                    "httpVersion": "HTTP/2",
                    "headers": [{"name": "authorization", "value": "[REDACTED]"}],
                    "queryString": [],
                    "cookies": [],
                    "headersSize": -1,
                    "bodySize": 0,
                },
                "response": {
                    "status": exchange["response"]["http_status"],
                    "statusText": "captured",
                    "httpVersion": "HTTP/2",
                    "headers": [],
                    "cookies": [],
                    "content": {"size": 0, "mimeType": "application/json", "text": "[REDACTED]"},
                    "redirectURL": "",
                    "headersSize": -1,
                    "bodySize": 0,
                },
                "cache": {},
                "timings": {"send": 0, "wait": 25 + index, "receive": 0},
            }
        )
        browser_journeys.append(
            {
                "journey_id": journey_id,
                "status": "passed",
                "route_mocked": False,
                "dom_checkpoint": {
                    "rendered_element": f"[data-testid='{journey_id}']",
                    "observed": True,
                },
                "screenshot_artifact": f"screenshots/{journey_id}.png",
                "screenshot_sha256": _sha256(screenshot),
                "har_entry_indices": [index],
                "executed_at": f"2026-08-26T08:{20 + index:02d}:01Z",
            }
        )

    har_path = bundle_dir / "browser-network.har"
    har_path.write_text(
        json.dumps(
            {
                "log": {
                    "version": "1.2",
                    "creator": {"name": "Playwright", "version": "test-fixture"},
                    "pages": [],
                    "entries": har_entries,
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    browser_payload = {
        "schema_version": "pantheon.external-source-management.browser-evidence.v2",
        "task_id": "SRCM-P1-HOSTED-ACCEPTANCE-20260824",
        "program_id": "SRCM-PHASE1-20260824",
        "capture": {
            "status": "passed",
            "runner": "playwright",
            "execution_mode": "hosted",
            "capture_profile": "bounded-write-proof",
            "route_interception_count": 0,
            "frontend_sha": EXPECTED_FE_SHA,
            "backend_sha": EXPECTED_BFF_SHA,
            "normal_profile_restored": "read-only",
            "vite_bff_real_writes_default": "false",
            "source_ingestion_posture": "manual_reconcile_only",
            "producer": {
                "repository": "ajoe734/execute-plans",
                "workflow": ".github/workflows/srcm-p1-mgmt-ui-hosted-acceptance.yml",
                "run_id": 32999900001,
                "run_attempt": 1,
                "head_sha": "a" * 40,
                "served_frontend_sha": EXPECTED_FE_SHA,
            },
        },
        "har_artifact": har_path.name,
        "har_sha256": _sha256(har_path),
        "browser_journeys_count": len(browser_journeys),
        "browser_journeys": browser_journeys,
    }
    (bundle_dir / "browser-evidence.json").write_text(
        json.dumps(browser_payload, indent=2) + "\n",
        encoding="utf-8",
    )


def _setup_valid_bundle(tmp_path: Path) -> Path:
    target_dir = tmp_path / "evidence"
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(DEFAULT_EVIDENCE_DIR, target_dir)
    deployment_path = target_dir / "deployment.json"
    deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    deployment["exact_pair"].update(
        {
            "backend_sha": EXPECTED_BFF_SHA,
            "frontend_sha": EXPECTED_FE_SHA,
            "source_definitions_sha": EXPECTED_SOURCE_DEFINITIONS_SHA,
            "fe_manifest_bff_sha": EXPECTED_BFF_SHA,
        }
    )
    deployment["feature_posture"].update(
        {
            "SOURCE_INGEST_CONTROLLER_MODE": "reconcile_only",
            "SOURCE_INGEST_CONTROLLER_MAX_TICKS": "0",
            "SOURCE_INGEST_CONTROLLER_RESTART_POLICY": "unless-stopped",
        }
    )
    deployment_path.write_text(json.dumps(deployment, indent=2) + "\n", encoding="utf-8")
    _install_valid_browser_capture(target_dir)
    _bind_manifest_checksums(target_dir)
    return target_dir


def _make_mock_transport(
    *,
    fe_status: int = 200,
    fe_sha: str = EXPECTED_FE_SHA,
    fe_manifest_bff_sha: str = EXPECTED_BFF_SHA,
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
                "bffCommit": fe_manifest_bff_sha,
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
        elif url.endswith("/healthz"):
            return 200, {"status": "ok", "live": True, "ready": True}
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
    assert exc_info.value.code == "browser_evidence.invalid_journey_count"


def test_browser_evidence_runner_head_may_differ_from_served_frontend(tmp_path: Path) -> None:
    evidence_dir = _setup_valid_bundle(tmp_path)

    result = ExternalSourceManagementHostedAcceptanceVerifier(
        AcceptanceConfig(evidence_dir=evidence_dir, offline_only=True)
    ).run()

    assert result.browser_evidence["status"] == "passed"


def test_browser_evidence_served_frontend_mismatch_fails_closed(tmp_path: Path) -> None:
    evidence_dir = _setup_valid_bundle(tmp_path)
    browser_path = evidence_dir / "browser-evidence.json"
    payload = json.loads(browser_path.read_text(encoding="utf-8"))
    payload["capture"]["producer"]["served_frontend_sha"] = "b" * 40
    browser_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _bind_manifest_checksums(evidence_dir)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        ExternalSourceManagementHostedAcceptanceVerifier(
            AcceptanceConfig(evidence_dir=evidence_dir, offline_only=True)
        ).run()

    assert exc_info.value.code == "browser_evidence.invalid_capture_provenance"


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


def test_live_verify_exact_pair_drift_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=False)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        config,
        transport=_make_mock_transport(fe_manifest_bff_sha="40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0"),
    )

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "live.exact_pair_drift"


def test_live_verify_fe_unsupported_baseline_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=False)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        config,
        transport=_make_mock_transport(fe_sha=UNSUPPORTED_READONLY_FE_BASELINE),
    )

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "live.fe_unsupported_baseline"


def test_identity_fe_manifest_bff_drift_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    deploy_file = bundle_dir / "deployment.json"
    with deploy_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["exact_pair"]["fe_manifest_bff_sha"] = "40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0"
    with deploy_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "identity.fe_manifest_bff_drift"


def test_browser_evidence_missing_screenshot_file_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    # Remove one screenshot file
    (bundle_dir / "screenshots" / "journey_01_public_source_create_disabled.png").unlink()

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "browser_evidence.missing_screenshot_file"


def test_browser_evidence_invalid_png_file_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    # Write invalid bytes to screenshot file
    (bundle_dir / "screenshots" / "journey_01_public_source_create_disabled.png").write_bytes(b"not a png image")

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "browser_evidence.invalid_png_file"


def test_browser_evidence_screenshot_sha_mismatch_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    browser_file = bundle_dir / "browser-evidence.json"
    with browser_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["browser_journeys"][0]["screenshot_sha256"] = "0" * 64
    with browser_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "browser_evidence.screenshot_sha_mismatch"


def test_browser_evidence_missing_har_entry_fails(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    browser_file = bundle_dir / "browser-evidence.json"
    with browser_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["browser_journeys"][0]["har_entry_indices"] = []
    with browser_file.open("w", encoding="utf-8") as f:
        json.dump(data, f)

    config = AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(config)

    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "browser_evidence.missing_har_entry"


def test_browser_evidence_pending_capture_fails_closed(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    browser_file = bundle_dir / "browser-evidence.json"
    browser_file.write_text(
        json.dumps(
            {
                "schema_version": "pantheon.external-source-management.browser-evidence.v2",
                "capture": {"status": "not_run"},
                "browser_journeys_count": 0,
                "browser_journeys": [],
            }
        ),
        encoding="utf-8",
    )

    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    )
    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "browser_evidence.capture_not_passed"


def test_browser_evidence_legacy_static_summary_fails_closed(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    browser_file = bundle_dir / "browser-evidence.json"
    data = json.loads(browser_file.read_text(encoding="utf-8"))
    data["schema_version"] = "pantheon.external-source-management.browser-evidence.v1"
    browser_file.write_text(json.dumps(data), encoding="utf-8")

    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    )
    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "browser_evidence.unverifiable_static_summary"


def test_browser_evidence_missing_har_file_fails_closed(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    (bundle_dir / "browser-network.har").unlink()

    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    )
    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "browser_evidence.missing_har_file"


def test_browser_evidence_placeholder_screenshot_fails_closed(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    browser_file = bundle_dir / "browser-evidence.json"
    data = json.loads(browser_file.read_text(encoding="utf-8"))
    screenshot_path = bundle_dir / data["browser_journeys"][0]["screenshot_artifact"]
    _write_synthetic_screenshot(
        screenshot_path,
        red=1,
        green=2,
        blue=3,
        width=1,
        height=1,
    )
    data["browser_journeys"][0]["screenshot_sha256"] = _sha256(screenshot_path)
    browser_file.write_text(json.dumps(data), encoding="utf-8")

    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    )
    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "browser_evidence.placeholder_screenshot"


def test_source_ingestion_one_shot_normal_posture_fails_closed(tmp_path: Path) -> None:
    bundle_dir = _setup_valid_bundle(tmp_path)
    deployment_path = bundle_dir / "deployment.json"
    deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    deployment["feature_posture"]["SOURCE_INGEST_CONTROLLER_MAX_TICKS"] = "1"
    deployment["feature_posture"]["SOURCE_INGEST_CONTROLLER_RESTART_POLICY"] = "no"
    deployment_path.write_text(json.dumps(deployment), encoding="utf-8")

    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        AcceptanceConfig(evidence_dir=bundle_dir, offline_only=True)
    )
    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "posture.source_ingestion_not_safe_normal"


def test_verify_functional_closure_offline() -> None:
    result = ExternalSourceManagementHostedAcceptanceVerifier(
        AcceptanceConfig(
            evidence_dir=DEFAULT_EVIDENCE_DIR,
            offline_only=True,
            functional_only=True,
        )
    ).run()

    assert result.passed is True
    assert result.verification_scope == "functional_closure_with_hosted_proof_follow_up"
    assert result.functional_closure["status"] == "passed"
    assert result.functional_closure["bounded_recovery"]["unresolved_dlq_count"] == 0
    assert result.feature_posture["SOURCE_INGEST_CONTROLLER_MAX_TICKS"] == "0"
    assert result.journeys["status"] == "follow_up"
    assert result.browser_evidence["accepted"] is False


def test_verify_functional_closure_live_mock(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "functional-evidence"
    shutil.copytree(DEFAULT_EVIDENCE_DIR, bundle_dir)
    deployment = json.loads((bundle_dir / "deployment.json").read_text(encoding="utf-8"))
    pair = deployment["exact_pair"]

    result = ExternalSourceManagementHostedAcceptanceVerifier(
        AcceptanceConfig(
            evidence_dir=bundle_dir,
            functional_only=True,
        ),
        transport=_make_mock_transport(
            fe_sha=pair["frontend_sha"],
            fe_manifest_bff_sha=pair["backend_sha"],
            bff_sha=pair["backend_sha"],
            source_defs_sha=pair["source_definitions_sha"],
        ),
    ).run()

    assert result.passed is True
    assert result.exact_pair["frontend_sha"] == pair["frontend_sha"]
    assert result.exact_pair["backend_sha"] == pair["backend_sha"]


def test_functional_source_catalog_auth_requirement_is_follow_up(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "functional-evidence"
    shutil.copytree(DEFAULT_EVIDENCE_DIR, bundle_dir)
    deployment = json.loads((bundle_dir / "deployment.json").read_text(encoding="utf-8"))
    pair = deployment["exact_pair"]

    result = ExternalSourceManagementHostedAcceptanceVerifier(
        AcceptanceConfig(
            evidence_dir=bundle_dir,
            functional_only=True,
        ),
        transport=_make_mock_transport(
            fe_sha=pair["frontend_sha"],
            fe_manifest_bff_sha=pair["backend_sha"],
            bff_sha=pair["backend_sha"],
            source_defs_status=401,
        ),
    ).run()

    assert result.passed is True
    assert any("Source Definitions follow_up_auth_required" in item for item in result.diagnostics)


def test_functional_closure_recovery_tamper_fails_closed(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "functional-evidence"
    shutil.copytree(DEFAULT_EVIDENCE_DIR, bundle_dir)
    closure_path = bundle_dir / "functional-closure-20260828.json"
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    closure["bounded_recovery"]["unresolved_dlq_count"] = 1
    closure_path.write_text(json.dumps(closure, indent=2) + "\n", encoding="utf-8")

    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        AcceptanceConfig(
            evidence_dir=bundle_dir,
            offline_only=True,
            functional_only=True,
        )
    )
    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "functional_closure.invalid_recovery_count"


def test_default_bundle_does_not_claim_full_hosted_acceptance() -> None:
    verifier = ExternalSourceManagementHostedAcceptanceVerifier(
        AcceptanceConfig(evidence_dir=DEFAULT_EVIDENCE_DIR, offline_only=True)
    )
    with pytest.raises(SourceManagementAcceptanceError) as exc_info:
        verifier.run()
    assert exc_info.value.code == "browser_evidence.capture_not_passed"
