from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
NONPROD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nonprod-deploy.yml"
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_nonprod_vm.sh"
GCP_BASELINE_SCRIPT = REPO_ROOT / "scripts" / "gcp_nonprod_baseline.sh"

DEV_PROJECT_ID = "pantheon-lupin-dev-20260719"
DEV_PROJECT_NUMBER = "317269804408"
DEV_PUBLIC_IP = "35.201.204.12"
DEV_REMOTE_DIR = "/home/lupin/pantheon"

RETIRED_PROJECT_ID = "pantheon-benjamin-20260528"
RETIRED_PUBLIC_IP = "35.201.239.38"
RETIRED_REMOTE_DIR = "/home/lupin/code/pantheon"


def _workflow_jobs() -> tuple[str, str]:
    workflow = NONPROD_WORKFLOW.read_text(encoding="utf-8")
    dev_job, staging_job = workflow.split("  deploy-staging-live:", maxsplit=1)
    return dev_job, staging_job


def test_dev_workflow_targets_replacement_project_only() -> None:
    dev_job, staging_job = _workflow_jobs()

    assert "vars.DEV_GCP_WIF_PROVIDER" in dev_job
    assert "vars.DEV_GCP_DEPLOY_SERVICE_ACCOUNT" in dev_job
    assert "vars.DEV_GCP_DEPLOY_PROJECT_ID" in dev_job
    assert DEV_PROJECT_ID in dev_job
    assert DEV_PROJECT_NUMBER in dev_job
    assert DEV_PUBLIC_IP in dev_job
    assert DEV_REMOTE_DIR in dev_job

    assert RETIRED_PROJECT_ID not in dev_job
    assert RETIRED_PUBLIC_IP not in dev_job
    assert RETIRED_REMOTE_DIR not in dev_job

    # The staging-live job remains independently configured during the dev cutover.
    assert "vars.GCP_WIF_PROVIDER" in staging_job
    assert "vars.GCP_DEPLOY_PROJECT_ID" in staging_job
    assert RETIRED_PROJECT_ID in staging_job


def test_deploy_script_defaults_to_replacement_dev_vm() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    dev_defaults = script.split('STAGING_CONTROL_VM="', maxsplit=1)[0]

    assert f'PROJECT_ID="${{PROJECT_ID:-{DEV_PROJECT_ID}}}"' in dev_defaults
    assert f'DEV_REMOTE_DIR="${{DEV_REMOTE_DIR:-{DEV_REMOTE_DIR}}}"' in dev_defaults
    assert DEV_PUBLIC_IP in dev_defaults

    assert RETIRED_PROJECT_ID not in dev_defaults
    assert RETIRED_PUBLIC_IP not in dev_defaults
    assert RETIRED_REMOTE_DIR not in dev_defaults


def test_gcp_baseline_grants_deploy_sa_compute_access() -> None:
    script = GCP_BASELINE_SCRIPT.read_text(encoding="utf-8")
    role_block = script.split('info "Step 5/6:', maxsplit=1)[0]

    assert (
        'ensure_project_role "serviceAccount:${CLOUD_BUILD_SA}" '
        '"roles/compute.instanceAdmin.v1"'
    ) in role_block
    assert '--member="serviceAccount:${CLOUD_BUILD_SA}"' in role_block
    assert '--role="roles/iam.serviceAccountUser"' in role_block
