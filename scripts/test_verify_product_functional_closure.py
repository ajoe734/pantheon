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
        "producer": {
            "kind": "github-actions",
            "repository": "ajoe734/pantheon",
            "workflow": "nonprod-deploy.yml",
            "run_id": "12345",
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
        "producer": {
            "kind": "github-actions",
            "repository": "ajoe734/execute-plans",
            "workflow": "agora-hosted-acceptance.yml",
            "run_id": "12346",
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
        "producer": {
            "kind": "github-actions",
            "repository": "ajoe734/execute-plans",
            "workflow": "pfg-mgmt-journey-e2e-20260820-hosted-acceptance.yml",
            "run_id": "12347",
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
        "producer": {
            "kind": "github-actions",
            "repository": "ajoe734/execute-plans",
            "workflow": "pfg-mgmt-journey-e2e-20260820-hosted-acceptance.yml",
            "run_id": "12348",
        },
    }
    restart_payload = {
        "schema_version": "pantheon.product_functional_closure.restart_evidence.v1",
        "task": {"id": "PFG-RESTART-20260820"},
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
        "task": {"id": "PFG-HOSTED-ACCEPT-20260820"},
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
        "producer": {
            "kind": "github-actions",
            "repository": "ajoe734/pantheon",
            "workflow": "nonprod-deploy.yml",
            "run_id": "12349",
        },
    }
    source_runtime_payload = {
        "schema_version": "pantheon.product_functional_closure.source_runtime.v1",
        "task": {"id": "PFG-SOURCE-MANUAL-ONCE-20260820"},
        "status": "passed",
        "mode": "hosted",
        "observed_at": _timestamp(),
        "exact_pair": {
            "backend_sha": BFF_SHA,
            "frontend_sha": FE_SHA,
            "bff_url": BFF_URL,
            "fe_url": FE_URL,
        },
        "scheduler_mode": "reconcile_only",
        "max_ticks": 0,
        "recurring_provider_process": "absent",
        "continuous_egress": "disabled",
        "zero_continuous_egress": True,
        "before_after": "reconcile_only",
    }
    paper_runtime_payload = {
        "schema_version": "pantheon.product_functional_closure.paper_runtime.v1",
        "task": {"id": "PFG-RUNTIME-BINDING-R2-20260820"},
        "status": "passed",
        "mode": "hosted",
        "observed_at": _timestamp(),
        "exact_pair": {
            "backend_sha": BFF_SHA,
            "frontend_sha": FE_SHA,
            "bff_url": BFF_URL,
            "fe_url": FE_URL,
        },
        "environment_scope": "paper",
        "deployment_sha": BFF_SHA,
        "paper_fleet_ready": True,
        "executable_binding_contract": "admitted",
        "bounded_lifecycle": "enforced",
    }
    disposition_payload = {
        "schema_version": "pantheon.product_functional_closure.code_disposition.v1",
        "program_id": "pantheon-product-functional-closure-20260820",
        "task_id": "PFG-HOSTED-ACCEPT-20260820",
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
        ("source_runtime", source_runtime_payload),
        ("paper_runtime", paper_runtime_payload),
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


def _github_run(
    *,
    repository: str,
    run_id: str,
    workflow: str,
    head_sha: str,
    status: str = "completed",
    conclusion: str = "success",
) -> dict[str, Any]:
    return {
        "id": int(run_id),
        "status": status,
        "conclusion": conclusion,
        "head_sha": head_sha,
        "path": workflow,
        "html_url": f"https://github.com/{repository}/actions/runs/{run_id}",
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
    dev_login_enabled: bool = True,
    readyz_ok: bool = True,
    healthz_ok: bool = True,
    deployment_state: str = "accepted",
    profile: str = "read-only",
    bff_host: str = BFF_URL,
    lifecycle_projector_override: Any = None,
    dependencies_override: Any = None,
    github_runs_override: Optional[Mapping[str, Mapping[str, Any]]] = None,
):
    runs = {
        f"https://api.github.com/repos/ajoe734/pantheon/actions/runs/12345": _github_run(
            repository="ajoe734/pantheon",
            run_id="12345",
            workflow="nonprod-deploy.yml",
            head_sha=BFF_SHA,
        ),
        f"https://api.github.com/repos/ajoe734/execute-plans/actions/runs/12346": _github_run(
            repository="ajoe734/execute-plans",
            run_id="12346",
            workflow="agora-hosted-acceptance.yml",
            head_sha=FE_SHA,
        ),
        f"https://api.github.com/repos/ajoe734/execute-plans/actions/runs/12347": _github_run(
            repository="ajoe734/execute-plans",
            run_id="12347",
            workflow="pfg-mgmt-journey-e2e-20260820-hosted-acceptance.yml",
            head_sha=FE_SHA,
        ),
        f"https://api.github.com/repos/ajoe734/execute-plans/actions/runs/12348": _github_run(
            repository="ajoe734/execute-plans",
            run_id="12348",
            workflow="pfg-mgmt-journey-e2e-20260820-hosted-acceptance.yml",
            head_sha=FE_SHA,
        ),
        f"https://api.github.com/repos/ajoe734/pantheon/actions/runs/12349": _github_run(
            repository="ajoe734/pantheon",
            run_id="12349",
            workflow="nonprod-deploy.yml",
            head_sha=BFF_SHA,
        ),
    }
    if github_runs_override:
        runs.update(github_runs_override)

    def transport(url: str, _timeout: float) -> tuple[int, Mapping[str, Any]]:
        if url in runs:
            return 200, runs[url]
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
                "config_posture": {
                    "auth_mode": auth_mode,
                    "auth_stub": auth_stub,
                    "dev_login_enabled": dev_login_enabled,
                },
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
        source_runtime_evidence=paths.get("source_runtime"),
        paper_runtime_evidence=paths.get("paper_runtime"),
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


def test_hosted_functional_acceptance_permissive_stub_happy_path(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths, profile="hosted-functional"),
        transport=_transport(auth_mode="permissive", auth_stub=True, dev_login_enabled=True),
    )
    report = verifier.run_full_acceptance()

    assert report.overall_status == "PASSED"
    assert report.exact_pair["deployment_profile"] == "functional-accepted"


def test_privileged_acceptance_happy_path(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths, profile="privileged"),
        transport=_transport(auth_mode="strict", auth_stub=False),
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
        ({"auth_mode": "invalid_mode"}, "auth posture"),
        ({"auth_mode": "permissive", "dev_login_enabled": False}, "auth posture"),
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


@pytest.mark.parametrize(
    ("transport_kwargs", "expected_err"),
    [
        ({"auth_stub": True}, "auth posture"),
        ({"auth_mode": "permissive"}, "auth posture"),
    ],
)
def test_gate_01_privileged_profile_rejects_non_strict_auth(
    tmp_path: Path, transport_kwargs, expected_err: str
) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths, profile="privileged", strict=True),
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


def test_gate_02_missing_source_runtime_evidence_arg_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    cfg = _config(tmp_path, paths)
    cfg.source_runtime_evidence = None
    verifier = ProductFunctionalClosureVerifier(cfg, transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "source-runtime-evidence is required" in str(gate_02.error)


def test_gate_02_source_runtime_file_not_found_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paths["source_runtime"].unlink()
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "does not exist" in str(gate_02.error)


def test_gate_02_source_runtime_missing_schema_version_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    del src["schema_version"]
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "must declare a non-empty schema_version" in str(gate_02.error)


def test_gate_02_source_runtime_missing_task_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    del src["task"]
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "must declare an associated task id" in str(gate_02.error)


def test_gate_02_source_runtime_non_hosted_mode_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    src["mode"] = "local"
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "must declare mode='hosted'" in str(gate_02.error)


def test_gate_02_source_runtime_missing_observed_at_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    del src["observed_at"]
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "must declare observed_at timestamp" in str(gate_02.error)


def test_gate_02_source_runtime_stale_observed_at_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    src["observed_at"] = _timestamp(age_seconds=50000)
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "outside the allowed freshness window" in str(gate_02.error)


def test_gate_02_source_runtime_partial_exact_pair_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    del src["exact_pair"]["bff_url"]
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "exact_pair.bff_url is missing" in str(gate_02.error)


def test_gate_02_source_runtime_exact_pair_mismatch_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    src["exact_pair"]["backend_sha"] = "0" * 40
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "exact_pair.backend_sha is" in str(gate_02.error)


def test_gate_02_source_runtime_mode_invalid_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    src["scheduler_mode"] = "continuous_pull"
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "source runtime scheduler mode is" in str(gate_02.error)


def test_gate_02_source_runtime_max_ticks_nonzero_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    src["max_ticks"] = 1
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "max_ticks is 1" in str(gate_02.error)


def test_gate_02_source_runtime_missing_max_ticks_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    del src["max_ticks"]
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "source runtime max_ticks is None" in str(gate_02.error)


def test_gate_02_source_recurring_process_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    src["recurring_provider_process"] = "present"
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "recurring_provider_process='present'" in str(gate_02.error)


def test_gate_02_source_runtime_missing_recurring_process_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    del src["recurring_provider_process"]
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "recurring_provider_process=None" in str(gate_02.error)


def test_gate_02_source_continuous_egress_enabled_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    src["continuous_egress"] = "enabled"
    src["zero_continuous_egress"] = False
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "continuous egress" in str(gate_02.error)


def test_gate_02_source_missing_continuous_egress_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    del src["continuous_egress"]
    del src["zero_continuous_egress"]
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "continuous egress disabled" in str(gate_02.error)


def test_gate_02_source_missing_before_after_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    del src["before_after"]
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "source_before_after_assertion" in str(gate_02.error)


def test_gate_02_source_invalid_before_after_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    src = json.loads(paths["source_runtime"].read_text())
    src["before_after"] = "unverified"
    paths["source_runtime"].write_text(json.dumps(src))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next(r for r in report.gate_results if r.gate_id == "gate_02_source_manual_only_readiness")
    assert gate_02.status == "FAILED"
    assert "source_before_after_assertion" in str(gate_02.error)


def test_gate_03_missing_paper_runtime_evidence_arg_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    cfg = _config(tmp_path, paths)
    cfg.paper_runtime_evidence = None
    verifier = ProductFunctionalClosureVerifier(cfg, transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "paper-runtime-evidence is required" in str(gate_03.error)


def test_gate_03_paper_runtime_file_not_found_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paths["paper_runtime"].unlink()
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "does not exist" in str(gate_03.error)


def test_gate_03_paper_runtime_missing_schema_version_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    del paper["schema_version"]
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "must declare a non-empty schema_version" in str(gate_03.error)


def test_gate_03_paper_runtime_missing_task_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    del paper["task"]
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "must declare an associated task id" in str(gate_03.error)


def test_gate_03_paper_runtime_non_hosted_mode_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    paper["mode"] = "local"
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "must declare mode='hosted'" in str(gate_03.error)


def test_gate_03_paper_runtime_missing_observed_at_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    del paper["observed_at"]
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "must declare observed_at timestamp" in str(gate_03.error)


def test_gate_03_paper_runtime_stale_observed_at_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    paper["observed_at"] = _timestamp(age_seconds=50000)
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "outside the allowed freshness window" in str(gate_03.error)


def test_gate_03_paper_runtime_partial_exact_pair_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    del paper["exact_pair"]["frontend_sha"]
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "exact_pair.frontend_sha is missing" in str(gate_03.error)


def test_gate_03_paper_runtime_exact_pair_mismatch_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    paper["exact_pair"]["backend_sha"] = "0" * 40
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "exact_pair.backend_sha is" in str(gate_03.error)


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


def test_gate_03_paper_fleet_unready_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    paper["paper_fleet_ready"] = False
    paths["paper_runtime"].write_text(json.dumps(paper))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "paper_fleet_ready=False" in str(gate_03.error)


def test_gate_03_paper_runtime_missing_fleet_readiness_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    del paper["paper_fleet_ready"]
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "paper_fleet_ready=None" in str(gate_03.error)


def test_gate_03_paper_runtime_missing_binding_contract_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    del paper["executable_binding_contract"]
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "executable_binding_contract=None" in str(gate_03.error)


def test_gate_03_paper_runtime_invalid_binding_contract_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    paper["executable_binding_contract"] = "unadmitted"
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "executable_binding_contract='unadmitted'" in str(gate_03.error)


def test_gate_03_paper_runtime_missing_environment_scope_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    del paper["environment_scope"]
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "paper_environment_scope" in str(gate_03.error)


def test_gate_03_paper_runtime_non_paper_environment_scope_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    paper["environment_scope"] = "production"
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "paper_environment_scope" in str(gate_03.error)


def test_gate_03_paper_runtime_missing_deployment_sha_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    del paper["deployment_sha"]
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "paper_deployment_sha" in str(gate_03.error)


def test_gate_03_paper_runtime_deployment_sha_mismatch_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    paper["deployment_sha"] = "0" * 40
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "paper_deployment_sha" in str(gate_03.error)


def test_gate_03_paper_runtime_missing_bounded_lifecycle_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    paper = json.loads(paths["paper_runtime"].read_text())
    del paper["bounded_lifecycle"]
    paths["paper_runtime"].write_text(json.dumps(paper))
    verifier = ProductFunctionalClosureVerifier(_config(tmp_path, paths), transport=_transport())
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_03 = next(r for r in report.gate_results if r.gate_id == "gate_03_paper_runtime_execution")
    assert gate_03.status == "FAILED"
    assert "bounded_lifecycle" in str(gate_03.error)


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


def test_gate_04_journey_missing_schema_version_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    l12 = json.loads(paths["l12"].read_text())
    del l12["schema_version"]
    paths["l12"].write_text(json.dumps(l12))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "must declare a non-empty schema_version" in str(gate_04.error)


def test_gate_04_journey_missing_task_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    agora = json.loads(paths["agora"].read_text())
    del agora["task"]
    paths["agora"].write_text(json.dumps(agora))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "must declare an associated task id" in str(gate_04.error)


def test_gate_04_journey_non_hosted_mode_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    mgmt = json.loads(paths["mgmt"].read_text())
    mgmt["mode"] = "local"
    paths["mgmt"].write_text(json.dumps(mgmt))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "must declare mode='hosted'" in str(gate_04.error)


def test_gate_04_journey_missing_observed_at_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    mgmt_ai = json.loads(paths["mgmt_ai"].read_text())
    del mgmt_ai["observed_at"]
    paths["mgmt_ai"].write_text(json.dumps(mgmt_ai))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "must declare observed_at timestamp" in str(gate_04.error)


def test_gate_04_journey_stale_observed_at_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    l12 = json.loads(paths["l12"].read_text())
    l12["observed_at"] = _timestamp(age_seconds=50000)
    paths["l12"].write_text(json.dumps(l12))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "outside the allowed freshness window" in str(gate_04.error)


def test_gate_04_journey_partial_exact_pair_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    agora = json.loads(paths["agora"].read_text())
    del agora["exact_pair"]["bff_url"]
    paths["agora"].write_text(json.dumps(agora))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "exact_pair.bff_url is missing" in str(gate_04.error)


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


def test_gate_04_journey_missing_producer_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    mgmt = json.loads(paths["mgmt"].read_text())
    del mgmt["producer"]
    paths["mgmt"].write_text(json.dumps(mgmt))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "must declare a successful GitHub Actions producer" in str(gate_04.error)


def test_gate_04_journey_producer_repo_invalid_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    mgmt_ai = json.loads(paths["mgmt_ai"].read_text())
    mgmt_ai["producer"]["repository"] = "untrusted/repo"
    paths["mgmt_ai"].write_text(json.dumps(mgmt_ai))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "is not an allowed repository" in str(gate_04.error)


def test_gate_04_journey_github_run_failed_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(
            github_runs_override={
                "https://api.github.com/repos/ajoe734/execute-plans/actions/runs/12347": _github_run(
                    repository="ajoe734/execute-plans",
                    run_id="12347",
                    workflow="pfg-mgmt-journey-e2e-20260820-hosted-acceptance.yml",
                    head_sha=FE_SHA,
                    conclusion="failure",
                )
            }
        ),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "GitHub run is not a successful exact-head run" in str(gate_04.error)


def test_gate_04_journey_github_run_head_sha_mismatch_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(
            github_runs_override={
                "https://api.github.com/repos/ajoe734/pantheon/actions/runs/12345": _github_run(
                    repository="ajoe734/pantheon",
                    run_id="12345",
                    workflow="nonprod-deploy.yml",
                    head_sha="0" * 40,
                )
            }
        ),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next(r for r in report.gate_results if r.gate_id == "gate_04_authenticated_product_journeys")
    assert gate_04.status == "FAILED"
    assert "GitHub run is not a successful exact-head run" in str(gate_04.error)


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


def test_gate_05_schema_mismatch_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    disp = json.loads(paths["code_disposition"].read_text())
    disp["schema_version"] = "invalid.schema"
    paths["code_disposition"].write_text(json.dumps(disp))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_05 = next(r for r in report.gate_results if r.gate_id == "gate_05_code_disposition_and_simplification")
    assert gate_05.status == "FAILED"
    assert "code disposition schema_version" in str(gate_05.error)


def test_gate_05_task_id_mismatch_fails(tmp_path: Path) -> None:
    paths = _write_evidence_files(tmp_path)
    disp = json.loads(paths["code_disposition"].read_text())
    disp["task_id"] = "WRONG-TASK-ID"
    paths["code_disposition"].write_text(json.dumps(disp))

    verifier = ProductFunctionalClosureVerifier(
        _config(tmp_path, paths),
        transport=_transport(),
    )
    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_05 = next(r for r in report.gate_results if r.gate_id == "gate_05_code_disposition_and_simplification")
    assert gate_05.status == "FAILED"
    assert "code disposition task_id" in str(gate_05.error)


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
        "--source-runtime-evidence", str(paths["source_runtime"]),
        "--paper-runtime-evidence", str(paths["paper_runtime"]),
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
