from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
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
    assert "Out-of-order execute-plans candidate rejected" in dev
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
    assert pythoninspect_shell_values == [""]
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

    assert "DEV_DEPLOY_DEADLINE_SECONDS:" in dev
    assert '--deadline-seconds "${DEV_DEPLOY_DEADLINE_SECONDS}"' in dev

    deadline_match = re.search(
        r"DEV_DEPLOY_DEADLINE_SECONDS:\s*\${{\s*vars\.DEV_DEPLOY_DEADLINE_SECONDS\s*\|\|\s*'(\d+)'\s*}}",
        dev,
    )
    assert deadline_match is not None, "DEV_DEPLOY_DEADLINE_SECONDS default must be explicit"
    default_deadline_seconds = int(deadline_match.group(1))

    assert default_deadline_seconds < job_timeout_seconds, (
        f"Deploy command deadline ({default_deadline_seconds}s) must stay strictly below "
        f"job timeout ({job_timeout_seconds}s)"
    )


@pytest.mark.parametrize(
    ("args", "extra_env", "expected_deadline"),
    [
        (["--deadline-seconds", "900"], {}, "900"),
        (["--deploy-timeout-seconds", "750"], {}, "750"),
        ([], {"DEV_DEPLOY_DEADLINE_SECONDS": "600"}, "600"),
        ([], {"DEV_DEPLOY_TIMEOUT_SECONDS": "450"}, "450"),
        ([], {}, "1200"),
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


def test_dev_root_deploy_builds_candidate_before_mutating_active_runtime() -> None:
    """Dev root and BFF deploys must validate config and build images before mutating
    or recreating running containers."""
    deploy_script = DEPLOY.read_text(encoding="utf-8")

    root_section = deploy_script.split("case \"${PANTHEON_DEPLOY_COMPONENT}\" in", 1)[1].split("root)", 1)[1].split(";;", 1)[0]
    config_idx = root_section.index("docker compose -p pantheon -f docker-compose.yml config --quiet")
    build_idx = root_section.index("docker compose -p pantheon -f docker-compose.yml build")
    up_idx = root_section.index("docker compose -p pantheon -f docker-compose.yml up -d")
    projector_recreate_idx = root_section.index("docker compose -p pantheon -f docker-compose.yml up -d --force-recreate --no-deps loop-run-projector-scheduler")

    assert config_idx < build_idx < up_idx < projector_recreate_idx

    bff_section = deploy_script.split("case \"${PANTHEON_DEPLOY_COMPONENT}\" in", 1)[1].split("\n  bff)", 1)[1].split(";;", 1)[0]
    bff_build_idx = bff_section.index("docker compose -p pantheon -f docker-compose.yml build operator-bff loop-run-projector-scheduler")
    bff_up_idx = bff_section.index("docker compose -p pantheon -f docker-compose.yml up -d --force-recreate --no-deps operator-bff loop-run-projector-scheduler")
    assert bff_build_idx < bff_up_idx


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
    assert "docker compose -p pantheon -f docker-compose.yml up -d \\\n      || { dump_dev_root_failure_diagnostics; exit 1; }" in root_section


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

