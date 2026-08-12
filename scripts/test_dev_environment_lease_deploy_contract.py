from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


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


def test_rollback_baseline_uses_the_accepted_frontend_pair_manifest() -> None:
    """A failed BFF cannot erase the last accepted release identity needed to
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
    assert 'baseline_source="accepted_frontend_pair_manifest"' in baseline
    assert 'baseline_source="accepted_frontend_pair_manifest+live_bff_match"' in baseline
    assert 'baseline_source="accepted_frontend_pair_manifest+live_bff_drift_recovery"' in baseline
    assert '[[ "${observed_previous}" =~ ^[0-9a-f]{40}$ ]]' in baseline
    assert '"${observed_previous}" refs/remotes/origin/dev' in baseline
    assert "Hosted BFF drift identity is not an exact commit SHA." in baseline
    assert "Hosted BFF drift identity is not contained in Pantheon dev." in baseline
    assert "retaining the accepted pair as rollback authority" in baseline
    assert "recovering from the accepted frontend pair manifest" in baseline
    assert 'manifest.get("deploymentState") != "accepted"' in baseline
    assert 'manifest.get("bffCommitEvidence") is not True' in baseline
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
    assert workflow.count(f"google-github-actions/auth@{AUTH_SHA}") == 3
    assert workflow.count(f"google-github-actions/setup-gcloud@{GCLOUD_SHA}") == 3
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
