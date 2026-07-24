from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_nonprod_vm.sh"
EVIDENCE_SCRIPT = ROOT / "scripts" / "ppl_alloc_009_deployment_evidence.py"
TARGET_SHA = "a" * 40


def _run_deploy(
    *,
    environment: str,
    component: str,
    enabled: str,
    auth_mode: str = "strict",
    auth_stub: str = "false",
    allow_dirty: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED": enabled,
        "DEV_BFF_AUTH_MODE": auth_mode,
        "DEV_BFF_AUTH_STUB": auth_stub,
    }
    command = [
        "bash",
        str(DEPLOY_SCRIPT),
        "--environment",
        environment,
        "--component",
        component,
        "--sha",
        TARGET_SHA,
        "--project-id",
        "test-project",
        "--dry-run",
    ]
    if allow_dirty:
        command.append("--allow-dirty")
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_workflow_input_defaults_false_and_is_shape_locked() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    input_start = workflow.index("      ppl_alloc_009_dev_proof_enabled:")
    input_end = workflow.index("      run_evolution_dispatch_probe:", input_start)
    input_block = workflow[input_start:input_end]
    assert "default: false" in input_block
    assert "type: boolean" in input_block

    dev_start = workflow.index("  deploy-dev:")
    staging_start = workflow.index("  deploy-staging-live:")
    dev = workflow[dev_start:staging_start]
    staging = workflow[staging_start:]
    for marker in (
        '"${TARGET_COMPONENT}" != "root"',
        '"${DEV_AUTH_PROFILE}" != "strict"',
        '"${ALLOW_DIRTY}" != "false"',
        '"${RUN_EVOLUTION_DISPATCH_PROBE}" != "false"',
        '"${RUN_LOOP_PROD_TEL_002_PROBE}" != "false"',
        "DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED:",
        "assert_ppl_alloc_009_dev_proof_gate",
        "ppl_alloc_009_deployment_evidence.py",
        "docker inspect pantheon-operator-bff-1",
        "Upload dev deployment posture evidence",
    ):
        assert marker in dev
    assert (
        '[[ "${PPL_ALLOC_009_DEV_PROOF_ENABLED}" != "false" ]]'
        in staging
    )
    assert "PPL-ALLOC-009 dev proof cannot be enabled for staging-live" in staging


def test_deploy_script_defaults_false_and_passes_exact_compose_env() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert (
        'DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED="${'
        'DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED:-false}"'
    ) in script
    assert script.count(
        'PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED="'
        '${PANTHEON_DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED}"'
    ) >= 3
    assert script.count("assert_ppl_alloc_009_dev_proof_gate") >= 3

    result = _run_deploy(
        environment="dev",
        component="root",
        enabled="false",
    )
    assert result.returncode == 0, result.stderr
    assert "dev_ppl_alloc_009_dev_proof_enabled=false" in result.stdout


def test_deploy_script_allows_only_clean_strict_dev_root_when_enabled() -> None:
    accepted = _run_deploy(
        environment="dev",
        component="root",
        enabled="true",
    )
    assert accepted.returncode == 0, accepted.stderr
    assert "dev_ppl_alloc_009_dev_proof_enabled=true" in accepted.stdout

    rejected = [
        _run_deploy(
            environment="dev",
            component="bff",
            enabled="true",
        ),
        _run_deploy(
            environment="dev",
            component="root",
            enabled="true",
            auth_mode="permissive",
            auth_stub="true",
        ),
        _run_deploy(
            environment="dev",
            component="root",
            enabled="true",
            allow_dirty=True,
        ),
        _run_deploy(
            environment="staging-live",
            component="all",
            enabled="true",
        ),
    ]
    for result in rejected:
        assert result.returncode != 0
        assert "requires a clean dev/root deploy" in result.stderr


def _run_evidence(
    tmp_path: Path,
    *,
    expected: str,
    effective_line: str,
    component: str = "root",
    auth_profile: str = "strict",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(EVIDENCE_SCRIPT),
            "--output",
            str(tmp_path / "evidence.json"),
            "--target-sha",
            TARGET_SHA,
            "--component",
            component,
            "--auth-profile",
            auth_profile,
            "--expected-enabled",
            expected,
            "--effective-env-line",
            effective_line,
            "--workflow-run-id",
            "123",
            "--workflow-run-attempt",
            "1",
            "--workflow-run-url",
            "https://github.example/runs/123",
            "--bff-url",
            "https://dev-bff.example",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_evidence_writer_records_only_verified_secret_free_posture(
    tmp_path: Path,
) -> None:
    result = _run_evidence(
        tmp_path,
        expected="true",
        effective_line="PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED=true",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))
    assert payload["backendSourceCommitSha"] == TARGET_SHA
    assert payload["component"] == "root"
    assert payload["authProfile"] == "strict"
    assert payload["featureFlags"] == {
        "PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED": True
    }
    assert payload["observation"]["source"] == (
        "docker_inspect_container_environment"
    )
    assert "secret" not in json.dumps(payload).lower()


@pytest.mark.parametrize(
    ("expected", "effective_line", "component", "auth_profile"),
    [
        (
            "true",
            "PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED=false",
            "root",
            "strict",
        ),
        (
            "true",
            "PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED=true",
            "bff",
            "strict",
        ),
        (
            "true",
            "PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED=true",
            "root",
            "permissive-stub",
        ),
    ],
)
def test_evidence_writer_rejects_mismatch_or_unsafe_enabled_shape(
    tmp_path: Path,
    expected: str,
    effective_line: str,
    component: str,
    auth_profile: str,
) -> None:
    result = _run_evidence(
        tmp_path,
        expected=expected,
        effective_line=effective_line,
        component=component,
        auth_profile=auth_profile,
    )
    assert result.returncode != 0
    assert not (tmp_path / "evidence.json").exists()
