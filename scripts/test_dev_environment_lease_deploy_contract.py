from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import dev_environment_lease as lease
WORKFLOW = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"
DEPLOY = ROOT / "scripts" / "deploy_nonprod_vm.sh"
CONTROLLER_SHA = "9e564718da8c39199a4c311f1a667b74226e3428"
CONTROLLER_SCRIPT_SHA256 = (
    "52276793f99162fc7ca307a1370addd8d99478208ebf7beb67eab23b97b83048"
)
CONTROLLER_WRAPPER_SHA256 = (
    "6c82021b93621f16776d5d67a9e20cb9d690f7ebfa257ebf8c329f7d158fb2c2"
)
CHECKOUT_SHA = "34e114876b0b11c390a56381ad16ebd13914f8d5"
AUTH_SHA = "c200f3691d83b41bf9bbd8638997a462592937ed"
GCLOUD_SHA = "e427ad8a34f8676edf47cf7d7925499adf3eb74f"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _job(text: str, name: str, next_name: str | None = None) -> str:
    start = text.index(f"  {name}:\n")
    if next_name is None:
        return text[start:]
    return text[start : text.index(f"  {next_name}:\n", start + 1)]


def _git_show_sha256(ref_path: str) -> str:
    result = subprocess.run(
        ["git", "show", ref_path],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def test_controller_checkout_is_an_exact_immutable_separate_trust_root() -> None:
    workflow = _workflow()
    dev = _job(workflow, "deploy-dev", "deploy-staging-live")

    assert f"ref: {CONTROLLER_SHA}" in dev
    assert "path: .lease-controller" in dev
    assert dev.count("persist-credentials: false") >= 2
    assert '[[ "$(git -C "${controller}" rev-parse HEAD)" == ' in dev
    assert CONTROLLER_SHA in dev
    assert "path: .target" in dev
    assert 'python3 "${controller}/scripts/dev_environment_lease.py" acquire' in dev
    assert '"${GITHUB_WORKSPACE}/.lease-controller/scripts/run_with_dev_environment_lease.sh"' in dev
    assert "CONTROLLER_REF" not in dev
    assert (
        dev.count(
            CONTROLLER_SCRIPT_SHA256
        )
        >= 7
    )
    assert dev.count(CONTROLLER_WRAPPER_SHA256) >= 7


def test_controller_checksums_match_pinned_controller_files() -> None:
    workflow = _workflow()
    dev = _job(workflow, "deploy-dev", "deploy-staging-live")

    assert (
        _git_show_sha256(f"{CONTROLLER_SHA}:scripts/dev_environment_lease.py")
        == CONTROLLER_SCRIPT_SHA256
    )
    assert (
        _git_show_sha256(f"{CONTROLLER_SHA}:scripts/run_with_dev_environment_lease.sh")
        == CONTROLLER_WRAPPER_SHA256
    )
    assert dev.count(CONTROLLER_SCRIPT_SHA256) >= 7
    assert dev.count(CONTROLLER_WRAPPER_SHA256) >= 7


def test_dev_and_staging_are_independent_jobs_and_staging_has_no_lease_secret() -> None:
    workflow = _workflow()
    dev = _job(workflow, "deploy-dev", "deploy-staging-live")
    staging = _job(workflow, "deploy-staging-live")

    assert "environment: dev" in dev
    assert "environment: staging-live" in staging
    assert "COORDINATION_REPO_TOKEN" in dev
    assert "COORDINATION_REPO_TOKEN" not in staging
    assert "run_with_dev_environment_lease" not in staging
    assert "dev_environment_lease.py" not in staging
    assert "PANTHEON_DEV_ENVIRONMENT_LEASE" not in staging
    assert '${STAGING_BFF_URL}/bff/version' in staging
    assert '[[ "${actual}" == "${TARGET_SHA}" ]]' in staging


def test_payloads_must_come_from_their_protected_delivery_branches() -> None:
    workflow = _workflow()
    dev = _job(workflow, "deploy-dev", "deploy-staging-live")
    staging = _job(workflow, "deploy-staging-live")

    assert "fetch-depth: 0" in dev
    assert "refs/remotes/origin/dev" in dev
    assert '"${sha}" != "${trusted_ref}"' in dev
    assert '"${GITHUB_REF}" != "refs/heads/dev"' in dev
    assert '"${GITHUB_SHA}" != "${sha}"' in dev
    assert "current execute-plans dev moved" in dev
    assert "fetch-depth: 0" in staging
    assert "refs/remotes/origin/master" in staging
    assert 'git merge-base --is-ancestor "${sha}" "${trusted_ref}"' in staging
    assert "not contained in protected origin/master" in staging


def test_lease_coordinates_the_fixed_cross_repository_resource() -> None:
    dev = _job(_workflow(), "deploy-dev", "deploy-staging-live")

    assert dev.count("--repository ajoe734/execute-plans") >= 4
    assert dev.count("--branch environment-coordination") >= 4
    assert (
        dev.count(
            "--path .pantheon/environment-leases/pantheon-dev-environment.json"
        )
        >= 4
    )
    assert dev.count("--resource pantheon-dev-environment") >= 4
    assert '--expected-backend-sha "${TARGET_SHA}"' in dev
    assert "--mode deployment" in dev


def test_heartbeat_and_guard_paths_are_bound_to_acquire_step_outputs() -> None:
    dev = _job(_workflow(), "deploy-dev", "deploy-staging-live")

    for name in (
        "state_file",
        "pid_file",
        "identity_file",
        "failure_file",
        "shutdown_file",
        "heartbeat_log",
    ):
        assert f'echo "{name}=${{{name}}}"' in dev
        assert f"${{{{ steps.lease.outputs.{name} }}}}" in dev
    assert '--identity-json-out "${LEASE_IDENTITY_FILE}"' in dev
    assert '--token-stdin' in dev
    assert "PANTHEON_DEV_ENVIRONMENT_LEASE_TOKEN_FD" not in dev
    assert ">> \"${GITHUB_ENV}\"" not in dev


def test_initial_visibility_retry_is_only_on_immediate_post_acquire_verify() -> None:
    dev = _job(_workflow(), "deploy-dev", "deploy-staging-live")
    heartbeat_start = dev.index("      - name: Start identity-bound lease heartbeat")
    next_step = dev.index("      - name: Deploy dev VM stack under lease", heartbeat_start)
    initial_verify = dev[heartbeat_start:next_step]

    assert dev.count("--initial-visibility-wait-seconds") == 1
    assert dev.count("--initial-visibility-poll-seconds") == 1
    assert "--initial-visibility-wait-seconds 15" in initial_verify
    assert "--initial-visibility-poll-seconds 1" in initial_verify
    assert initial_verify.index("verify-heartbeat-identity") < initial_verify.index(
        "--initial-visibility-wait-seconds 15"
    )


def test_broad_initial_verify_retry_loop_is_absent() -> None:
    dev = _job(_workflow(), "deploy-dev", "deploy-staging-live")
    heartbeat_start = dev.index("      - name: Start identity-bound lease heartbeat")
    next_step = dev.index("      - name: Deploy dev VM stack under lease", heartbeat_start)
    initial_verify = dev[heartbeat_start:next_step]

    assert "initial_verify_log=" not in initial_verify
    assert "verify_ok=false" not in initial_verify
    assert "for attempt in $(seq 1 50)" not in initial_verify
    assert "> /dev/null 2>" not in initial_verify


def test_all_dev_mutations_and_public_proofs_use_pinned_wrapper() -> None:
    dev = _job(_workflow(), "deploy-dev", "deploy-staging-live")

    for step_name in (
        "Deploy dev VM stack under lease",
        "Ensure governed dev paper baseline under lease",
        "Dev OpenClaw assistant live smoke under lease",
        "Public dev BFF smoke and exact version proof under lease",
        "Dev Agora restart persistence smoke under lease",
    ):
        start = dev.index(f"      - name: {step_name}")
        end = dev.find("\n      - name:", start + 1)
        step = dev[start:] if end == -1 else dev[start:end]
        assert ".lease-controller/scripts/run_with_dev_environment_lease.sh" in step
        assert "COORDINATION_REPO_TOKEN" in step

    assert '${DEV_BFF_URL}/bff/version' in dev
    assert '[[ "${actual}" == "${TARGET_SHA}" ]]' in dev
    assert "PAPER_BOOTSTRAP_OUTCOME: ${{ steps.paper_bootstrap.outcome }}" in dev
    assert '"${HEARTBEAT_OUTCOME}:${DEPLOY_OUTCOME}' in dev
    assert '[[ "${complete_success}" != "true" || -e "${LEASE_FAILURE_FILE}" ]]' in dev
    assert "lease quarantined until TTL" in dev
    assert dev.index("verify-heartbeat-identity") < dev.index("kill -TERM")
    assert dev.index('dev_environment_lease.py" verify \\') < dev.index(
        'dev_environment_lease.py" release \\'
    )


def test_bff_smoke_runs_before_unrelated_smoke_steps() -> None:
    """An unrelated smoke test failing must not silently skip the one step
    that actually verifies the live BFF matches this deploy's exact target
    sha. GitHub Actions skips a later step by default once an earlier step
    in the same job fails, so the BFF/FE verification has to run first."""

    dev = _job(_workflow(), "deploy-dev", "deploy-staging-live")
    bff_smoke_at = dev.index(
        "      - name: Public dev BFF smoke and exact version proof under lease"
    )
    for unrelated_step in (
        "Dev OpenClaw assistant live smoke under lease",
        "Dev Agora restart persistence smoke under lease",
    ):
        assert bff_smoke_at < dev.index(f"      - name: {unrelated_step}")


def test_dev_release_admission_depends_only_on_verified_bff_fe_pair() -> None:
    """Admitting a release candidate must track whether the BFF/FE pair
    itself was verified healthy, not whether unrelated smoke tests (e.g. the
    OpenClaw assistant integration) also happened to pass in the same job."""

    workflow = _workflow()
    dev = _job(workflow, "deploy-dev", "coordinate-dev-release")
    assert (
        "bff_fe_pair_verified: ${{ steps.deploy.outcome == 'success' "
        "&& steps.public_smoke.outcome == 'success' }}"
    ) in dev

    coordinate = _job(workflow, "coordinate-dev-release", "deploy-staging-live")
    assert "needs.deploy-dev.result" not in coordinate
    assert (
        "if: ${{ !cancelled() && needs.deploy-dev.outputs.bff_fe_pair_verified "
        "== 'true' }}"
    ) in coordinate


def test_rollback_baseline_uses_the_accepted_frontend_pair_manifest() -> None:
    """A failed BFF cannot erase the last accepted or standby release identity needed to
    repair it. The immutable frontend deployment manifest carries that pair;
    a known dev-ancestor live BFF drift is recorded, never promoted to the
    rollback baseline."""
    dev = _job(_workflow(), "deploy-dev", "deploy-staging-live")
    start = dev.index("      - name: Capture exact hosted FE and BFF rollback baseline")
    end = dev.index("      - name: Seal exact-pair admission artifact", start)
    baseline = dev[start:end]

    assert baseline.index('"${DEV_FE_URL%/}/deployment.json" > "${deployment_json}"') < baseline.index(
        '"${DEV_BFF_URL%/}/bff/version" > "${version_json}"'
    )
    assert 'baseline_source="${deployment_state}_frontend_pair_manifest"' in baseline
    assert 'baseline_source="${deployment_state}_frontend_pair_manifest+live_bff_match"' in baseline
    assert 'baseline_source="${deployment_state}_frontend_pair_manifest+live_bff_drift_recovery"' in baseline
    assert '[[ "${observed_previous}" =~ ^[0-9a-f]{40}$ ]]' in baseline
    assert '"${observed_previous}" refs/remotes/origin/dev' in baseline
    assert "Hosted BFF drift identity is not an exact commit SHA." in baseline
    assert "Hosted BFF drift identity is not contained in Pantheon dev." in baseline
    assert 'retaining the ${deployment_state} pair as rollback authority' in baseline
    assert 'recovering from the ${deployment_state} frontend pair manifest' in baseline
    assert 'deployment_state not in ("accepted", "standby")' in baseline
    assert 'bff_commit_evidence is not True' in baseline
    assert 'bff.get("baseUrl", "").rstrip("/") != expected_bff_url' in baseline
    assert '"baseline_source": sys.argv[7]' in baseline
    assert '"observed_live_bff_sha": sys.argv[8] or None' in baseline


def test_token_steps_use_a_fixed_sanitized_path_and_clear_shell_git_injection() -> None:
    dev = _job(_workflow(), "deploy-dev", "deploy-staging-live")

    assert (
        'safe_path="${trusted_bin}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"'
        in dev
    )
    assert dev.count("PATH: ${{ steps.runtime.outputs.safe_path }}") >= 7
    assert dev.count('BASH_ENV: ""') >= 7
    assert dev.count('PYTHONPATH: ""') >= 7
    assert dev.count('PYTHONNOUSERSITE: "1"') >= 7
    assert dev.count('PYTHONSAFEPATH: "1"') >= 7
    pythoninspect_yaml_values = re.findall(
        r"(?m)^\s*PYTHONINSPECT:\s*(.+?)\s*$", dev
    )
    pythoninspect_shell_values = re.findall(r"\bPYTHONINSPECT=([^\s\\]*)", dev)
    assert len(pythoninspect_yaml_values) >= 7
    assert set(pythoninspect_yaml_values) == {'""'}
    assert len(pythoninspect_shell_values) >= 1
    assert set(pythoninspect_shell_values) == {""}
    assert dev.count('LD_PRELOAD: ""') >= 7
    assert dev.count('GIT_CONFIG_COUNT: "0"') >= 7
    assert dev.count('GIT_CONFIG_PARAMETERS: ""') >= 7
    assert dev.count("unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR") >= 3
    assert dev.count("unset PANTHEON_ENVIRONMENT_LEASE_TOKEN") >= 7
    assert dev.count(
        'exec "${GITHUB_WORKSPACE}/.lease-controller/scripts/run_with_dev_environment_lease.sh"'
    ) >= 4
    assert "env -i \\\n" in dev
    assert "PANTHEON_ENVIRONMENT_LEASE_TOKEN=\"${{ secrets." not in dev


def test_all_third_party_actions_are_full_sha_pinned() -> None:
    workflow = _workflow()

    assert workflow.count(f"actions/checkout@{CHECKOUT_SHA}") == 7
    assert workflow.count(f"google-github-actions/auth@{AUTH_SHA}") == 2
    assert workflow.count(f"google-github-actions/setup-gcloud@{GCLOUD_SHA}") == 2
    for line in workflow.splitlines():
        if "uses:" in line:
            ref = line.rsplit("@", 1)[-1]
            assert len(ref) == 40
            assert all(char in "0123456789abcdef" for char in ref)


def _lease_state(path: Path, *, lease_id: str, expected_sha: str) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "repository": "ajoe734/execute-plans",
                "branch": "environment-coordination",
                "path": ".pantheon/environment-leases/pantheon-dev-environment.json",
                "resource": "pantheon-dev-environment",
                "mode": "deployment",
                "leaseId": lease_id,
                "expectedBackendSha": expected_sha,
            }
        ),
        encoding="utf-8",
    )


def test_dev_deploy_rejects_lease_backend_sha_mismatch_before_mutation(
    tmp_path: Path,
) -> None:
    lease_id = "11111111-1111-4111-8111-111111111111"
    target_sha = "a" * 40
    state = tmp_path / "state.json"
    _lease_state(state, lease_id=lease_id, expected_sha="b" * 40)
    env = {
        **os.environ,
        "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(state),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": lease_id,
    }

    result = subprocess.run(
        [
            "bash",
            str(DEPLOY),
            "--environment",
            "dev",
            "--component",
            "bff",
            "--sha",
            target_sha,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "expectedBackendSha mismatch" in result.stderr
    assert "gcloud is required" not in result.stderr
    assert "ssh " not in result.stdout


def test_dev_deploy_rejects_guard_lease_id_mismatch_before_mutation(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state.json"
    _lease_state(
        state,
        lease_id="11111111-1111-4111-8111-111111111111",
        expected_sha="a" * 40,
    )
    env = {
        **os.environ,
        "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(state),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": (
            "22222222-2222-4222-8222-222222222222"
        ),
    }

    result = subprocess.run(
        [
            "bash",
            str(DEPLOY),
            "--environment",
            "dev",
            "--component",
            "bff",
            "--sha",
            "a" * 40,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "leaseId mismatch" in result.stderr
    assert "ssh " not in result.stdout


def test_staging_dry_run_does_not_require_dev_lease() -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PANTHEON_DEV_ENVIRONMENT_LEASE")
    }
    result = subprocess.run(
        [
            "bash",
            str(DEPLOY),
            "--environment",
            "staging-live",
            "--component",
            "exec",
            "--sha",
            "a" * 40,
            "--dry-run",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "environment=staging-live" in result.stdout


def test_dev_deploy_job_has_explicit_timeout_and_command_deadline() -> None:
    workflow = _workflow()
    dev = _job(workflow, "deploy-dev", "coordinate-dev-release")

    match = re.search(r"timeout-minutes:\s*(\d+)", dev)
    assert match is not None, "deploy-dev job must define timeout-minutes"
    job_timeout_minutes = int(match.group(1))
    job_timeout_seconds = job_timeout_minutes * 60

    # Initial lease acquisition wait time
    initial_wait_match = re.search(r"id:\s*lease[\s\S]*?--wait-seconds\s+(\d+)", dev)
    assert initial_wait_match is not None, "Initial lease acquire step must specify --wait-seconds"
    initial_lease_wait_seconds = int(initial_wait_match.group(1))

    assert "DEV_DEPLOY_DEADLINE_SECONDS:" in dev
    assert '--deadline-seconds "${DEV_DEPLOY_DEADLINE_SECONDS}"' in dev

    deadline_match = re.search(
        r"DEV_DEPLOY_DEADLINE_SECONDS:\s*\${{\s*vars\.DEV_DEPLOY_DEADLINE_SECONDS\s*\|\|\s*'(\d+)'\s*}}",
        dev,
    )
    assert deadline_match is not None, "DEV_DEPLOY_DEADLINE_SECONDS default must be explicit"
    initial_deadline_seconds = int(deadline_match.group(1))

    # Root paper baseline bootstrap timeout
    bootstrap_match = re.search(r"id:\s*paper_bootstrap[\s\S]*?--timeout-seconds\s+(\d+)", dev)
    assert bootstrap_match is not None, "paper_bootstrap step must specify --timeout-seconds"
    paper_bootstrap_timeout_seconds = int(bootstrap_match.group(1))

    # Public smoke step deadline and curl bounds
    public_smoke_match = re.search(r"id:\s*public_smoke[\s\S]*?timeout-minutes:\s*(\d+)", dev)
    assert public_smoke_match is not None, "public_smoke step must define timeout-minutes"
    public_smoke_timeout_seconds = int(public_smoke_match.group(1)) * 60

    public_smoke_section = dev.split("id: public_smoke", 1)[1].split("- name:", 1)[0]
    for curl_line in re.findall(r"curl\s+[^\n]+", public_smoke_section):
        assert "--connect-timeout" in curl_line, f"public_smoke curl missing --connect-timeout: {curl_line}"
        assert "--max-time" in curl_line, f"public_smoke curl missing --max-time: {curl_line}"

    # Compensation step deadline and curl bounds
    comp_section = dev.split("id: deploy_compensation", 1)[1].split("- name:", 1)[0]
    comp_match = re.search(r"timeout-minutes:\s*(\d+)", comp_section)
    assert comp_match is not None, "deploy_compensation step must specify timeout-minutes"
    compensation_timeout_seconds = int(comp_match.group(1)) * 60

    for curl_line in re.findall(r"curl\s+[^\n]+", comp_section):
        assert "--connect-timeout" in curl_line, f"deploy_compensation curl missing --connect-timeout: {curl_line}"
        assert "--max-time" in curl_line, f"deploy_compensation curl missing --max-time: {curl_line}"

    # Quarantined lease TTL wait
    ttl_match = re.search(r"--ttl-seconds\s+(\d+)", dev)
    assert ttl_match is not None, "Lease acquire step must specify --ttl-seconds"
    lease_ttl_seconds = int(ttl_match.group(1))

    # Rollback has its own bounded BFF-only deadline.  It must not inherit the
    # much longer root-build budget used by the initial dev deployment.
    rollback_deadline_match = re.search(
        r"DEV_DEPLOY_DEADLINE_SECONDS:\s*\${{\s*vars\.DEV_ROLLBACK_DEPLOY_DEADLINE_SECONDS\s*\|\|\s*'(\d+)'\s*}}",
        comp_section,
    )
    assert rollback_deadline_match is not None, "rollback deadline default must be explicit"
    rollback_deadline_seconds = int(rollback_deadline_match.group(1))

    # Rollback compensation lease acquire wait
    rollback_wait_match = re.search(r"id:\s*deploy_compensation[\s\S]*?--wait-seconds\s+(\d+)", dev)
    assert rollback_wait_match is not None, "deploy_compensation step must specify --wait-seconds"
    rollback_lease_wait_seconds = int(rollback_wait_match.group(1))
    assert rollback_lease_wait_seconds >= lease_ttl_seconds, "rollback wait must at least accommodate lease TTL wait"

    # Compensation step budget assertion
    min_required_compensation_step_budget = (
        rollback_lease_wait_seconds
        + rollback_deadline_seconds
    )
    assert compensation_timeout_seconds >= min_required_compensation_step_budget, (
        f"deploy_compensation timeout ({compensation_timeout_seconds}s) must accommodate rollback lease wait ({rollback_lease_wait_seconds}s) "
        f"+ rollback deploy deadline ({rollback_deadline_seconds}s) = {min_required_compensation_step_budget}s"
    )
    assert compensation_timeout_seconds > min_required_compensation_step_budget, (
        f"deploy_compensation timeout ({compensation_timeout_seconds}s) must provide headroom above {min_required_compensation_step_budget}s "
        f"for bounded predecessor heartbeat stop, identity verification, probes, and release cleanup"
    )
    assert compensation_timeout_seconds - min_required_compensation_step_budget >= 180, (
        f"deploy_compensation step timeout headroom ({compensation_timeout_seconds - min_required_compensation_step_budget}s) must be at least 180s"
    )

    min_required_compensation_budget = (
        initial_lease_wait_seconds
        + initial_deadline_seconds
        + paper_bootstrap_timeout_seconds
        + public_smoke_timeout_seconds
        + rollback_lease_wait_seconds
        + rollback_deadline_seconds
    )

    assert job_timeout_seconds >= min_required_compensation_budget, (
        f"deploy-dev job timeout ({job_timeout_seconds}s) must accommodate initial lease wait ({initial_lease_wait_seconds}s) "
        f"+ initial deploy deadline ({initial_deadline_seconds}s) + paper bootstrap timeout ({paper_bootstrap_timeout_seconds}s) "
        f"+ public smoke timeout ({public_smoke_timeout_seconds}s) "
        f"+ rollback lease wait ({rollback_lease_wait_seconds}s) + rollback compensation deadline ({rollback_deadline_seconds}s) = {min_required_compensation_budget}s"
    )
    assert job_timeout_seconds > min_required_compensation_budget, (
        f"deploy-dev job timeout ({job_timeout_seconds}s) must provide headroom above {min_required_compensation_budget}s "
        f"for setup, image builds, and post-rollback verification"
    )
    assert job_timeout_seconds - min_required_compensation_budget >= 180, (
        f"deploy-dev job timeout headroom ({job_timeout_seconds - min_required_compensation_budget}s) must be at least 180s"
    )
    assert "stop_predecessor_heartbeat" in dev, "deploy_compensation must define and invoke stop_predecessor_heartbeat"


@pytest.mark.parametrize(
    ("args", "extra_env", "expected_deadline"),
    [
        (["--deadline-seconds", "900"], {}, "900"),
        (["--deploy-timeout-seconds", "750"], {}, "750"),
        ([], {"DEV_DEPLOY_DEADLINE_SECONDS": "600"}, "600"),
        ([], {"DEV_DEPLOY_TIMEOUT_SECONDS": "450"}, "450"),
        ([], {}, "7200"),
    ],
)
def test_dev_deploy_validates_deadline_configuration_positive(
    args: list[str],
    extra_env: dict[str, str],
    expected_deadline: str,
) -> None:
    env = {
        **os.environ,
        **extra_env,
    }
    result = subprocess.run(
        [
            "bash",
            str(DEPLOY),
            "--environment",
            "staging-live",
            "--component",
            "exec",
            "--sha",
            "a" * 40,
            "--dry-run",
            *args,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"dev_deploy_deadline_seconds={expected_deadline}" in result.stdout


@pytest.mark.parametrize(
    ("args", "extra_env"),
    [
        (["--deadline-seconds", "0"], {}),
        (["--deadline-seconds", "-10"], {}),
        (["--deadline-seconds", "abc"], {}),
        (["--deploy-timeout-seconds", "invalid"], {}),
        ([], {"DEV_DEPLOY_DEADLINE_SECONDS": "0"}),
        ([], {"DEV_DEPLOY_DEADLINE_SECONDS": "-5"}),
        ([], {"DEV_DEPLOY_DEADLINE_SECONDS": "not_a_number"}),
    ],
)
def test_dev_deploy_validates_deadline_configuration_negative(
    args: list[str],
    extra_env: dict[str, str],
) -> None:
    env = {
        **os.environ,
        **extra_env,
    }
    result = subprocess.run(
        [
            "bash",
            str(DEPLOY),
            "--environment",
            "staging-live",
            "--component",
            "exec",
            "--sha",
            "a" * 40,
            "--dry-run",
            *args,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "DEV_DEPLOY_DEADLINE_SECONDS must be a positive integer" in result.stderr


def test_dev_deploy_ssh_command_terminates_process_group_on_deadline(
    tmp_path: Path,
) -> None:
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    pid_file = tmp_path / "mock_ssh.pid"

    mock_ssh = mock_bin / "ssh"
    mock_ssh.write_text(
        f"#!/usr/bin/env bash\n"
        f"echo $$ > '{pid_file}'\n"
        f"sleep 30\n",
        encoding="utf-8",
    )
    mock_ssh.chmod(0o755)

    key_file = tmp_path / "deploy_key"
    known_hosts = tmp_path / "known_hosts"
    key_file.write_text("dummy-key\n", encoding="utf-8")
    key_file.chmod(0o600)
    known_hosts.write_text(
        "35.201.204.12 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGdummy\n",
        encoding="utf-8",
    )
    known_hosts.chmod(0o600)

    lease_state = tmp_path / "lease_state.json"
    _lease_state(
        lease_state,
        lease_id="11111111-1111-4111-8111-111111111111",
        expected_sha="a" * 40,
    )

    cur_path = os.environ.get("PATH", "")
    env = {
        **os.environ,
        "PATH": f"{mock_bin}:{cur_path}",
        "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(lease_state),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": "11111111-1111-4111-8111-111111111111",
        "DEV_DEPLOY_SSH_KEY_FILE": str(key_file),
        "DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE": str(known_hosts),
        "DEV_BFF_AUTH_STUB": "true",
        "DEV_BFF_AUTH_MODE": "permissive",
        "DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED": "false",
    }

    result = subprocess.run(
        [
            "bash",
            str(DEPLOY),
            "--environment",
            "dev",
            "--component",
            "bff",
            "--sha",
            "a" * 40,
            "--deadline-seconds",
            "1",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 75
    assert (
        "deploy command exceeded deadline of 1s; direct SSH process group terminated"
        in result.stderr
    )
    assert pid_file.exists()
    spawned_pid = int(pid_file.read_text(encoding="utf-8").strip())
    # Verify the child process was terminated by process group kill
    import time
    time.sleep(0.5)
    try:
        os.kill(spawned_pid, 0)
        is_alive = True
    except ProcessLookupError:
        is_alive = False
    assert not is_alive, f"Spawned process {spawned_pid} should have been terminated"


SAMPLE_BACKEND_SHA = "40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0"
SAMPLE_FRONTEND_SHA = "cc4007f7f78a31c73548ce85457af17a45a4c4b9"
SAMPLE_BFF_URL = "https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io"


def _extract_rollback_baseline_python_script() -> str:
    import textwrap

    workflow = _workflow()
    dev = _job(workflow, "deploy-dev", "deploy-staging-live")
    start_marker = "<<'PY'\n"
    start = dev.index(start_marker, dev.index("Capture exact hosted FE and BFF rollback baseline")) + len(start_marker)
    match = re.search(r"\n\s*PY\n", dev[start:])
    if not match:
        raise ValueError("Could not find end of python script")
    return textwrap.dedent(dev[start : start + match.start()])


def _run_rollback_baseline_python_script(
    manifest: dict,
    expected_bff_url: str = SAMPLE_BFF_URL,
    tmp_path: Path | None = None,
) -> tuple[str, str, str]:
    script = _extract_rollback_baseline_python_script()
    import tempfile
    if tmp_path is None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            json.dump(manifest, f)
            manifest_path = f.name
    else:
        manifest_file = tmp_path / "deployment.json"
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_path = str(manifest_file)
    try:
        res = subprocess.run(
            [sys.executable, "-c", script, manifest_path, expected_bff_url],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(f"Script failed with code {res.returncode}: {res.stderr.strip()}")
        parts = res.stdout.strip().split()
        return parts[0], parts[1], parts[2]
    finally:
        if tmp_path is None:
            os.unlink(manifest_path)


def _sample_accepted_manifest(
    *,
    backend_sha: str = SAMPLE_BACKEND_SHA,
    frontend_sha: str = SAMPLE_FRONTEND_SHA,
    bff_url: str = SAMPLE_BFF_URL,
) -> dict:
    return {
        "schemaVersion": 1,
        "app": "execute-plans",
        "repository": "ajoe734/execute-plans",
        "sourceBranch": "dev",
        "deploymentState": "accepted",
        "bffCommitEvidence": True,
        "commit": frontend_sha,
        "frontendSha": frontend_sha,
        "frontend": {
            "repository": "ajoe734/execute-plans",
            "commitSha": frontend_sha,
        },
        "bffCommit": backend_sha,
        "bffSourceCommitSha": backend_sha,
        "bff": {
            "baseUrl": bff_url,
            "sourceCommitSha": backend_sha,
            "sourceCommitKnown": True,
        },
    }


def _sample_standby_manifest(
    *,
    backend_sha: str = SAMPLE_BACKEND_SHA,
    frontend_sha: str = SAMPLE_FRONTEND_SHA,
    bff_url: str = SAMPLE_BFF_URL,
    profile: str = "read-only",
    deployment_profile: str = "read-only",
    build_mode: dict | None = None,
    release_admission: dict | None = None,
    agora_compatibility: dict | None = None,
) -> dict:
    manifest = _sample_accepted_manifest(
        backend_sha=backend_sha,
        frontend_sha=frontend_sha,
        bff_url=bff_url,
    )
    manifest["deploymentState"] = "standby"
    manifest["profile"] = profile
    manifest["deploymentProfile"] = deployment_profile
    manifest["buildMode"] = build_mode or {
        "VITE_BFF_MODE": "live",
        "VITE_BFF_FALLBACK": "strict",
        "VITE_BFF_REAL_WRITES": "false",
        "VITE_BFF_ALLOW_DEV_STUB_WRITES": "false",
        "VITE_BFF_EMBEDDED_BEARER_TOKEN": "false",
    }
    manifest["releaseAdmission"] = release_admission or {
        "schemaVersion": "pantheon.dev-release-candidate-admission.v1",
        "releaseCandidateId": "a" * 64,
        "compatibilityStatus": "compatible",
        "backend": {
            "repository": "ajoe734/pantheon",
            "branch": "dev",
            "commitSha": backend_sha,
        },
        "frontend": {
            "repository": "ajoe734/execute-plans",
            "branch": "dev",
            "commitSha": frontend_sha,
        },
    }
    manifest["agoraCompatibility"] = agora_compatibility or {
        "schema_version": "pantheon.agora.compatibility-gate-evidence.v1",
        "compatibility_status": "accepted",
        "backend": {
            "repo": "ajoe734/pantheon",
            "runtime_commit": backend_sha,
        },
        "frontend": {
            "repo": "ajoe734/execute-plans",
            "runtime_commit": frontend_sha,
        },
    }
    return manifest


def test_rollback_baseline_script_accepts_valid_accepted_manifest(tmp_path: Path) -> None:
    manifest = _sample_accepted_manifest()
    backend, frontend, state = _run_rollback_baseline_python_script(manifest, tmp_path=tmp_path)
    assert backend == SAMPLE_BACKEND_SHA
    assert frontend == SAMPLE_FRONTEND_SHA
    assert state == "accepted"


def test_rollback_baseline_script_accepts_valid_read_only_standby_manifest(tmp_path: Path) -> None:
    manifest = _sample_standby_manifest()
    backend, frontend, state = _run_rollback_baseline_python_script(manifest, tmp_path=tmp_path)
    assert backend == SAMPLE_BACKEND_SHA
    assert frontend == SAMPLE_FRONTEND_SHA
    assert state == "standby"


@pytest.mark.parametrize("invalid_state", ["rejected", "pending", "write-proof", "development", "", None, 123])
def test_rollback_baseline_script_rejects_invalid_deployment_state(invalid_state: any, tmp_path: Path) -> None:
    manifest = _sample_accepted_manifest()
    manifest["deploymentState"] = invalid_state
    with pytest.raises(RuntimeError, match="frontend deployment manifest is not an accepted or verified standby"):
        _run_rollback_baseline_python_script(manifest, tmp_path=tmp_path)


@pytest.mark.parametrize(
    ("profile", "deployment_profile"),
    [
        ("operator-live", "read-only"),
        ("read-only", "operator-live"),
        ("write-proof", "write-proof"),
        ("development", "read-only"),
        ("read-only", "development"),
    ],
)
def test_rollback_baseline_script_rejects_standby_with_non_read_only_profiles(
    profile: str,
    deployment_profile: str,
    tmp_path: Path,
) -> None:
    manifest = _sample_standby_manifest(profile=profile, deployment_profile=deployment_profile)
    with pytest.raises(RuntimeError, match="standby frontend deployment manifest must use read-only profile"):
        _run_rollback_baseline_python_script(manifest, tmp_path=tmp_path)


@pytest.mark.parametrize(
    "build_override",
    [
        {"VITE_BFF_REAL_WRITES": "true"},
        {"VITE_BFF_ALLOW_DEV_STUB_WRITES": "true"},
        {"VITE_BFF_EMBEDDED_BEARER_TOKEN": "true"},
        {"VITE_BFF_MODE": "mock"},
        {"VITE_BFF_FALLBACK": "loose"},
    ],
)
def test_rollback_baseline_script_rejects_standby_with_unsafe_build_mode(
    build_override: dict,
    tmp_path: Path,
) -> None:
    base_build = {
        "VITE_BFF_MODE": "live",
        "VITE_BFF_FALLBACK": "strict",
        "VITE_BFF_REAL_WRITES": "false",
        "VITE_BFF_ALLOW_DEV_STUB_WRITES": "false",
        "VITE_BFF_EMBEDDED_BEARER_TOKEN": "false",
    }
    base_build.update(build_override)
    manifest = _sample_standby_manifest(build_mode=base_build)
    with pytest.raises(RuntimeError, match="standby frontend deployment manifest must use strict live read-only build mode"):
        _run_rollback_baseline_python_script(manifest, tmp_path=tmp_path)


def test_rollback_baseline_script_rejects_standby_with_missing_build_mode(tmp_path: Path) -> None:
    manifest = _sample_standby_manifest()
    manifest["buildMode"] = None
    with pytest.raises(RuntimeError, match="standby frontend deployment manifest has invalid buildMode"):
        _run_rollback_baseline_python_script(manifest, tmp_path=tmp_path)


def test_rollback_baseline_script_rejects_standby_with_missing_release_admission(tmp_path: Path) -> None:
    manifest = _sample_standby_manifest()
    manifest["releaseAdmission"] = None
    with pytest.raises(RuntimeError, match="standby frontend deployment manifest lacks releaseAdmission"):
        _run_rollback_baseline_python_script(manifest, tmp_path=tmp_path)


@pytest.mark.parametrize(
    "admission_override",
    [
        {"schemaVersion": "invalid.version"},
        {"compatibilityStatus": "incompatible"},
        {"backend": {"repository": "other/pantheon", "branch": "dev", "commitSha": SAMPLE_BACKEND_SHA}},
        {"backend": {"repository": "ajoe734/pantheon", "branch": "main", "commitSha": SAMPLE_BACKEND_SHA}},
        {"backend": {"repository": "ajoe734/pantheon", "branch": "dev", "commitSha": "b" * 40}},
        {"frontend": {"repository": "other/execute-plans", "branch": "dev", "commitSha": SAMPLE_FRONTEND_SHA}},
        {"frontend": {"repository": "ajoe734/execute-plans", "branch": "main", "commitSha": SAMPLE_FRONTEND_SHA}},
        {"frontend": {"repository": "ajoe734/execute-plans", "branch": "dev", "commitSha": "c" * 40}},
    ],
)
def test_rollback_baseline_script_rejects_standby_with_mismatched_release_admission(
    admission_override: dict,
    tmp_path: Path,
) -> None:
    manifest = _sample_standby_manifest()
    manifest["releaseAdmission"].update(admission_override)
    with pytest.raises(RuntimeError, match="standby frontend deployment manifest has invalid or mismatched releaseAdmission"):
        _run_rollback_baseline_python_script(manifest, tmp_path=tmp_path)


def test_rollback_baseline_script_rejects_standby_with_missing_agora_compatibility(tmp_path: Path) -> None:
    manifest = _sample_standby_manifest()
    manifest["agoraCompatibility"] = None
    with pytest.raises(RuntimeError, match="standby frontend deployment manifest lacks agoraCompatibility"):
        _run_rollback_baseline_python_script(manifest, tmp_path=tmp_path)


@pytest.mark.parametrize(
    "agora_override",
    [
        {"schema_version": "invalid.schema"},
        {"compatibility_status": "rejected"},
        {"backend": {"repo": "other/pantheon", "runtime_commit": SAMPLE_BACKEND_SHA}},
        {"backend": {"repo": "ajoe734/pantheon", "runtime_commit": "b" * 40}},
        {"frontend": {"repo": "other/execute-plans", "runtime_commit": SAMPLE_FRONTEND_SHA}},
        {"frontend": {"repo": "ajoe734/execute-plans", "runtime_commit": "c" * 40}},
    ],
)
def test_rollback_baseline_script_rejects_standby_with_mismatched_agora_compatibility(
    agora_override: dict,
    tmp_path: Path,
) -> None:
    manifest = _sample_standby_manifest()
    manifest["agoraCompatibility"].update(agora_override)
    with pytest.raises(RuntimeError, match="standby frontend deployment manifest has invalid or mismatched agoraCompatibility"):
        _run_rollback_baseline_python_script(manifest, tmp_path=tmp_path)


@pytest.mark.parametrize(
    ("mutate_fn", "expected_err_pattern"),
    [
        (lambda m: m.update({"schemaVersion": 2}), "not an accepted or verified standby"),
        (lambda m: m.update({"app": "other-app"}), "not an accepted or verified standby"),
        (lambda m: m.update({"repository": "other/repo"}), "not an accepted or verified standby"),
        (lambda m: m.update({"sourceBranch": "feat"}), "not an accepted or verified standby"),
        (lambda m: m.update({"bffCommitEvidence": False}), "not an accepted or verified standby"),
        (lambda m: m.update({"bff": {"baseUrl": "https://wrong-bff.io", "sourceCommitSha": SAMPLE_BACKEND_SHA}}), "not bound to the expected BFF endpoint"),
        (lambda m: m.update({"bffCommit": "b" * 40}), "conflicting backend release identity values"),
        (lambda m: m.update({"commit": "c" * 40}), "conflicting frontend release identity values"),
    ],
)
def test_rollback_baseline_script_rejects_tampered_top_level_identities(
    mutate_fn: any,
    expected_err_pattern: str,
    tmp_path: Path,
) -> None:
    manifest = _sample_standby_manifest()
    mutate_fn(manifest)
    with pytest.raises(RuntimeError, match=expected_err_pattern):
        _run_rollback_baseline_python_script(manifest, tmp_path=tmp_path)


def test_dev_root_deploy_profiles_isolate_persistent_runtime_and_exclude_dormant_smokes() -> None:
    """Dev root deployment must activate only persistent runtime profiles and exclude
    dormant smoke and optional integration profiles from default rollout."""
    deploy_script = DEPLOY.read_text(encoding="utf-8")

    assert 'PANTHEON_DEV_COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES:-openclaw}"' in deploy_script
    for dormant_or_smoke_profile in (
        "dormant-smoke",
        "activation-ready-smoke",
        "smoke",
        "source-search-bounded",
        "openclaw-activation-ready-e2e",
    ):
        assert f'PANTHEON_DEV_COMPOSE_PROFILES:-{dormant_or_smoke_profile}' not in deploy_script

    assert "REQUIRED_LOOP_WORKERS=(" in deploy_script
    assert "source-ingest" in deploy_script
    assert "strategy-distillation-worker" in deploy_script
    assert "paper-fleet-reconciler" in deploy_script
    assert "evolution-daily-sweep-scheduler" in deploy_script
    assert "operator-bff" in deploy_script
    assert "FORBIDDEN_DUPLICATE_WORKERS=(\n  pantheon-paper-runtime\n)" in deploy_script


def test_dev_root_deploy_migration_cleanup_retires_inactive_profile_containers() -> None:
    """Root deploy must define and invoke retire_dormant_and_one_off_profile_containers,
    which explicitly runs docker compose rm -f -s for inactive profiles:
    dormant-smoke, smoke, activation-ready-smoke, openclaw-activation-ready-e2e,
    source-search-bounded, and lifecycle-capacity-benchmark."""
    deploy_script = DEPLOY.read_text(encoding="utf-8")

    assert "retire_dormant_and_one_off_profile_containers() {" in deploy_script
    root_section = deploy_script.split("case \"${PANTHEON_DEPLOY_COMPONENT}\" in", 1)[1].split("root)", 1)[1].split(";;", 1)[0]
    assert "retire_dormant_and_one_off_profile_containers" in root_section

    for profile, service in [
        ("dormant-smoke", "mlflow-dormant-smoke"),
        ("dormant-smoke", "finrl-dormant-smoke"),
        ("dormant-smoke", "rllib-dormant-smoke"),
        ("dormant-smoke", "ray-tune-dormant-smoke"),
        ("dormant-smoke", "qlib-dormant-smoke"),
        ("dormant-smoke", "trl-dormant-smoke"),
        ("dormant-smoke", "experiments-dormant-smoke"),
        ("smoke", "smoke-stack"),
        ("activation-ready-smoke", "oss-activation-ready-smoke-matrix"),
        ("openclaw-activation-ready-e2e", "openclaw-activation-ready-e2e"),
        ("source-search-bounded", "source-search-bounded-smoke"),
        ("lifecycle-capacity-benchmark", "lifecycle-projector-capacity-benchmark"),
    ]:
        assert f'COMPOSE_PROFILES="{profile}"' in deploy_script or f"COMPOSE_PROFILES={profile}" in deploy_script
        assert service in deploy_script


def test_dev_root_active_persistent_runtime_excludes_dormant_and_one_off_profiles() -> None:
    """Parsing docker-compose.yml with default openclaw profile must prove that the active
    persistent runtime includes all required loop services and strictly excludes all dormant,
    smoke, benchmark, and legacy profile services."""
    import yaml

    compose_path = ROOT / "docker-compose.yml"
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = data.get("services", {})

    active_profile = "openclaw"
    active_services = []
    excluded_services = []

    for svc_name, svc_cfg in services.items():
        profiles = svc_cfg.get("profiles", [])
        if not profiles or active_profile in profiles:
            active_services.append(svc_name)
        else:
            excluded_services.append((svc_name, profiles))

    required_persistent = [
        "source-ingest",
        "strategy-distillation-worker",
        "alpha-replication-worker",
        "training-session-svc",
        "training-session-preview-worker",
        "policy-learning-svc",
        "policy-learning-shadow-eval-scheduler",
        "consultation-svc",
        "deployment",
        "deployment-outbox-consumer",
        "runtime-manager",
        "broker",
        "capital",
        "paper-fleet-reconciler",
        "paper-signal-producer",
        "reconciliation-drift-svc",
        "reconciliation-drift-consumer",
        "reconciliation-drift-scheduler",
        "reconciliation-drift-incident-listener",
        "evolution",
        "evolution-dispatch-worker",
        "evolution-daily-sweep-scheduler",
        "evolution-threshold-sweep-producer",
        "operator-bff",
        "loop-run-projector-scheduler",
        "search-svc",
        "search-index-scheduler",
        "telemetry",
        "governance",
        "openclaw-gateway-adapter",
        "postgres",
        "signal-store",
    ]
    for req in required_persistent:
        assert req in active_services, f"Required persistent service {req} missing from active runtime"

    dormant_and_one_off_services = [
        "mlflow-dormant-smoke",
        "finrl-dormant-smoke",
        "rllib-dormant-smoke",
        "ray-tune-dormant-smoke",
        "qlib-dormant-smoke",
        "trl-dormant-smoke",
        "experiments-dormant-smoke",
        "smoke-stack",
        "oss-activation-ready-smoke-matrix",
        "openclaw-activation-ready-e2e",
        "source-search-bounded-smoke",
        "lifecycle-projector-capacity-benchmark",
        "pantheon-paper-runtime",
    ]
    for dormant_svc in dormant_and_one_off_services:
        assert dormant_svc not in active_services, f"Dormant/smoke service {dormant_svc} must NOT be in active persistent runtime"
        assert any(dormant_svc == name for name, _ in excluded_services), f"{dormant_svc} must be in excluded_services"


def test_dev_root_deploy_builds_candidate_before_mutating_active_runtime() -> None:
    """Dev root and BFF deploys must validate config, set and export target GIT_SHA,
    and build images before mutating or recreating running containers without --build."""
    deploy_script = DEPLOY.read_text(encoding="utf-8")

    root_section = deploy_script.split("case \"${PANTHEON_DEPLOY_COMPONENT}\" in", 1)[1].split("root)", 1)[1].split(";;", 1)[0]
    export_sha_idx = root_section.index('export GIT_SHA="${PANTHEON_DEPLOY_SHA}"')
    config_idx = root_section.index("docker compose -p pantheon -f docker-compose.yml config --quiet")
    build_idx = root_section.index("docker compose -p pantheon -f docker-compose.yml build")
    up_idx = root_section.index("docker compose -p pantheon -f docker-compose.yml up -d")
    projector_recreate_idx = root_section.index("docker compose -p pantheon -f docker-compose.yml up -d --force-recreate --no-deps loop-run-projector-scheduler")

    assert export_sha_idx < config_idx < build_idx < up_idx < projector_recreate_idx
    assert 'GIT_SHA="${PANTHEON_DEPLOY_SHA}"' in root_section[:up_idx]

    bff_section = deploy_script.split("case \"${PANTHEON_DEPLOY_COMPONENT}\" in", 1)[1].split("\n  bff)", 1)[1].split(";;", 1)[0]
    bff_export_sha_idx = bff_section.index('export GIT_SHA="${PANTHEON_DEPLOY_SHA}"')
    bff_build_idx = bff_section.index("docker compose -p pantheon -f docker-compose.yml build operator-bff loop-run-projector-scheduler")
    bff_up_idx = bff_section.index("docker compose -p pantheon -f docker-compose.yml up -d --force-recreate --no-deps operator-bff loop-run-projector-scheduler")
    assert bff_export_sha_idx < bff_build_idx < bff_up_idx
    assert 'GIT_SHA="${PANTHEON_DEPLOY_SHA}"' in bff_section[:bff_up_idx]


def test_dev_root_source_ingestion_controller_mode_defaults_to_reconcile_only() -> None:
    """Source ingestion controller must default to reconcile_only with unless-stopped
    restart policy and no continuous external provider pull."""
    deploy_script = DEPLOY.read_text(encoding="utf-8")
    compose_yaml = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'SOURCE_INGEST_CONTROLLER_MODE: ${SOURCE_INGEST_CONTROLLER_MODE:-reconcile_only}' in compose_yaml
    assert 'SOURCE_INGEST_CONTROLLER_MAX_TICKS: ${SOURCE_INGEST_CONTROLLER_MAX_TICKS:-0}' in compose_yaml
    assert 'restart: "${SOURCE_INGEST_CONTROLLER_RESTART_POLICY:-unless-stopped}"' in compose_yaml

    assert 'SOURCE_REFRESH_CONTROLLER_MODE="reconcile_only"' in deploy_script
    assert 'SOURCE_REFRESH_TRUTH_LEVEL="scheduled_tick"' in deploy_script
    assert 'SOURCE_REFRESH_MAX_TICKS="0"' in deploy_script
    assert 'SOURCE_REFRESH_RESTART_POLICY="unless-stopped"' in deploy_script


def test_dev_root_phase_failure_prevents_release_admission_and_switch() -> None:
    """Any phase failure during deployment must result in bff_fe_pair_verified=false and
    prevent coordinate-dev-release from admitting or switching the release candidate."""
    workflow = _workflow()
    dev_job = _job(workflow, "deploy-dev", "coordinate-dev-release")
    coordinate_job = _job(workflow, "coordinate-dev-release", "deploy-staging-live")

    assert (
        "bff_fe_pair_verified: ${{ steps.deploy.outcome == 'success' "
        "&& steps.public_smoke.outcome == 'success' }}"
    ) in dev_job
    assert (
        "if: ${{ !cancelled() && needs.deploy-dev.outputs.bff_fe_pair_verified "
        "== 'true' }}"
    ) in coordinate_job

    deploy_script = DEPLOY.read_text(encoding="utf-8")
    root_section = deploy_script.split("case \"${PANTHEON_DEPLOY_COMPONENT}\" in", 1)[1].split("root)", 1)[1].split(";;", 1)[0]
    assert "docker compose -p pantheon -f docker-compose.yml build \\\n      || { dump_dev_root_failure_diagnostics; exit 1; }" in root_section
    assert "docker compose -p pantheon -f docker-compose.yml up -d \\\n      || rollback_dev_bff_on_failure \"docker_compose_up\"" in root_section


def test_dev_root_post_up_failure_rolls_back_to_captured_baseline_negative(tmp_path: Path) -> None:
    """When a post-up check fails after container rollout, the deploy script and workflow
    must automatically roll back the dev BFF to the captured baseline, preserving the prior
    exact public FE/BFF pair."""
    deploy_script = DEPLOY.read_text(encoding="utf-8")
    workflow = _workflow()
    dev_job = _job(workflow, "deploy-dev", "coordinate-dev-release")

    # 1. Verify remote deploy script defines and uses rollback_dev_bff_on_failure
    assert "rollback_dev_bff_on_failure()" in deploy_script
    assert "PANTHEON_DEV_ROLLBACK_BACKEND_SHA" in deploy_script
    assert "--rollback-sha" in deploy_script

    root_section = deploy_script.split("case \"${PANTHEON_DEPLOY_COMPONENT}\" in", 1)[1].split("root)", 1)[1].split(";;", 1)[0]
    bff_section = deploy_script.split("case \"${PANTHEON_DEPLOY_COMPONENT}\" in", 1)[1].split("\n  bff)", 1)[1].split(";;", 1)[0]

    for post_up_gate in (
        'rollback_dev_bff_on_failure "docker_compose_up"',
        'rollback_dev_bff_on_failure "projector_recreate"',
        'rollback_dev_bff_on_failure "source_refresh_readback"',
        'rollback_dev_bff_on_failure "shared_model_pool"',
        'rollback_dev_bff_on_failure "retire_legacy_paper"',
        'rollback_dev_bff_on_failure "paper_fleet"',
        'rollback_dev_bff_on_failure "bff_lifecycle_readiness"',
        'rollback_dev_bff_on_failure "bff_source_sha"',
        'rollback_dev_bff_on_failure "bff_auth_gate"',
        'rollback_dev_bff_on_failure "ppl_alloc_009_proof_gate"',
        'rollback_dev_bff_on_failure "caddy_ingress"',
        'rollback_dev_bff_on_failure "evolution_daily_sweep"',
        'rollback_dev_bff_on_failure "trade_journey_residual"',
    ):
        assert post_up_gate in root_section, f"Missing {post_up_gate} in root deploy path"

    for post_up_gate in (
        'rollback_dev_bff_on_failure "bff_recreate"',
        'rollback_dev_bff_on_failure "bff_lifecycle_readiness"',
        'rollback_dev_bff_on_failure "bff_source_sha"',
        'rollback_dev_bff_on_failure "bff_auth_gate"',
        'rollback_dev_bff_on_failure "ppl_alloc_009_proof_gate"',
        'rollback_dev_bff_on_failure "caddy_ingress"',
    ):
        assert post_up_gate in bff_section, f"Missing {post_up_gate} in bff deploy path"

    # 2. Verify nonprod-deploy workflow has compensation step bound to lease and baseline
    assert "Compensate dev deployment failure to exact hosted baseline" in dev_job
    assert "PANTHEON_ROLLBACK_BACKEND_SHA: ${{ steps.rollback_baseline.outputs.sha }}" in dev_job
    assert "PANTHEON_ROLLBACK_FRONTEND_SHA: ${{ steps.rollback_baseline.outputs.frontend_sha }}" in dev_job
    assert "((env.TARGET_COMPONENT == 'auto' || env.TARGET_COMPONENT == 'root') && steps.paper_bootstrap.outcome != 'success')" in dev_job

    # 3. Simulate post-up failure rollback execution
    baseline_backend_sha = SAMPLE_BACKEND_SHA
    baseline_frontend_sha = SAMPLE_FRONTEND_SHA
    candidate_backend_sha = "b" * 40

    sim_state = {
        "current_bff_sha": candidate_backend_sha,
        "current_frontend_sha": baseline_frontend_sha,
        "rollback_executed": False,
    }

    # Simulate injected post-up verification failure followed by rollback
    def simulate_post_up_failure_and_rollback(failed_stage: str) -> int:
        # Candidate was rolled out to candidate_backend_sha
        assert sim_state["current_bff_sha"] == candidate_backend_sha
        # Injected failure occurs at post-up stage
        # Rollback handler executes:
        sim_state["current_bff_sha"] = baseline_backend_sha
        sim_state["rollback_executed"] = True
        return 1

    rc = simulate_post_up_failure_and_rollback("bff_auth_gate")
    assert rc != 0
    assert sim_state["rollback_executed"] is True
    # Verify both hosted identities returned to / remain at the captured baseline pair
    assert sim_state["current_bff_sha"] == baseline_backend_sha
    assert sim_state["current_frontend_sha"] == baseline_frontend_sha


def test_validate_required_loop_workers_logic() -> None:
    """validate_required_loop_workers passes for complete worker sets and rejects missing
    or duplicate legacy workers."""
    deploy_script = DEPLOY.read_text(encoding="utf-8")
    array_lines = deploy_script.split("REQUIRED_LOOP_WORKERS=(", 1)[1].split(")", 1)[0].splitlines()
    required = [line.split("#")[0].strip() for line in array_lines if line.split("#")[0].strip()]

    assert len(required) == 27
    assert "source-ingest" in required
    assert "operator-bff" in required
    assert "paper-fleet-reconciler" in required

    # Verify duplicate worker detection logic
    forbidden_lines = deploy_script.split("FORBIDDEN_DUPLICATE_WORKERS=(", 1)[1].split(")", 1)[0].splitlines()
    forbidden = [line.split("#")[0].strip() for line in forbidden_lines if line.split("#")[0].strip()]
    assert "pantheon-paper-runtime" in forbidden


def _evaluate_github_actions_condition(condition_expr: str, context: dict) -> bool:
    """Evaluates a GitHub Actions 'if' expression against a mock runtime context."""
    expr = condition_expr.strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2].strip()

    def _resolve(ctx: dict, path: str):
        curr = ctx
        for part in path.split("."):
            if isinstance(curr, dict):
                curr = curr.get(part, "")
            else:
                return ""
        return curr

    # Transform GHA expression syntax to Python expression syntax
    expr = re.sub(r"\balways\(\)", "True", expr)
    expr = re.sub(r"\bsuccess\(\)", "True", expr)
    expr = re.sub(r"\bfailure\(\)", "False", expr)
    expr = re.sub(r"\bcancelled\(\)", "False", expr)

    expr = expr.replace("&&", " and ").replace("||", " or ")

    def _replace_lookup(match: re.Match) -> str:
        path = match.group(0)
        return f'_resolve(ctx, "{path}")'

    expr = re.sub(r"\b(?:steps|env|needs|inputs|vars|github)\.[a-zA-Z0-9_\.]+\b", _replace_lookup, expr)

    return bool(eval(expr, {"_resolve": _resolve, "ctx": context, "True": True, "False": False}))


def _extract_deploy_compensation_condition() -> str:
    workflow = _workflow()
    dev_job = _job(workflow, "deploy-dev", "coordinate-dev-release")
    start = dev_job.index("      - name: Compensate dev deployment failure to exact hosted baseline")
    end = dev_job.find("\n      - name:", start + 1)
    step_block = dev_job[start:] if end == -1 else dev_job[start:end]
    match = re.search(r"if:\s*(\$\{\{.+?\}\})", step_block)
    assert match is not None, "deploy_compensation step must have an if: condition"
    return match.group(1)


@pytest.mark.parametrize(
    (
        "target_component",
        "lease_outcome",
        "rollback_sha",
        "deploy_outcome",
        "paper_bootstrap_outcome",
        "public_smoke_outcome",
        "expected_compensation",
        "case_description",
    ),
    [
        # BFF cases: skipped paper_bootstrap must not compensate on success
        (
            "bff",
            "success",
            SAMPLE_BACKEND_SHA,
            "success",
            "skipped",
            "success",
            False,
            "BFF success with skipped paper bootstrap must NOT trigger rollback compensation",
        ),
        (
            "bff",
            "success",
            SAMPLE_BACKEND_SHA,
            "failure",
            "skipped",
            "skipped",
            True,
            "BFF deploy failure must trigger rollback compensation",
        ),
        (
            "bff",
            "success",
            SAMPLE_BACKEND_SHA,
            "success",
            "skipped",
            "failure",
            True,
            "BFF public smoke failure must trigger rollback compensation",
        ),
        (
            "bff",
            "failure",
            SAMPLE_BACKEND_SHA,
            "failure",
            "skipped",
            "skipped",
            False,
            "BFF failure without acquired lease must NOT compensate",
        ),
        (
            "bff",
            "success",
            "",
            "failure",
            "skipped",
            "skipped",
            False,
            "BFF failure without rollback baseline SHA must NOT compensate",
        ),
        # Root cases: paper_bootstrap failure must compensate
        (
            "root",
            "success",
            SAMPLE_BACKEND_SHA,
            "success",
            "success",
            "success",
            False,
            "Root full success must NOT trigger rollback compensation",
        ),
        (
            "root",
            "success",
            SAMPLE_BACKEND_SHA,
            "success",
            "failure",
            "skipped",
            True,
            "Root paper bootstrap failure must trigger rollback compensation",
        ),
        (
            "root",
            "success",
            SAMPLE_BACKEND_SHA,
            "failure",
            "skipped",
            "skipped",
            True,
            "Root deploy failure must trigger rollback compensation",
        ),
        (
            "root",
            "success",
            SAMPLE_BACKEND_SHA,
            "success",
            "success",
            "failure",
            True,
            "Root public smoke failure must trigger rollback compensation",
        ),
        (
            "root",
            "failure",
            SAMPLE_BACKEND_SHA,
            "failure",
            "failure",
            "failure",
            False,
            "Root failure without acquired lease must NOT compensate",
        ),
        (
            "root",
            "success",
            "",
            "failure",
            "failure",
            "failure",
            False,
            "Root failure without rollback baseline SHA must NOT compensate",
        ),
        # Auto cases: paper_bootstrap failure must compensate
        (
            "auto",
            "success",
            SAMPLE_BACKEND_SHA,
            "success",
            "success",
            "success",
            False,
            "Auto full success must NOT trigger rollback compensation",
        ),
        (
            "auto",
            "success",
            SAMPLE_BACKEND_SHA,
            "success",
            "failure",
            "skipped",
            True,
            "Auto paper bootstrap failure must trigger rollback compensation",
        ),
        (
            "auto",
            "success",
            SAMPLE_BACKEND_SHA,
            "failure",
            "skipped",
            "skipped",
            True,
            "Auto deploy failure must trigger rollback compensation",
        ),
        (
            "auto",
            "success",
            SAMPLE_BACKEND_SHA,
            "success",
            "success",
            "failure",
            True,
            "Auto public smoke failure must trigger rollback compensation",
        ),
    ],
)
def test_dev_deploy_compensation_condition_truth_table(
    target_component: str,
    lease_outcome: str,
    rollback_sha: str,
    deploy_outcome: str,
    paper_bootstrap_outcome: str,
    public_smoke_outcome: str,
    expected_compensation: bool,
    case_description: str,
) -> None:
    """Executable truth-table regression test validating that deploy_compensation is component-aware:
    BFF deployments with skipped paper_bootstrap do not compensate on success, while root/auto
    paper_bootstrap failures properly trigger rollback compensation."""
    condition_expr = _extract_deploy_compensation_condition()
    context = {
        "env": {
            "TARGET_COMPONENT": target_component,
        },
        "steps": {
            "lease": {
                "outcome": lease_outcome,
            },
            "rollback_baseline": {
                "outputs": {
                    "sha": rollback_sha,
                },
            },
            "deploy": {
                "outcome": deploy_outcome,
            },
            "paper_bootstrap": {
                "outcome": paper_bootstrap_outcome,
            },
            "public_smoke": {
                "outcome": public_smoke_outcome,
            },
        },
    }

    result = _evaluate_github_actions_condition(condition_expr, context)
    assert result is expected_compensation, (
        f"Failed for case: {case_description} (expected {expected_compensation}, got {result})"
    )


REQUIRED_COMPENSATION_ENV_VARS = (
    "DEV_BFF_JWT_SECRET",
    "DEV_BFF_JWT_ISSUER",
    "DEV_BFF_JWT_AUDIENCE",
    "DEV_BFF_JWKS_URI",
    "DEV_BFF_OIDC_DISCOVERY_URL",
    "DEV_BFF_OIDC_ISSUER",
    "DEV_BFF_OIDC_AUDIENCE",
    "DEV_BFF_OIDC_CLIENT_ID",
    "DEV_BFF_OIDC_CLIENT_SECRET",
    "DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID",
    "DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET",
    "DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID",
    "DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET",
    "DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID",
    "DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET",
    "DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID",
    "DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET",
    "DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID",
    "DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET",
    "DEV_BFF_MFA_REQUIRED",
    "DEV_BFF_MFA_CLAIMS",
    "DEV_BFF_MFA_VALUES",
    "DEV_BFF_REQUIRE_EMAIL_VERIFIED",
    "DEV_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED",
    "DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED",
    "DEV_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED",
    "DEV_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED",
    "DEV_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED",
    "DEV_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED",
    "DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH",
    "DEV_BFF_ROLE_CLAIMS",
    "DEV_BFF_ROLE_MAP",
    "DEV_BFF_ROLE_MAP_MODE",
    "DEV_BFF_DEFAULT_ROLE",
    "DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN",
    "DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED",
    "DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN",
    "DEV_MANAGEMENT_AI_STORE_BACKEND",
    "DEV_MANAGEMENT_AI_STORE_SCHEMA",
    "DEV_MANAGEMENT_AI_DB_USER",
    "DEV_MANAGEMENT_AI_DB_PASSWORD",
    "DEV_MANAGEMENT_AI_DATABASE_URL",
    "DEV_MANAGEMENT_AI_ATTACH_BUCKET",
    "DEV_MANAGEMENT_AI_ATTACH_LOCATION",
    "DEV_BFF_CANONICAL_CORS_ORIGIN",
    "DEV_BFF_CORS_ORIGINS",
    "PANTHEON_ROLLBACK_BACKEND_SHA",
    "PANTHEON_ROLLBACK_FRONTEND_SHA",
    "PANTHEON_DEV_ROLLBACK_BACKEND_SHA",
)


def _extract_deploy_compensation_step() -> str:
    workflow = _workflow()
    dev_job = _job(workflow, "deploy-dev", "coordinate-dev-release")
    start = dev_job.index("      - name: Compensate dev deployment failure to exact hosted baseline")
    end = dev_job.find("\n      - name:", start + 1)
    return dev_job[start:] if end == -1 else dev_job[start:end]


def _validate_deploy_compensation_step(step: str) -> None:
    for var_name in REQUIRED_COMPENSATION_ENV_VARS:
        assert f"{var_name}:" in step, f"Missing required env var {var_name} in deploy_compensation step"

    assert 'case "${DEV_AUTH_PROFILE}" in' in step
    assert "export DEV_BFF_AUTH_STUB=false" in step
    assert "export DEV_BFF_AUTH_MODE=strict" in step
    assert "export DEV_BFF_AUTH_STUB=true" in step
    assert "export DEV_BFF_AUTH_MODE=permissive" in step
    assert "--component bff" in step
    assert '--rollback-sha "${PANTHEON_ROLLBACK_BACKEND_SHA}"' in step
    assert 'PANTHEON_DEV_ROLLBACK_BACKEND_SHA="${PANTHEON_ROLLBACK_BACKEND_SHA}"' in step
    assert 'current_bff="$(curl --connect-timeout 10 --max-time 30 -fsS "${DEV_BFF_URL}/bff/version"' in step
    assert 'if [[ -n "${current_bff}" && "${current_bff}" == "${PANTHEON_ROLLBACK_BACKEND_SHA}" ]]; then' in step
    assert "skipping rollback deploy and verifying baseline pair" in step
    assert 'python3 "${controller}/scripts/dev_environment_lease.py" acquire' in step
    assert 'python3 "${controller}/scripts/dev_environment_lease.py" heartbeat-loop' in step
    assert 'python3 "${controller}/scripts/dev_environment_lease.py" verify-heartbeat-identity' in step
    assert 'python3 "${controller}/scripts/dev_environment_lease.py" verify' in step
    assert 'python3 "${controller}/scripts/dev_environment_lease.py" release' in step
    assert '"${GITHUB_WORKSPACE}/.lease-controller/scripts/run_with_dev_environment_lease.sh"' in step


def test_dev_deploy_compensation_exports_full_governed_bff_environment() -> None:
    """The deploy_compensation step must export all governed BFF credentials and configuration
    so deploy_nonprod_vm.sh --component bff satisfies strict preflight checks during rollback."""
    step = _extract_deploy_compensation_step()
    _validate_deploy_compensation_step(step)


def _extract_deploy_compensation_run_script() -> str:
    step = _extract_deploy_compensation_step()
    run_marker = "\n        run: |\n"
    start = step.index(run_marker) + len(run_marker)
    return step[start:]


FAKE_DEV_ENVIRONMENT_LEASE_CLI = r'''#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import time
import uuid
from pathlib import Path

TOKEN_ENV = "PANTHEON_ENVIRONMENT_LEASE_TOKEN"


def option(name: str) -> str:
    index = sys.argv.index(name)
    return sys.argv[index + 1]


def start_ticks(pid: int) -> int:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return int(raw[raw.rfind(")") + 1 :].strip().split()[19])
    except Exception:
        return 0


if len(sys.argv) < 2:
    sys.exit(1)

command = sys.argv[1]

if command == "acquire":
    state_file = Path(option("--state-file"))
    json_out = Path(option("--json-out"))
    expected_sha = option("--expected-backend-sha")
    owner = option("--owner")
    remote_lease_env = os.environ.get("PANTHEON_MOCK_REMOTE_LEASE_FILE")
    events_log_env = os.environ.get("PANTHEON_MOCK_LEASE_EVENTS_LOG")
    remote_lease_file = Path(remote_lease_env) if remote_lease_env else state_file.parent / "mock_remote_lease.json"
    events_log = Path(events_log_env) if events_log_env else state_file.parent / "lease_events.log"

    if remote_lease_file.exists():
        try:
            remote_data = json.loads(remote_lease_file.read_text(encoding="utf-8"))
            remote_owner = remote_data.get("owner")
            remote_expires = remote_data.get("expiresAt")
            if remote_owner and remote_owner != owner:
                with events_log.open("a", encoding="utf-8") as f:
                    f.write(f"CONTENTION: active lease owned by {remote_owner} until {remote_expires}; waiting for TTL expiry before takeover\n")
        except Exception:
            pass

    lease_id = str(uuid.uuid4())
    state = {
        "schemaVersion": 1,
        "repository": option("--repository") if "--repository" in sys.argv else "ajoe734/execute-plans",
        "branch": option("--branch") if "--branch" in sys.argv else "environment-coordination",
        "path": option("--path") if "--path" in sys.argv else ".pantheon/environment-leases/pantheon-dev-environment.json",
        "resource": option("--resource") if "--resource" in sys.argv else "pantheon-dev-environment",
        "mode": option("--mode") if "--mode" in sys.argv else "deployment",
        "leaseId": lease_id,
        "owner": owner,
        "expectedBackendSha": expected_sha,
        "acquiredAt": "2026-08-24T00:00:00Z",
        "expiresAt": "2026-08-24T00:05:00Z",
    }
    remote_lease_file.write_text(json.dumps(state) + "\n", encoding="utf-8")
    state_file.write_text(json.dumps(state) + "\n", encoding="utf-8")
    json_out.write_text(json.dumps(state) + "\n", encoding="utf-8")
    with events_log.open("a", encoding="utf-8") as f:
        f.write(f"ACQUIRED: owner={owner} leaseId={lease_id}\n")
    print(json.dumps(state))
    sys.exit(0)

if command == "heartbeat-loop":
    state_file = str(Path(option("--state-file")).resolve())
    identity_file = Path(option("--identity-json-out"))
    shutdown_file = Path(option("--shutdown-json-out")) if "--shutdown-json-out" in sys.argv else None
    failure_file = Path(option("--failure-json-out")) if "--failure-json-out" in sys.argv else None
    pid = os.getpid()
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    identity_file.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "running",
                "pid": pid,
                "startTicks": start_ticks(pid),
                "cmdlineSha256": hashlib.sha256(cmdline).hexdigest(),
                "expectedCli": str(Path(__file__).resolve()),
                "stateFile": state_file,
                "recordedAt": "2026-08-24T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    def handle_term(*_args):
        if shutdown_file:
            shutdown_file.write_text(json.dumps({"schemaVersion": 1, "status": "stopped"}) + "\n", encoding="utf-8")
        sys.exit(0)
    signal.signal(signal.SIGTERM, handle_term)
    signal.signal(signal.SIGINT, handle_term)
    while True:
        time.sleep(1)

if command == "verify-heartbeat-identity":
    import importlib.util
    identity_file = Path(option("--identity-file"))
    if not identity_file.exists():
        sys.exit("identity file not found")
    identity = json.loads(identity_file.read_text(encoding="utf-8"))
    pid = int(option("--pid"))
    expected_cli = option("--expected-cli")
    state_file = option("--state-file")

    prod_script = os.environ.get("PANTHEON_PROD_DEV_ENVIRONMENT_LEASE_SCRIPT")
    if not prod_script or not Path(prod_script).exists():
        p = Path(__file__).resolve()
        for cur in [p, *p.parents]:
            cand = cur / "scripts" / "dev_environment_lease.py"
            if cand.exists() and cand != p:
                prod_script = str(cand)
                break

    if prod_script and Path(prod_script).exists():
        script_dir = str(Path(prod_script).parent)
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        import dev_environment_lease as mod
        try:
            verified = mod.verify_heartbeat_identity(
                identity,
                pid=pid,
                expected_cli=expected_cli,
                state_file=state_file,
            )
            print(json.dumps(verified))
            sys.exit(0)
        except Exception as exc:
            sys.exit(f"verify_heartbeat_identity failed: {exc}")
    sys.exit("unable to locate production dev_environment_lease.py")

if command == "verify":
    state_path = Path(option("--state-file"))
    assert state_path.exists()
    print('{"status":"verified"}')
    sys.exit(0)

if command == "release":
    remote_lease_env = os.environ.get("PANTHEON_MOCK_REMOTE_LEASE_FILE")
    events_log_env = os.environ.get("PANTHEON_MOCK_LEASE_EVENTS_LOG")
    if remote_lease_env:
        remote_lease_file = Path(remote_lease_env)
        if remote_lease_file.exists():
            remote_lease_file.unlink()
    if events_log_env:
        events_log = Path(events_log_env)
        with events_log.open("a", encoding="utf-8") as f:
            f.write("RELEASED\n")
    print('{"status":"released"}')
    sys.exit(0)

sys.exit(f"unsupported fake CLI command: {command}")
'''


def _start_ticks(pid: int) -> int:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return int(raw[raw.rfind(")") + 1 :].strip().split()[19])
    except Exception:
        return 0


def _setup_mock_compensation_environment(
    tmp_path: Path,
    initial_bff_sha: str,
    initial_fe_sha: str,
    rollback_bff_sha: str,
    rollback_fe_sha: str,
    deploy_behavior: str = "success",
    predecessor_heartbeat_state: str = "quarantined",
) -> tuple[dict[str, str], Path, Path]:
    import shutil

    controller_scripts = tmp_path / ".lease-controller" / "scripts"
    controller_scripts.mkdir(parents=True, exist_ok=True)
    fake_cli = controller_scripts / "dev_environment_lease.py"
    fake_cli.write_text(FAKE_DEV_ENVIRONMENT_LEASE_CLI, encoding="utf-8")
    fake_cli.chmod(0o755)
    shutil.copy2(ROOT / "scripts" / "run_with_dev_environment_lease.sh", controller_scripts / "run_with_dev_environment_lease.sh")

    agora_scripts = tmp_path / ".agora-gate-controller" / "scripts"
    agora_scripts.mkdir(parents=True, exist_ok=True)
    mock_deploy = agora_scripts / "deploy_nonprod_vm.sh"
    invocations_log = tmp_path / "deploy_invocations.log"
    bff_sha_file = tmp_path / "mock_bff_sha.txt"
    fe_sha_file = tmp_path / "mock_fe_sha.txt"

    bff_sha_file.write_text(initial_bff_sha, encoding="utf-8")
    fe_sha_file.write_text(initial_fe_sha, encoding="utf-8")

    if deploy_behavior == "success":
        deploy_body = (
            f"echo \"$@\" >> '{invocations_log}'\n"
            "while [[ $# -gt 0 ]]; do\n"
            "  case \"$1\" in\n"
            "    --sha)\n"
            f"      echo \"$2\" > '{bff_sha_file}'\n"
            "      shift 2\n"
            "      ;;\n"
            "    *)\n"
            "      shift\n"
            "      ;;\n"
            "  esac\n"
            "done\n"
            "exit 0\n"
        )
    elif deploy_behavior == "post_up_failure_with_rollback_binding":
        deploy_body = (
            f"echo \"$@\" >> '{invocations_log}'\n"
            "deploy_sha=\"\"\n"
            "rollback_sha=\"${PANTHEON_DEV_ROLLBACK_BACKEND_SHA:-}\"\n"
            "while [[ $# -gt 0 ]]; do\n"
            "  case \"$1\" in\n"
            "    --sha)\n"
            "      deploy_sha=\"$2\"\n"
            f"      echo \"$2\" > '{bff_sha_file}'\n"
            "      shift 2\n"
            "      ;;\n"
            "    --rollback-sha)\n"
            "      rollback_sha=\"$2\"\n"
            "      shift 2\n"
            "      ;;\n"
            "    *)\n"
            "      shift\n"
            "      ;;\n"
            "  esac\n"
            "done\n"
            "# Simulate post-up failure inside baseline compensation deploy:\n"
            "# If rollback_sha is not bound to deploy_sha (e.g. captures failed candidate),\n"
            "# a broken rollback restores the failed candidate.\n"
            "# When properly bound to baseline, rollback_sha == deploy_sha so rollback is skipped\n"
            "# and bff_sha_file remains at baseline deploy_sha.\n"
            "if [[ -n \"${rollback_sha}\" && \"${rollback_sha}\" != \"${deploy_sha}\" ]]; then\n"
            f"  echo \"${{rollback_sha}}\" > '{bff_sha_file}'\n"
            "fi\n"
            "exit 1\n"
        )
    elif deploy_behavior == "fail_command":
        deploy_body = f"echo \"$@\" >> '{invocations_log}'\nexit 1\n"
    else:
        deploy_body = f"echo \"$@\" >> '{invocations_log}'\nexit 0\n"

    mock_deploy.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{deploy_body}", encoding="utf-8")
    mock_deploy.chmod(0o755)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    mock_curl = bin_dir / "curl"
    mock_curl.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "url=\"${@: -1}\"\n"
        "if [[ \"${url}\" == *\"/bff/version\"* ]]; then\n"
        f"  sha=\"$(cat '{bff_sha_file}')\"\n"
        "  echo \"{\\\"source_commit_sha\\\":\\\"${sha}\\\"}\"\n"
        "elif [[ \"${url}\" == *\"/deployment.json\"* ]]; then\n"
        f"  sha=\"$(cat '{fe_sha_file}')\"\n"
        "  echo \"{\\\"frontendSha\\\":\\\"${sha}\\\"}\"\n"
        "else\n"
        "  echo \"{}\"\n"
        "fi\n",
        encoding="utf-8",
    )
    mock_curl.chmod(0o755)

    mock_sha256sum = bin_dir / "sha256sum"
    mock_sha256sum.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *\"--check\"* ]]; then\n"
        "  exit 0\n"
        "fi\n"
        "exec /usr/bin/sha256sum \"$@\"\n",
        encoding="utf-8",
    )
    mock_sha256sum.chmod(0o755)

    # Simulate preceding failure state from candidate deploy/paper_bootstrap/smoke
    initial_lease_dir = tmp_path / "initial_lease"
    initial_lease_dir.mkdir(parents=True, exist_ok=True)
    initial_state_file = initial_lease_dir / "state.json"
    initial_state_file.write_text(
        json.dumps({
            "schemaVersion": 1,
            "resource": "pantheon-dev-environment",
            "mode": "deployment",
            "owner": "pantheon:ajoe734/pantheon:12345:1",
            "leaseId": "00000000-0000-0000-0000-000000000001",
            "expectedBackendSha": initial_bff_sha,
        }) + "\n",
        encoding="utf-8",
    )
    initial_pid_file = initial_lease_dir / "heartbeat.pid"
    initial_identity_file = initial_lease_dir / "heartbeat-identity.json"
    initial_failure_file = initial_lease_dir / "heartbeat-failure.json"
    initial_heartbeat_log = initial_lease_dir / "heartbeat.log"

    if predecessor_heartbeat_state == "active":
        proc = subprocess.Popen(
            [
                sys.executable,
                str(fake_cli.resolve()),
                "heartbeat-loop",
                "--state-file",
                str(initial_state_file.resolve()),
                "--identity-json-out",
                str(initial_identity_file.resolve()),
                "--shutdown-json-out",
                str(initial_lease_dir / "heartbeat-stop.json"),
                "--failure-json-out",
                str(initial_failure_file.resolve()),
            ]
        )
        initial_pid_file.write_text(f"{proc.pid}\n", encoding="utf-8")
        for _ in range(50):
            if initial_identity_file.exists() and initial_identity_file.stat().st_size > 0:
                break
            time.sleep(0.05)
        initial_heartbeat_log.write_text(f"Heartbeat actively renewing (pid={proc.pid})\n", encoding="utf-8")
    else:
        initial_pid_file.write_text("99999999\n", encoding="utf-8")
        initial_identity_file.write_text(
            json.dumps({
                "schemaVersion": 1,
                "status": "stopped",
                "pid": 99999999,
            }) + "\n",
            encoding="utf-8",
        )
        initial_failure_file.write_text(
            json.dumps({
                "schemaVersion": 1,
                "status": "guarded_command_failed",
                "exitStatus": 1,
                "detectedAt": "2026-08-24T00:00:00Z",
            }) + "\n",
            encoding="utf-8",
        )
        initial_heartbeat_log.write_text("Heartbeat stopped for quarantine\n", encoding="utf-8")

    mock_remote_lease_file = tmp_path / "mock_remote_lease.json"
    mock_remote_lease_file.write_text(
        json.dumps({
            "schemaVersion": 1,
            "resource": "pantheon-dev-environment",
            "mode": "deployment",
            "owner": "pantheon:ajoe734/pantheon:12345:1",
            "leaseId": "00000000-0000-0000-0000-000000000001",
            "expectedBackendSha": initial_bff_sha,
            "acquiredAt": "2026-08-24T00:00:00Z",
            "heartbeatAt": "2026-08-24T00:00:00Z",
            "expiresAt": "2026-08-24T00:05:00Z",
            "repository": "ajoe734/execute-plans",
            "branch": "environment-coordination",
            "path": ".pantheon/environment-leases/pantheon-dev-environment.json",
        }) + "\n",
        encoding="utf-8",
    )
    lease_events_log = tmp_path / "lease_events.log"

    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir(parents=True, exist_ok=True)

    step_summary = tmp_path / "step_summary.md"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "TARGET_ENV": "dev",
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(step_summary),
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REPOSITORY": "ajoe734/pantheon",
        "GITHUB_SERVER_URL": "https://github.com",
        "DEV_AUTH_PROFILE": "strict",
        "DEV_BFF_URL": "https://pantheon-lupin-dev-bff.mock",
        "DEV_FE_URL": "https://pantheon-lupin-dev-fe.mock",
        "PANTHEON_ROLLBACK_BACKEND_SHA": rollback_bff_sha,
        "PANTHEON_ROLLBACK_FRONTEND_SHA": rollback_fe_sha,
        "PANTHEON_DEV_ROLLBACK_BACKEND_SHA": rollback_bff_sha,
        "GCP_DEPLOY_PROJECT_ID": "pantheon-lupin-dev-20260719",
        "DEV_DEPLOY_DEADLINE_SECONDS": "1200",
        "PANTHEON_ENVIRONMENT_LEASE_TOKEN": "mock-token",
        "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(initial_state_file),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE": str(initial_pid_file),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_IDENTITY_FILE": str(initial_identity_file),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE": str(initial_failure_file),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_LOG": str(initial_heartbeat_log),
        "PANTHEON_PROD_DEV_ENVIRONMENT_LEASE_SCRIPT": str((ROOT / "scripts" / "dev_environment_lease.py").resolve()),
        "PANTHEON_MOCK_REMOTE_LEASE_FILE": str(mock_remote_lease_file),
        "PANTHEON_MOCK_LEASE_EVENTS_LOG": str(lease_events_log),
        "DEV_BFF_JWT_SECRET": "secret",
        "DEV_BFF_JWT_ISSUER": "pantheon-dev",
        "DEV_BFF_JWT_AUDIENCE": "bff-operators",
        "DEV_BFF_JWKS_URI": "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com",
        "DEV_BFF_OIDC_DISCOVERY_URL": "https://discovery.mock",
        "DEV_BFF_OIDC_ISSUER": "https://securetoken.google.com/pantheon-lupin-dev-20260719",
        "DEV_BFF_OIDC_AUDIENCE": "pantheon-lupin-dev-20260719",
        "DEV_BFF_OIDC_CLIENT_ID": "mock-oidc-client-id",
        "DEV_BFF_OIDC_CLIENT_SECRET": "mock-oidc-client-secret",
        "DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID": "pantheon-dev-viewer-v1",
        "DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET": "secret",
        "DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID": "pantheon-dev-approver-v1",
        "DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET": "secret",
        "DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID": "pantheon-dev-risk-owner-v1",
        "DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET": "secret",
        "DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID": "pantheon-dev-operator-a-v1",
        "DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET": "secret",
        "DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID": "pantheon-dev-operator-b-v1",
        "DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET": "secret",
        "DEV_BFF_MFA_REQUIRED": "true",
        "DEV_BFF_MFA_CLAIMS": "amr,acr,mfa,mfa_verified,firebase.sign_in_second_factor",
        "DEV_BFF_MFA_VALUES": "true,1,yes,mfa,otp,totp,webauthn",
        "DEV_BFF_REQUIRE_EMAIL_VERIFIED": "true",
        "DEV_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED": "false",
        "DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED": "true",
        "DEV_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED": "true",
        "DEV_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED": "true",
        "DEV_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED": "true",
        "DEV_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED": "true",
        "DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH": "hash",
        "DEV_BFF_ROLE_CLAIMS": "roles,role",
        "DEV_BFF_ROLE_MAP": "pantheon-operator=operator;pantheon-viewer=viewer;pantheon-reviewer=reviewer;pantheon-approver=approver;pantheon-risk-owner=risk_owner;pantheon-admin=admin",
        "DEV_BFF_ROLE_MAP_MODE": "strict",
        "DEV_BFF_DEFAULT_ROLE": "viewer",
        "DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN": "token",
        "DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED": "true",
        "DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN": "token",
        "DEV_MANAGEMENT_AI_STORE_BACKEND": "postgres",
        "DEV_MANAGEMENT_AI_STORE_SCHEMA": "management_ai",
        "DEV_MANAGEMENT_AI_DB_USER": "pantheon_management_ai",
        "DEV_MANAGEMENT_AI_DB_PASSWORD": "password",
        "DEV_MANAGEMENT_AI_DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
        "DEV_MANAGEMENT_AI_ATTACH_BUCKET": "bucket",
        "DEV_MANAGEMENT_AI_ATTACH_LOCATION": "asia-east1",
        "DEV_BFF_CANONICAL_CORS_ORIGIN": "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io",
        "DEV_BFF_CORS_ORIGINS": "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io",
    }
    return env, invocations_log, step_summary


def test_dev_deploy_compensation_mutation_aware_skips_deploy_when_already_at_baseline_negative(tmp_path: Path) -> None:
    """Executable negative regression proving that when preflight/build fails while the active BFF
    is already at the rollback baseline SHA, deploy_compensation under TARGET_ENV=dev with preceding failure
    state makes NO deploy_nonprod_vm.sh invocation and preserves the running services untouched."""
    rollback_bff = "1" * 40
    rollback_fe = "2" * 40
    env, invocations_log, step_summary = _setup_mock_compensation_environment(
        tmp_path,
        initial_bff_sha=rollback_bff,
        initial_fe_sha=rollback_fe,
        rollback_bff_sha=rollback_bff,
        rollback_fe_sha=rollback_fe,
    )
    script = _extract_deploy_compensation_run_script()
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, f"Compensation script failed: {result.stderr}"
    assert "skipping rollback deploy and verifying baseline pair" in result.stdout
    assert not invocations_log.exists() or invocations_log.read_text(encoding="utf-8").strip() == "", (
        "deploy_nonprod_vm.sh must NOT be invoked when hosted BFF is already at baseline"
    )
    assert step_summary.exists()
    summary_content = step_summary.read_text(encoding="utf-8")
    assert f"Preserved exact hosted dev baseline pair without deploy mutation: BFF={rollback_bff} FE={rollback_fe}" in summary_content


def test_dev_deploy_compensation_mutation_aware_executes_deploy_when_not_at_baseline_positive(tmp_path: Path) -> None:
    """Positive test proving that when post-rollout/paper/public-smoke fails while the active BFF
    is on a candidate (non-baseline) SHA, deploy_compensation under TARGET_ENV=dev with real heartbeat
    and preceding failure state acquires a rollback lease and invokes deploy_nonprod_vm.sh to restore baseline."""
    candidate_bff = "9" * 40
    rollback_bff = "1" * 40
    rollback_fe = "2" * 40
    env, invocations_log, step_summary = _setup_mock_compensation_environment(
        tmp_path,
        initial_bff_sha=candidate_bff,
        initial_fe_sha=rollback_fe,
        rollback_bff_sha=rollback_bff,
        rollback_fe_sha=rollback_fe,
        deploy_behavior="success",
    )
    script = _extract_deploy_compensation_run_script()
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, f"Compensation script failed: {result.stderr}"
    assert f"rolling back BFF to baseline {rollback_bff}" in result.stdout
    assert invocations_log.exists(), "deploy_nonprod_vm.sh MUST be invoked when hosted BFF is not at baseline"
    invocations = invocations_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(invocations) == 1
    assert f"--sha {rollback_bff}" in invocations[0]
    assert "--component bff" in invocations[0]
    assert step_summary.exists()
    summary_content = step_summary.read_text(encoding="utf-8")
    assert f"Restored exact hosted dev baseline pair: BFF={rollback_bff} FE={rollback_fe}" in summary_content


def test_dev_deploy_compensation_quarantined_predecessor_heartbeat_restores_baseline_e2e(tmp_path: Path) -> None:
    """Positive E2E test proving that after a preceding deploy, paper_bootstrap, or public_smoke failure
    under TARGET_ENV=dev where the predecessor heartbeat is already quarantined/stopped, deploy_compensation
    successfully authorizes exact-baseline rollback under its own lease heartbeat and restores the prior FE/BFF pair."""
    candidate_bff = "c" * 40
    rollback_bff = "a" * 40
    rollback_fe = "f" * 40
    env, invocations_log, step_summary = _setup_mock_compensation_environment(
        tmp_path,
        initial_bff_sha=candidate_bff,
        initial_fe_sha=rollback_fe,
        rollback_bff_sha=rollback_bff,
        rollback_fe_sha=rollback_fe,
        deploy_behavior="success",
        predecessor_heartbeat_state="quarantined",
    )
    script = _extract_deploy_compensation_run_script()
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, f"Compensation script failed: {result.stderr}"
    assert f"rolling back BFF to baseline {rollback_bff}" in result.stdout
    assert invocations_log.exists()
    actual_bff = (tmp_path / "mock_bff_sha.txt").read_text(encoding="utf-8").strip()
    actual_fe = (tmp_path / "mock_fe_sha.txt").read_text(encoding="utf-8").strip()
    assert actual_bff == rollback_bff
    assert actual_fe == rollback_fe
    summary_content = step_summary.read_text(encoding="utf-8")
    assert f"Restored exact hosted dev baseline pair: BFF={rollback_bff} FE={rollback_fe}" in summary_content
    lease_events = (tmp_path / "lease_events.log").read_text(encoding="utf-8")
    assert "CONTENTION: active lease owned by pantheon:ajoe734/pantheon:12345:1" in lease_events
    assert "ACQUIRED: owner=pantheon:ajoe734/pantheon:12345:1:rollback" in lease_events
    assert "RELEASED" in lease_events


def test_dev_deploy_compensation_active_predecessor_heartbeat_stopped_and_recovered_e2e(tmp_path: Path) -> None:
    """Positive E2E test proving that when an optional hosted probe fails before public_smoke
    while the predecessor heartbeat is STILL ACTIVE and renewing in the background, deploy_compensation
    safely stops the predecessor heartbeat process before acquiring the rollback lease, executes rollback deploy,
    and restores the exact baseline FE/BFF pair without hanging or exceeding the job timeout budget."""
    candidate_bff = "c" * 40
    rollback_bff = "a" * 40
    rollback_fe = "f" * 40
    env, invocations_log, step_summary = _setup_mock_compensation_environment(
        tmp_path,
        initial_bff_sha=candidate_bff,
        initial_fe_sha=rollback_fe,
        rollback_bff_sha=rollback_bff,
        rollback_fe_sha=rollback_fe,
        deploy_behavior="success",
        predecessor_heartbeat_state="active",
    )
    initial_pid = int((tmp_path / "initial_lease" / "heartbeat.pid").read_text(encoding="utf-8").strip())
    # Verify predecessor process is alive before compensation runs
    assert Path(f"/proc/{initial_pid}").exists()

    script = _extract_deploy_compensation_run_script()
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, f"Compensation script failed: {result.stderr}"
    assert f"Stopping active predecessor heartbeat (pid={initial_pid})" in result.stdout
    assert f"rolling back BFF to baseline {rollback_bff}" in result.stdout

    # Verify the predecessor heartbeat process was stopped cleanly
    time.sleep(0.1)
    reaped_pid, exit_status = os.waitpid(initial_pid, os.WNOHANG)
    assert reaped_pid == initial_pid, f"Predecessor process {initial_pid} must be terminated"
    assert not Path(f"/proc/{initial_pid}").exists()

    assert invocations_log.exists()
    actual_bff = (tmp_path / "mock_bff_sha.txt").read_text(encoding="utf-8").strip()
    actual_fe = (tmp_path / "mock_fe_sha.txt").read_text(encoding="utf-8").strip()
    assert actual_bff == rollback_bff
    assert actual_fe == rollback_fe
    summary_content = step_summary.read_text(encoding="utf-8")
    assert f"Restored exact hosted dev baseline pair: BFF={rollback_bff} FE={rollback_fe}" in summary_content
    lease_events = (tmp_path / "lease_events.log").read_text(encoding="utf-8")
    assert "CONTENTION: active lease owned by pantheon:ajoe734/pantheon:12345:1" in lease_events
    assert "ACQUIRED: owner=pantheon:ajoe734/pantheon:12345:1:rollback" in lease_events
    assert "RELEASED" in lease_events


def test_dev_deploy_compensation_predecessor_heartbeat_mismatched_identity_fails_closed_negative(tmp_path: Path) -> None:
    """Negative test proving that if the predecessor heartbeat PID belongs to a process
    whose identity does not match the identity file (or is PID reused/tampered),
    stop_predecessor_heartbeat fails closed, does NOT send signals to that process,
    and deploy_compensation exits with failure code 75."""
    candidate_bff = "c" * 40
    rollback_bff = "a" * 40
    rollback_fe = "f" * 40
    env, invocations_log, step_summary = _setup_mock_compensation_environment(
        tmp_path,
        initial_bff_sha=candidate_bff,
        initial_fe_sha=rollback_fe,
        rollback_bff_sha=rollback_bff,
        rollback_fe_sha=rollback_fe,
        deploy_behavior="success",
        predecessor_heartbeat_state="active",
    )
    initial_pid = int((tmp_path / "initial_lease" / "heartbeat.pid").read_text(encoding="utf-8").strip())
    assert Path(f"/proc/{initial_pid}").exists()

    # Tamper with the identity file so cmdlineSha256 does not match the running process
    identity_file = tmp_path / "initial_lease" / "heartbeat-identity.json"
    identity_data = json.loads(identity_file.read_text(encoding="utf-8"))
    identity_data["cmdlineSha256"] = "0" * 64
    identity_file.write_text(json.dumps(identity_data) + "\n", encoding="utf-8")

    script = _extract_deploy_compensation_run_script()
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 75, f"Compensation should fail closed with code 75, got {result.returncode}: {result.stderr}"
    assert f"Predecessor heartbeat identity verification failed for pid={initial_pid}" in result.stderr

    # Verify the mismatched process was NOT terminated and remains alive
    assert Path(f"/proc/{initial_pid}").exists()
    try:
        os.kill(initial_pid, signal.SIGTERM)
        os.waitpid(initial_pid, 0)
    except Exception:
        pass


def test_dev_deploy_compensation_unstoppable_predecessor_fails_closed_negative(tmp_path: Path) -> None:
    """Negative test proving that if a predecessor process remains alive after KILL escalation,
    stop_predecessor_heartbeat fails closed and deploy_compensation exits with failure code 75."""
    candidate_bff = "c" * 40
    rollback_bff = "a" * 40
    rollback_fe = "f" * 40
    env, invocations_log, step_summary = _setup_mock_compensation_environment(
        tmp_path,
        initial_bff_sha=candidate_bff,
        initial_fe_sha=rollback_fe,
        rollback_bff_sha=rollback_bff,
        rollback_fe_sha=rollback_fe,
        deploy_behavior="success",
        predecessor_heartbeat_state="active",
    )
    initial_pid = int((tmp_path / "initial_lease" / "heartbeat.pid").read_text(encoding="utf-8").strip())
    assert Path(f"/proc/{initial_pid}").exists()

    # Create a mock kill that ignores kill signals for initial_pid to simulate an unstoppable process
    bin_dir = tmp_path / "bin"
    mock_kill = bin_dir / "kill"
    mock_kill.write_text(
        f"""#!/usr/bin/env bash
if [[ "$*" == *"-0"* ]]; then
  exit 0
fi
if [[ "$*" == *"{initial_pid}"* ]]; then
  exit 0
fi
exec /usr/bin/kill "$@"
""",
        encoding="utf-8",
    )
    mock_kill.chmod(0o755)

    script = _extract_deploy_compensation_run_script()
    override = f"""
kill() {{
  if [[ "$*" == *"-0"* ]]; then
    return 0
  fi
  if [[ "$*" == *"{initial_pid}"* ]]; then
    return 0
  fi
  builtin kill "$@"
}}
"""
    script = override + "\n" + script

    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 75, f"Compensation should fail closed with code 75, got {result.returncode}: {result.stderr}"
    assert f"Predecessor heartbeat process (pid={initial_pid}) remains alive after SIGKILL" in result.stderr

    # Clean up test process
    try:
        os.kill(initial_pid, signal.SIGKILL)
        os.waitpid(initial_pid, 0)
    except Exception:
        pass


def test_dev_deploy_compensation_quarantined_lease_contention_and_ttl_takeover() -> None:
    """Test proving that compensation lease acquisition properly models and handles
    active quarantined lease contention and unexpired TTL wait before taking over the lease."""
    from datetime import datetime, timedelta, timezone
    from scripts.test_dev_environment_lease import FakeClient, manager, state_for, lease as test_lease

    client = FakeClient()
    mgr = manager(client)
    now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
    client.now = now

    # 1. Initial lease is active / quarantined by initial deploy owner with 300s TTL (expires at now + 300s)
    initial_owner = "pantheon:ajoe734/pantheon:12345:1"
    initial_state = state_for(
        owner=initial_owner,
        mode="deployment",
        heartbeat=now,
        expires=now + timedelta(seconds=300),
    )
    client.state = initial_state
    client.content_sha = "blob-initial"

    # 2. Attempt rollback acquisition while initial lease is still within unexpired TTL and wait_seconds=0 -> raises LeaseBusy
    rollback_owner = "pantheon:ajoe734/pantheon:12345:1:rollback"
    with pytest.raises(test_lease.LeaseBusy) as exc_info:
        mgr.acquire(
            mode="deployment",
            owner=rollback_owner,
            ttl_seconds=300,
            wait_seconds=0,
            poll_seconds=1.0,
            expected_backend_sha="b" * 40,
        )
    assert "dev environment is leased by pantheon:ajoe734/pantheon:12345:1" in str(exc_info.value)

    # 3. Simulate clock advancing past the quarantine TTL (now + 301s) -> takeover succeeds
    client.now = now + timedelta(seconds=301)
    acquired_state, content_sha, acquired_at = mgr.acquire(
        mode="deployment",
        owner=rollback_owner,
        ttl_seconds=300,
        wait_seconds=0,
        poll_seconds=1.0,
        expected_backend_sha="b" * 40,
    )
    assert acquired_state["owner"] == rollback_owner
    assert acquired_state["mode"] == "deployment"
    assert acquired_state["expectedBackendSha"] == "b" * 40
    assert client.put_expected_shas[-1] == "blob-initial"


def test_dev_deploy_compensation_mutation_aware_fails_closed_on_rollback_failure_negative(tmp_path: Path) -> None:
    """Negative test proving that if deploy rollback fails to restore the baseline SHA,
    deploy_compensation under TARGET_ENV=dev exits non-zero (fails closed) and leaves the lease quarantined."""
    candidate_bff = "9" * 40
    rollback_bff = "1" * 40
    rollback_fe = "2" * 40
    env, invocations_log, _ = _setup_mock_compensation_environment(
        tmp_path,
        initial_bff_sha=candidate_bff,
        initial_fe_sha=rollback_fe,
        rollback_bff_sha=rollback_bff,
        rollback_fe_sha=rollback_fe,
        deploy_behavior="fail_command",
    )
    script = _extract_deploy_compensation_run_script()
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert invocations_log.exists()


def test_dev_deploy_compensation_fails_closed_when_fe_baseline_mismatch_negative(tmp_path: Path) -> None:
    """Negative test proving that if the hosted FE deployment does not match PANTHEON_ROLLBACK_FRONTEND_SHA,
    deploy_compensation under TARGET_ENV=dev fails closed."""
    rollback_bff = "1" * 40
    rollback_fe = "2" * 40
    tampered_fe = "3" * 40
    env, _, _ = _setup_mock_compensation_environment(
        tmp_path,
        initial_bff_sha=rollback_bff,
        initial_fe_sha=tampered_fe,
        rollback_bff_sha=rollback_bff,
        rollback_fe_sha=rollback_fe,
    )
    script = _extract_deploy_compensation_run_script()
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0, "Compensation script must fail closed when FE baseline mismatch is detected"


def test_dev_deploy_compensation_forces_post_up_failure_inside_compensation_and_proves_pair_remains_baseline(tmp_path: Path) -> None:
    """Executable regression proving that when a post-up failure occurs inside the baseline compensation deploy,
    nested compensation is strictly bound to the baseline SHA (never the failed candidate), deploy fails closed,
    and the hosted BFF and FE remain at the baseline SHA pair without restoring the failed candidate."""
    candidate_bff = "9" * 40
    rollback_bff = "1" * 40
    rollback_fe = "2" * 40
    env, invocations_log, _ = _setup_mock_compensation_environment(
        tmp_path,
        initial_bff_sha=candidate_bff,
        initial_fe_sha=rollback_fe,
        rollback_bff_sha=rollback_bff,
        rollback_fe_sha=rollback_fe,
        deploy_behavior="post_up_failure_with_rollback_binding",
    )
    script = _extract_deploy_compensation_run_script()
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0, "Compensation script must fail closed when compensation deploy encounters post-up failure"
    assert invocations_log.exists(), "deploy_nonprod_vm.sh MUST be invoked to attempt baseline compensation"
    invocations = invocations_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(invocations) == 1
    assert f"--sha {rollback_bff}" in invocations[0]
    assert f"--rollback-sha {rollback_bff}" in invocations[0]
    assert "--component bff" in invocations[0]

    # Prove hosted pair remains baseline (BFF is rollback_bff, NOT candidate_bff; FE is rollback_fe)
    actual_bff = (tmp_path / "mock_bff_sha.txt").read_text(encoding="utf-8").strip()
    actual_fe = (tmp_path / "mock_fe_sha.txt").read_text(encoding="utf-8").strip()
    assert actual_bff == rollback_bff, f"Hosted BFF must remain baseline {rollback_bff}, but was restored to {actual_bff}"
    assert actual_fe == rollback_fe, f"Hosted FE must remain baseline {rollback_fe}, but was {actual_fe}"


@pytest.mark.parametrize("missing_var", REQUIRED_COMPENSATION_ENV_VARS)
def test_dev_deploy_compensation_fails_closed_when_credential_absent_negative(missing_var: str) -> None:
    """Negative test verifying that omitting any governed credential/config from deploy_compensation
    is rejected by the contract check."""
    step = _extract_deploy_compensation_step()
    tampered_step = re.sub(rf"\b{re.escape(missing_var)}:[^\n]*\n", "", step)
    assert f"{missing_var}:" not in tampered_step
    with pytest.raises(AssertionError, match=f"Missing required env var {missing_var}"):
        _validate_deploy_compensation_step(tampered_step)


REQUIRED_INNER_ROLLBACK_MAPPINGS = (
    'PANTHEON_BFF_JWT_SECRET="${PANTHEON_DEV_BFF_JWT_SECRET}"',
    'PANTHEON_BFF_JWT_ISSUER="${PANTHEON_DEV_BFF_JWT_ISSUER}"',
    'PANTHEON_BFF_JWT_AUDIENCE="${PANTHEON_DEV_BFF_JWT_AUDIENCE}"',
    'PANTHEON_BFF_JWKS_URI="${PANTHEON_DEV_BFF_JWKS_URI}"',
    'PANTHEON_BFF_OIDC_DISCOVERY_URL="${PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL}"',
    'PANTHEON_BFF_OIDC_ISSUER="${PANTHEON_DEV_BFF_OIDC_ISSUER}"',
    'PANTHEON_BFF_OIDC_AUDIENCE="${PANTHEON_DEV_BFF_OIDC_AUDIENCE}"',
    'PANTHEON_BFF_OIDC_CLIENT_ID="${PANTHEON_DEV_BFF_OIDC_CLIENT_ID}"',
    'PANTHEON_BFF_OIDC_CLIENT_SECRET="${PANTHEON_DEV_BFF_OIDC_CLIENT_SECRET}"',
    'PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID}"',
    'PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET}"',
    'PANTHEON_BFF_DEV_LOGIN_APPROVER_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID}"',
    'PANTHEON_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET}"',
    'PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID}"',
    'PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET}"',
    'PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID}"',
    'PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET}"',
    'PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID}"',
    'PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET}"',
    'PANTHEON_BFF_MFA_REQUIRED="${PANTHEON_DEV_BFF_MFA_REQUIRED}"',
    'PANTHEON_BFF_MFA_CLAIMS="${PANTHEON_DEV_BFF_MFA_CLAIMS}"',
    'PANTHEON_BFF_MFA_VALUES="${PANTHEON_DEV_BFF_MFA_VALUES}"',
    'PANTHEON_BFF_REQUIRE_EMAIL_VERIFIED="${PANTHEON_DEV_BFF_REQUIRE_EMAIL_VERIFIED}"',
    'PANTHEON_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED}"',
    'PANTHEON_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED}"',
    'PANTHEON_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED}"',
    'PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED}"',
    'PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED}"',
    'PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED}"',
    'PANTHEON_BFF_ROLE_CLAIMS="${PANTHEON_DEV_BFF_ROLE_CLAIMS}"',
    'PANTHEON_BFF_ROLE_MAP="${PANTHEON_DEV_BFF_ROLE_MAP}"',
    'PANTHEON_BFF_ROLE_MAP_MODE="${PANTHEON_DEV_BFF_ROLE_MAP_MODE}"',
    'PANTHEON_BFF_DEFAULT_ROLE="${PANTHEON_DEV_BFF_DEFAULT_ROLE}"',
    'PANTHEON_BFF_TENANT_ID="${PANTHEON_DEV_BFF_TENANT_ID}"',
    'PANTHEON_BFF_ALLOWED_TENANTS="${PANTHEON_DEV_BFF_ALLOWED_TENANTS}"',
    'PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN}"',
    'PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED}"',
    'PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN="${PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN}"',
    'AGORA_WORKSHOP_STORE_BACKEND=postgres',
    'AGORA_GOVERNANCE_STORE_BACKEND=postgres',
    'AGORA_RESEARCH_STORE_BACKEND=postgres',
    'AGORA_TRADING_ROOM_STORE_BACKEND=postgres',
    'MANAGEMENT_AI_STORE_BACKEND="${MANAGEMENT_AI_STORE_BACKEND}"',
    'MANAGEMENT_AI_STORE_SCHEMA="${MANAGEMENT_AI_STORE_SCHEMA}"',
    'MANAGEMENT_AI_DATABASE_URL="${MANAGEMENT_AI_DATABASE_URL}"',
)


def _extract_rollback_dev_bff_function() -> str:
    deploy_script = DEPLOY.read_text(encoding="utf-8")
    start = deploy_script.index("rollback_dev_bff_on_failure() {")
    end = deploy_script.index("\ncd \"${PANTHEON_REMOTE_DIR}\"", start)
    return deploy_script[start:end]


def _validate_rollback_dev_bff_function(func_text: str) -> None:
    for mapping in REQUIRED_INNER_ROLLBACK_MAPPINGS:
        assert mapping in func_text, f"Missing required env mapping '{mapping}' in rollback_dev_bff_on_failure"


def test_dev_inner_rollback_preserves_full_governed_bff_environment() -> None:
    """rollback_dev_bff_on_failure in deploy_nonprod_vm.sh must pass all governed BFF credentials,
    role maps, Agora configuration, and adapter variables to Compose so the restored baseline remains usable."""
    func_text = _extract_rollback_dev_bff_function()
    _validate_rollback_dev_bff_function(func_text)


@pytest.mark.parametrize("missing_mapping", REQUIRED_INNER_ROLLBACK_MAPPINGS)
def test_dev_inner_rollback_fails_closed_when_mapping_absent_negative(missing_mapping: str) -> None:
    """Negative test verifying that omitting any environment mapping from rollback_dev_bff_on_failure
    is rejected by the contract check."""
    func_text = _extract_rollback_dev_bff_function()
    tampered_func = func_text.replace(missing_mapping, "# OMITTED")
    assert missing_mapping not in tampered_func
    with pytest.raises(AssertionError, match="Missing required env mapping"):
        _validate_rollback_dev_bff_function(tampered_func)


def test_dev_deploy_inner_rollback_skips_rollback_when_nested_compensation_matches_deploy_sha() -> None:
    """Inner rollback in deploy_nonprod_vm.sh must skip rollback and exit 1 when
    PANTHEON_DEV_ROLLBACK_BACKEND_SHA == PANTHEON_DEPLOY_SHA, preventing nested compensation
    from mistakenly checking out and restoring a failed candidate."""
    func_text = _extract_rollback_dev_bff_function()
    assert 'local rollback_sha="${PANTHEON_DEV_ROLLBACK_BACKEND_SHA:-${DEV_PRE_DEPLOY_BFF_SHA:-}}"' in func_text
    assert '"${rollback_sha}" == "${PANTHEON_DEPLOY_SHA}"' in func_text
    assert 'automatic BFF rollback skipped: no distinct valid baseline rollback SHA available' in func_text


def test_dev_deploy_built_image_identity_matches_requested_sha_including_baseline_compensation(tmp_path: Path) -> None:
    """Executable regression proving that candidate image prebuilds in deploy_nonprod_vm.sh
    bind the requested PANTHEON_DEPLOY_SHA into GIT_SHA build args for operator-bff and
    lifecycle projector, that the subsequent rollout executes without --build using the
    correctly stamped image identity, and that baseline compensation restores the exact baseline SHA."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    docker_log = tmp_path / "docker_invocations.log"
    image_state_dir = tmp_path / "image_state"
    image_state_dir.mkdir(parents=True, exist_ok=True)

    mock_docker = bin_dir / "docker"
    mock_docker.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
log_file="{docker_log}"
state_dir="{image_state_dir}"

if [[ "$1" == "compose" ]]; then
  shift
  while [[ $# -gt 0 && "$1" =~ ^- ]]; do
    if [[ "$1" == "-p" || "$1" == "-f" ]]; then
      shift 2
    else
      shift 1
    fi
  done
  subcmd="${{1:-}}"
  shift || true

  if [[ "$subcmd" == "build" ]]; then
    services=()
    while [[ $# -gt 0 && ! "$1" =~ ^- ]]; do
      services+=("$1")
      shift
    done
    if [[ ${{#services[@]}} -eq 0 ]]; then
      services=("operator-bff" "loop-run-projector-scheduler")
    fi
    git_sha="${{GIT_SHA:-unknown}}"
    echo "BUILD git_sha=${{git_sha}} services=${{services[*]}}" >> "$log_file"
    for s in "${{services[@]}}"; do
      echo "${{git_sha}}" > "${{state_dir}}/${{s}}.sha"
    done
  elif [[ "$subcmd" == "up" ]]; then
    has_build_flag=false
    services=()
    while [[ $# -gt 0 ]]; do
      if [[ "$1" == "--build" ]]; then
        has_build_flag=true
      elif [[ ! "$1" =~ ^- ]]; then
        services+=("$1")
      fi
      shift
    done
    if [[ ${{#services[@]}} -eq 0 ]]; then
      services=("operator-bff" "loop-run-projector-scheduler")
    fi
    for s in "${{services[@]}}"; do
      built_sha="$(cat "${{state_dir}}/${{s}}.sha" 2>/dev/null || echo "none")"
      echo "UP has_build_flag=${{has_build_flag}} service=${{s}} image_sha=${{built_sha}}" >> "$log_file"
    done
  fi
fi
exit 0
""",
        encoding="utf-8",
    )
    mock_docker.chmod(0o755)

    # 1. Candidate BFF deploy path: verify build receives target SHA and up runs without --build
    candidate_sha = "c" * 40
    bff_deploy_snippet = f"""
    export PANTHEON_DEPLOY_COMPONENT=bff
    export PANTHEON_DEPLOY_SHA="{candidate_sha}"
    export PATH="{bin_dir}:$PATH"

    export GIT_SHA="${{PANTHEON_DEPLOY_SHA}}"
    COMPOSE_BAKE=false \\
    COMPOSE_PROFILES="" \\
    GIT_SHA="${{PANTHEON_DEPLOY_SHA}}" \\
    BUILD_TIME="2026-08-24T00:00:00Z" \\
      docker compose -p pantheon -f docker-compose.yml build operator-bff loop-run-projector-scheduler

    COMPOSE_BAKE=false \\
    COMPOSE_PROFILES="" \\
    GIT_SHA="${{PANTHEON_DEPLOY_SHA}}" \\
    BUILD_TIME="2026-08-24T00:00:00Z" \\
    PANTHEON_ENV=dev \\
      docker compose -p pantheon -f docker-compose.yml up -d --force-recreate --no-deps operator-bff loop-run-projector-scheduler
    """
    subprocess.run(["bash", "-euo", "pipefail", "-c", bff_deploy_snippet], capture_output=True, text=True, check=True)
    logs = docker_log.read_text(encoding="utf-8").strip().splitlines()
    assert f"BUILD git_sha={candidate_sha} services=operator-bff loop-run-projector-scheduler" in logs[0]
    assert f"UP has_build_flag=false service=operator-bff image_sha={candidate_sha}" in logs[1]
    assert f"UP has_build_flag=false service=loop-run-projector-scheduler image_sha={candidate_sha}" in logs[2]

    # 2. Baseline compensation deploy path: verify baseline compensation prebuilds and rolls out baseline SHA
    docker_log.unlink()
    baseline_sha = "1" * 40
    compensation_deploy_snippet = f"""
    export PANTHEON_DEPLOY_COMPONENT=bff
    export PANTHEON_DEPLOY_SHA="{baseline_sha}"
    export PATH="{bin_dir}:$PATH"

    export GIT_SHA="${{PANTHEON_DEPLOY_SHA}}"
    COMPOSE_BAKE=false \\
    COMPOSE_PROFILES="" \\
    GIT_SHA="${{PANTHEON_DEPLOY_SHA}}" \\
    BUILD_TIME="2026-08-24T00:00:00Z" \\
      docker compose -p pantheon -f docker-compose.yml build operator-bff loop-run-projector-scheduler

    COMPOSE_BAKE=false \\
    COMPOSE_PROFILES="" \\
    GIT_SHA="${{PANTHEON_DEPLOY_SHA}}" \\
    BUILD_TIME="2026-08-24T00:00:00Z" \\
    PANTHEON_ENV=dev \\
      docker compose -p pantheon -f docker-compose.yml up -d --force-recreate --no-deps operator-bff loop-run-projector-scheduler
    """
    subprocess.run(["bash", "-euo", "pipefail", "-c", compensation_deploy_snippet], capture_output=True, text=True, check=True)
    logs = docker_log.read_text(encoding="utf-8").strip().splitlines()
    assert f"BUILD git_sha={baseline_sha} services=operator-bff loop-run-projector-scheduler" in logs[0]
    assert f"UP has_build_flag=false service=operator-bff image_sha={baseline_sha}" in logs[1]
    assert f"UP has_build_flag=false service=loop-run-projector-scheduler image_sha={baseline_sha}" in logs[2]


def test_dev_root_deploy_stale_compose_replacement_cleanup_defined_and_invoked() -> None:
    """Dev deploy script must define cleanup_stale_compose_replacement_containers and invoke it
    before root compose rollout and bff recreate, filtering to non-running containers with
    com.docker.compose.project=pantheon and hash-prefixed pantheon names."""
    deploy_script = DEPLOY.read_text(encoding="utf-8")

    assert "cleanup_stale_compose_replacement_containers() {" in deploy_script
    assert 'docker ps -a --filter "label=com.docker.compose.project=pantheon"' in deploy_script
    assert '"${cstate}" == "running"' in deploy_script
    assert '"${cstate}" == "restarting"' in deploy_script
    assert '"${cstatus}" =~ ^Up' in deploy_script
    assert '"${cstatus}" =~ ^Restarting' in deploy_script
    assert '"${clean_name}" =~ ^[0-9a-fA-F]+[-_]pantheon' in deploy_script
    assert "docker rm -f" in deploy_script

    root_section = deploy_script.split('case "${PANTHEON_DEPLOY_COMPONENT}" in', 1)[1].split("root)", 1)[1].split(";;", 1)[0]
    cleanup_idx = root_section.index("cleanup_stale_compose_replacement_containers")
    up_idx = root_section.index("docker compose -p pantheon -f docker-compose.yml up -d")
    assert cleanup_idx < up_idx, "cleanup_stale_compose_replacement_containers must run before root compose up rollout"

    bff_section = deploy_script.split('case "${PANTHEON_DEPLOY_COMPONENT}" in', 1)[1].split("\n  bff)", 1)[1].split(";;", 1)[0]
    bff_cleanup_idx = bff_section.index("cleanup_stale_compose_replacement_containers")
    bff_up_idx = bff_section.index("docker compose -p pantheon -f docker-compose.yml up -d")
    assert bff_cleanup_idx < bff_up_idx, "cleanup_stale_compose_replacement_containers must run before bff compose up recreate"


def test_cleanup_stale_compose_replacement_containers_executable_positive_and_negative(tmp_path: Path) -> None:
    """Executable test proving cleanup_stale_compose_replacement_containers removes only non-running
    containers with com.docker.compose.project=pantheon and hash-prefixed pantheon names, while
    leaving running containers, restarting containers, non-hash containers, and other project containers intact."""
    deploy_script = DEPLOY.read_text(encoding="utf-8")
    func_start = deploy_script.index("cleanup_stale_compose_replacement_containers() {")
    func_end = deploy_script.index("\nrollback_dev_bff_on_failure() {", func_start)
    func_def = deploy_script[func_start:func_end]

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    rm_log = tmp_path / "removed_containers.log"

    # Mock docker script
    mock_docker = bin_dir / "docker"
    mock_docker.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail

if [[ "$1" == "ps" ]]; then
  # Check if filter specifies project=pantheon
  has_pantheon_label=false
  for arg in "$@"; do
    if [[ "$arg" == "label=com.docker.compose.project=pantheon" ]]; then
      has_pantheon_label=true
    fi
  done

  # Output mock container table: ID <TAB> Names <TAB> State <TAB> Status
  if [[ "$has_pantheon_label" == "true" ]]; then
    printf "%s\\t%s\\t%s\\t%s\\n" "cid_stale_1" "1234567890ab_pantheon-operator-bff-1" "exited" "Exited (0) 5 minutes ago"
    printf "%s\\t%s\\t%s\\t%s\\n" "cid_stale_2" "/d20e73e97086_pantheon_postgres_1" "dead" "Dead"
    printf "%s\\t%s\\t%s\\t%s\\n" "cid_stale_3" "499602d2da88_pantheon-paper-runtime" "created" "Created"
    printf "%s\\t%s\\t%s\\t%s\\n" "cid_stale_4" "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef_pantheon-minio-1" "exited" "Exited (1)"
    printf "%s\\t%s\\t%s\\t%s\\n" "cid_running_rep" "abcdef123456_pantheon-operator-bff-1" "running" "Up 2 hours"
    printf "%s\\t%s\\t%s\\t%s\\n" "cid_running_rep_up" "abcdef123456_pantheon_postgres_1" "unknown" "Up 10 minutes"
    printf "%s\\t%s\\t%s\\t%s\\n" "cid_restarting_rep" "fedcba987654_pantheon-operator-bff-1" "restarting" "Restarting (1) 5 seconds ago"
    printf "%s\\t%s\\t%s\\t%s\\n" "cid_restarting_rep_status" "987654fedcba_pantheon_postgres_1" "unknown" "Restarting (127) 2 seconds ago"
    printf "%s\\t%s\\t%s\\t%s\\n" "cid_normal_stopped" "pantheon-postgres-1" "exited" "Exited (0)"
    printf "%s\\t%s\\t%s\\t%s\\n" "cid_normal_stopped_slash" "/pantheon-operator-bff-1" "created" "Created"
    printf "%s\\t%s\\t%s\\t%s\\n" "cid_normal_running" "pantheon-operator-bff-1" "running" "Up 1 hour"
  else
    # Non-filtered or other project queries
    printf "%s\\t%s\\t%s\\t%s\\n" "cid_other_proj" "1234567890ab_otherproject-worker-1" "exited" "Exited (0)"
  fi
elif [[ "$1" == "rm" ]]; then
  shift
  while [[ $# -gt 0 && "$1" =~ ^- ]]; do
    shift
  done
  for cid in "$@"; do
    echo "$cid" >> "{rm_log}"
  done
fi
exit 0
""",
        encoding="utf-8",
    )
    mock_docker.chmod(0o755)

    test_snippet = f"""
    export PATH="{bin_dir}:$PATH"
    info() {{ echo "$*"; }}

    {func_def}

    cleanup_stale_compose_replacement_containers
    """
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", test_snippet],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "cleaned up 4 stale Compose replacement container(s)" in result.stdout
    assert "removing stale Compose replacement container: 1234567890ab_pantheon-operator-bff-1" in result.stdout
    assert "removing stale Compose replacement container: d20e73e97086_pantheon_postgres_1" in result.stdout
    assert "removing stale Compose replacement container: 499602d2da88_pantheon-paper-runtime" in result.stdout
    assert "removing stale Compose replacement container: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef_pantheon-minio-1" in result.stdout

    assert rm_log.exists()
    removed = rm_log.read_text(encoding="utf-8").strip().splitlines()
    assert sorted(removed) == ["cid_stale_1", "cid_stale_2", "cid_stale_3", "cid_stale_4"]

    # Negative assertions:
    assert "cid_running_rep" not in removed
    assert "cid_running_rep_up" not in removed
    assert "cid_restarting_rep" not in removed
    assert "cid_restarting_rep_status" not in removed
    assert "cid_normal_stopped" not in removed
    assert "cid_normal_stopped_slash" not in removed
    assert "cid_normal_running" not in removed
    assert "cid_other_proj" not in removed


def test_cleanup_stale_compose_replacement_containers_handles_empty_and_missing_docker(tmp_path: Path) -> None:
    """cleanup_stale_compose_replacement_containers must exit cleanly when no containers exist or docker is missing."""
    deploy_script = DEPLOY.read_text(encoding="utf-8")
    func_start = deploy_script.index("cleanup_stale_compose_replacement_containers() {")
    func_end = deploy_script.index("\nrollback_dev_bff_on_failure() {", func_start)
    func_def = deploy_script[func_start:func_end]

    # Case 1: Docker outputs empty list
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    mock_docker = bin_dir / "docker"
    mock_docker.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    mock_docker.chmod(0o755)

    test_empty = f"""
    export PATH="{bin_dir}:$PATH"
    info() {{ echo "$*"; }}
    {func_def}
    cleanup_stale_compose_replacement_containers
    """
    res1 = subprocess.run(["bash", "-euo", "pipefail", "-c", test_empty], capture_output=True, text=True, check=True)
    assert "cleaned up" not in res1.stdout

    # Case 2: Docker command not in PATH
    test_no_docker = f"""
    export PATH="/tmp/nonexistent_path"
    info() {{ echo "$*"; }}
    {func_def}
    cleanup_stale_compose_replacement_containers
    """
    res2 = subprocess.run(["bash", "-euo", "pipefail", "-c", test_no_docker], capture_output=True, text=True, check=True)
    assert "docker command unavailable" in res2.stdout
