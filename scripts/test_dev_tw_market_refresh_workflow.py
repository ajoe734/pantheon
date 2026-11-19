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
        "source_ingest_family_services",
        "remove_source_ingest_compose_leftovers",
        "verify_source_ingest_container_counts",
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
            "error() { LAST_REMOTE_DEPLOY_ERROR=\"$*\"; echo \"ERROR: $*\" >&2; exit 1; }",
            "sleep() { :; }",
            "curl_with_retry() { [[ -n \"${CURL_RETRY_LOG:-}\" ]] && echo \"$*\" >> \"${CURL_RETRY_LOG}\"; return \"${CURL_RETRY_RC:-0}\"; }",
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


def test_chip_workflow_executes_every_profile_under_lease_and_preserves_failures(tmp_path):
    from services.source_ingestion.connector_definitions import TW_CHIP_REFRESH_PROFILES
    wf = _load_workflow()
    assert wf['jobs']['refresh']['timeout-minutes'] == 150
    step = next(s for s in wf['jobs']['refresh']['steps'] if s.get('id') == 'chip_refresh')
    assert step['timeout-minutes'] == 90
    assert step['env']['SOURCE_INGEST_MAX_RECORDS'] == 500
    assert step['env']['SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS'] == 300
    assert step['continue-on-error'] is True  # a failed profile must not skip later steps
    assert step['run'].count('run_with_dev_environment_lease.sh') == 1
    wrapper = tmp_path / '.lease-controller/scripts/run_with_dev_environment_lease.sh'
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text('''#!/usr/bin/env bash
if [[ -f "${GUARD_FAILED:-}" ]]; then
  echo "lease heartbeat already quarantined" >&2
  exit 75
fi
printf '%s %s\\n' "$1" "$2" >> "${GUARD_CALLS}"
"$@"
rc=$?
if (( rc != 0 )) && [[ -n "${GUARD_FAILED:-}" ]]; then
  touch "${GUARD_FAILED}"
fi
exit "${rc}"
''')
    wrapper.chmod(0o755)
    deploy = tmp_path / 'scripts/deploy_nonprod_vm.sh'
    deploy.parent.mkdir()
    deploy.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
# One failure cannot suppress the remaining datasets' recorded outcomes.
code = 1 if 'tw-official-twse-margin-balances' in args else 0
with open(args[args.index('--output') + 1], 'w') as stream:
    json.dump({'status': 'failed' if code else 'completed', 'reason': 'fixture'}, stream)
sys.exit(code)
''')
    deploy.chmod(0o755)
    guard_calls = tmp_path / 'guard_calls'
    guard_failed = tmp_path / 'guard_failed'
    proc = subprocess.run(['bash', '-e', '-c', step['run']], cwd=tmp_path,
                          env={**os.environ, 'RUNNER_TEMP': str(tmp_path), 'FORCE_REFRESH': 'true',
                               'CALLS': str(tmp_path / 'calls'), 'GUARD_CALLS': str(guard_calls),
                               'GUARD_FAILED': str(guard_failed)}, text=True, capture_output=True, timeout=20)
    assert proc.returncode == 1, proc.stderr
    assert not guard_failed.exists(), 'guarded command must exit 0 so the guard does not quarantine the lease'
    assert guard_calls.read_text().splitlines() == ['bash -c']
    assert 'tw-official-twse-margin-balances exit 1' in (tmp_path / 'chip-refresh-failed-profiles.txt').read_text()
    calls = [json.loads(l) for l in (tmp_path / 'calls').read_text().splitlines()]
    chip_profiles = {k: v for k, v in TW_CHIP_REFRESH_PROFILES.items() if v['base'] != 'tw-finmind-fundamentals'}
    assert len(calls) == len(chip_profiles) == 6
    for call in calls:
        cid = call[call.index('--connector-id') + 1]
        assert call[call.index('--dataset') + 1] == chip_profiles[cid]['dataset']
        assert '--force' in call and '--refresh-only' in call
        assert (tmp_path / f'{cid}-refresh-outcome.json').is_file()
    assert "30 10" in step['if'] and "30 12" in step['if']


def test_workflow_requests_taifex_tx_daily_once_and_publishes_its_outcome():
    workflow = _load_workflow()
    step = next(s for s in workflow['jobs']['refresh']['steps'] if s.get('id') == 'chip_refresh')
    lines = [ln.split() for ln in re.split(r"<<'?PROFILES'?\n", step['run'], maxsplit=1)[1].split('\nPROFILES')[0].splitlines() if ln.strip()]
    assert [ln for ln in lines if ln[1] == 'taifex_tx_daily'] == [
        ['tw-taifex-tx-daily', 'taifex_tx_daily', 'openapi.taifex.com.tw,www.taifex.com.tw']
    ]
    upload = next(s for s in workflow['jobs']['refresh']['steps'] if s.get('name') == 'Upload refresh outcome artifact')
    assert 'tw-taifex-tx-daily-refresh-outcome.json' in upload['with']['path']



def test_workflow_requests_each_finmind_fundamental_dataset_once_after_publication_with_the_secret():
    workflow = _load_workflow()
    steps = workflow['jobs']['refresh']['steps']
    step = next(s for s in steps if s.get('id') == 'finmind_fundamentals_refresh')
    block = step['run'].split("PROFILES='", 1)[1].split("'\n", 1)[0]
    lines = [ln.split() for ln in block.splitlines() if ln.strip()]
    assert lines == [
        ['tw-finmind-monthly-revenue', 'tw_monthly_revenue', 'api.finmindtrade.com'],
        ['tw-finmind-quarterly-statements', 'tw_financial_statement', 'api.finmindtrade.com'],
        ['tw-finmind-valuation', 'tw_valuation_bwibbu', 'api.finmindtrade.com'],
    ]
    # FinMind publishes after the close: never on the early crons; the credential is the dev environment secret.
    assert "'0 7 * * 1-5'" in step['if'] and "'0 8 * * 1-5'" in step['if'] and '!=' in step['if']
    assert step['env']['FINMIND_API_TOKEN'] == '${{ secrets.FINMIND_API_TOKEN }}'
    assert int(step['env']['SOURCE_INGEST_MAX_RECORDS']) <= 500
    assert 'missing_credential' in step['run']
    upload = next(s for s in steps if s.get('name') == 'Upload refresh outcome artifact')
    for cid in ('tw-finmind-monthly-revenue', 'tw-finmind-quarterly-statements', 'tw-finmind-valuation'):
        assert f'{cid}-refresh-outcome.json' in upload['with']['path']
    # the early-cron chip profiles are unchanged: FinMind fundamentals are not among them
    chip = next(s for s in steps if s.get('id') == 'chip_refresh')
    assert 'finmind' not in chip['run']


def test_refresh_entrypoint_waits_for_readyz_36x5_before_preflight_and_fails_closed(tmp_path: Path) -> None:
    ok_dir = tmp_path / "ok"
    ok_dir.mkdir()
    retry_log = ok_dir / "curl_retry.log"
    result, calls, _ = _run_refresh_entrypoint(
        ok_dir, "2330.TW", "0", {"CURL_RETRY_LOG": str(retry_log)}
    )
    assert result.returncode == 0, result.stderr
    assert retry_log.read_text(encoding="utf-8").splitlines() == [
        "http://127.0.0.1:18097/readyz 36 5 --max-time 5 --connect-timeout 5"
    ]
    assert "manage bounded" in calls and "manage restore" in calls

    fail_dir = tmp_path / "fail"
    fail_dir.mkdir()
    fail_retry_log = fail_dir / "curl_retry.log"
    failed, fail_calls, fail_out = _run_refresh_entrypoint(
        fail_dir, "2330.TW", "0", {"CURL_RETRY_LOG": str(fail_retry_log), "CURL_RETRY_RC": "1"}
    )
    assert failed.returncode != 0
    assert "source-ingest /readyz did not become ready before bounded refresh preflight" in failed.stderr
    assert fail_calls == []
    outcome = json.loads(fail_out.read_text(encoding="utf-8"))
    assert outcome["status"] == "failed"
    assert outcome["reason"] == "source-ingest /readyz did not become ready before bounded refresh preflight"


def test_non_blocking_taifex_and_finmind_steps_stay_exit_safe_under_lease(tmp_path: Path) -> None:
    steps = {s.get("id"): s for s in _load_workflow()["jobs"]["refresh"]["steps"] if s.get("id")}
    wrapper = tmp_path / ".lease-controller/scripts/run_with_dev_environment_lease.sh"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(
        """#!/usr/bin/env bash
if [[ -f "${GUARD_FAILED:-}" ]]; then
  echo "lease heartbeat already quarantined" >&2
  exit 75
fi
printf '%s %s\\n' "$1" "$2" >> "${GUARD_CALLS}"
"$@"
rc=$?
if (( rc != 0 )) && [[ -n "${GUARD_FAILED:-}" ]]; then
  touch "${GUARD_FAILED}"
fi
exit "${rc}"
""",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    deploy = tmp_path / "scripts/deploy_nonprod_vm.sh"
    deploy.parent.mkdir(parents=True)
    deploy.write_text(
        """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["CALLS"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")
out_path = args[args.index("--output") + 1]
with open(out_path, "w", encoding="utf-8") as stream:
    json.dump({"status": "failed", "reason": "simulated_nonblocking_failure", "log": "redacted log"}, stream)
sys.exit(1)
""",
        encoding="utf-8",
    )
    deploy.chmod(0o755)
    guard_calls = tmp_path / "guard_calls"
    guard_failed = tmp_path / "guard_failed"
    calls_file = tmp_path / "calls"
    taifex_out = tmp_path / "taifex-ssf-refresh-outcome.json"
    finmind_out = tmp_path / "finmind-broker-refresh-outcome.json"

    base_env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "FORCE_REFRESH": "true",
        "CALLS": str(calls_file),
        "GUARD_CALLS": str(guard_calls),
        "GUARD_FAILED": str(guard_failed),
    }
    taifex_proc = subprocess.run(
        ["bash", "-e", "-c", steps["taifex_refresh"]["run"]],
        cwd=tmp_path,
        env={**base_env, "REFRESH_OUTPUT_PATH": str(taifex_out)},
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert taifex_proc.returncode == 0, taifex_proc.stderr
    assert not guard_failed.exists(), "Non-blocking TAIFEX failure must not quarantine the lease heartbeat"
    taifex_payload = json.loads(taifex_out.read_text(encoding="utf-8"))
    assert taifex_payload["status"] == "failed"
    assert taifex_payload["reason"] == "simulated_nonblocking_failure"

    finmind_proc = subprocess.run(
        ["bash", "-e", "-c", steps["finmind_refresh"]["run"]],
        cwd=tmp_path,
        env={**base_env, "REFRESH_OUTPUT_PATH": str(finmind_out), "FINMIND_API_TOKEN": "secret-token"},
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert finmind_proc.returncode == 0, finmind_proc.stderr
    assert not guard_failed.exists(), "Non-blocking FinMind failure must not quarantine the lease heartbeat"
    assert guard_calls.read_text(encoding="utf-8").splitlines() == ["bash -c", "bash -c"]
    recorded_calls = [json.loads(line) for line in calls_file.read_text(encoding="utf-8").splitlines()]
    assert len(recorded_calls) == 2
    assert "--non-blocking" in recorded_calls[0] and "--force" in recorded_calls[0]
    assert "--non-blocking" in recorded_calls[1] and "--force" in recorded_calls[1]



# --- TW-REFRESH-STEADY-RESTORE-ON-INTERRUPT-20261118 -------------------------


def _steps() -> list[dict[str, Any]]:
    return _load_workflow()["jobs"]["refresh"]["steps"]


def _step(name_part: str) -> dict[str, Any]:
    return next(s for s in _steps() if name_part in s.get("name", ""))


def test_steady_check_step_runs_after_refresh_steps_and_before_upload_and_release() -> None:
    names = [s.get("name", "") for s in _steps()]
    check = next(i for i, n in enumerate(names) if "steady source-ingest posture" in n)
    for refresh in ("Taiwan chip refresh", "FinMind fundamentals refresh", "bounded Taiwan market refresh"):
        assert names.index(next(n for n in names if refresh in n)) < check
    assert check < next(i for i, n in enumerate(names) if "Upload refresh outcome" in n)
    assert check < next(i for i, n in enumerate(names) if "Stop heartbeat" in n)
    # always() is what makes a step run after failure, timeout and cancellation.
    assert _step("steady source-ingest posture")["if"] == "${{ always() && steps.lease.outcome == 'success' }}"
    assert "always()" in _step("Upload refresh outcome")["if"]
    assert "cancelled" not in _step("Upload refresh outcome")["if"]


def test_chip_step_budget_covers_six_profiles_at_measured_time() -> None:
    chip = _step("Taiwan chip refresh")
    profiles = [ln for ln in chip["run"].splitlines() if ln.startswith("tw-") or ln.strip().startswith("tw-")]
    assert len(profiles) == 6
    per_profile_minutes = 9.5  # measured in run 44273374890
    assert chip["timeout-minutes"] >= len(profiles) * per_profile_minutes
    assert "timeout --kill-after=60 900 ./scripts/deploy_nonprod_vm.sh" in chip["run"]
    job = _load_workflow()["jobs"]["refresh"]
    assert job["timeout-minutes"] > chip["timeout-minutes"] + 15 + 10


def _run_check_step(tmp_path: Path, deploy_body: str, existing_outcome: str | None) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    """Run the real step script in the job checkout layout against fake scripts."""
    step = _step("steady source-ingest posture")
    (tmp_path / ".lease-controller" / "scripts").mkdir(parents=True)
    wrapper = tmp_path / ".lease-controller" / "scripts" / "run_with_dev_environment_lease.sh"
    wrapper.write_text('#!/usr/bin/env bash\nexec "$@"\n', encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    deploy = tmp_path / "scripts" / "deploy_nonprod_vm.sh"
    deploy.write_text("#!/usr/bin/env bash\n" + deploy_body, encoding="utf-8")
    for f in (wrapper, deploy):
        f.chmod(0o755)
    runner_temp = tmp_path / "temp"
    runner_temp.mkdir()
    outcome = runner_temp / "tw-refresh-outcome.json"
    if existing_outcome is not None:
        outcome.write_text(existing_outcome, encoding="utf-8")
    env = {
        "PATH": os.environ["PATH"],
        "STEADY_CHECK_OUTPUT_PATH": str(runner_temp / "steady-restore-check.json"),
        "REFRESH_OUTCOME_PATH": str(outcome),
    }
    proc = subprocess.run(["bash", "--noprofile", "--norc", "-c", step["run"]], cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    return proc, json.loads(outcome.read_text(encoding="utf-8"))


WRITE = 'out=""; while [[ $# -gt 0 ]]; do [[ "$1" == --output ]] && out="$2"; shift; done\n'


def test_steady_check_step_passes_and_records_probe_when_healthy(tmp_path: Path) -> None:
    body = WRITE + 'echo \'{"mode":"collector_steady_pull","reason":"","status":"healthy"}\' > "$out"\n'
    proc, outcome = _run_check_step(tmp_path, body, '{"status":"failed","reason":"timeout"}')
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outcome["steady_restore_check"] == {"reason": "", "status": "healthy"}
    assert outcome["status"] == "failed"  # existing fields preserved


def test_steady_check_step_fails_with_named_reason_after_restore(tmp_path: Path) -> None:
    body = WRITE + 'echo \'{"mode":"collector_steady_pull","reason":"source-ingest_not_running","status":"restored"}\' > "$out"\n'
    proc, outcome = _run_check_step(tmp_path, body, None)
    assert proc.returncode == 1
    assert "::error::steady_restore_check_restored: source-ingest_not_running" in proc.stdout
    assert outcome["steady_restore_check"] == {"reason": "source-ingest_not_running", "status": "restored"}


def test_steady_check_step_fails_closed_when_transport_produces_nothing(tmp_path: Path) -> None:
    proc, outcome = _run_check_step(tmp_path, "exit 255\n", "not json")
    assert proc.returncode == 1
    assert outcome["steady_restore_check"] == {"reason": "steady_check_transport_exit_255", "status": "failed"}


# --- non-blocking step failure visibility (TW-REFRESH-NON-BLOCKING-STEP-FAILURE-VISIBLE-20261118) ---

import importlib.util
import sys as _sys

_SUMMARY_PATH = ROOT / "scripts" / "tw_refresh_outcome_summary.py"
_spec = importlib.util.spec_from_file_location("tw_refresh_outcome_summary", _SUMMARY_PATH)
summary_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(summary_mod)

_FUNDAMENTALS = ("tw-finmind-monthly-revenue", "tw-finmind-quarterly-statements", "tw-finmind-valuation")
_CHIPS = (
    "tw-official-twse-margin-balances", "tw-official-tpex-margin-balances", "tw-official-tpex-institutional-flow",
    "tw-taifex-institutional-positions", "tw-taifex-put-call", "tw-taifex-tx-daily",
)


def _write(d: Path, name: str, **payload: Any) -> None:
    (d / name).write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _real_run(d: Path, overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, str]:
    """Write outcome files shaped like a healthy full run; return all step outcomes."""
    overrides = overrides or {}
    files = {"taifex-ssf-refresh-outcome.json": "tw-taifex-futures-options-chip", "finmind-broker-refresh-outcome.json": "tw-finmind-broker-daily-report"}
    files.update({f"{c}-refresh-outcome.json": c for c in _CHIPS + _FUNDAMENTALS})
    for fname, connector in files.items():
        payload = {"connector_id": connector, "log": "", "reason": "refreshed", "status": "completed"}
        payload.update(overrides.get(connector, {}))
        _write(d, fname, **payload)
    _write(d, "tw-refresh-outcome.json", status="completed", reason="refreshed",
           steady_restore_check={"reason": "", "status": "healthy"})
    return {k: "success" for k in ("taifex_refresh", "finmind_refresh", "refresh", "chip_refresh", "finmind_fundamentals_refresh", "steady_check")}


def _run_summary(tmp_path: Path, steps: dict[str, str]) -> tuple[int, str, str]:
    summary = tmp_path / "summary.md"
    args = ["--outcomes-dir", str(tmp_path), "--summary-file", str(summary)]
    for k, v in steps.items():
        args += ["--step", f"{k}={v}"]
    proc = subprocess.run([_sys.executable, str(_SUMMARY_PATH), *args], capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout, summary.read_text(encoding="utf-8")


def test_summary_all_passing_run_stays_green(tmp_path: Path) -> None:
    steps = _real_run(tmp_path)
    rc, out, table = _run_summary(tmp_path, steps)
    assert rc == 0, out
    assert "tw-finmind-valuation" in table and "steady_restore_check" in table
    assert "FAILED" not in table


def test_summary_failed_fundamentals_step_fails_run_and_names_it(tmp_path: Path) -> None:
    steps = _real_run(tmp_path, {c: {"status": "failed", "reason": "exit_1"} for c in _FUNDAMENTALS})
    steps["finmind_fundamentals_refresh"] = "failure"
    rc, out, table = _run_summary(tmp_path, steps)
    assert rc == 1
    assert "Execute bounded FinMind fundamentals refresh" in out
    for c in _FUNDAMENTALS:
        assert c in out and c in table


def test_summary_failed_step_outcome_alone_fails_run(tmp_path: Path) -> None:
    steps = _real_run(tmp_path)
    steps["finmind_fundamentals_refresh"] = "failure"
    rc, out, _ = _run_summary(tmp_path, steps)
    assert rc == 1 and "FinMind fundamentals refresh" in out


def test_summary_not_yet_published_missing_credential_and_skipped_do_not_fail(tmp_path: Path) -> None:
    steps = _real_run(tmp_path, {
        "tw-taifex-tx-daily": {"status": "not_yet_published", "reason": "not_yet_published"},
        "tw-finmind-valuation": {"status": "skipped", "reason": "missing_credential"},
        "tw-finmind-broker-daily-report": {"status": "noop", "reason": "already_fresh"},
    })
    rc, out, _ = _run_summary(tmp_path, steps)
    assert rc == 0, out


def test_summary_unknown_or_missing_status_fails_run(tmp_path: Path) -> None:
    steps = _real_run(tmp_path, {"tw-taifex-put-call": {"status": "weird"}})
    rc, out, _ = _run_summary(tmp_path, steps)
    assert rc == 1 and "tw-taifex-put-call" in out
    (tmp_path / "tw-taifex-tx-daily-refresh-outcome.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "tw-finmind-valuation-refresh-outcome.json").unlink()
    rc, out, _ = _run_summary(tmp_path, _real_run_steps_only())
    assert rc == 1 and "tw-taifex-tx-daily" in out and "tw-finmind-valuation" in out


def _real_run_steps_only() -> dict[str, str]:
    return {k: "success" for k in ("taifex_refresh", "finmind_refresh", "refresh", "chip_refresh", "finmind_fundamentals_refresh", "steady_check")}


def test_summary_failing_steady_restore_check_fails_run(tmp_path: Path) -> None:
    steps = _real_run(tmp_path)
    _write(tmp_path, "tw-refresh-outcome.json", status="completed", steady_restore_check={"reason": "scheduler_mode_x", "status": "failed"})
    rc, out, _ = _run_summary(tmp_path, steps)
    assert rc == 1 and "steady_restore_check" in out


def test_summary_schedule_skipped_steps_do_not_fail(tmp_path: Path) -> None:
    # 07:00/08:00 UTC cron: broker and fundamentals skipped, no files for them.
    _write(tmp_path, "tw-refresh-outcome.json", status="completed", steady_restore_check={"reason": "", "status": "healthy"})
    steps = {"taifex_refresh": "skipped", "finmind_refresh": "skipped", "refresh": "success", "chip_refresh": "skipped",
             "finmind_fundamentals_refresh": "skipped", "steady_check": "success"}
    rc, out, _ = _run_summary(tmp_path, steps)
    assert rc == 0, out


def test_summary_step_is_last_always_and_keeps_continue_on_error() -> None:
    steps = _steps()
    final = steps[-1]
    assert "Summarize refresh outcomes" in final["name"]
    assert final["if"] == "${{ always() && steps.lease.outcome == 'success' }}"
    assert "tw_refresh_outcome_summary.py" in final["run"]
    assert "ssh" not in final["run"].lower() and "deploy_nonprod_vm" not in final["run"]
    for name in ("TAIFEX contract refresh", "FinMind broker branch refresh", "FinMind fundamentals refresh"):
        assert _step(name)["continue-on-error"] is True
    upload = next(i for i, s in enumerate(steps) if "Upload refresh outcome" in s.get("name", ""))
    assert upload < len(steps) - 1


# --- TW-REFRESH-STEADY-CHECK-AFTER-LEASE-QUARANTINE-20261119 -----------------
# Reproduces run 44421268724: one chip profile fails its readback, the lease guard
# records guarded_command_failed + stops the heartbeat, and every later guarded
# command is refused with exit 75.

_QUARANTINING_WRAPPER = """#!/usr/bin/env bash
if [[ -e "${GUARD_FAILURE_FILE}" ]]; then
  echo "[dev-environment-lease-guard] ERROR: lease heartbeat is not healthy before command start" >&2
  exit 75
fi
printf '%s\\n' "$*" >> "${GUARD_CALLS}"
"$@"; rc=$?
if (( rc != 0 )); then echo '{"status":"guarded_command_failed"}' > "${GUARD_FAILURE_FILE}"; exit 75; fi
exit 0
"""


def _layout(tmp_path: Path, deploy_py: str) -> dict[str, str]:
    wrapper = tmp_path / ".lease-controller/scripts/run_with_dev_environment_lease.sh"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(_QUARANTINING_WRAPPER)
    deploy = tmp_path / "scripts/deploy_nonprod_vm.sh"
    deploy.parent.mkdir()
    deploy.write_text("#!/usr/bin/env python3\n" + deploy_py)
    for f in (wrapper, deploy):
        f.chmod(0o755)
    (tmp_path / "temp").mkdir()
    return {**os.environ, "RUNNER_TEMP": str(tmp_path / "temp"), "FORCE_REFRESH": "false",
            "GUARD_FAILURE_FILE": str(tmp_path / "heartbeat-failure.json"), "GUARD_CALLS": str(tmp_path / "guard_calls"),
            "CALLS": str(tmp_path / "calls")}


_DEPLOY_FAIL_FIRST = """import json, os, sys
a = sys.argv[1:]
open(os.environ['CALLS'], 'a').write(a[a.index('--connector-id') + 1] + '\\n' if '--connector-id' in a else 'steady\\n')
if '--steady-check' in a:
    json.dump({'mode': 'collector_steady_pull', 'reason': '', 'status': 'healthy'}, open(a[a.index('--output') + 1], 'w'))
    sys.exit(0)
bad = a[a.index('--connector-id') + 1] in ('tw-official-twse-margin-balances', 'tw-finmind-monthly-revenue')
json.dump({'status': 'failed' if bad else 'completed', 'reason': 'readback_failed' if bad else 'refreshed'}, open(a[a.index('--output') + 1], 'w'))
sys.exit(1 if bad else 0)
"""


def _run(step_run: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "--noprofile", "--norc", "-c", step_run], cwd=cwd, env=env, text=True, capture_output=True, timeout=30)


def test_failed_chip_profile_does_not_quarantine_lease_or_stop_fundamentals_and_probe_runs(tmp_path: Path) -> None:
    env = _layout(tmp_path, _DEPLOY_FAIL_FIRST)
    env["FINMIND_API_TOKEN"] = "x"
    chip = _run(_step("Taiwan chip refresh")["run"], tmp_path, env)
    assert chip.returncode == 1, chip.stdout + chip.stderr  # step fails -> summary turns the run red
    assert not (tmp_path / "heartbeat-failure.json").exists()
    fund = _run(_step("FinMind fundamentals refresh")["run"], tmp_path, env)
    assert fund.returncode == 1, fund.stdout + fund.stderr
    assert not (tmp_path / "heartbeat-failure.json").exists()
    calls = (tmp_path / "calls").read_text().split()
    for c in _CHIPS + _FUNDAMENTALS:
        assert c in calls, c  # every profile ran, none skipped after the failure
    check = _run(_step("steady source-ingest posture")["run"], tmp_path, {**env, "STEADY_CHECK_OUTPUT_PATH": str(tmp_path / "temp/steady.json"), "REFRESH_OUTCOME_PATH": str(tmp_path / "temp/tw-refresh-outcome.json")})
    assert check.returncode == 0, check.stdout + check.stderr
    assert json.loads((tmp_path / "temp/tw-refresh-outcome.json").read_text())["steady_restore_check"]["status"] == "healthy"
    # Summary names the failed profiles with their own reason.
    rc, out, _ = _run_summary(tmp_path / "temp", {"chip_refresh": "failure", "finmind_fundamentals_refresh": "failure", "steady_check": "success"})
    assert rc == 1
    assert "tw-official-twse-margin-balances" in out and "readback_failed" in out
    assert "tw-finmind-monthly-revenue" in out


def test_steady_probe_never_records_lease_quarantine_as_probe_result(tmp_path: Path) -> None:
    env = _layout(tmp_path, _DEPLOY_FAIL_FIRST)
    (tmp_path / "heartbeat-failure.json").write_text("{}")  # e.g. step timeout / cancellation quarantine
    env.update(STEADY_CHECK_OUTPUT_PATH=str(tmp_path / "temp/steady.json"), REFRESH_OUTCOME_PATH=str(tmp_path / "temp/o.json"))
    proc = _run(_step("steady source-ingest posture")["run"], tmp_path, env)
    assert proc.returncode == 1
    reason = json.loads((tmp_path / "temp/o.json").read_text())["steady_restore_check"]["reason"]
    assert reason == "probe_not_run_lease_guard_refused_exit_75"
    assert "transport" not in reason


_FAKE_LEASE_CLI = """#!/usr/bin/env python3
import json, os, sys, time
cmd = sys.argv[1]
args = sys.argv[2:]
def opt(n): return args[args.index(n) + 1] if n in args else ''
if cmd == 'verify':
    sys.exit(75 if os.environ.get('FAKE_LEASE_LOST') else 0)
if cmd == 'verify-heartbeat-identity':
    sys.exit(0 if os.path.exists(opt('--identity-file')) else 1)
if cmd == 'heartbeat-loop':
    open(opt('--identity-json-out'), 'w').write('{"pid": %d}' % os.getpid())
    time.sleep(30)
    sys.exit(0)
sys.exit(2)
"""


def _rearm(tmp_path: Path, lost: bool, quarantined: bool = True) -> tuple[subprocess.CompletedProcess[str], Path]:
    ctrl = tmp_path / ".lease-controller/scripts"
    ctrl.mkdir(parents=True)
    cli = ctrl / "dev_environment_lease.py"
    cli.write_text(_FAKE_LEASE_CLI)
    cli.chmod(0o755)
    ld = tmp_path / "ld"
    ld.mkdir()
    (ld / "state.json").write_text('{"leaseId":"x"}')
    (ld / "heartbeat.pid").write_text("999999\n")
    (ld / "heartbeat-identity.json").write_text("{}")
    (ld / "heartbeat-stop.json").write_text("{}")
    if quarantined:
        (ld / "heartbeat-failure.json").write_text('{"status":"guarded_command_failed"}')
    gh = tmp_path / "gh_output"
    env = {**os.environ, "CONTROLLER": str(tmp_path / ".lease-controller"), "LD": str(ld), "GITHUB_OUTPUT": str(gh),
           "PANTHEON_ENVIRONMENT_LEASE_TOKEN": "t"}
    if lost:
        env["FAKE_LEASE_LOST"] = "1"
    step = _step("Re-arm lease heartbeat")
    assert step["if"] == "${{ always() && steps.lease.outcome == 'success' }}"
    return _run(step["run"], tmp_path, env), ld


def test_rearm_renews_same_lease_after_quarantine_without_release(tmp_path: Path) -> None:
    proc, ld = _rearm(tmp_path, lost=False)
    pid = int((ld / "heartbeat.pid").read_text())
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "rearm=done" in (tmp_path / "gh_output").read_text()
        assert not (ld / "heartbeat-failure.json").exists()
        assert (ld / "heartbeat-failure.quarantine.json").is_file()  # evidence kept
        assert not (ld / "heartbeat-stop.json").exists()  # stale stop marker would allow early release
        assert pid != 999999 and (ld / "heartbeat-identity.json").read_text().strip() == '{"pid": %d}' % pid
        assert "release" not in _step("Re-arm lease heartbeat")["run"].replace("never released", "")
    finally:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def test_rearm_does_not_touch_lease_when_ownership_is_lost(tmp_path: Path) -> None:
    proc, ld = _rearm(tmp_path, lost=True)
    assert proc.returncode == 0
    assert "rearm=failed" in (tmp_path / "gh_output").read_text()
    assert (ld / "heartbeat-failure.json").is_file()  # still quarantined; probe will not run


def test_rearm_is_noop_without_quarantine(tmp_path: Path) -> None:
    proc, ld = _rearm(tmp_path, lost=False, quarantined=False)
    assert proc.returncode == 0 and (ld / "heartbeat.pid").read_text().strip() == "999999"


def test_rearm_sits_between_refresh_steps_and_probe_and_release_needs_healthy_probe() -> None:
    names = [s.get("name", "") for s in _steps()]
    rearm = next(i for i, n in enumerate(names) if "Re-arm lease heartbeat" in n)
    assert rearm + 1 == next(i for i, n in enumerate(names) if "steady source-ingest posture" in n)
    assert "STEADY_CHECK_OUTCOME}\" == success" in _step("Stop heartbeat")["run"]
    for name in ("bounded Taiwan market refresh", "Taiwan chip refresh"):
        assert _step(name)["continue-on-error"] is True
