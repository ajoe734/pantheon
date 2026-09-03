from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"


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

    assert 'Repeated bootstrap is rejected: host already has a deployment.json manifest.' in baseline_step
    assert 'curl --fail-with-body --silent --show-error \\\n              --connect-timeout 10 --max-time 30 \\\n              "${DEV_FE_URL%/}/deployment.json" >/dev/null 2>&1' in baseline_step


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
    assert "python3 scripts/agora_compat_manifest.py write" in baseline_step
    assert "python3 scripts/agora_compat_manifest.py deployment-gate" in baseline_step
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
