"""Tests for the fail-closed Pantheon product functional closure hosted acceptance verifier."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

from scripts.verify_product_functional_closure import (
    AcceptanceConfig,
    ProductFunctionalClosureAcceptanceError,
    ProductFunctionalClosureVerifier,
    main,
)


FE_SHA = "f" * 40
BFF_SHA = "b" * 40
FE_URL = "https://pantheon-fe.example.test"
BFF_URL = "https://pantheon-bff.example.test"


def _timestamp(*, age_seconds: int = 0) -> str:
    value = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_evidence_files(root: Path) -> dict[str, Path]:
    l12_payload = {
        "schema_version": "pantheon.product_functional_closure.cross_loop_truth_evidence.v1",
        "task": {"id": "PFG-L12-TRUTH-CROSSLOOP-20260820"},
        "status": "passed",
        "result": "passed",
        "mode": "hosted",
        "observed_at": _timestamp(),
        "unskipped_mandatory_cases": True,
        "skipped_mandatory_count": 0,
        "exact_pair": {
            "backend_sha": BFF_SHA,
            "frontend_sha": FE_SHA,
            "bff_url": BFF_URL,
            "fe_url": FE_URL,
        },
    }
    agora_payload = {
        "schema_version": "pantheon.agora.hosted-service-journey-evidence.v1",
        "task": {"id": "PFG-AGORA-JOURNEY-E2E-20260820"},
        "status": "passed",
        "result": "passed",
        "mode": "hosted",
        "observed_at": _timestamp(),
        "unskipped_mandatory_cases": True,
        "skipped_mandatory_count": 0,
        "exact_pair": {
            "backend_sha": BFF_SHA,
            "frontend_sha": FE_SHA,
            "bff_url": BFF_URL,
            "fe_url": FE_URL,
        },
    }
    mgmt_payload = {
        "schema_version": "pantheon.product_functional_closure.mgmt_journey_evidence.v1",
        "task": {"id": "PFG-MGMT-JOURNEY-E2E-20260820"},
        "status": "passed",
        "result": "passed",
        "mode": "hosted",
        "observed_at": _timestamp(),
        "unskipped_mandatory_cases": True,
        "skipped_mandatory_count": 0,
        "exact_pair": {
            "backend_sha": BFF_SHA,
            "frontend_sha": FE_SHA,
            "bff_url": BFF_URL,
            "fe_url": FE_URL,
        },
    }
    mgmt_ai_payload = {
        "schema_version": "pantheon.product_functional_closure.mgmt_ai_evidence.v1",
        "task": {"id": "PFG-MGMT-AI-PROVIDER-20260820"},
        "status": "passed",
        "result": "passed",
        "mode": "hosted",
        "observed_at": _timestamp(),
        "unskipped_mandatory_cases": True,
        "skipped_mandatory_count": 0,
        "exact_pair": {
            "backend_sha": BFF_SHA,
            "frontend_sha": FE_SHA,
            "bff_url": BFF_URL,
            "fe_url": FE_URL,
        },
    }
    restart_payload = {
        "schema_version": "pantheon.product_functional_closure.restart_evidence.v1",
        "status": "passed",
        "result": "passed",
        "mode": "hosted",
        "observed_at": _timestamp(),
        "exact_pair": {
            "backend_sha": BFF_SHA,
            "frontend_sha": FE_SHA,
            "bff_url": BFF_URL,
            "fe_url": FE_URL,
        },
    }
    rollback_payload = {
        "schema_version": "pantheon.product_functional_closure.rollback_evidence.v1",
        "status": "passed",
        "result": "passed",
        "mode": "hosted",
        "observed_at": _timestamp(),
        "checks": {
            "candidate_pre_switch_passed": True,
            "atomic_switch_passed": True,
            "post_switch_exact_pair_passed": True,
        },
        "exact_pair": {
            "backend_sha": BFF_SHA,
            "frontend_sha": FE_SHA,
            "bff_url": BFF_URL,
            "fe_url": FE_URL,
        },
    }
    disposition_payload = {
        "schema_version": "pantheon.product_functional_closure.code_disposition.v1",
        "canonical_owner": "services/source_ingestion/controller_worker.py",
        "new_parallel_owner_created": False,
    }

    paths: dict[str, Path] = {}
    for name, payload in (
        ("l12", l12_payload),
        ("agora", agora_payload),
        ("mgmt", mgmt_payload),
        ("mgmt_ai", mgmt_ai_payload),
        ("restart", restart_payload),
        ("rollback", rollback_payload),
        ("code_disposition", disposition_payload),
    ):
        p = root / f"{name}.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = p
    return paths


def _manifest(
    *,
    fe_sha: str = FE_SHA,
    manifest_bff_sha: str = BFF_SHA,
    real_writes: str = "false",
    allow_dev_stub_writes: str = "false",
    embedded_bearer: str = "false",
    bff_mode: str = "live",
    bff_fallback: str = "strict",
    deployment_state: str = "accepted",
    profile: str = "read-only",
    bff_host: str = BFF_URL,
) -> dict[str, Any]:
    return {
        "pairId": "p" * 64,
        "commit": fe_sha,
        "frontend": {"commitSha": fe_sha},
        "bffHost": bff_host,
        "bffCommit": manifest_bff_sha,
        "bff": {"baseUrl": bff_host, "sourceCommitSha": manifest_bff_sha},
        "deploymentState": deployment_state,
        "profile": profile,
        "buildMode": {
            "VITE_BFF_MODE": bff_mode,
            "VITE_BFF_FALLBACK": bff_fallback,
            "VITE_BFF_REAL_WRITES": real_writes,
            "VITE_BFF_ALLOW_DEV_STUB_WRITES": allow_dev_stub_writes,
            "VITE_BFF_EMBEDDED_BEARER_TOKEN": embedded_bearer,
        },
    }


def _transport(
    *,
    fe_sha: str = FE_SHA,
    manifest_bff_sha: str = BFF_SHA,
    runtime_bff_sha: str = BFF_SHA,
    real_writes: str = "false",
    allow_dev_stub_writes: str = "false",
    embedded_bearer: str = "false",
    bff_mode: str = "live",
    bff_fallback: str = "strict",
    auth_mode: str = "strict",
    auth_stub: bool = False,
    readyz_ok: bool = True,
    healthz_ok: bool = True,
    deployment_state: str = "accepted",
    profile: str = "read-only",
    bff_host: str = BFF_URL,
    lifecycle_projector_override: Any = None,
    dependencies_override: Any = None,
):
    def transport(url: str, _timeout: float) -> tuple[int, Mapping[str, Any]]:
        if url == f"{FE_URL}/deployment.json":
            return 200, _manifest(
                fe_sha=fe_sha,
                manifest_bff_sha=manifest_bff_sha,
                real_writes=real_writes,
                allow_dev_stub_writes=allow_dev_stub_writes,
                embedded_bearer=embedded_bearer,
                bff_mode=bff_mode,
                bff_fallback=bff_fallback,
                deployment_state=deployment_state,
                profile=profile,
                bff_host=bff_host,
            )
        if url == f"{BFF_URL}/bff/version":
            return 200, {
                "source_commit_sha": runtime_bff_sha,
                "config_posture": {"auth_mode": auth_mode, "auth_stub": auth_stub},
            }
        if url == f"{BFF_URL}/healthz":
            return 200, {
                "status": "ok" if healthz_ok else "degraded",
                "live": healthz_ok,
                "ready": healthz_ok,
            }
        if url == f"{BFF_URL}/readyz":
            if dependencies_override is not None:
                deps = dependencies_override
            else:
                lp = lifecycle_projector_override or {
                    "ready": True,
                    "status": "ready",
                    "environment_scope": "paper",
                    "deployment_sha": runtime_bff_sha,
                    "mode": "live",
                    "accepted_live": True,
                    "controller": {"status": "ready"},
                }
                deps = {
                    "source-ingest": {"status": "ok", "ready": True},
                    "paper-fleet-reconciler": {"status": "ok", "ready": True},
                    "lifecycle_projector": lp,
                }
            return 200, {
                "status": "ok" if readyz_ok else "unready",
                "live": True,
                "ready": readyz_ok,
                "dependencies": deps,
            }
        raise AssertionError(f"unexpected URL: {url}")

    return transport


def _config(
    tmp_path: Path,
    paths: Mapping[str, Path],
    *,
    strict: bool = False,
    profile: str = "hosted-functional",
) -> AcceptanceConfig:
    return AcceptanceConfig(
        expected_bff_sha=BFF_SHA,
        expected_fe_sha=FE_SHA,
        l12_evidence=paths.get("l12"),
        agora_evidence=paths.get("agora"),
        mgmt_evidence=paths.get("mgmt"),
        mgmt_ai_evidence=paths.get("mgmt_ai"),
        restart_evidence=paths.get("restart"),
        rollback_evidence=paths.get("rollback"),
        code_disposition_path=paths.get("code_disposition"),
        bff_base_url=BFF_URL,
        fe_base_url=FE_URL,
        evidence_dir=tmp_path / "output",
        strict=strict,
        profile=profile,
    )


def test_hosted_functional_acceptance_happy_path(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths, profile="hosted-functional"),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()

    assert report.overall_status == "PASSED"
    assert report.mode == "hosted"
    assert report.exact_pair["deployment_profile"] == "functional-accepted"
    assert report.summary["passed_gates"] == 6
    assert report.summary["failed_gates"] == 0
    assert all(row["status"] == "RESOLVED" for row in report.gap_matrix)

    out_dir = tmp_path / "output"
    assert (out_dir / "report.json").exists()
    assert (out_dir / "QUALIFICATION.json").exists()
    assert (out_dir / "VERIFICATION_REPORT.md").exists()
    assert (out_dir / "GAP_EVIDENCE_MATRIX.md").exists()
    assert (out_dir / "DEPLOYMENT_AUDIT.md").exists()


def test_privileged_acceptance_happy_path(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths, profile="privileged"),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()

    assert report.overall_status == "PASSED"
    assert report.exact_pair["deployment_profile"] == "accepted"


def test_simulated_mode_rejected(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    cfg = _config(tmp_path, paths)
    cfg.mode = "simulated-hosted"
    with pytest.raises(ProductFunctionalClosureAcceptanceError, match="only mode=hosted"):
        ProductFunctionalClosureVerifier(cfg, transport=_transport())


def test_invalid_profile_rejected(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    cfg = _config(tmp_path, paths)
    cfg.profile = "invalid-profile"
    with pytest.raises(ProductFunctionalClosureAcceptanceError, match="profile must be hosted-functional or privileged"):
        ProductFunctionalClosureVerifier(cfg, transport=_transport())


@pytest.mark.parametrize(
    ("transport_kwargs", "expected_err"),
    [
        ({"fe_sha": "a" * 40}, "served FE SHA"),
        ({"manifest_bff_sha": "a" * 40}, "manifest BFF SHA"),
        ({"runtime_bff_sha": "a" * 40}, "runtime BFF SHA"),
        ({"real_writes": "true"}, "unsafe"),
        ({"allow_dev_stub_writes": "true"}, "unsafe"),
        ({"embedded_bearer": "true"}, "unsafe"),
        ({"bff_mode": "mock"}, "unsafe"),
        ({"bff_fallback": "loose"}, "unsafe"),
        ({"auth_stub": True}, "auth posture"),
        ({"auth_mode": "permissive"}, "auth posture"),
        ({"deployment_state": "pending"}, "is not accepted read-only"),
        ({"profile": "operator-live"}, "is not accepted read-only"),
        ({"bff_host": "https://different-host.test"}, "frontend manifest is bound to"),
    ],
)
def test_gate_01_validation_errors(tmp_path: Path, transport_kwargs, expected_err: str) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths, strict=True),
        transport=_transport(**transport_kwargs),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_01 = report.gate_results[0]
    assert gate_01.gate_id == "gate_01_manifest_exact_pair"
    assert gate_01.status == "FAILED"
    assert expected_err in str(gate_01.error)


def test_gate_02_healthz_unready_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(healthz_ok=False),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "reported unready" in str(gate_02.error)


def test_gate_02_source_dep_unready_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(
            dependencies_override={
                "source-ingest": {"status": "unhealthy", "ready": False},
                "paper-fleet-reconciler": {"status": "ok", "ready": True},
            }
        ),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "source ingestion dependency" in str(gate_02.error)


def test_gate_03_empty_dependencies_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(dependencies_override={}),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "empty dependencies" in str(gate_03.error)


def test_gate_03_lifecycle_projector_unready_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(
            lifecycle_projector_override={
                "ready": False,
                "status": "degraded",
                "environment_scope": "paper",
            }
        ),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "lifecycle_projector is not ready" in str(gate_03.error)


def test_gate_03_environment_scope_non_paper_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(
            lifecycle_projector_override={
                "ready": True,
                "status": "ready",
                "environment_scope": "live-capital",
            }
        ),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "expected 'paper'" in str(gate_03.error)


def test_gate_03_deployment_sha_mismatch_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(
            lifecycle_projector_override={
                "ready": True,
                "status": "ready",
                "environment_scope": "paper",
                "deployment_sha": "0" * 40,
            }
        ),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "deployment_sha" in str(gate_03.error)


@pytest.mark.parametrize("missing_journey", ["l12", "agora", "mgmt", "mgmt_ai"])
def test_gate_04_missing_journey_evidence_arg_fails(tmp_path: Path, missing_journey: str) -> None:
    paths = _write_evidence_files(tmp_path)
    paths.pop(missing_journey)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "required and must be provided" in str(gate_04.error)


def test_gate_04_journey_file_not_found_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paths["l12"].unlink()
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "does not exist" in str(gate_04.error)


def test_gate_04_journey_skipped_mandatory_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    mgmt = json.loads(paths["mgmt"].read_text())
    mgmt["skipped_mandatory_count"] = 1
    paths["mgmt"].write_text(json.dumps(mgmt))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "skipped mandatory cases" in str(gate_04.error)


def test_gate_04_journey_exact_pair_mismatch_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    agora = json.loads(paths["agora"].read_text())
    agora["exact_pair"]["frontend_sha"] = "0" * 40
    paths["agora"].write_text(json.dumps(agora))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "exact_pair.frontend_sha is" in str(gate_04.error)


def test_gate_05_missing_code_disposition_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paths["code_disposition"].unlink()
    cfg = _config(tmp_path, paths)
    cfg.code_disposition_path = tmp_path / "non_existent_disposition.json"

    verifier = ProductFunctionalClosureVerifier(
        cfg,
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_05 = next(r for r in report.gate_results if r.gate_id == "gate_05_code_disposition_and_simplification")
    assert gate_05.status == "FAILED"
    assert "code disposition manifest is required" in str(gate_05.error)


def test_gate_05_new_parallel_owner_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    disp = json.loads(paths["code_disposition"].read_text())
    disp["new_parallel_owner_created"] = True
    paths["code_disposition"].write_text(json.dumps(disp))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_05 = next(r for r in report.gate_results if r.gate_id == "gate_05_code_disposition_and_simplification")
    assert gate_05.status == "FAILED"
    assert "new_parallel_owner_created" in str(gate_05.error)


def test_gate_06_missing_rollback_evidence_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    cfg = _config(tmp_path, paths)
    cfg.rollback_evidence = None

    verifier = ProductFunctionalClosureVerifier(
        cfg,
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_06 = next(r for r in report.gate_results if r.gate_id == "gate_06_rollback_and_switch_safety")
    assert gate_06.status == "FAILED"
    assert "--rollback-evidence is required and must exist" in str(gate_06.error)


def test_gate_06_rollback_check_false_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    rollback = json.loads(paths["rollback"].read_text())
    rollback["checks"]["atomic_switch_passed"] = False
    paths["rollback"].write_text(json.dumps(rollback))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_06 = next(r for r in report.gate_results if r.gate_id == "gate_06_rollback_and_switch_safety")
    assert gate_06.status == "FAILED"
    assert "atomic_switch_passed" in str(gate_06.error)


def test_strict_mode_stops_at_first_failure(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths, strict=True),
        transport=_transport(fe_sha="a" * 40),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    assert len(report.gate_results) == 1


def test_main_cli_success(tmp_path: Path, monkeypatch) -> None:
    paths = _write_evidence_files(tmp_path)
    args = [
        "verify_product_functional_closure.py",
        "--expected-bff-sha", BFF_SHA,
        "--expected-fe-sha", FE_SHA,
        "--bff-url", BFF_URL,
        "--fe-url", FE_URL,
        "--l12-evidence", str(paths["l12"]),
        "--agora-evidence", str(paths["agora"]),
        "--mgmt-evidence", str(paths["mgmt"]),
        "--mgmt-ai-evidence", str(paths["mgmt_ai"]),
        "--rollback-evidence", str(paths["rollback"]),
        "--code-disposition", str(paths["code_disposition"]),
        "--evidence-dir", str(tmp_path / "cli_out"),
    ]
    monkeypatch.setattr("sys.argv", args)
    monkeypatch.setattr(
        "scripts.verify_product_functional_closure._default_transport",
        _transport(),
    )
    rc = main()
    assert rc == 0
