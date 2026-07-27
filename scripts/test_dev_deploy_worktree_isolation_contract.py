from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_nonprod_vm.sh"
NONPROD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nonprod-deploy.yml"
HOSTED_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "tj-e2e-012-hosted-acceptance.yml"
)
STAGE_ZERO_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "stage-0-ci.yml"
DEPLOYMENT_PROBE = REPO_ROOT / "scripts" / "run_loop_prod_dep_001_hosted.py"

COMMAND_ROOT = "/home/lupin/pantheon-ci-deploy/dev-root"
DEPLOY_ROOT = "/home/lupin/pantheon-ci-deploy/managed-deploy-worktrees"
DEV_ROOT_WORKTREE = f"{DEPLOY_ROOT}/dev-root"
CONTRACT_VERSION = "dev-root-isolation-v1"


def deploy_script() -> str:
    return DEPLOY_SCRIPT.read_text(encoding="utf-8")


def isolation_guard_source() -> str:
    marked = deploy_script().split(
        "# BEGIN_DEV_DEPLOY_PATH_ISOLATION_PY",
        1,
    )[1].split(
        "# END_DEV_DEPLOY_PATH_ISOLATION_PY",
        1,
    )[0]
    return marked.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def run_isolation_guard(
    deploy_root: Path,
    deploy_dir: Path,
    command_root: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-",
            str(deploy_root),
            str(deploy_dir),
            str(command_root),
        ],
        input=isolation_guard_source(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_deploy_script_keeps_dev_isolated_and_staging_layout_stable() -> None:
    script = deploy_script()

    assert (
        f'PANTHEON_DEPLOY_CONTROLLER_CONTRACT_VERSION="{CONTRACT_VERSION}"'
        in script
    )
    assert 'root="${HOME}/pantheon-ci-deploy/managed-deploy-worktrees"' in script
    assert 'root="${HOME}/pantheon-ci-deploy"' in script
    assert '[[ "${PANTHEON_DEPLOY_ENV}" == "dev" ]]' in script
    assert "BEGIN_DEV_DEPLOY_PATH_ISOLATION_PY" in script


def test_path_guard_accepts_disjoint_dev_roots(tmp_path: Path) -> None:
    command_root = tmp_path / "command-root"
    command_root.mkdir()
    deploy_root = tmp_path / "managed"

    result = run_isolation_guard(
        deploy_root,
        deploy_root / "dev-root",
        command_root,
    )

    assert result.returncode == 0, result.stderr


def test_path_guard_rejects_equal_and_nested_roots(tmp_path: Path) -> None:
    exact_parent = tmp_path / "exact"
    exact_command = exact_parent / "dev-root"
    exact_command.mkdir(parents=True)
    nested_command = tmp_path / "nested-command"
    nested_command.mkdir()
    reverse_root = tmp_path / "reverse"
    reverse_command = reverse_root / "dev-root" / "command"
    reverse_command.mkdir(parents=True)

    nested_deploy_root = nested_command / "managed"
    cases = (
        (exact_parent, exact_parent / "dev-root", exact_command),
        (
            nested_deploy_root,
            nested_deploy_root / "dev-root",
            nested_command,
        ),
        (reverse_root, reverse_root / "dev-root", reverse_command),
    )
    for deploy_root, deploy_dir, command_root in cases:
        result = run_isolation_guard(deploy_root, deploy_dir, command_root)
        assert result.returncode != 0
        assert "must be disjoint" in result.stderr
    assert not nested_deploy_root.exists()


def test_path_guard_rejects_symlink_alias(tmp_path: Path) -> None:
    real_deploy_root = tmp_path / "real-deploy"
    real_deploy_root.mkdir()
    deploy_alias = tmp_path / "deploy-alias"
    deploy_alias.symlink_to(real_deploy_root, target_is_directory=True)
    command_root = tmp_path / "command-root"
    command_root.mkdir()

    result = run_isolation_guard(
        deploy_alias,
        deploy_alias / "dev-root",
        command_root,
    )

    assert result.returncode != 0
    assert "symlink component" in result.stderr


def test_nonprod_deploy_uses_protected_controller_and_binds_both_roots() -> None:
    workflow = NONPROD_WORKFLOW.read_text(encoding="utf-8")
    deploy_step = workflow.split(
        "\n      - name: Deploy dev VM stack under lease",
        1,
    )[1].split(
        "\n      - name: Ensure governed dev paper baseline under lease",
        1,
    )[0]

    assert "PANTHEON_DEPLOY_WORKTREE_ROOT:" in deploy_step
    assert "vars.DEV_DEPLOY_WORKTREE_ROOT" in deploy_step
    assert DEPLOY_ROOT in deploy_step
    assert "PANTHEON_DEV_SUPERVISOR_COMMAND_ROOT:" in deploy_step
    assert "vars.DEV_SUPERVISOR_COMMAND_ROOT" in deploy_step
    assert COMMAND_ROOT in deploy_step
    assert (
        'bash "${GITHUB_WORKSPACE}/.agora-gate-controller/'
        'scripts/deploy_nonprod_vm.sh"'
    ) in deploy_step
    assert (
        'bash "${GITHUB_WORKSPACE}/.target/scripts/deploy_nonprod_vm.sh"'
        not in deploy_step
    )

    controller_gate = workflow.split(
        "\n      - name: Generate immutable exact-pair admission before any dev switch",
        1,
    )[1].split(
        "\n      - name: Enforce dev auth deployment floor",
        1,
    )[0]
    assert CONTRACT_VERSION in controller_gate
    assert "grep -Fxq" in controller_gate
    assert "scripts/agora_compat_manifest.py write" in controller_gate
    assert "scripts/agora_compat_manifest.py deployment-gate" in controller_gate
    assert '--frontend-runtime-commit "${{ steps.frontend.outputs.sha }}"' in controller_gate


def test_hosted_probes_follow_the_isolated_dev_root_worktree() -> None:
    workflows = (
        NONPROD_WORKFLOW,
        HOSTED_WORKFLOW,
        STAGE_ZERO_WORKFLOW,
    )

    for workflow in workflows:
        lines = workflow.read_text(encoding="utf-8").splitlines()
        bindings = [line for line in lines if "DEV_DEPLOY_WORKTREE:" in line]
        assert bindings, workflow
        for binding in bindings:
            assert "format('{0}/dev-root'" in binding
            assert "vars.DEV_DEPLOY_WORKTREE_ROOT" in binding
            assert DEPLOY_ROOT in binding
            assert COMMAND_ROOT not in binding

    probe = DEPLOYMENT_PROBE.read_text(encoding="utf-8")
    assert DEV_ROOT_WORKTREE in probe
