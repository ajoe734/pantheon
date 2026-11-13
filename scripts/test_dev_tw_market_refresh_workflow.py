from __future__ import annotations

import json
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


STUB_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "${DOCKER_LOG}"
case "$*" in
  *"ps -q source-ingest"*) echo steady-api ;;
  *"images -q"*) echo "${COMPOSE_IMAGE_ID#sha256:}" ;;
  *"inspect --format {{.Image}}"*) echo "${IMAGE_ID}" ;;
  *"ps --status running -q source-ingest-scheduler") echo "${STEADY_SCHEDULER_ID}" ;;
  *"ps --status running -q source-ingest-agora-projector") echo "${STEADY_PROJECTOR_ID}" ;;
  *"inspect --format {{json .Config.Env}} ${STEADY_SCHEDULER_ID}")
    if [ -n "${NO_DEPLOY_VALUES:-}" ]; then echo '["A=1"]'; else echo "${STEADY_SCHEDULER_ENV}"; fi ;;
  *"inspect --format {{json .Config.Env}} ${STEADY_PROJECTOR_ID}") echo "${STEADY_PROJECTOR_ENV}" ;;
  *"inspect --format {{json .Config.Env}} steady-api") echo '["PANTHEON_EXTERNAL_EGRESS=deny"]' ;;
  *"inspect --format {{.Id}} pantheon-source-ingest-agora-projector:latest") echo "${PROJECTOR_IMAGE_ID}" ;;
  *"inspect --format {{index .Config.Labels \\"org.opencontainers.image.revision\\"}} ${PROJECTOR_IMAGE_ID}") echo "${PROJECTOR_REVISION}" ;;
  "ps -a -q --filter name=^pantheon-bounded-refresh-source-ingest-scheduler"*) echo bounded-scheduler ;;
  "ps -a -q --filter name=^pantheon-bounded-refresh-source-ingest-agora-projector"*) echo bounded-projector ;;
  *"inspect --format {{.State.Status}}"*) echo exited ;;
  *"inspect --format {{.State.ExitCode}} bounded-scheduler") echo 0 ;;
  *"inspect --format {{.State.ExitCode}} bounded-projector") echo "${BOUNDED_EXIT_CODE:-0}" ;;
  *" run -d "*)
    echo "run-env tenant=${PANTHEON_TENANT_ID} env=${PANTHEON_ENV} sha=${GIT_SHA}" >> "${DOCKER_LOG}" ;;
  "logs --tail 200 "*) echo 'bounded fixture log' ;;
  "stop "*|"start "*|"rm -f "*|*" build source-ingest-agora-projector") ;;
  *) echo "unexpected Docker fixture command: $*" >&2; exit 1 ;;
esac
"""


def _run_refresh_entrypoint(tmp_path: Path, symbols: str, exit_code: str, extra_env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    names = (
        "redact_bounded_refresh_log",
        "dump_bounded_refresh_container_logs",
        "wait_for_bounded_source_refresh_service",
        "prepare_bounded_source_refresh_dataset",
        "execute_bounded_source_refresh_entrypoint",
    )
    functions = []
    for name in names:
        # A Python heredoc can contain an unindented closing brace, so delimit
        # shell helpers by the next shell function rather than the first brace.
        match = re.search(rf"^{name}\(\) \{{\n.*?(?=^\w+\(\) \{{|\Z)", script, re.S | re.M)
        assert match, name
        functions.append(match.group(0))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(STUB_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    out = tmp_path / "out.json"
    harness = "\n".join(
        [
            "set -uo pipefail",
            "info() { echo \"$*\"; }",
            "error() { echo \"ERROR: $*\" >&2; exit 1; }",
            "sleep() { :; }",
            "check_taiwan_refresh_preflight() { echo '{\"status\": \"proceed\"}'; }",
            "validate_source_refresh_profile() { :; }",
            f"resolve_bounded_source_refresh_active_symbols() {{ export SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS='{symbols}'; }}",
            "verify_bounded_source_refresh_readback() { :; }",
            # These cases exercise container lifecycle, not HTTP/DLQ recovery.
            "recover_bounded_source_refresh_dlq() { :; }",
            "manage_source_ingest_refresh_runtime() { echo \"manage $2\" >> \"${DOCKER_LOG}\"; }",
            *functions,
            f"execute_bounded_source_refresh_entrypoint true {out}",
        ]
    )
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "IMAGE_ID": "sha256:" + "a" * 64,
        "COMPOSE_IMAGE_ID": "sha256:" + "a" * 64,
        "PROJECTOR_IMAGE_ID": "sha256:" + "b" * 64,
        "PROJECTOR_REVISION": "abc123",
        "STEADY_SCHEDULER_ID": "steady-scheduler",
        "STEADY_PROJECTOR_ID": "steady-projector",
        "STEADY_SCHEDULER_ENV": json.dumps([
            "PANTHEON_TENANT_ID=tenant-dev", "PANTHEON_ENV=dev", "GIT_SHA=abc123",
            "SOURCE_INGEST_CONTROLLER_STATE_PATH=/data/source-ingest/controller_state.json",
        ]),
        "STEADY_PROJECTOR_ENV": json.dumps([
            "PANTHEON_TENANT_ID=tenant-dev", "PANTHEON_ENV=dev", "GIT_SHA=abc123",
            "SOURCE_INGEST_AGORA_PROJECTOR_STATE_PATH=/data/source-ingest/agora_projector_state.json",
        ]),
        "BOUNDED_EXIT_CODE": exit_code,
        **(extra_env or {}),
    }
    result = subprocess.run(["bash", "-c", harness], env=env, cwd=tmp_path, text=True, capture_output=True, check=False, timeout=20)
    assert "command not found" not in result.stderr, result.stderr
    assert "unexpected Docker fixture command" not in result.stderr, result.stderr
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, calls, out


def _steady_service_mutations(calls: list[str]) -> list[str]:
    steady = ("source-ingest-scheduler", "source-ingest-agora-projector")
    return [
        call
        for call in calls
        if any(re.search(rf"(^| ){verb}( |$)", call) for verb in ("up", "rm", "restart"))
        and any(re.search(rf"(^| ){service}( |$)", call) for service in steady)
    ]


def test_refresh_skips_without_touching_containers_when_no_taiwan_symbols(tmp_path: Path) -> None:
    result, calls, out = _run_refresh_entrypoint(tmp_path, "", "0")
    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "status": "skipped", "reason": "no_active_taiwan_symbols",
        "connector_id": "tw-twse-tpex-official-market", "log": "",
    }
    assert calls == []


@pytest.mark.parametrize("exit_code,expected_rc", [("0", 0), ("1", 1)])
def test_refresh_never_replaces_steady_scheduler_or_projector(tmp_path: Path, exit_code: str, expected_rc: int) -> None:
    result, calls, out = _run_refresh_entrypoint(tmp_path, "0050.TW", exit_code)
    assert result.returncode == expected_rc, result.stderr
    assert _steady_service_mutations(calls) == []
    runs = [call for call in calls if " run -d " in call]
    assert len(runs) == 2
    assert all("--name pantheon-bounded-refresh-" in call for call in runs)
    assert calls.count("run-env tenant=tenant-dev env=dev sha=abc123") == 2
    assert not any("ps -a -q source-ingest-" in call for call in calls)
    assert "manage restore" in calls
    assert any(call.startswith("rm -f pantheon-bounded-refresh-") for call in calls)
    outcome = json.loads(out.read_text())
    assert outcome["status"] == ("completed" if expected_rc == 0 else "failed")
    assert outcome["log"] == ("" if expected_rc == 0 else "bounded fixture log")


@pytest.mark.parametrize("exit_code", ["0", "1"])
def test_refresh_pauses_and_restarts_same_steady_containers_with_own_state(tmp_path: Path, exit_code: str) -> None:
    result, calls, _ = _run_refresh_entrypoint(tmp_path, "0050.TW", exit_code)
    assert result.returncode == int(exit_code), result.stderr
    stop = calls.index("stop steady-scheduler steady-projector")
    assert calls.index("start steady-scheduler steady-projector") > max(
        i for i, call in enumerate(calls) if " run -d " in call
    ) > stop
    assert not any("rm" in call.split() and "steady-" in call for call in calls)
    runs = [call for call in calls if " run -d " in call]
    assert len(runs) == 2
    assert all("SOURCE_INGEST_CONTROLLER_ALIVE_PATH=/data/source-ingest/bounded-refresh/controller_alive" in c for c in runs)
    # Since DEV-TW-REFRESH-AUTHORITATIVE-STATE-20261008 the bounded writer must
    # share the API/steady journal; only liveness is isolated. An override to a
    # separate state file leaves authoritative readback stale.
    assert not any("SOURCE_INGEST_CONTROLLER_STATE_PATH=" in c for c in runs)
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    for service in ("source-ingest", "source-ingest-scheduler"):
        assert services[service]["environment"]["SOURCE_INGEST_CONTROLLER_STATE_PATH"] == "/data/source-ingest/controller_state.json"
        assert "source-ingest-data:/data/source-ingest" in services[service]["volumes"]


def test_refresh_fails_closed_without_steady_deploy_values(tmp_path: Path) -> None:
    baseline = tmp_path / "valid"
    baseline.mkdir()
    sibling_env = {
        "STEADY_SCHEDULER_ENV": json.dumps([
            "PANTHEON_TENANT_ID=tenant-sibling", "PANTHEON_ENV=dev", "GIT_SHA=sibling123",
        ]),
        "PROJECTOR_REVISION": "sibling123",
    }
    valid, valid_calls, _ = _run_refresh_entrypoint(baseline, "0050.TW", "0", sibling_env)
    assert valid.returncode == 0, valid.stderr
    assert valid_calls.count("run-env tenant=tenant-sibling env=dev sha=sibling123") == 2
    assert "start steady-scheduler steady-projector" in valid_calls

    missing = tmp_path / "missing"
    missing.mkdir()
    result, calls, out = _run_refresh_entrypoint(missing, "0050.TW", "0", {**sibling_env, "NO_DEPLOY_VALUES": "1"})
    assert result.returncode != 0
    assert "missing deploy-time value PANTHEON_TENANT_ID" in result.stderr
    assert "steady_scheduler_deploy_values_missing" in result.stderr
    assert "inspect --format {{json .Config.Env}} steady-scheduler" in calls
    assert json.loads(out.read_text())["status"] == "failed"
    assert not any(
        " run -d " in call or " build " in call or call.startswith(("stop ", "start ", "rm ", "manage "))
        for call in calls
    )
