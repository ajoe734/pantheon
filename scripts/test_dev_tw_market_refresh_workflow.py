from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "dev-tw-market-refresh.yml"


def _load_workflow() -> dict[str, Any]:
    assert WORKFLOW_PATH.exists(), f"Workflow file not found: {WORKFLOW_PATH}"
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_workflow_declares_environment_dev() -> None:
    wf = _load_workflow()
    jobs = wf.get("jobs", {})
    refresh_job = jobs.get("refresh", {})
    assert refresh_job.get("environment") == "dev", (
        "Refresh job must declare environment: dev so it accesses dev secrets and variables"
    )


def test_workflow_sources_ssh_inputs_strictly() -> None:
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    wf = _load_workflow()
    refresh_job = wf.get("jobs", {}).get("refresh", {})
    refresh_steps = refresh_job.get("steps", [])

    job_env = refresh_job.get("env", {})
    assert job_env.get("DEV_DEPLOY_SSH_HOST") == "${{ vars.DEV_DEPLOY_SSH_HOST }}", (
        "DEV_DEPLOY_SSH_HOST must be declared at job level env from vars.DEV_DEPLOY_SSH_HOST"
    )
    assert job_env.get("DEV_DEPLOY_SSH_USER") == "${{ vars.NONPROD_REMOTE_USER }}", (
        "DEV_DEPLOY_SSH_USER must be declared at job level env from vars.NONPROD_REMOTE_USER"
    )

    # Check the step environment declarations
    env_vars: dict[str, str] = dict(job_env)
    for step in refresh_steps:
        if isinstance(step, dict) and "env" in step:
            env_vars.update(step["env"])

    # 1. DEV_DEPLOY_SSH_PRIVATE_KEY must come from secrets
    assert env_vars.get("DEV_DEPLOY_SSH_PRIVATE_KEY") == "${{ secrets.DEV_DEPLOY_SSH_PRIVATE_KEY }}", (
        "DEV_DEPLOY_SSH_PRIVATE_KEY must be read from environment secret secrets.DEV_DEPLOY_SSH_PRIVATE_KEY"
    )

    # 2. DEV_DEPLOY_SSH_KNOWN_HOSTS must come from vars (not secrets)
    assert env_vars.get("DEV_DEPLOY_SSH_KNOWN_HOSTS") == "${{ vars.DEV_DEPLOY_SSH_KNOWN_HOSTS }}", (
        "DEV_DEPLOY_SSH_KNOWN_HOSTS must be read from environment variable vars.DEV_DEPLOY_SSH_KNOWN_HOSTS"
    )

    # 3. DEV_DEPLOY_SSH_HOST must come from vars
    assert env_vars.get("DEV_DEPLOY_SSH_HOST") == "${{ vars.DEV_DEPLOY_SSH_HOST }}", (
        "DEV_DEPLOY_SSH_HOST must be read from environment variable vars.DEV_DEPLOY_SSH_HOST"
    )

    # 4. DEV_DEPLOY_SSH_USER must come from vars.NONPROD_REMOTE_USER
    assert env_vars.get("DEV_DEPLOY_SSH_USER") == "${{ vars.NONPROD_REMOTE_USER }}", (
        "DEV_DEPLOY_SSH_USER must be read from environment variable vars.NONPROD_REMOTE_USER"
    )

    # 5. PANTHEON_DEPLOY_WORKTREE_ROOT must come from vars.DEV_DEPLOY_WORKTREE_ROOT
    assert env_vars.get("PANTHEON_DEPLOY_WORKTREE_ROOT") == "${{ vars.DEV_DEPLOY_WORKTREE_ROOT }}", (
        "PANTHEON_DEPLOY_WORKTREE_ROOT must be read from environment variable vars.DEV_DEPLOY_WORKTREE_ROOT"
    )


def test_workflow_does_not_execute_in_dev_remote_dir() -> None:
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    # Must NOT cd into DEV_REMOTE_DIR
    assert "cd '${DEV_REMOTE_DIR}'" not in content, (
        "Workflow must not cd into DEV_REMOTE_DIR on the VM; that directory is stale"
    )
    assert 'cd "${DEV_REMOTE_DIR}"' not in content
    assert "cd $DEV_REMOTE_DIR" not in content

    # The runner must invoke deploy_nonprod_vm.sh directly from runner checkout
    assert "./scripts/deploy_nonprod_vm.sh --refresh-only" in content, (
        "Runner must invoke ./scripts/deploy_nonprod_vm.sh --refresh-only directly from its own checkout"
    )


def test_workflow_runs_under_dev_environment_lease() -> None:
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert re.search(r"dev_environment_lease\.py[\"']?\s+acquire", content), (
        "Workflow must acquire the dev environment lease via dev_environment_lease.py acquire"
    )
    assert "heartbeat-loop" in content, (
        "Workflow must run heartbeat-loop for the dev environment lease"
    )
    assert "run_with_dev_environment_lease.sh" in content, (
        "Execution step must be wrapped by run_with_dev_environment_lease.sh"
    )
    assert re.search(r"dev_environment_lease\.py[\"']?\s+release", content), (
        "Workflow must release the lease on completion"
    )


def test_workflow_uploads_outcome_artifact() -> None:
    wf = _load_workflow()
    refresh_steps = wf.get("jobs", {}).get("refresh", {}).get("steps", [])
    upload_step = next(
        (s for s in refresh_steps if isinstance(s, dict) and "upload-artifact" in s.get("uses", "")),
        None,
    )
    assert upload_step is not None, "Workflow must upload the refresh outcome JSON as an artifact"
    with_opts = upload_step.get("with", {})
    assert "tw-refresh-outcome.json" in with_opts.get("path", "")


def test_workflow_schedule_multiple_times_per_trading_day() -> None:
    wf = _load_workflow()
    on = wf.get("on") or wf.get(True)
    schedule = on.get("schedule", [])
    cron_exprs = [item.get("cron") for item in schedule if isinstance(item, dict)]
    assert len(cron_exprs) > 1, f"Workflow must be scheduled more than once per trading day, got: {cron_exprs}"
    assert "0 7 * * 1-5" in cron_exprs, f"Expected primary cron '0 7 * * 1-5' in {cron_exprs}"
    for cron in cron_exprs:
        assert cron.endswith("1-5"), f"Schedule must target trading days (1-5): {cron}"


DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_nonprod_vm.sh"
REFRESH_CALL = 'execute_bounded_source_refresh_entrypoint "${FORCE_REFRESH:-false}" "${REFRESH_OUTPUT_PATH:-}"'


def _run_refresh_payload(tmp_path: Path, remote_dir: Path | None) -> subprocess.CompletedProcess[str]:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    payload = script.split("run_remote_payload <<'REMOTE'\n", 1)[1].split("\nREMOTE\n", 1)[0]
    assert REFRESH_CALL in payload
    payload = payload.replace(REFRESH_CALL, 'echo "REFRESH_ENTRYPOINT cwd=$(pwd -P) force=${FORCE_REFRESH}"')
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "PANTHEON_DEPLOY_ENV": "dev",
        "PANTHEON_DEPLOY_COMPONENT": "refresh-only",
        "FORCE_REFRESH": "false",
        "REFRESH_OUTPUT_PATH": "",
    }
    if remote_dir is not None:
        env["PANTHEON_REMOTE_DIR"] = str(remote_dir)
    return subprocess.run(
        ["bash", "-s"], input=payload, env=env, cwd=home, text=True, capture_output=True, check=False
    )


def test_refresh_only_payload_reaches_entrypoint_in_managed_worktree(tmp_path: Path) -> None:
    worktree = tmp_path / "managed" / "dev-root"
    worktree.mkdir(parents=True)
    (worktree / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(worktree)], check=True)
    result = _run_refresh_payload(tmp_path, worktree)
    assert result.returncode == 0, result.stderr
    assert f"REFRESH_ENTRYPOINT cwd={worktree.resolve()} force=false" in result.stdout


def test_refresh_only_payload_fails_closed_without_managed_worktree(tmp_path: Path) -> None:
    for remote_dir in (None, tmp_path / "missing" / "dev-root"):
        result = _run_refresh_payload(tmp_path, remote_dir)
        assert result.returncode != 0
        assert "no managed deploy worktree containing docker-compose.yml" in result.stdout + result.stderr
        assert "REFRESH_ENTRYPOINT" not in result.stdout
