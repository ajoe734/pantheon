"""Tests for the dev/root workflow_dispatch restore-accepted-baseline mode.

Covers:
1. Workflow inputs (restore_accepted_baseline_run_id and
   restore_accepted_baseline_run_attempt defaulting to "1") and job gating:
   no candidate build or deploy, no frontend gate or switch, and no paper or
   chain probes run in this mode.
2. Fetching attempt 1 artifacts while the referenced run is on a later attempt.
3. Refusal when the manifest's previous backend/frontend pair differs from
   hosted deployment.json.
4. Refusal when there is no live /bff/version drift or when the drifted SHA is
   not contained in Pantheon dev.
5. Whole-stack restore execution (dev_release_artifact_driver.py restore
   --target baseline --scope stack) with the current controller driver and
   streaming Source probe against a prior run's captured baseline manifest,
   followed by readback verification, plus non-zero exit with driver failure
   reason and lease quarantine on failure.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import yaml

import pytest

from scripts import capture_dev_artifact_baseline as capture
from scripts import dev_release_artifact_driver as driver_mod
from scripts import dev_release_artifacts as artifacts_mod
from scripts import fetch_dev_artifact_evidence as restore_mod
from scripts.test_dev_release_artifact_driver import admit, case as driver_case, execute, seal  # noqa: F401
from scripts.test_fetch_dev_artifact_evidence import _sealed_attempt_fixtures, archive

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/nonprod-deploy.yml"
WORKFLOW_TEXT = WORKFLOW_PATH.read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(WORKFLOW_TEXT)
INPUTS = (WORKFLOW.get("on") or WORKFLOW.get(True))["workflow_dispatch"]["inputs"]
JOBS = WORKFLOW["jobs"]
RESTORE_JOB = JOBS["restore-accepted-baseline"]
RESTORE_STEPS = {step["name"]: step for step in RESTORE_JOB["steps"]}


def _accepted_deployment_json(backend_sha: str, frontend_sha: str) -> dict:
    return {
        "schemaVersion": 1,
        "app": "execute-plans",
        "repository": "ajoe734/execute-plans",
        "sourceBranch": "dev",
        "deploymentState": "accepted",
        "bffCommitEvidence": True,
        "bffCommit": backend_sha,
        "bffSourceCommitSha": backend_sha,
        "commit": frontend_sha,
        "frontendSha": frontend_sha,
        "bff": {
            "baseUrl": "https://api.dev.mvl-cap.tw",
            "sourceCommitSha": backend_sha,
        },
        "frontend": {
            "commitSha": frontend_sha,
        },
    }


def test_workflow_inputs_and_exclusive_restore_only_mode() -> None:
    assert "restore_accepted_baseline_run_id" in INPUTS
    assert INPUTS["restore_accepted_baseline_run_id"]["default"] == ""
    assert INPUTS["restore_accepted_baseline_run_id"]["type"] == "string"
    assert "restore_accepted_baseline_run_attempt" in INPUTS
    assert INPUTS["restore_accepted_baseline_run_attempt"]["default"] == "1"
    assert INPUTS["restore_accepted_baseline_run_attempt"]["type"] == "string"

    # When restore_accepted_baseline_run_id is provided, no candidate deploy,
    # frontend coordination, isolated qualification, or hosted chain job runs.
    for job_name in ("deploy-dev", "isolated-l12-provider-qualification", "hosted-all12-chain-phase"):
        assert "inputs.restore_accepted_baseline_run_id == ''" in JOBS[job_name]["if"]
    assert JOBS["coordinate-dev-release"]["needs"] == ["deploy-dev"]

    assert "inputs.restore_accepted_baseline_run_id != ''" in RESTORE_JOB["if"]
    assert "!inputs.isolated_provider_qualification" in RESTORE_JOB["if"]
    assert "!inputs.hosted_all12_chain" in RESTORE_JOB["if"]
    assert "needs" not in RESTORE_JOB

    # No candidate build/deploy, no frontend gate/switch, and no paper/chain probes.
    restore_yaml = yaml.safe_dump(RESTORE_JOB)
    for forbidden in (
        "cross_repo_release_controller.py",
        "deploy-dev-vm.sh",
        "restore-sealed",
        "seal-candidate",
        "bootstrap_dev_paper_baseline.py",
        "run_dev_paper_baseline_with_diagnostics.sh",
        "run_isolated_l12_runtime_e2e.py",
        "test_hosted_chain_phase",
        "retire_dev_paper_release_cases.py",
        "run_dev_paired_readonly_qa.py",
        "--refresh-only",
    ):
        assert forbidden not in restore_yaml


def test_preflight_guard_rejects_non_root_component_and_probe_or_drill_flags(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    subprocess.run(["git", "init", "-q", "-b", "dev", str(remote)], check=True)
    for key, value in (("user.name", "t"), ("user.email", "t@t")):
        subprocess.run(["git", "-C", str(remote), "config", key, value], check=True)
    (remote / "f").write_text("1", encoding="utf-8")
    subprocess.run(["git", "-C", str(remote), "add", "."], check=True)
    subprocess.run(["git", "-C", str(remote), "commit", "-qm", "c1"], check=True)
    sha = subprocess.check_output(["git", "-C", str(remote), "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["git", "-C", str(remote), "update-ref", "refs/remotes/origin/dev", sha], check=True)

    script = RESTORE_STEPS["Fetch prior run attempt baseline and verify drift preconditions"]["run"]
    # Extract the pre-network validation prefix before `evidence_dir=`.
    guard_prefix = script.split("evidence_dir=", 1)[0]
    base_env = {
        **os.environ,
        "GITHUB_REF": "refs/heads/dev",
        "GITHUB_SHA": sha,
        "TARGET_COMPONENT": "root",
        "ALLOW_DIRTY": "false",
        "DEV_AUTH_PROFILE": "strict",
        "COMPENSATION_DRILL": "false",
        "ISOLATED_REQUESTED": "false",
        "HOSTED_CHAIN_REQUESTED": "false",
        "ROUNDTRIP_REQUESTED": "false",
        "BOOTSTRAP_EMPTY_HOST": "false",
        "PPL_ALLOC_009_DEV_PROOF_ENABLED": "false",
        "BOUNDED_SOURCE_REFRESH_ENABLED": "false",
        "RUN_EVOLUTION_DISPATCH_PROBE": "false",
        "RUN_LOOP_PROD_TEL_002_PROBE": "false",
        "PRIOR_RUN_ID": "43718489118",
        "PRIOR_RUN_ATTEMPT": "1",
    }

    def run_guard(**overrides):
        return subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", guard_prefix],
            cwd=remote,
            env={**base_env, **overrides},
            capture_output=True,
            text=True,
        )

    assert run_guard(TARGET_COMPONENT="root").returncode == 0
    assert run_guard(TARGET_COMPONENT="auto").returncode == 0
    assert run_guard(TARGET_COMPONENT="bff").returncode != 0
    assert run_guard(DEV_AUTH_PROFILE="permissive-stub").returncode != 0
    assert run_guard(ALLOW_DIRTY="true").returncode != 0
    assert run_guard(COMPENSATION_DRILL="true").returncode != 0
    assert run_guard(PPL_ALLOC_009_DEV_PROOF_ENABLED="true").returncode != 0
    assert run_guard(BOUNDED_SOURCE_REFRESH_ENABLED="true").returncode != 0
    assert run_guard(RUN_EVOLUTION_DISPATCH_PROBE="true").returncode != 0
    assert run_guard(RUN_LOOP_PROD_TEL_002_PROBE="true").returncode != 0
    assert run_guard(PRIOR_RUN_ID="0").returncode != 0
    assert run_guard(PRIOR_RUN_ATTEMPT="abc").returncode != 0


def test_fetch_attempt_1_artifacts_when_referenced_run_is_on_attempt_2(tmp_path: Path) -> None:
    fixtures = _sealed_attempt_fixtures(run_id="43718489118", attempt="1")
    attempt2 = _sealed_attempt_fixtures(run_id="43718489118", attempt="2")
    listing = {
        "total_count": 4,
        "artifacts": [
            {
                "id": 24163830735,
                "name": "pantheon-dev-artifact-baseline-43718489118-2",
                "expired": False,
                "digest": "sha256:" + attempt2["baseline_zip_sha256"],
                "size_in_bytes": len(attempt2["baseline_zip"]),
                "workflow_run": {"id": 43718489118, "run_attempt": 2},
            },
            {
                "id": 14163830735,
                "name": "pantheon-dev-artifact-baseline-43718489118-1",
                "expired": False,
                "digest": "sha256:" + fixtures["baseline_zip_sha256"],
                "size_in_bytes": len(fixtures["baseline_zip"]),
                "workflow_run": {"id": 43718489118, "run_attempt": 2},
            },
            {
                "id": 14164431785,
                "name": "pantheon-dev-artifact-candidate-43718489118-1",
                "expired": False,
                "digest": "sha256:" + fixtures["candidate_zip_sha256"],
                "size_in_bytes": len(fixtures["candidate_zip"]),
                "workflow_run": {"id": 43718489118, "run_attempt": 2},
            },
        ],
    }

    def api(path: str) -> bytes:
        if path == "repos/ajoe734/pantheon/actions/runs/43718489118/artifacts":
            return json.dumps(listing).encode()
        if path == "repos/ajoe734/pantheon/actions/artifacts/14163830735/zip":
            return fixtures["baseline_zip"]
        if path == "repos/ajoe734/pantheon/actions/artifacts/14164431785/zip":
            return fixtures["candidate_zip"]
        raise AssertionError(f"unexpected API call: {path}")

    b_out = restore_mod.fetch(
        kind="baseline",
        run_id="43718489118",
        attempt="1",
        output_dir=tmp_path / "baseline",
        api=api,
    )
    c_out = restore_mod.fetch(
        kind="candidate",
        run_id="43718489118",
        attempt="1",
        output_dir=tmp_path / "candidate",
        api=api,
    )
    assert b_out["artifact_id"] == "14163830735"
    assert b_out["attempt"] == "1"
    assert b_out["manifest_sha256"] == fixtures["manifest_sha256"]
    assert b_out["previous_backend_sha"] == "94b73806ae004049f82f26d1d225f290086a4942"
    assert b_out["previous_frontend_sha"] == "87fc32ab860325573a205500ff381092c2012617"
    assert c_out["artifact_id"] == "14164431785"
    assert c_out["candidate_image_manifest_sha256"] == fixtures["candidate_sha256"]


@pytest.mark.parametrize("mismatch_kind", ["backend", "frontend", "standby_state"])
def test_refuses_when_manifest_previous_pair_differs_from_deployment_json(mismatch_kind: str) -> None:
    fixtures = _sealed_attempt_fixtures()
    manifest = fixtures["manifest"]
    deployment = _accepted_deployment_json(
        "94b73806ae004049f82f26d1d225f290086a4942",
        "87fc32ab860325573a205500ff381092c2012617",
    )
    if mismatch_kind == "backend":
        other = "1" * 40
        deployment["bffCommit"] = other
        deployment["bffSourceCommitSha"] = other
        deployment["bff"]["sourceCommitSha"] = other
    elif mismatch_kind == "frontend":
        other = "2" * 40
        deployment["commit"] = other
        deployment["frontendSha"] = other
        deployment["frontend"]["commitSha"] = other
    else:
        deployment["deploymentState"] = "standby"

    with pytest.raises(capture.CaptureError, match="deployment.json"):
        restore_mod.verify_restore_accepted_baseline_preconditions(
            baseline_manifest=manifest,
            deployment_manifest=deployment,
            bff_version={"source_commit_sha": "725836718295f445fd9923790b58ed29842d9869"},
            is_ancestor=lambda _sha: True,
        )


def test_refuses_when_there_is_no_drift_or_drifted_sha_not_in_pantheon_dev() -> None:
    fixtures = _sealed_attempt_fixtures()
    manifest = fixtures["manifest"]
    deployment = _accepted_deployment_json(
        "94b73806ae004049f82f26d1d225f290086a4942",
        "87fc32ab860325573a205500ff381092c2012617",
    )
    # 1. No drift: live /bff/version already reports the accepted backend SHA.
    with pytest.raises(capture.CaptureError, match="no drift to restore"):
        restore_mod.verify_restore_accepted_baseline_preconditions(
            baseline_manifest=manifest,
            deployment_manifest=deployment,
            bff_version={"source_commit_sha": "94b73806ae004049f82f26d1d225f290086a4942"},
            is_ancestor=lambda _sha: True,
        )

    # 2. Drift SHA is not contained in Pantheon dev.
    with pytest.raises(capture.CaptureError, match="not contained in Pantheon dev"):
        restore_mod.verify_restore_accepted_baseline_preconditions(
            baseline_manifest=manifest,
            deployment_manifest=deployment,
            bff_version={"source_commit_sha": "725836718295f445fd9923790b58ed29842d9869"},
            is_ancestor=lambda sha: sha != "725836718295f445fd9923790b58ed29842d9869",
        )


def test_whole_stack_restore_succeeds_with_current_controller_and_streaming_source_probe(
    driver_case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from scripts.test_dev_release_artifact_driver import _attach_real_source_codec

    # Configure a real Source journal on driver_case so capture_stack and
    # restore_stack exercise the current controller's streaming Source probe.
    _attach_real_source_codec(driver_case, tmp_path)
    driver_case.docker.tags["pantheon-source-ingest"] = driver_case.docker.source_row["image_id"]

    # Capture v2 stack baseline under prior controller identity (simulating 43718489118 attempt 1).
    prior_controller_sha = "725836718295f445fd9923790b58ed29842d9869"
    driver_case.args.controller_sha = prior_controller_sha
    driver_case.args.run_id = "43718489118"
    driver_case.args.attempt = "1"
    baseline_res = seal(driver_case, admit_candidate=False)
    receipt = admit(driver_case)

    # Simulate failed compensation leaving the BFF containers and live /bff/version
    # on the rejected candidate while deployment.json remains on the accepted baseline pair.
    for service, row in driver_case.docker.built.items():
        driver_case.docker.containers[service]["image_id"] = row["image_id"]
    driver_case.http.source = driver_case.args.candidate_backend_sha
    fe_manifest_before = driver_case.release / "deployment.json"
    fe_bytes_before = fe_manifest_before.read_bytes()

    monkeypatch.setattr(capture, "ARTIFACT_ROOT", driver_mod.ROOT)
    monkeypatch.setattr(restore_mod, "ARTIFACT_ROOT", driver_mod.ROOT)
    monkeypatch.setitem(restore_mod.validate_receipt.__globals__, "ARTIFACT_ROOT", driver_mod.ROOT)
    original_matches = capture.matches
    monkeypatch.setattr(
        capture,
        "matches",
        lambda value, pattern: original_matches(
            value, pattern.replace("/var/www/pantheon-dev-fe-releases", os.fspath(driver_mod.FE_STORE))
        ),
    )
    monkeypatch.setattr(restore_mod, "matches", capture.matches)

    work_dir = tmp_path / "work"
    work_dir.mkdir(mode=0o700)
    baseline_file = work_dir / "artifact-baseline.json"
    baseline_raw = capture.encoded(baseline_res["manifest"])
    baseline_file.write_bytes(baseline_raw)
    baseline_file.chmod(0o600)

    receipt_file = work_dir / "candidate-receipt.json"
    receipt_file.write_bytes(capture.encoded(receipt))
    receipt_file.chmod(0o600)

    deployment_file = work_dir / "deployment.json"
    deployment_file.write_bytes(fe_bytes_before)
    deployment_file.chmod(0o600)

    bff_version_file = work_dir / "bff-version.json"
    bff_version_file.write_text(
        json.dumps({"source_commit_sha": driver_case.args.candidate_backend_sha}) + "\n",
        encoding="utf-8",
    )
    bff_version_file.chmod(0o600)

    # Prepare a local git checkout representing the current controller HEAD
    # that also contains the previous_backend_sha commit with the baseline compose file.
    controller_repo = tmp_path / "controller-repo"
    controller_repo.mkdir()
    subprocess.run(["git", "-C", str(controller_repo), "init", "-q", "-b", "dev"], check=True)
    for k, v in (("user.name", "t"), ("user.email", "t@t")):
        subprocess.run(["git", "-C", str(controller_repo), "config", k, v], check=True)
    (controller_repo / "scripts").mkdir()
    for name in ("dev_release_artifact_driver.py", "dev_release_artifacts.py", "deploy_nonprod_vm.sh"):
        (controller_repo / "scripts" / name).write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
    (controller_repo / "docker-compose.yml").write_bytes(driver_case.baseline_compose.read_bytes())
    subprocess.run(["git", "-C", str(controller_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(controller_repo), "commit", "-qm", "current controller"], check=True)
    current_controller_sha = subprocess.check_output(
        ["git", "-C", str(controller_repo), "rev-parse", "HEAD"], text=True
    ).strip()

    monkeypatch.setattr(
        subprocess,
        "run",
        _git_show_compose_shim(subprocess.run, driver_case.args.previous_backend_sha, driver_case.baseline_compose.read_bytes()),
    )

    def guarded_runner(remote_script: bytes) -> subprocess.CompletedProcess[bytes]:
        # Verify the remote script installs the current controller and invokes
        # `restore` with `--scope stack` and the prior run's `--controller-sha`.
        text = remote_script.decode("utf-8")
        assert f"PANTHEON_DEV_ARTIFACT_DRIVER_CONTROLLER_SHA={current_controller_sha}" in text
        assert f"PANTHEON_DEV_ARTIFACT_CONTROLLER_SHA={prior_controller_sha}" in text
        assert "PANTHEON_DEV_ARTIFACT_SCOPE=stack" in text
        driver_case.args.command = "restore"
        driver_case.args.scope = "stack"
        driver_case.args.target = "baseline"
        driver_case.args.compose_file = driver_case.baseline_compose
        os.environ["PANTHEON_BFF_AUTH_STUB"] = "false"
        os.environ["PANTHEON_BFF_AUTH_MODE"] = "strict"
        readback = driver_mod.run(
            driver_case.args,
            docker=driver_case.docker,
            http=driver_case.http,
            barrier=driver_case.barrier,
        )
        out = b"PANTHEON_ARTIFACT_READBACK_V1 " + capture.encoded(readback)
        return subprocess.CompletedProcess(["guarded"], 0, stdout=out, stderr=b"")

    readback_out = work_dir / "readback.json"
    result = restore_mod.execute_restore_accepted_baseline(
        baseline_path=baseline_file,
        expected_baseline_sha256=baseline_res["manifest_sha256"],
        candidate_receipt_path=receipt_file,
        deployment_manifest_path=deployment_file,
        bff_version_path=bff_version_file,
        repo_root=controller_repo,
        output_readback_path=readback_out,
        env={"PANTHEON_CURRENT_CONTROLLER_SHA": current_controller_sha},
        is_ancestor=lambda _sha: True,
        guarded_runner=guarded_runner,
    )
    assert result["operation"] == "restore"
    assert result["stack"]["scope"] == "stack"
    assert result["public"]["source_sha"] == driver_case.args.previous_backend_sha
    assert driver_case.http.source == driver_case.args.previous_backend_sha
    for service in artifacts_mod.SERVICES:
        assert (
            driver_case.docker.containers[service]["image_id"]
            == baseline_res["manifest"]["image_bundle"]["services"][service]["image_id"]
        )
    assert fe_manifest_before.read_bytes() == fe_bytes_before
    assert readback_out.is_file()


def _git_show_compose_shim(real_run, previous_backend_sha: str, compose_bytes: bytes):
    def _run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) >= 5 and cmd[0] == "git" and cmd[3] == "show" and cmd[4] == f"{previous_backend_sha}:docker-compose.yml":
            return subprocess.CompletedProcess(cmd, 0, stdout=compose_bytes, stderr=b"")
        return real_run(cmd, *args, **kwargs)

    return _run


def test_restore_failure_propagates_driver_reason_and_keeps_lease_quarantined(
    driver_case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    prior_controller_sha = "725836718295f445fd9923790b58ed29842d9869"
    driver_case.args.controller_sha = prior_controller_sha
    driver_case.args.run_id = "43718489118"
    driver_case.args.attempt = "1"
    baseline_res = seal(driver_case, admit_candidate=False)
    receipt = admit(driver_case)

    monkeypatch.setattr(capture, "ARTIFACT_ROOT", driver_mod.ROOT)
    monkeypatch.setattr(restore_mod, "ARTIFACT_ROOT", driver_mod.ROOT)
    monkeypatch.setitem(restore_mod.validate_receipt.__globals__, "ARTIFACT_ROOT", driver_mod.ROOT)
    original_matches = capture.matches
    monkeypatch.setattr(
        capture,
        "matches",
        lambda value, pattern: original_matches(
            value, pattern.replace("/var/www/pantheon-dev-fe-releases", os.fspath(driver_mod.FE_STORE))
        ),
    )
    monkeypatch.setattr(restore_mod, "matches", capture.matches)

    work_dir = tmp_path / "work"
    work_dir.mkdir(mode=0o700)
    baseline_file = work_dir / "artifact-baseline.json"
    baseline_file.write_bytes(capture.encoded(baseline_res["manifest"]))
    baseline_file.chmod(0o600)
    receipt_file = work_dir / "candidate-receipt.json"
    receipt_file.write_bytes(capture.encoded(receipt))
    receipt_file.chmod(0o600)
    deployment_file = work_dir / "deployment.json"
    deployment_file.write_bytes((driver_case.release / "deployment.json").read_bytes())
    deployment_file.chmod(0o600)
    bff_version_file = work_dir / "bff-version.json"
    bff_version_file.write_text(
        json.dumps({"source_commit_sha": driver_case.args.candidate_backend_sha}) + "\n",
        encoding="utf-8",
    )
    bff_version_file.chmod(0o600)

    controller_repo = tmp_path / "controller-repo"
    controller_repo.mkdir()
    subprocess.run(["git", "-C", str(controller_repo), "init", "-q", "-b", "dev"], check=True)
    for k, v in (("user.name", "t"), ("user.email", "t@t")):
        subprocess.run(["git", "-C", str(controller_repo), "config", k, v], check=True)
    (controller_repo / "scripts").mkdir()
    for name in ("dev_release_artifact_driver.py", "dev_release_artifacts.py", "deploy_nonprod_vm.sh"):
        (controller_repo / "scripts" / name).write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
    (controller_repo / "docker-compose.yml").write_bytes(driver_case.baseline_compose.read_bytes())
    subprocess.run(["git", "-C", str(controller_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(controller_repo), "commit", "-qm", "current controller"], check=True)
    current_controller_sha = subprocess.check_output(
        ["git", "-C", str(controller_repo), "rev-parse", "HEAD"], text=True
    ).strip()

    monkeypatch.setattr(
        subprocess,
        "run",
        _git_show_compose_shim(subprocess.run, driver_case.args.previous_backend_sha, driver_case.baseline_compose.read_bytes()),
    )

    driver_failure = artifacts_mod.failure_record(
        artifacts_mod.SourceCompatibilityError("probe_killed"), "restore"
    )
    mixed_line = (
        driver_mod.MIXED_MARKER
        + json.dumps({"kind": "stack", "services": ["source-ingest"]}, sort_keys=True)
        + "\n"
    ).encode()

    def failing_guarded_runner(_remote_script: bytes) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            ["guarded"],
            75,
            stdout=mixed_line,
            stderr=(json.dumps(driver_failure, sort_keys=True) + "\n").encode(),
        )

    summary_file = tmp_path / "step-summary.md"
    readback_out = work_dir / "readback.json"
    with pytest.raises(restore_mod.RestoreExecutionError) as exc_info:
        restore_mod.execute_restore_accepted_baseline(
            baseline_path=baseline_file,
            expected_baseline_sha256=baseline_res["manifest_sha256"],
            candidate_receipt_path=receipt_file,
            deployment_manifest_path=deployment_file,
            bff_version_path=bff_version_file,
            repo_root=controller_repo,
            output_readback_path=readback_out,
            env={
                "PANTHEON_CURRENT_CONTROLLER_SHA": current_controller_sha,
                "GITHUB_STEP_SUMMARY": str(summary_file),
            },
            is_ancestor=lambda _sha: True,
            guarded_runner=failing_guarded_runner,
        )
    assert exc_info.value.exit_code == 75
    assert exc_info.value.failure == driver_failure
    assert "probe_killed" in str(exc_info.value)
    assert exc_info.value.mixed is not None and "source-ingest" in exc_info.value.mixed
    assert not readback_out.exists()

    # Verify the workflow lease_cleanup step quarantines the lease and never calls `release` when RESTORE_OUTCOME != success.
    cleanup_script = RESTORE_STEPS["Stop heartbeat and release lease only after complete restore success"]["run"]
    assert 'if [[ "${RESTORE_OUTCOME}" != "success" || -e "${LEASE_FAILURE_FILE}" ]]; then' in cleanup_script
    assert 'echo "Restore accepted baseline was incomplete; lease quarantined until TTL." >&2' in cleanup_script
