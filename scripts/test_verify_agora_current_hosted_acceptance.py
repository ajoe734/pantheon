"""Tests for the fail-closed Agora real hosted acceptance aggregator."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

from scripts.verify_agora_current_hosted_acceptance import (
    AcceptanceConfig,
    AgoraAcceptanceError,
    AgoraHostedAcceptanceVerifier,
    LINEAGE_KEYS,
    NEGATIVE_CONTROL_KEYS,
    SERVICE_STAGE_IDS,
)


FE_SHA = "f" * 40
BFF_SHA = "b" * 40
FE_URL = "https://frontend.example.test"
BFF_URL = "https://bff.example.test"


def _timestamp(*, age_seconds: int = 0) -> str:
    value = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _common(kind: str, run_id: int, repository: str, workflow: str) -> dict[str, Any]:
    return {
        "schema_version": f"pantheon.agora.hosted-{kind.replace('_', '-')}-evidence.v1",
        "result": "passed",
        "mode": "hosted",
        "observed_at": _timestamp(),
        "artifact_digest_sha256": "d" * 64,
        "exact_pair": {
            "backend_sha": BFF_SHA,
            "frontend_sha": FE_SHA,
            "bff_url": BFF_URL,
            "fe_url": FE_URL,
        },
        "producer": {
            "kind": "github-actions",
            "repository": repository,
            "workflow": workflow,
            "run_id": run_id,
        },
    }


def _write_artifacts(root: Path) -> dict[str, Path]:
    service = _common(
        "service_journey",
        101,
        "ajoe734/pantheon",
        ".github/workflows/agora-hosted-acceptance.yml",
    )
    service.update(
        {
            "authenticated_request_count": 72,
            "stages": [{"stage_id": stage_id, "status": "passed"} for stage_id in SERVICE_STAGE_IDS],
            "lineage": {key: f"value-{key}" for key in LINEAGE_KEYS},
            "negative_controls": {key: True for key in NEGATIVE_CONTROL_KEYS},
            "authentication": {
                "mode": "strict",
                "stub": False,
                "operator_subject": "operator-a",
                "independent_reviewer_subject": "reviewer-b",
            },
        }
    )
    browser = _common(
        "browser",
        102,
        "ajoe734/execute-plans",
        ".github/workflows/agora-hosted-acceptance.yml",
    )
    browser["viewports"] = [
        {
            "name": name,
            "status": "passed",
            "authenticated": True,
            "bff_request_count": 12,
            "unexpected_console_error_count": 0,
            "routes": ["/agora/strategy-workshop", "/agora/trading-room"],
        }
        for name in ("desktop", "mobile")
    ]
    restart = _common(
        "restart",
        103,
        "ajoe734/pantheon",
        ".github/workflows/nonprod-deploy.yml",
    )
    restart.update(
        {
            "restart_executed": True,
            "store_backends": {"workshop": "postgres", "governance": "postgres", "dataset": "postgres"},
            "before_restart": {"instance_id": "container-before", "resource_ids": {"workshop_id": "ws-1"}},
            "after_restart": {
                "instance_id": "container-after",
                "resource_ids": {"workshop_id": "ws-1"},
                "ready": True,
                "deployment_sha": BFF_SHA,
            },
            "data_loss_detected": False,
            "corruption_detected": False,
        }
    )
    rollback = _common(
        "rollback",
        104,
        "ajoe734/execute-plans",
        ".github/workflows/pantheon-dev-fe-deploy.yml",
    )
    rollback.update(
        {
            "checks": {
                "candidate_pre_switch_passed": True,
                "atomic_switch_passed": True,
                "post_switch_exact_pair_passed": True,
                "failure_injection_executed": True,
                "failed_candidate_rejected": True,
                "last_accepted_pair_preserved": True,
                "rollback_target_verified": True,
            },
            "prior_accepted_pair": {"backend_sha": "a" * 40, "frontend_sha": "c" * 40},
        }
    )
    paths: dict[str, Path] = {}
    for kind, payload in (
        ("service_journey", service),
        ("browser", browser),
        ("restart", restart),
        ("rollback", rollback),
    ):
        path = root / f"{kind}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[kind] = path
    return paths


def _manifest(*, fe_sha: str = FE_SHA, manifest_bff_sha: str = BFF_SHA) -> dict[str, Any]:
    return {
        "pairId": "1" * 64,
        "commit": fe_sha,
        "frontend": {"commitSha": fe_sha},
        "bffHost": BFF_URL,
        "bffCommit": manifest_bff_sha,
        "bff": {"baseUrl": BFF_URL, "sourceCommitSha": manifest_bff_sha},
        "deploymentState": "accepted",
        "profile": "read-only",
        "buildMode": {
            "VITE_BFF_MODE": "live",
            "VITE_BFF_FALLBACK": "strict",
            "VITE_BFF_REAL_WRITES": "false",
            "VITE_BFF_ALLOW_DEV_STUB_WRITES": "false",
            "VITE_BFF_EMBEDDED_BEARER_TOKEN": "false",
        },
        "agoraCompatibility": {
            "compatibility_status": "accepted",
            "backend": {"runtime_commit": manifest_bff_sha},
            "frontend": {"runtime_commit": fe_sha},
        },
        "gate": {"runId": "100", "runUrl": "https://github.com/ajoe734/execute-plans/actions/runs/100"},
    }


def _transport(
    *,
    fe_sha: str = FE_SHA,
    manifest_bff_sha: str = BFF_SHA,
    runtime_bff_sha: str = BFF_SHA,
    github_head_overrides: Mapping[int, str] | None = None,
):
    overrides = dict(github_head_overrides or {})

    def transport(url: str, _timeout: float) -> tuple[int, Mapping[str, Any]]:
        if url == f"{FE_URL}/deployment.json":
            return 200, _manifest(fe_sha=fe_sha, manifest_bff_sha=manifest_bff_sha)
        if url == f"{BFF_URL}/bff/version":
            return 200, {
                "source_commit_sha": runtime_bff_sha,
                "config_posture": {"auth_mode": "strict", "auth_stub": False},
            }
        if url == f"{BFF_URL}/healthz":
            return 200, {"status": "ok", "live": True, "ready": True}
        if url == f"{BFF_URL}/livez":
            return 200, {"status": "ok", "live": True, "ready": True}
        if url == f"{BFF_URL}/readyz":
            return 200, {
                "status": "ok",
                "live": True,
                "ready": True,
                "dependencies": {
                    "lifecycle_projector": {
                        "status": "ok",
                        "ready": True,
                        "worker_status": "ready",
                        "controller_status": "ready",
                        "accepted_live": True,
                        "deployment_sha": runtime_bff_sha,
                        "checkpoint": 42,
                        "source_high_watermark": 42,
                        "backlog": 0,
                        "freshness": {"stale": False, "age_seconds": 0.1},
                    }
                },
            }
        prefix = "https://api.github.com/repos/"
        if url.startswith(prefix):
            parts = url.removeprefix(prefix).split("/actions/runs/")
            repository, run_id_raw = parts
            run_id = int(run_id_raw)
            details = {
                101: ("ajoe734/pantheon", ".github/workflows/agora-hosted-acceptance.yml", BFF_SHA),
                102: ("ajoe734/execute-plans", ".github/workflows/agora-hosted-acceptance.yml", FE_SHA),
                103: ("ajoe734/pantheon", ".github/workflows/nonprod-deploy.yml", BFF_SHA),
                104: ("ajoe734/execute-plans", ".github/workflows/pantheon-dev-fe-deploy.yml", FE_SHA),
            }[run_id]
            assert repository == details[0]
            return 200, {
                "status": "completed",
                "conclusion": "success",
                "head_sha": overrides.get(run_id, details[2]),
                "path": details[1],
                "html_url": f"https://github.com/{repository}/actions/runs/{run_id}",
            }
        raise AssertionError(f"unexpected URL: {url}")

    return transport


def _config(tmp_path: Path, paths: Mapping[str, Path], *, strict: bool = False) -> AcceptanceConfig:
    return AcceptanceConfig(
        expected_bff_sha=BFF_SHA,
        expected_fe_sha=FE_SHA,
        service_journey_evidence=paths["service_journey"],
        browser_evidence=paths["browser"],
        restart_evidence=paths["restart"],
        rollback_evidence=paths["rollback"],
        bff_base_url=BFF_URL,
        fe_base_url=FE_URL,
        evidence_dir=tmp_path / "output",
        strict=strict,
    )


def test_real_hosted_acceptance_passes_only_with_all_live_and_run_evidence(tmp_path: Path) -> None:
    paths = _write_artifacts(tmp_path)
    report = AgoraHostedAcceptanceVerifier(_config(tmp_path, paths), transport=_transport()).run_full_acceptance()

    assert report.overall_status == "PASSED"
    assert report.mode == "hosted"
    assert report.summary == pytest.approx(
        {
            "total_gates": 6,
            "executed_gates": 6,
            "passed_gates": 6,
            "failed_gates": 0,
            "duration_ms": report.summary["duration_ms"],
        }
    )
    assert report.exact_pair["deployment_profile"] == "accepted"
    assert all(row["status"] == "RESOLVED" for row in report.gap_matrix)
    assert json.loads((tmp_path / "output" / "evidence.json").read_text())["overall_status"] == "PASSED"


def test_simulated_mode_is_not_supported(tmp_path: Path) -> None:
    paths = _write_artifacts(tmp_path)
    config = _config(tmp_path, paths)
    config.mode = "simulated-hosted"

    with pytest.raises(AgoraAcceptanceError, match="only mode=hosted"):
        AgoraHostedAcceptanceVerifier(config, transport=_transport())


@pytest.mark.parametrize(
    ("transport", "expected_error"),
    [
        (_transport(fe_sha="e" * 40), "served FE SHA"),
        (_transport(manifest_bff_sha="a" * 40), "manifest BFF SHA"),
        (_transport(runtime_bff_sha="c" * 40), "runtime BFF SHA"),
    ],
)
def test_manifest_or_runtime_identity_drift_fails_closed(tmp_path: Path, transport, expected_error: str) -> None:
    paths = _write_artifacts(tmp_path)
    report = AgoraHostedAcceptanceVerifier(
        _config(tmp_path, paths, strict=True), transport=transport
    ).run_full_acceptance()

    assert report.overall_status == "FAILED"
    assert report.gate_results[0].gate_id == "gate_01_manifest_exact_pair"
    assert expected_error in str(report.gate_results[0].error)
    assert report.summary["executed_gates"] == 1


def test_missing_service_journey_evidence_cannot_be_replaced_by_in_process_results(tmp_path: Path) -> None:
    paths = _write_artifacts(tmp_path)
    paths["service_journey"].unlink()
    report = AgoraHostedAcceptanceVerifier(_config(tmp_path, paths), transport=_transport()).run_full_acceptance()

    gate = next(result for result in report.gate_results if result.gate_id == "gate_03_agora_product_journey")
    assert gate.status == "FAILED"
    assert "does not exist" in str(gate.error)
    assert report.overall_status == "FAILED"


def test_stale_evidence_fails_closed(tmp_path: Path) -> None:
    paths = _write_artifacts(tmp_path)
    payload = json.loads(paths["browser"].read_text())
    payload["observed_at"] = _timestamp(age_seconds=21601)
    paths["browser"].write_text(json.dumps(payload))

    report = AgoraHostedAcceptanceVerifier(_config(tmp_path, paths), transport=_transport()).run_full_acceptance()
    gate = next(result for result in report.gate_results if result.gate_id == "gate_03_agora_product_journey")
    assert gate.status == "FAILED"
    assert "freshness window" in str(gate.error)


def test_github_run_head_must_match_exact_pair(tmp_path: Path) -> None:
    paths = _write_artifacts(tmp_path)
    report = AgoraHostedAcceptanceVerifier(
        _config(tmp_path, paths),
        transport=_transport(github_head_overrides={102: "a" * 40}),
    ).run_full_acceptance()

    gate = next(result for result in report.gate_results if result.gate_id == "gate_03_agora_product_journey")
    assert gate.status == "FAILED"
    assert "successful exact-head run" in str(gate.error)


def test_restart_requires_a_different_runtime_instance_and_exact_readback(tmp_path: Path) -> None:
    paths = _write_artifacts(tmp_path)
    payload = json.loads(paths["restart"].read_text())
    payload["after_restart"]["instance_id"] = payload["before_restart"]["instance_id"]
    paths["restart"].write_text(json.dumps(payload))

    report = AgoraHostedAcceptanceVerifier(_config(tmp_path, paths), transport=_transport()).run_full_acceptance()
    gate = next(
        result for result in report.gate_results if result.gate_id == "gate_05_restart_persistence_readback"
    )
    assert gate.status == "FAILED"
    assert "actual restart" in str(gate.error)


def test_historical_simulated_closeout_is_marked_invalidated() -> None:
    root = Path(__file__).resolve().parents[1]
    historical = root / "docs/deployment/evidence/agora/AGORA-HOSTED-ACCEPTANCE-20260813"

    assert json.loads((historical / "evidence.json").read_text())["overall_status"] == "INVALIDATED"
    assert json.loads((historical / "QUALIFICATION.json").read_text())["qualification_status"] == "INVALIDATED"
    assert (historical / "INVALIDATION.md").exists()
