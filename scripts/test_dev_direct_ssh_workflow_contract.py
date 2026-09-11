from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEV_WORKFLOWS = (
    ROOT / ".github/workflows/nonprod-deploy.yml",
    ROOT / ".github/workflows/stage-0-ci.yml",
    ROOT / ".github/workflows/tj-e2e-012-hosted-acceptance.yml",
    ROOT / ".github/workflows/agora-hosted-acceptance.yml",
)


def test_nonprod_workflow_has_no_retired_account_or_path_fallback():
    workflow = (ROOT / ".github/workflows/nonprod-deploy.yml").read_text()
    assert "'lupin'" not in workflow
    assert "/home/lupin/" not in workflow
    assert "REMOTE_USER: ${{ vars.NONPROD_REMOTE_USER }}" in workflow
    assert "DEV_REMOTE_DIR: ${{ vars.DEV_REMOTE_DIR }}" in workflow


def test_dev_workflows_have_one_direct_ssh_transport_and_no_metadata_ssh() -> None:
    for workflow in DEV_WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        assert "gcloud compute ssh" not in text, workflow
        assert "gcloud compute scp" not in text, workflow
        assert "DEV_DEPLOY_SSH_PRIVATE_KEY" in text, workflow
        assert "DEV_DEPLOY_SSH_KNOWN_HOSTS" in text, workflow
        assert "dev_vm_ssh.sh" in text, workflow


def test_deploy_script_uses_direct_dev_and_retains_staging_transport() -> None:
    deploy = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    assert 'if [[ "$DEPLOY_ENV" == "dev" ]]' in deploy
    assert 'remote_command=("$SCRIPT_DIR/dev_vm_ssh.sh" exec "$command_prefix")' in deploy
    assert 'gcloud compute ssh "${REMOTE_USER}@${vm}"' in deploy
    assert 'require_cmd ssh' in deploy
    assert 'require_cmd gcloud' in deploy


def test_dev_baseline_does_not_regrant_metadata_mutation_role() -> None:
    baseline = (ROOT / "scripts/gcp_nonprod_baseline.sh").read_text(encoding="utf-8")
    assert 'if [[ "${ENV_NAME}" != "dev" ]]' in baseline
    assert "Dev deployment uses its pinned direct-SSH transport" in baseline
