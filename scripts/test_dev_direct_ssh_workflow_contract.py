from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEV_WORKFLOWS = (
    ROOT / ".github/workflows/nonprod-deploy.yml",
    ROOT / ".github/workflows/stage-0-ci.yml",
    ROOT / ".github/workflows/tj-e2e-012-hosted-acceptance.yml",
    ROOT / ".github/workflows/agora-hosted-acceptance.yml",
)


def test_all_dev_workflows_have_no_retired_host_account_or_path_fallback():
    for path in DEV_WORKFLOWS:
        workflow = path.read_text()
        for retired in ("'lupin'", "/home/lupin/", "35.201.204.12", "35.201.239.38", "34.81.75.241"):
            assert retired not in workflow, (path, retired)
        assert "DEV_DEPLOY_SSH_USER: ${{ vars.NONPROD_REMOTE_USER }}" in workflow
    workflow = DEV_WORKFLOWS[0].read_text()
    assert "REMOTE_USER: ${{ vars.NONPROD_REMOTE_USER }}" in workflow
    assert "DEV_REMOTE_DIR: ${{ vars.DEV_REMOTE_DIR }}" in workflow


def test_hosted_dispatch_origins_default_to_current_repository_variables():
    for path in DEV_WORKFLOWS[1:]:
        workflow = yaml.safe_load(path.read_text())
        inputs = workflow.get("on", workflow.get(True))["workflow_dispatch"]["inputs"]
        for key in ("bff_base_url", "fe_base_url", "fe_deployment_url"):
            if key in inputs:
                assert inputs[key].get("required") is False
                assert not inputs[key].get("default")
        text = path.read_text()
        assert "inputs.bff_base_url || " in text and "vars.DEV_BFF_URL" in text
        assert "DEV_DEPLOY_SSH_HOST: ${{ vars.DEV_DEPLOY_SSH_HOST }}" in text
    stage0 = DEV_WORKFLOWS[1].read_text()
    assert stage0.count("inputs.bff_base_url || (inputs.environment == 'dev' && vars.DEV_BFF_URL) || ''") == 3


def test_agora_origin_preflight_has_configured_values_and_rejects_empty_pair():
    workflow = yaml.safe_load(DEV_WORKFLOWS[3].read_text())
    job = workflow["jobs"]["hosted-service-proof"]
    assert job["env"]["CANONICAL_DEV_BFF_URL"] == "${{ vars.DEV_BFF_URL }}"
    assert job["env"]["CANONICAL_DEV_FE_URL"] == "${{ vars.DEV_FE_URL }}"
    preflight = next(step for step in job["steps"] if step["name"] == "Require the dispatched revision and safe dev origins")
    assert '[[ -n "${CANONICAL_DEV_BFF_URL}" && -n "${CANONICAL_DEV_FE_URL}" ]]' in preflight["run"]
    environment = {
        "GITHUB_REF": "refs/heads/dev", "EXPECTED_BFF_SHA": "a" * 40, "EXPECTED_FE_SHA": "b" * 40,
        "BFF_BASE_URL": "https://bff.example.test", "FE_BASE_URL": "https://fe.example.test",
        "CANONICAL_DEV_BFF_URL": "https://bff.example.test", "CANONICAL_DEV_FE_URL": "https://fe.example.test",
        "AGORA_HOSTED_OPERATOR_CLIENT_SECRET": "fixture", "AGORA_HOSTED_PEER_CLIENT_SECRET": "fixture",
        "AGORA_HOSTED_VIEWER_CLIENT_SECRET": "fixture",
    }
    # Execute only this existing local validation step; git is a harmless
    # function and no SSH, HTTP, lease or product operation is invoked.
    command = 'git() { printf "%s\\n" "$EXPECTED_BFF_SHA"; }\n' + preflight["run"]
    for overrides, expected in (({}, 0), ({"CANONICAL_DEV_BFF_URL": "", "BFF_BASE_URL": ""}, 1),
                                ({"CANONICAL_DEV_FE_URL": "", "FE_BASE_URL": ""}, 1),
                                ({"BFF_BASE_URL": "https://other.example.test"}, 1)):
        result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", command],
                                env={**environment, **overrides}, capture_output=True, text=True)
        assert result.returncode == expected, result.stderr


def test_all_dev_workflow_shell_fragments_parse_without_external_execution():
    for path in DEV_WORKFLOWS:
        workflow = yaml.safe_load(path.read_text())
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if "run" not in step or step.get("shell", "").startswith("python"):
                    continue
                body = re.sub(r"\$\{\{.*?\}\}", "fixture-value", step["run"])
                result = subprocess.run(["bash", "-n"], input=body, text=True, capture_output=True)
                assert result.returncode == 0, (path, step.get("name"), result.stderr)


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
