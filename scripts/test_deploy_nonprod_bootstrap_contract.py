from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_nonprod_vm.sh"
DUMMY_SHA = "249cd9c03675e2566a3d5f1e6a4be06af405da45"

VALID_NEUTRAL_STAGING_ENV = {
    "PROJECT_ID": "neutral-staging-project",
    "REMOTE_USER": "deployer",
    "STAGING_CONTROL_VM": "neutral-staging-control",
    "STAGING_CONTROL_ZONE": "asia-east1-b",
    "STAGING_CONTROL_REMOTE_DIR": "/home/deployer/pantheon",
    "STAGING_EXEC_VM": "neutral-staging-exec",
    "STAGING_EXEC_ZONE": "asia-east1-b",
    "STAGING_EXEC_REMOTE_DIR": "/home/deployer/pantheon",
    "STAGING_EXEC_HEALTH_URL": "http://10.0.0.1:28081",
    "STAGING_BFF_CANONICAL_CORS_ORIGIN": "https://neutral-staging-fe.example.com",
    "STAGING_BFF_CORS_ORIGINS": "https://neutral-staging-fe.example.com",
}


def _clean_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    clean = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if extra_env:
        clean.update(extra_env)
    return clean


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _extract_job(workflow: str, start_job: str, next_job: str | None = None) -> str:
    start = workflow.index(f"  {start_job}:")
    if next_job:
        end = workflow.index(f"  {next_job}:", start)
        return workflow[start:end]
    return workflow[start:]


def test_workflow_declares_dev_only_bootstrap_inputs() -> None:
    workflow = _workflow_text()
    inputs_block = workflow[: workflow.index("  push:")]

    assert "bootstrap_empty_host:" in inputs_block
    assert "bootstrap_predecessor_backend_sha:" in inputs_block
    assert "bootstrap_predecessor_frontend_sha:" in inputs_block

    assert "default: false" in inputs_block
    assert 'default: ""' in inputs_block


def test_workflow_target_step_rejects_bootstrap_on_non_dev_environment() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    target_step = dev_job[
        dev_job.index("- name: Resolve and validate dev payload") :
        dev_job.index("- name: Resolve exact execute-plans dev payload")
    ]

    assert 'if [[ "${BOOTSTRAP_EMPTY_HOST}" == "true" ]]; then' in target_step
    assert 'if [[ "${TARGET_ENV}" != "dev" ]]; then' in target_step
    assert "Bootstrap input is only permitted for dev environment" in target_step


def test_workflow_target_step_rejects_malformed_bootstrap_shas() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    target_step = dev_job[
        dev_job.index("- name: Resolve and validate dev payload") :
        dev_job.index("- name: Resolve exact execute-plans dev payload")
    ]

    assert '[[ "${BOOTSTRAP_PREDECESSOR_BACKEND_SHA,,}" =~ ^[0-9a-f]{40}$ ]]' in target_step
    assert "bootstrap_predecessor_backend_sha must be one exact lowercase 40-character commit SHA" in target_step
    assert '[[ "${BOOTSTRAP_PREDECESSOR_FRONTEND_SHA,,}" =~ ^[0-9a-f]{40}$ ]]' in target_step
    assert "bootstrap_predecessor_frontend_sha must be one exact lowercase 40-character commit SHA" in target_step


def test_workflow_staging_step_rejects_bootstrap_flag() -> None:
    workflow = _workflow_text()
    staging_job = _extract_job(workflow, "deploy-staging-live")

    assert 'if [[ "${{ inputs.bootstrap_empty_host || false }}" == "true" ]]; then' in staging_job
    assert "Staging environment does not support bootstrap_empty_host; bootstrap is dev-only." in staging_job


def test_workflow_rollback_baseline_rejects_missing_manifest_when_not_bootstrapping() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    baseline_step = dev_job[
        dev_job.index("- name: Capture exact hosted FE and BFF rollback baseline") :
        dev_job.index("- name: Seal exact-pair admission artifact")
    ]

    assert 'if [[ "${BOOTSTRAP_EMPTY_HOST:-false}" == "true" ]]; then' in baseline_step
    assert 'else' in baseline_step
    assert '"${DEV_FE_URL%/}/deployment.json" > "${deployment_json}"' in baseline_step


def test_workflow_rollback_baseline_rejects_repeated_bootstrap_when_manifest_exists() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    baseline_step = dev_job[
        dev_job.index("- name: Capture exact hosted FE and BFF rollback baseline") :
        dev_job.index("- name: Seal exact-pair admission artifact")
    ]

    assert 'manifest_status="$(curl --silent --show-error' in baseline_step
    assert '--output "${deployment_json}" --write-out \'%{http_code}\'' in baseline_step
    assert 'if [[ "${manifest_status}" != "404" ]]; then' in baseline_step
    assert "expected explicit HTTP 404" in baseline_step
    assert "deployment.json was unreachable" in baseline_step


def test_workflow_rollback_baseline_requires_ancestor_commits_for_bootstrap() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    baseline_step = dev_job[
        dev_job.index("- name: Capture exact hosted FE and BFF rollback baseline") :
        dev_job.index("- name: Seal exact-pair admission artifact")
    ]

    assert 'git -C .agora-gate-controller merge-base --is-ancestor \\\n              "${bootstrap_backend}" refs/remotes/origin/dev' in baseline_step
    assert 'git -C .agora-frontend merge-base --is-ancestor \\\n              "${bootstrap_frontend}" refs/remotes/origin/dev' in baseline_step
    assert "Bootstrap predecessor backend commit ${bootstrap_backend} is not contained in Pantheon dev." in baseline_step
    assert "Bootstrap predecessor frontend commit ${bootstrap_frontend} is not contained in execute-plans dev." in baseline_step


def test_workflow_rollback_baseline_admits_predecessor_via_agora_compat_manifest() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    baseline_step = dev_job[
        dev_job.index("- name: Capture exact hosted FE and BFF rollback baseline") :
        dev_job.index("- name: Seal exact-pair admission artifact")
    ]

    assert "bootstrap-predecessor-compatibility-manifest.json" in baseline_step
    assert "bootstrap-predecessor-candidate-ledger.json" in baseline_step
    assert "python3 .target/scripts/agora_compat_manifest.py write" in baseline_step
    assert "python3 .target/scripts/agora_compat_manifest.py deployment-gate" in baseline_step
    assert "python3 scripts/agora_compat_manifest.py" not in baseline_step
    assert "bootstrap predecessor pair is not compatible" in baseline_step
    assert 'baseline_source="bootstrap_predecessor_pair"' in baseline_step


def test_workflow_deploys_predecessor_pair_under_lease_in_strict_read_only_mode() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")

    step_marker = "- name: Deploy bootstrap predecessor pair in strict live read-only mode under lease"
    assert step_marker in dev_job
    bootstrap_step = dev_job[
        dev_job.index(step_marker) :
        dev_job.index("- name: Deploy dev VM stack under lease")
    ]

    assert "if: ${{ env.BOOTSTRAP_EMPTY_HOST == 'true' }}" in bootstrap_step
    assert "deploy_nonprod_vm.sh" in bootstrap_step
    assert '--component bff' in bootstrap_step
    assert '--sha "${BOOTSTRAP_PREDECESSOR_BACKEND_SHA}"' in bootstrap_step
    assert "cross_repo_release_controller.py" in bootstrap_step
    assert '--candidate-profile "read-only"' in bootstrap_step
    assert "mismatched served identity" in bootstrap_step
    assert "bootstrap FE profile must be read-only" in bootstrap_step
    assert 'PANTHEON_DEV_LEASE_EXPECTED_BACKEND_SHA: ${{ steps.target.outputs.sha }}' in bootstrap_step
    assert 'PANTHEON_DEV_BOOTSTRAP_PREDECESSOR: "true"' in bootstrap_step
    assert 'DEV_BFF_JWT_SECRET: ${{ secrets.DEV_BFF_JWT_SECRET }}' in bootstrap_step
    assert 'DEV_BFF_OIDC_CLIENT_SECRET: ${{ secrets.DEV_BFF_OIDC_CLIENT_SECRET }}' in bootstrap_step
    assert 'DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET: ${{ secrets.DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET }}' in bootstrap_step
    assert 'DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN: ${{ secrets.DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN }}' in bootstrap_step
    assert 'export DEV_BFF_AUTH_MODE=strict' in bootstrap_step


def test_deploy_script_allows_only_explicit_bootstrap_lease_identity_override() -> None:
    script = (ROOT / "scripts" / "deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    contract = script[
        script.index("verify_dev_environment_lease_contract()") :
        script.index("usage()", script.index("verify_dev_environment_lease_contract()"))
    ]
    assert 'PANTHEON_DEV_LEASE_EXPECTED_BACKEND_SHA:-${DEPLOY_SHA}' in contract
    assert 'PANTHEON_DEV_BOOTSTRAP_PREDECESSOR:-false' in contract
    assert "dev lease expected backend override is only permitted for an explicit bootstrap predecessor" in contract


def test_workflow_candidate_deploy_requires_predecessor_served_identity_readback() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")

    pred_step_index = dev_job.index("- name: Deploy bootstrap predecessor pair in strict live read-only mode under lease")
    deploy_step_index = dev_job.index("- name: Deploy dev VM stack under lease")

    assert pred_step_index < deploy_step_index
    deploy_step = dev_job[
        deploy_step_index :
        dev_job.index("- name: Ensure governed dev paper baseline under lease", deploy_step_index)
    ]
    assert "PANTHEON_DEV_ROLLBACK_BACKEND_SHA: ${{ steps.rollback_baseline.outputs.sha }}" in deploy_step
    assert '--rollback-sha "${{ steps.rollback_baseline.outputs.sha }}"' in deploy_step


def test_workflow_coordinate_release_passes_predecessor_pair_shas() -> None:
    workflow = _workflow_text()
    coordinate_job = _extract_job(workflow, "coordinate-dev-release", "deploy-staging-live")

    assert "PREVIOUS_BACKEND_SHA: ${{ needs.deploy-dev.outputs.previous_backend_sha }}" in coordinate_job
    assert "PREVIOUS_FRONTEND_SHA: ${{ needs.deploy-dev.outputs.previous_frontend_sha }}" in coordinate_job
    assert '--predecessor-fe-sha "${PREVIOUS_FRONTEND_SHA}"' in coordinate_job
    assert '--predecessor-bff-sha "${PREVIOUS_BACKEND_SHA}"' in coordinate_job


def test_workflow_bootstrap_requires_dev_variables() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    bootstrap_step = dev_job[
        dev_job.index("- name: Deploy bootstrap predecessor pair in strict live read-only mode under lease") :
        dev_job.index("- name: Deploy dev VM stack under lease")
    ]

    assert "for var_name in DEV_VM DEV_ZONE GCP_DEPLOY_PROJECT_ID DEV_BFF_URL DEV_FE_URL DEV_DEPLOY_DEADLINE_SECONDS; do" in bootstrap_step
    assert 'echo "Required bootstrap variable ${var_name} is unset or empty; refusing to deploy." >&2' in bootstrap_step
    assert "exit 1" in bootstrap_step


def test_workflow_rollback_baseline_requires_dev_urls_when_bootstrapping() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    baseline_step = dev_job[
        dev_job.index("- name: Capture exact hosted FE and BFF rollback baseline") :
        dev_job.index("- name: Seal exact-pair admission artifact")
    ]

    assert 'if [[ -z "${DEV_FE_URL:-}" || -z "${DEV_BFF_URL:-}" ]]; then' in baseline_step
    assert 'Empty-host bootstrap requires DEV_FE_URL and DEV_BFF_URL to be set.' in baseline_step


def test_workflow_contains_no_retired_project_or_host_fallbacks_in_bootstrap_or_staging() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    staging_job = _extract_job(workflow, "deploy-staging-live")

    # In dev bootstrap steps
    bootstrap_step = dev_job[
        dev_job.index("- name: Deploy bootstrap predecessor pair in strict live read-only mode under lease") :
        dev_job.index("- name: Deploy dev VM stack under lease")
    ]
    assert "pantheon-lupin-dev-20260719" not in bootstrap_step
    assert "sslip.io" not in bootstrap_step
    assert "35.201.204.12" not in bootstrap_step

    # In staging job
    assert "pantheon-benjamin-20260528" not in staging_job
    assert "104.155.223.192" not in staging_job
    assert "sslip.io" not in staging_job


def test_deploy_script_contains_no_retired_fallbacks_in_source() -> None:
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "pantheon-lupin-staging-control" not in script_text
    assert "pantheon-lupin-staging-exec" not in script_text
    assert "10.50.0.21" not in script_text
    assert "pantheon-lupin-staging-fe.104.155.223.192.sslip.io" not in script_text
    assert "pantheon-lupin-dev-bff.35.201.204.12.sslip.io" not in script_text
    assert "pantheon-lupin-dev-fe.35.201.204.12.sslip.io" not in script_text


def test_deploy_script_staging_live_rejects_missing_target_identity() -> None:
    proc = subprocess.run(
        [str(DEPLOY_SCRIPT), "--environment", "staging-live", "--sha", DUMMY_SHA, "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert "staging-live deployment requires --project-id or PROJECT_ID to be set" in proc.stderr


def test_deploy_script_staging_live_rejects_missing_remote_user() -> None:
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "staging-live",
            "--project-id",
            "neutral-project",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert "staging-live deployment requires REMOTE_USER to be set" in proc.stderr


@pytest.mark.parametrize(
    "missing_var",
    [
        "STAGING_CONTROL_VM",
        "STAGING_CONTROL_ZONE",
        "STAGING_CONTROL_REMOTE_DIR",
        "STAGING_EXEC_VM",
        "STAGING_EXEC_ZONE",
        "STAGING_EXEC_REMOTE_DIR",
        "STAGING_EXEC_HEALTH_URL",
        "STAGING_BFF_CORS_ORIGINS",
    ],
)
def test_deploy_script_staging_live_rejects_missing_required_variable(missing_var: str) -> None:
    env = dict(VALID_NEUTRAL_STAGING_ENV)
    del env[missing_var]
    if missing_var == "STAGING_BFF_CORS_ORIGINS":
        env.pop("STAGING_BFF_CANONICAL_CORS_ORIGIN", None)
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "staging-live",
            "--component",
            "all",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(env),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert f"staging-live deployment requires {missing_var} to be set; refusing to deploy with missing target identity" in proc.stderr


@pytest.mark.parametrize(
    "retired_project",
    [
        "pantheon-benjamin-20260528",
        "pantheon-lupin-dev-20260719",
    ],
)
def test_deploy_script_rejects_retired_project(retired_project: str) -> None:
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--project-id",
            retired_project,
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert f"GCP project {retired_project} is retired; refusing to deploy" in proc.stderr


@pytest.mark.parametrize(
    ("var_name", "retired_value"),
    [
        ("DEV_BFF_PUBLIC_HOST", "pantheon-lupin-dev-bff.35.201.204.12.sslip.io"),
        ("DEV_DEPLOY_SSH_HOST", "35.201.204.12"),
        ("STAGING_CONTROL_VM", "pantheon-benjamin-20260528-control"),
        ("STAGING_BFF_CORS_ORIGINS", "https://pantheon-lupin-staging-fe.104.155.223.192.sslip.io"),
    ],
)
def test_deploy_script_rejects_retired_target_identity(var_name: str, retired_value: str) -> None:
    env = dict(VALID_NEUTRAL_STAGING_ENV)
    env[var_name] = retired_value
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "staging-live",
            "--component",
            "all",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(env),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert f"{var_name} contains retired target identity" in proc.stderr


def test_deploy_script_staging_live_accepts_valid_neutral_fixtures() -> None:
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "staging-live",
            "--component",
            "all",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(VALID_NEUTRAL_STAGING_ENV),
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"Staging dry run failed: {proc.stderr}"
    assert "environment=staging-live" in proc.stdout
    assert "component=all" in proc.stdout
    assert "staging_exec_health_url=http://10.0.0.1:28081" in proc.stdout
    assert "staging_bff_cors_origins=https://neutral-staging-fe.example.com" in proc.stdout

    for retired in [
        "sslip.io",
        "104.155.223.192",
        "35.201.204.12",
        "pantheon-benjamin-20260528",
        "pantheon-lupin-dev-20260719",
    ]:
        assert retired not in proc.stdout
        assert retired not in proc.stderr


def test_deploy_script_dev_dry_run_accepts_default_dev_identity() -> None:
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(),
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"Dev dry run failed: {proc.stderr}"
    assert "project=pantheon-dev-20260902" in proc.stdout
    assert "environment=dev" in proc.stdout
    assert "component=root" in proc.stdout
    assert "dev_bff_public_host=api.dev.mvl-cap.tw" in proc.stdout
    assert "dev_fe_public_host=app.dev.mvl-cap.tw" in proc.stdout

    for retired in [
        "sslip.io",
        "104.155.223.192",
        "35.201.204.12",
        "pantheon-benjamin-20260528",
        "pantheon-lupin-dev-20260719",
    ]:
        assert retired not in proc.stdout
        assert retired not in proc.stderr


def test_deploy_script_dev_rejects_composed_explicit_empty_variables() -> None:
    empty_env = {
        "PROJECT_ID": "",
        "REMOTE_USER": "",
        "DEV_VM": "",
        "DEV_ZONE": "",
        "DEV_REMOTE_DIR": "",
        "DEV_DEPLOY_SSH_HOST": "",
        "DEV_BFF_PUBLIC_HOST": "",
        "DEV_FE_PUBLIC_HOST": "",
        "DEV_FE_STATIC_ROOT": "",
        "DEV_BFF_CORS_ORIGINS": "",
    }
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(empty_env),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert "dev deployment requires --project-id or PROJECT_ID to be set" in proc.stderr


@pytest.mark.parametrize(
    ("empty_var", "expected_err"),
    [
        ("PROJECT_ID", "dev deployment requires --project-id or PROJECT_ID to be set"),
        ("REMOTE_USER", "dev deployment requires REMOTE_USER to be set"),
        ("DEV_VM", "dev deployment requires DEV_VM to be set; refusing to deploy with missing target identity"),
        ("DEV_ZONE", "dev deployment requires DEV_ZONE to be set; refusing to deploy with missing target identity"),
        ("DEV_REMOTE_DIR", "dev deployment requires DEV_REMOTE_DIR to be set; refusing to deploy with missing target identity"),
        ("DEV_DEPLOY_SSH_HOST", "dev deployment requires DEV_DEPLOY_SSH_HOST to be set; refusing to deploy with missing target identity"),
        ("DEV_BFF_PUBLIC_HOST", "dev deployment requires DEV_BFF_PUBLIC_HOST to be set; refusing to deploy with missing target identity"),
        ("DEV_FE_PUBLIC_HOST", "dev deployment requires DEV_FE_PUBLIC_HOST to be set; refusing to deploy with missing target identity"),
        ("DEV_FE_STATIC_ROOT", "dev deployment requires DEV_FE_STATIC_ROOT to be set; refusing to deploy with missing target identity"),
        ("DEV_BFF_CORS_ORIGINS", "dev deployment requires DEV_BFF_CORS_ORIGINS to be set; refusing to deploy with missing target identity"),
    ],
)
def test_deploy_script_dev_rejects_individual_explicit_empty_variable(empty_var: str, expected_err: str) -> None:
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env({empty_var: ""}),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert expected_err in proc.stderr


def test_deploy_script_dev_rejects_empty_cli_project_id() -> None:
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--project-id",
            "",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert "dev deployment requires --project-id or PROJECT_ID to be set" in proc.stderr
