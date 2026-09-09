"""Exercise actual deployment wrapper commands with an isolated SSH recorder."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts/run_dev_paper_baseline_with_diagnostics.sh"


def invoke(tmp_path, run_id="123456", attempt="1"):
    workspace = tmp_path / "workspace"
    scripts = workspace / ".agora-gate-controller/scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    recorder = scripts / "dev_vm_ssh.sh"
    recorder.write_text('#!/bin/sh\nprintf "%s\\n" "$2" > "$RECORDED_COMMAND"\n')
    recorder.chmod(0o755)
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir(exist_ok=True)
    command = tmp_path / "command"
    env = {key: value for key, value in os.environ.items() if not key.startswith("DEV_PAPER_")}
    env.update(
        GITHUB_WORKSPACE=str(workspace),
        DEV_PAPER_DIAGNOSTICS_DIR=str(diagnostics),
        DEV_PAPER_DIAGNOSTICS_COLLECTOR=str(ROOT / "scripts/collect_dev_paper_diagnostics.py"),
        EXPECTED_BFF_SHA="a" * 40,
        EXPECTED_FE_SHA="b" * 40,
        RECORDED_COMMAND=str(command),
    )
    if run_id is not None:
        env["DEV_PAPER_RUN_ID"] = run_id
    if attempt is not None:
        env["DEV_PAPER_ATTEMPT"] = attempt
    result = subprocess.run(["bash", str(WRAPPER)], env=env, capture_output=True, text=True, timeout=10)
    return result, command, diagnostics


def stimulus(command):
    argv = shlex.split(command.read_text())
    return argv[argv.index("--name") + 1], argv[argv.index("--idempotency-key") + 1]


def test_same_attempt_is_stable_and_each_new_attempt_has_both_new_identities(tmp_path):
    identities = []
    for index, (run_id, attempt) in enumerate((("123456", "1"), ("123456", "1"), ("123456", "2"), ("123457", "1"))):
        result, command, diagnostics = invoke(tmp_path / str(index), run_id, attempt)
        assert result.returncode == 0, result.stderr
        pair = stimulus(command)
        assert pair == (f"Pantheon Dev Paper Release {run_id} Attempt {attempt}", f"dev-paper-release-{run_id}-{attempt}")
        assert all(value in result.stdout for value in pair)
        assert json.loads((diagnostics / "collection-status.json").read_text())["collectionStatus"] == "not_required"
        assert not (diagnostics / "diagnostics.json").exists()
        identities.append(pair)
    assert identities[0] == identities[1]
    assert len({name for name, _ in identities}) == 3
    assert len({key for _, key in identities}) == 3


@pytest.mark.parametrize("run_id,attempt", [
    (None, "1"), ("1", None), ("", "1"), ("1", ""),
    ("1;touch pwn", "1"), ("1", "$(id)"), ("1\n2", "1"),
    ("1" * 21, "1"), ("1", "1" * 11),
])
def test_invalid_or_partial_identity_is_rejected_before_ssh(tmp_path, run_id, attempt):
    result, command, _ = invoke(tmp_path, run_id, attempt)
    assert result.returncode == 75
    assert not command.exists()


def test_identityless_manual_invocation_retains_existing_cli_defaults(tmp_path):
    result, command, _ = invoke(tmp_path, None, None)
    assert result.returncode == 0, result.stderr
    assert "--name" not in command.read_text()
    assert "--idempotency-key" not in command.read_text()


def test_official_workflow_always_supplies_run_and_attempt():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/nonprod-deploy.yml").read_text())
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    step = next(step for step in steps if step.get("id") == "paper_bootstrap")
    assert step["env"]["DEV_PAPER_RUN_ID"] == "${{ github.run_id }}"
    assert step["env"]["DEV_PAPER_ATTEMPT"] == "${{ github.run_attempt }}"
    assert "run_dev_paper_baseline_with_diagnostics.sh" in step["run"]
