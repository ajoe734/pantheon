import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts/deploy_nonprod_vm.sh"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"


def _failure_diagnostics_function() -> str:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = deploy.index("dump_dev_root_failure_diagnostics() {")
    end = deploy.index("\n}\n\nretire_dormant_and_one_off_profile_containers()", start) + 2
    return deploy[start:end]


def _run_root_failure(tmp_path: Path, **settings: str) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    """Execute the real diagnostics and rollback functions against Docker stubs."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        f"#!{sys.executable}\n" + """
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ['DOCKER_CALLS'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[:1] == ['compose']:
    if args[-4:-1] == ['ps', '-a', '-q']:
        service = args[-1]
        if service == 'search-index-scheduler':
            if os.getenv('MISSING'):
                sys.exit(0)
            if os.getenv('PS_FAIL'):
                sys.exit(19)
            print('failing-worker-id')
        elif service == 'search-svc':
            print('healthy-service-id')
    elif 'logs' in args:
        print('healthy-service-log' if args[-1] == 'search-svc' else 'other-service-log')
    sys.exit(0)
if args[:1] == ['logs']:
    assert args == ['logs', '--tail', '200', 'failing-worker-id']
    assert not Path(os.environ['RESTORE_MARKER']).exists()
    if os.getenv('LOGS_FAIL'):
        print('Error response from daemon: DAEMON_ERROR_SENTINEL '
              'Authorization: Bearer LOG_COMMAND_SECRET_SENTINEL', file=sys.stderr)
        sys.exit(20)
    print(os.environ['WORKER_LOG'], flush=True)
    print(os.environ['WORKER_STDERR'], file=sys.stderr, flush=True)
    sys.exit(0)
if args[:1] == ['inspect']:
    assert args[1] == '--format'
    if args[-1] == 'failing-worker-id':
        assert not Path(os.environ['RESTORE_MARKER']).exists()
        assert args[2] == ('status={{.State.Status}} restart_count={{.RestartCount}} '
                           'health={{if .State.Health}}{{.State.Health.Status}}{{else}}not_configured{{end}} '
                           'exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}}')
        if os.getenv('INSPECT_FAIL'):
            print('INSPECT_SECRET_SENTINEL', file=sys.stderr)
            sys.exit(21)
        print('status=restarting restart_count=5 health=unhealthy exit_code=1 oom_killed=false')
    else:
        assert args[-1] == 'healthy-service-id'
        print('status=running restart_count=0 health=healthy exit_code=0 oom_killed=false')
    sys.exit(0)
sys.exit(91)
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    redact_start = deploy.index("redact_bounded_refresh_log() {")
    redact_end = deploy.index("\n}\n", redact_start) + 2
    rollback_start = deploy.index("rollback_dev_bff_on_failure() {")
    rollback_end = deploy.index("\n}\n", rollback_start) + 2
    script = """set -euo pipefail
info() { echo "$*"; }
run_dev_artifact_driver() {
  printf '%s\\n' "$1" > "$RESTORE_MARKER"
  echo "artifact-driver:$1"
  return "${RESTORE_STATUS:-0}"
}
""" + deploy[redact_start:redact_end] + "\n" + _failure_diagnostics_function() + "\n" + deploy[rollback_start:rollback_end] + """
rollback_dev_bff_on_failure exact_component_deployment
echo UNREACHABLE_SUCCESS
"""
    calls = tmp_path / "docker-calls.jsonl"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_CALLS": str(calls),
        "RESTORE_MARKER": str(tmp_path / "restore"),
        "DEV_CANDIDATE_RECEIPT_ACKED": "true",
        "WORKER_LOG": "",
        "WORKER_STDERR": "",
        **settings,
    }
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=10, check=False,
    )
    return result, [json.loads(line) for line in calls.read_text().splitlines()]


@pytest.mark.parametrize('trace_stream', ['stdout', 'stderr'])
def test_search_worker_failure_retained_before_compensation_without_payloads(
    tmp_path: Path, trace_stream: str,
) -> None:
    # Synthetic fixture: a healthy API, a crashing worker, successful business
    # snapshots and failed-tick payloads must remain distinguishable.
    worker_log = '\n'.join([
        json.dumps({"tick": 1, "result": {"document": "SUCCESS_PAYLOAD_SENTINEL", "error_type": "RuntimeError"}}),
        json.dumps({"tick": 2, "status": "failed", "error_type": "HTTPError", "detail": "FAILED_PAYLOAD_SENTINEL", "token": "SECRET_TOKEN_SENTINEL"}),
        'Authorization: Bearer SECRET_HEADER_SENTINEL',
        'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.signature',
    ])
    traceback_log = '\n'.join([
        'Traceback (most recent call last):',
        '  File "/app/services/search/scheduler_worker.py", line 99, in main',
        '  File "/private/PRIVATE_PATH_SENTINEL/client.py", line 23, in request',
        '    request(payload="SOURCE_PAYLOAD_SENTINEL")',
        'TimeoutError: RAW_DOCUMENT_SENTINEL Authorization: Bearer SECRET_BEARER_SENTINEL',
        'Authorization: Bearer STDERR_SECRET_SENTINEL',
        'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwcml2YXRlIn0.stderr_signature',
    ])
    # Non-TTY Docker logs sends Python stderr to CLI stderr; successful and
    # failed tick JSON remains on stdout. Also keep the former stdout control.
    result, calls = _run_root_failure(
        tmp_path,
        WORKER_LOG=worker_log + ('\n' + traceback_log if trace_stream == 'stdout' else ''),
        WORKER_STDERR=traceback_log if trace_stream == 'stderr' else '',
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert 'healthy-service-log' in output
    assert 'Traceback (most recent call last):' in output
    assert '  File "scheduler_worker.py", line 99, in main' in output
    assert '/app/' not in output
    assert '/private/' not in output
    assert 'TimeoutError: [details omitted]' in output
    assert '{"error_type": "HTTPError", "status": "failed", "tick": 2}' in output
    assert 'status=restarting restart_count=5 health=unhealthy exit_code=1 oom_killed=false' in output
    assert output.index('TimeoutError:') < output.index('artifact-driver:restore')
    assert output.index('"error_type": "HTTPError"') < output.index('artifact-driver:restore')
    assert output.index('restart_count=5') < output.index('artifact-driver:restore')
    assert 'automatic BFF exact-artifact restore verified' in output
    assert 'SENTINEL' not in output
    assert 'eyJ' not in output
    assert 'RuntimeError' not in output
    assert 'UNREACHABLE_SUCCESS' not in output
    assert ['logs', '--tail', '200', 'failing-worker-id'] in calls


@pytest.mark.parametrize('failure', ['MISSING', 'PS_FAIL', 'LOGS_FAIL', 'INSPECT_FAIL'])
@pytest.mark.parametrize('restore_status,acked,operation', [('0', 'true', 'restore'), ('23', 'true', 'restore'), ('0', 'false', 'verify')])
def test_search_worker_diagnostics_unavailable_preserve_failure_and_rollback(
    tmp_path: Path, failure: str, restore_status: str, acked: str, operation: str,
) -> None:
    result, calls = _run_root_failure(
        tmp_path, **{failure: '1', 'RESTORE_STATUS': restore_status, 'DEV_CANDIDATE_RECEIPT_ACKED': acked},
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert f'artifact-driver:{operation}' in output
    assert 'UNREACHABLE_SUCCESS' not in output
    assert 'SENTINEL' not in output
    if failure in ('MISSING', 'PS_FAIL'):
        assert 'search-index-scheduler container unavailable after failure' in output
        assert not any('failing-worker-id' in call for call in calls)
    if failure == 'LOGS_FAIL':
        assert 'restart_count=5' in output
        assert 'Error response from daemon' not in output
    if restore_status == '23':
        assert 'automatic BFF exact-artifact restore failed; no source-build fallback' in output


def test_search_worker_success_only_logs_do_not_export_business_snapshot(tmp_path: Path) -> None:
    result, _ = _run_root_failure(
        tmp_path, WORKER_LOG=json.dumps({"tick": 1, "result": {"body": "SUCCESS_ONLY_SENTINEL"}}),
    )
    assert result.returncode == 1
    assert 'SUCCESS_ONLY_SENTINEL' not in result.stdout + result.stderr
    assert '"tick": 1' not in result.stdout
    assert 'artifact-driver:restore' in result.stdout


def _bounded_wait_function() -> str:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    # Include the log-dump helpers that the wait function calls on failure.
    start = deploy.index("redact_bounded_refresh_log() {")
    end = deploy.index("\n}\n\nverify_bounded_source_refresh_readback()", start) + 2
    return deploy[start:end]


def _run_bounded_wait(tmp_path: Path, *, exit_code: int) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "compose" ]]; then
  printf 'source-refresh-test-container\\n'
elif [[ "$1" == "inspect" && "$3" == *State.Status* ]]; then
  printf 'exited\\n'
elif [[ "$1" == "inspect" && "$3" == *State.ExitCode* ]]; then
  printf '%s\\n' "${FAKE_DOCKER_EXIT_CODE}"
elif [[ "$1" == "logs" ]]; then
  printf 'worker-boom Authorization: Bearer leakedbearer999\\n'
else
  exit 91
fi
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    script = """set -euo pipefail
error() {
  echo "$*" >&2
  exit 1
}
""" + _bounded_wait_function() + """
SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS=30
wait_for_bounded_source_refresh_service source-ingest-scheduler
"""
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_DOCKER_EXIT_CODE"] = str(exit_code)
    return subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_root_failure_diagnostics_capture_source_ingest_service_and_state() -> None:
    diagnostics = _failure_diagnostics_function()

    assert 'info "source-ingest service logs after failure"' in diagnostics
    assert "logs --no-color --tail=240 source-ingest" in diagnostics
    assert "ps -a -q source-ingest" in diagnostics
    assert 'info "source-ingest container restart and health state after failure"' in diagnostics
    assert "restart_count={{.RestartCount}}" in diagnostics
    assert "health={{if .State.Health}}{{.State.Health.Status}}" in diagnostics


def test_root_failure_diagnostics_keep_scheduler_logs_separate() -> None:
    diagnostics = _failure_diagnostics_function()

    assert 'info "source-ingest-scheduler logs after failure"' in diagnostics
    assert "logs --no-color --tail=120 source-ingest-scheduler" in diagnostics


def test_root_failure_diagnostics_capture_paper_signal_producer_reason() -> None:
    diagnostics = _failure_diagnostics_function()

    assert 'info "paper-signal-producer service logs after failure"' in diagnostics
    assert "logs --no-color --tail=240 paper-signal-producer" in diagnostics
    assert "ps -a -q paper-signal-producer" in diagnostics
    assert 'info "paper-signal-producer container restart and health state after failure"' in diagnostics


def test_bounded_source_refresh_profile_is_fail_closed() -> None:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = deploy.index("validate_source_refresh_profile() {")
    end = deploy.index("\n}\n\ncurl_with_retry()", start)
    gate = deploy[start:end]

    assert 'PANTHEON_EXTERNAL_EGRESS:-deny}" == "allowlist"' in gate
    assert 'SOURCE_INGEST_CONTROLLER_MODE:-}" == "reconcile_and_pull"' in gate
    assert 'SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL:-}" == "reconciled_live_proof"' in gate
    assert 'SOURCE_INGEST_CONTROLLER_RESTART_POLICY:-}" == "no"' in gate
    assert "requires a reviewed exact host allowlist" in gate
    assert "SOURCE_INGEST_CONTROLLER_MAX_TICKS >= 1" in gate
    assert "SOURCE_INGEST_CONTROLLER_MAX_TICKS <= 24" in gate
    assert "SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY <= 4" in gate
    assert "SOURCE_INGEST_MAX_RECORDS <= 500" in gate
    assert "SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS <= 3600" in gate
    assert 'required = {"openapi.twse.com.tw", "www.tpex.org.tw"}' in gate
    assert 'export SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS="${SOURCE_INGEST_BOUNDED_CONNECTOR_ID}"' in gate
    assert 'export SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS="${SOURCE_INGEST_BOUNDED_CONNECTOR_ID}"' in gate
    assert "from services.external_egress import allowed_hosts" in gate

    assert 'SOURCE_REFRESH_CONNECTOR_ID="${SOURCE_INGEST_BOUNDED_CONNECTOR_ID:-tw-twse-tpex-official-market}"' in deploy
    assert "www.twse.com.tw" not in deploy

    root_start = deploy.index("  root)\n")
    root_end = deploy.index("\n  bff)\n", root_start)
    root_case = deploy[root_start:root_end]
    assert root_case.index("validate_source_refresh_profile") < root_case.index("docker compose -p pantheon")
    default_profiles = next(
        line
        for line in root_case.splitlines()
        if line.strip().startswith('PANTHEON_DEV_COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES:-')
    )
    assert "source-ingest-scheduler" not in default_profiles


def test_nonprod_workflow_exposes_only_fixed_bounded_source_refresh() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "run_bounded_source_refresh:" in workflow
    assert "Bounded source refresh requires an isolated clean dev/root deploy with strict auth." in workflow
    assert (
        "PANTHEON_DEV_COMPOSE_PROFILES: ${{ env.BOUNDED_SOURCE_REFRESH_ENABLED == 'true' "
        "&& 'root,source-ingest-scheduler' || 'root' }}"
    ) in workflow
    # The steady posture is allowlist with exactly the collector host when one is
    # configured (deny otherwise); the bounded switch only widens it to the fixed hosts.
    assert (
        "PANTHEON_EXTERNAL_EGRESS: ${{ (env.BOUNDED_SOURCE_REFRESH_ENABLED == 'true' "
        "|| vars.PANTHEON_COLLECTOR_HOST != '') && 'allowlist' || 'deny' }}"
    ) in workflow
    assert (
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS: ${{ env.BOUNDED_SOURCE_REFRESH_ENABLED == 'true' "
        "&& 'openapi.twse.com.tw,www.tpex.org.tw' || vars.PANTHEON_COLLECTOR_HOST }}"
    ) in workflow
    assert "SOURCE_INGEST_BOUNDED_CONNECTOR_ID: tw-twse-tpex-official-market" in workflow
    assert 'SOURCE_INGEST_CONTROLLER_MAX_TICKS: "1"' in workflow
    assert 'SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY: "1"' in workflow
    assert 'SOURCE_INGEST_MAX_RECORDS: "100"' in workflow


def test_default_source_owner_is_unbounded_reconcile_only() -> None:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'SOURCE_REFRESH_CONTROLLER_MODE="reconcile_only"' in deploy
    assert 'SOURCE_REFRESH_TRUTH_LEVEL="scheduled_tick"' in deploy
    assert 'SOURCE_REFRESH_MAX_TICKS="0"' in deploy
    assert 'SOURCE_REFRESH_RESTART_POLICY="unless-stopped"' in deploy
    assert 'SOURCE_INGEST_CONTROLLER_MODE=$(shell_quote "${SOURCE_REFRESH_CONTROLLER_MODE}")' in deploy
    assert 'SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL=$(shell_quote "${SOURCE_REFRESH_TRUTH_LEVEL}")' in deploy
    assert 'SOURCE_INGEST_CONTROLLER_RESTART_POLICY=$(shell_quote "${SOURCE_REFRESH_RESTART_POLICY}")' in deploy
    assert 'info "source_refresh_controller_mode=${SOURCE_REFRESH_CONTROLLER_MODE}"' in deploy
    assert 'info "source_refresh_truth_level=${SOURCE_REFRESH_TRUTH_LEVEL}"' in deploy
    assert 'info "source_refresh_max_ticks=${SOURCE_REFRESH_MAX_TICKS}"' in deploy
    assert 'info "source_refresh_restart_policy=${SOURCE_REFRESH_RESTART_POLICY}"' in deploy

    start = deploy.index("validate_source_refresh_profile() {")
    end = deploy.index("\n}\n\ncurl_with_retry()", start)
    gate = deploy[start:end]
    assert 'SOURCE_INGEST_CONTROLLER_MODE:-}" == "reconcile_only"' in gate
    assert 'SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL:-}" == "scheduled_tick"' in gate
    assert 'SOURCE_INGEST_CONTROLLER_MAX_TICKS:-}" == "0"' in gate
    assert 'SOURCE_INGEST_CONTROLLER_RESTART_POLICY:-}" == "unless-stopped"' in gate


def test_bounded_source_refresh_deploy_waits_and_gates_readback() -> None:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = deploy.index("verify_bounded_source_refresh_readback() {")
    end = deploy.index("\n}\n\nensure_dev_caddy_ingress()", start)
    gate = deploy[start:end]
    assert "wait_for_bounded_source_refresh_service source-ingest-scheduler" in gate
    assert "wait_for_bounded_source_refresh_service source-ingest-agora-projector" in gate
    assert "/api/source-ingest/receipts" in gate
    assert "/api/source-ingest/controller/readback" in gate
    assert "agora_watchlist.json" in gate
    assert "source_timestamp_status" in gate
    assert "sourceTimeStatus" in gate
    assert "ingestRunId" in gate
    assert "SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS" in gate
    assert "/api/source-ingest/snapshots/latest?symbol=" in gate
    assert 'snapshot.get("symbol") != execution_symbol' in gate
    assert "canonical_taiwan_symbol(execution_symbol)" in gate
    assert "active paper snapshot is outside 24h" in gate
    assert "active paper snapshot requires at least two finite official closes" in gate
    assert "len(closes) < 2" in gate
    assert "math.isfinite(float(close))" in gate
    assert "active paper snapshot lacks official exchange lineage" in gate
    assert "if age_seconds < 0:" in gate
    assert "if age_seconds < -300:" not in gate
    # Taiwan market-session freshness gate reuses the shared governed rule
    # instead of a divergent local heuristic, so a valid Friday close is not
    # forced through the flat 24h comparison on a weekend deploy.
    assert "from services.execution.market_snapshot_admission import" in gate
    assert "evaluate_taiwan_market_freshness" in gate
    assert "is_taiwan_symbol(canonical_symbol)" in gate
    assert "failed Taiwan market-session freshness" in gate
    # Passing the exact event/decision clocks into the shared evaluator keeps
    # the deploy gate covered by the same pre-session-close rejection as the
    # producer, fleet reconciler, and readiness surfaces.
    assert "event_time_dt=event_time" in gate
    assert "now_dt=now_dt" in gate
    # The receipt clock is passed to the shared rule as well.  Therefore a
    # same-day snapshot received before the 13:30 close remains fail-closed
    # when this deploy gate evaluates it after the close.
    assert "refresh_receipt_dt=refresh_dt" in gate
    # The deploy gate consumes governed proof from the Source public snapshot;
    # it must not rely on a deploy-only calendar fixture or local heuristic.
    assert 'ev = snapshot.get("calendar_evidence")' in gate
    assert "calendar_evidence=ev" in gate

    root_start = deploy.index("  root)\n")
    root_end = deploy.index("\n  bff)\n", root_start)
    root_case = deploy[root_start:root_end]
    compose_up_pos = (
        root_case.index("run_dev_candidate_compose up -d")
        if "run_dev_candidate_compose up -d" in root_case
        else root_case.index("docker compose -p pantheon -f docker-compose.yml up -d")
    )
    assert compose_up_pos < root_case.index(
        "verify_bounded_source_refresh_readback"
    )
    assert root_case.index("docker compose -p pantheon -f docker-compose.yml build") < root_case.index(
        "resolve_bounded_source_refresh_active_symbols"
    )
    assert root_case.index("resolve_bounded_source_refresh_active_symbols") < compose_up_pos
    assert root_case.index("verify_bounded_source_refresh_readback") < root_case.index(
        "openclaw-configure-shared-model-pool.sh"
    )


def _resolver_body() -> str:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = deploy.index("resolve_bounded_source_refresh_active_symbols() {")
    end = deploy.index("\n}\n\nverify_bounded_source_refresh_readback()", start)
    return deploy[start:end]


def test_bounded_refresh_resolves_symbols_from_read_only_runtime_binding_store() -> None:
    resolver = _resolver_body()

    assert "runtime-manager - <<'PY'" in resolver
    assert "PANTHEON_RUNTIME_BINDING_STORE_PATH" in resolver
    # paper mode filter is still present; active-only check is intentionally
    # removed so market-input-paused bindings can also be included
    assert 'mode != "paper"' in resolver
    assert 'binding.get("symbol") or metadata.get("symbol")' in resolver
    assert "strategy_artifact" not in resolver
    assert 'export SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS="$priority_symbols"' in resolver
    assert "SOURCE_INGEST_MAX_RECORDS" not in resolver


def test_bounded_refresh_includes_market_input_paused_bindings() -> None:
    """Acceptance criterion 1: a binding paused for a market_input_ reason must
    be included in the priority list, not only active bindings."""
    resolver = _resolver_body()

    # The resolver must distinguish paused from active and check reason_code
    assert 'status == "paused"' in resolver
    assert "session_admission" in resolver
    assert "reason_code" in resolver
    assert "_MARKET_INPUT_PAUSE_PREFIX" in resolver
    assert 'reason_code.startswith(_MARKET_INPUT_PAUSE_PREFIX)' in resolver


def test_bounded_refresh_excludes_other_reason_paused_bindings() -> None:
    """Acceptance criterion 2: a binding paused for any non-market_input_ reason
    must still be excluded from the priority list (e.g. operator_requested_pause)."""
    resolver = _resolver_body()

    # The resolver must have an is_priority guard that defaults to False for
    # paused bindings without a market_input_ reason_code
    assert "is_priority" in resolver
    assert "if not is_priority:" in resolver


def test_bounded_refresh_priority_python_snippet_runs_correctly(tmp_path: Path) -> None:
    """End-to-end: run the embedded Python snippet with synthetic bindings and
    verify inclusion/exclusion rules for active, market-input-paused, and
    operator-paused paper bindings."""
    import json
    import subprocess
    import sys

    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    # Extract the Python heredoc between the heredoc open marker and the PY terminator
    heredoc_open = "runtime-manager - <<" + "'PY'"
    py_start = deploy.index(heredoc_open) + len(heredoc_open) + 1  # skip the newline after the marker
    py_end = deploy.index("\nPY\n", py_start)
    snippet = deploy[py_start:py_end]

    bindings = [
        # Active paper TW symbol -- must be included
        {
            "binding_id": "b-active-001",
            "deployment_mode": "paper",
            "status": "active",
            "symbol": "2330.TW",
        },
        # Paused due to market_input_insufficient -- must be included (AC1)
        {
            "binding_id": "b-paused-insufficient",
            "deployment_mode": "paper",
            "status": "paused",
            "symbol": "0050.TW",
            "metadata": {
                "session_admission": {
                    "reason_code": "market_input_insufficient",
                }
            },
        },
        # Paused due to market_input_stale -- must be included (AC1)
        {
            "binding_id": "b-paused-stale",
            "deployment_mode": "paper",
            "status": "paused",
            "symbol": "2317.TW",
            "metadata": {
                "session_admission": {
                    "reason_code": "market_input_stale",
                }
            },
        },
        # Paused due to operator_requested_pause -- must be excluded (AC2)
        {
            "binding_id": "b-paused-operator",
            "deployment_mode": "paper",
            "status": "paused",
            "symbol": "6505.TW",
            "metadata": {
                "session_admission": {
                    "reason_code": "operator_requested_pause",
                }
            },
        },
        # Paused with no session_admission -- must be excluded (AC2)
        {
            "binding_id": "b-paused-no-admission",
            "deployment_mode": "paper",
            "status": "paused",
            "symbol": "1301.TW",
        },
        # Live (non-paper) active -- must be excluded
        {
            "binding_id": "b-live-active",
            "deployment_mode": "live",
            "status": "active",
            "symbol": "2412.TW",
        },
        # Paper but non-TW symbol -- must be excluded by shape filter
        {
            "binding_id": "b-paper-non-tw",
            "deployment_mode": "paper",
            "status": "active",
            "symbol": "AAPL",
        },
    ]

    store = tmp_path / "runtime_bindings.json"
    store.write_text(json.dumps(bindings), encoding="utf-8")

    import os
    full_env = {**os.environ, "PANTHEON_RUNTIME_BINDING_STORE_PATH": str(store)}

    result = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        env=full_env,
    )
    assert result.returncode == 0, f"snippet failed: {result.stderr}"

    output = result.stdout.strip()
    symbols = [s for s in output.split(",") if s] if output else []

    assert "2330.TW" in symbols, "active paper binding must be included"
    assert "0050.TW" in symbols, "market_input_insufficient paused binding must be included"
    assert "2317.TW" in symbols, "market_input_stale paused binding must be included"
    assert "6505.TW" not in symbols, "operator_requested_pause binding must be excluded"
    assert "1301.TW" not in symbols, "paused-no-admission binding must be excluded"
    assert "2412.TW" not in symbols, "live binding must be excluded"
    assert "AAPL" not in symbols, "non-TW symbol must be excluded by shape filter"
def test_bounded_source_refresh_wait_accepts_zero_exit(tmp_path: Path) -> None:
    result = _run_bounded_wait(tmp_path, exit_code=0)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "source-refresh-test-container"


def test_bounded_source_refresh_wait_rejects_nonzero_exit(tmp_path: Path) -> None:
    result = _run_bounded_wait(tmp_path, exit_code=17)

    assert result.returncode != 0
    assert "source-ingest-scheduler exited with code 17" in result.stderr
    assert "worker-boom" in result.stderr
    assert "leakedbearer999" not in result.stderr


def test_root_failure_diagnostics_capture_search_service_and_state_without_env() -> None:
    diagnostics = _failure_diagnostics_function()

    assert 'info "search-svc service logs after failure"' in diagnostics
    assert "logs --no-color --tail=240 search-svc" in diagnostics
    assert "ps -a -q search-svc" in diagnostics
    assert 'info "search-svc container restart and health state after failure"' in diagnostics
    assert "restart_count={{.RestartCount}}" in diagnostics
    assert "health={{if .State.Health}}{{.State.Health.Status}}" in diagnostics
    assert ".Config.Env" not in diagnostics


def _validate_source_refresh_profile_function() -> str:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = deploy.index("validate_source_refresh_profile() {")
    end = deploy.index("\n}\n\ncurl_with_retry()", start) + 2
    return deploy[start:end]


def _run_source_refresh_profile_gate(
    connector_id: str,
    allowed_hosts: str,
    dataset: str = "",
) -> subprocess.CompletedProcess[str]:
    script = """set -euo pipefail
error() {
  echo "$*" >&2
  exit 1
}
""" + _validate_source_refresh_profile_function() + """
export PANTHEON_DEV_COMPOSE_PROFILES="root,source-ingest-scheduler"
export PANTHEON_EXTERNAL_EGRESS="allowlist"
export SOURCE_INGEST_CONTROLLER_MODE="reconcile_and_pull"
export SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL="reconciled_live_proof"
export SOURCE_INGEST_CONTROLLER_RESTART_POLICY="no"
export SOURCE_INGEST_CONTROLLER_MAX_TICKS="1"
export SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY="1"
export SOURCE_INGEST_MAX_RECORDS="100"
export SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS="180"
validate_source_refresh_profile
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] = connector_id
    env["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] = allowed_hosts
    env["SOURCE_INGEST_BOUNDED_DATASET"] = dataset
    return subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
    )


def test_validate_source_refresh_profile_taifex_and_twse_exact_host_contract() -> None:
    gate = _validate_source_refresh_profile_function()
    assert 'required = {"openapi.twse.com.tw", "www.tpex.org.tw"}' in gate
    assert 'required = {"openapi.taifex.com.tw", "www.taifex.com.tw"}' in gate
    assert "if hosts != required:" in gate

    # Accept TAIFEX with exact two hosts in either order
    for valid_taifex in (
        "openapi.taifex.com.tw,www.taifex.com.tw",
        "www.taifex.com.tw,openapi.taifex.com.tw",
    ):
        res = _run_source_refresh_profile_gate(
            "tw-taifex-futures-options-chip", valid_taifex, "taifex_ssf_contracts"
        )
        assert res.returncode == 0, res.stderr

    # Accept TWSE/TPEx with exact two hosts in either order
    for valid_twse in (
        "openapi.twse.com.tw,www.tpex.org.tw",
        "www.tpex.org.tw,openapi.twse.com.tw",
    ):
        res = _run_source_refresh_profile_gate(
            "tw-twse-tpex-official-market", valid_twse, "tw_price_daily"
        )
        assert res.returncode == 0, res.stderr

    # Reject any other host set or wildcard for both connectors
    for connector_id, invalid_hosts in (
        ("tw-taifex-futures-options-chip", "openapi.taifex.com.tw"),
        ("tw-taifex-futures-options-chip", "www.taifex.com.tw"),
        ("tw-taifex-futures-options-chip", "openapi.taifex.com.tw,www.taifex.com.tw,www.twse.com.tw"),
        ("tw-taifex-futures-options-chip", "openapi.twse.com.tw,www.twse.com.tw,www.tpex.org.tw,openapi.taifex.com.tw,www.taifex.com.tw"),
        ("tw-taifex-futures-options-chip", "*.taifex.com.tw,www.taifex.com.tw"),
        ("tw-taifex-futures-options-chip", "*"),
        ("tw-twse-tpex-official-market", "openapi.twse.com.tw,www.twse.com.tw,www.tpex.org.tw"),
        ("tw-twse-tpex-official-market", "openapi.twse.com.tw"),
        ("tw-twse-tpex-official-market", "openapi.twse.com.tw,www.twse.com.tw,www.tpex.org.tw,openapi.taifex.com.tw"),
        ("tw-twse-tpex-official-market", "openapi.twse.com.tw,www.twse.com.tw,www.tpex.org.tw,openapi.taifex.com.tw,www.taifex.com.tw"),
        ("tw-twse-tpex-official-market", "*.twse.com.tw,www.tpex.org.tw"),
    ):
        res = _run_source_refresh_profile_gate(connector_id, invalid_hosts)
        assert res.returncode != 0, f"Expected failure for {connector_id} with {invalid_hosts}"


def test_workflows_dispatch_taifex_ssf_contracts_as_separate_bounded_run_after_twse() -> None:
    import yaml

    refresh_wf_path = ROOT / ".github" / "workflows" / "dev-tw-market-refresh.yml"
    refresh_wf = yaml.safe_load(refresh_wf_path.read_text(encoding="utf-8"))
    refresh_steps = refresh_wf["jobs"]["refresh"]["steps"]
    refresh_by_id = {s.get("id"): (idx, s) for idx, s in enumerate(refresh_steps) if s.get("id")}

    assert "refresh" in refresh_by_id
    assert "taifex_refresh" in refresh_by_id
    twse_idx, twse_step = refresh_by_id["refresh"]
    taifex_idx, taifex_step = refresh_by_id["taifex_refresh"]
    assert taifex_idx < twse_idx, "TAIFEX refresh must run before TWSE refresh"

    assert twse_step["env"]["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] == "tw-twse-tpex-official-market"
    assert twse_step["env"]["SOURCE_INGEST_BOUNDED_DATASET"] == "tw_price_daily"
    assert twse_step["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "openapi.twse.com.tw,www.tpex.org.tw"

    assert taifex_step["env"]["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] == "tw-taifex-futures-options-chip"
    assert taifex_step["env"]["SOURCE_INGEST_BOUNDED_DATASET"] == "taifex_ssf_contracts"
    assert taifex_step["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "openapi.taifex.com.tw,www.taifex.com.tw"
    assert "--connector-id tw-taifex-futures-options-chip" in taifex_step["run"]
    assert "--allowed-hosts openapi.taifex.com.tw,www.taifex.com.tw" in taifex_step["run"]
    assert "--dataset taifex_ssf_contracts" in taifex_step["run"]

    deploy_wf = yaml.safe_load(DEPLOY_WORKFLOW.read_text(encoding="utf-8"))
    deploy_steps = deploy_wf["jobs"]["deploy-dev"]["steps"]
    deploy_by_id = {s.get("id"): (idx, s) for idx, s in enumerate(deploy_steps) if s.get("id")}

    assert "market_refresh" in deploy_by_id
    assert "deploy" in deploy_by_id
    assert "taifex_market_refresh" in deploy_by_id
    assert "bounded_taifex_refresh" in deploy_by_id
    assert "taifex_market_refresh_upload" in deploy_by_id
    assert "paper_bootstrap" in deploy_by_id
    m_twse_idx, m_twse_step = deploy_by_id["market_refresh"]
    d_twse_idx, d_twse_step = deploy_by_id["deploy"]
    m_taifex_idx, m_taifex_step = deploy_by_id["taifex_market_refresh"]
    d_taifex_idx, d_taifex_step = deploy_by_id["bounded_taifex_refresh"]
    taifex_upload_idx, taifex_upload_step = deploy_by_id["taifex_market_refresh_upload"]
    paper_idx, _ = deploy_by_id["paper_bootstrap"]
    assert d_twse_idx < m_twse_idx < m_taifex_idx < d_taifex_idx < taifex_upload_idx < paper_idx, (
        "TWSE market_refresh and TAIFEX refresh must both run after the candidate stack deploy and before the paper baseline"
    )
    assert m_twse_step["env"]["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] == "tw-twse-tpex-official-market"
    assert m_twse_step["env"]["SOURCE_INGEST_BOUNDED_DATASET"] == "tw_price_daily"
    assert m_twse_step["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "openapi.twse.com.tw,www.tpex.org.tw"
    assert m_taifex_step["env"]["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] == "tw-taifex-futures-options-chip"
    assert m_taifex_step["env"]["SOURCE_INGEST_BOUNDED_DATASET"] == "taifex_ssf_contracts"
    assert m_taifex_step["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "openapi.taifex.com.tw,www.taifex.com.tw"
    assert m_taifex_step.get("continue-on-error") is True
    assert "--non-blocking" in m_taifex_step["run"]
    assert "taifex-market-refresh.json" in m_taifex_step["run"]
    assert "if log_text:\n    print(log_text)" in m_taifex_step["run"]

    assert d_twse_step["env"]["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] == "tw-twse-tpex-official-market"
    assert d_twse_step["env"]["SOURCE_INGEST_BOUNDED_DATASET"] == "tw_price_daily"
    assert d_taifex_step["env"]["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] == "tw-taifex-futures-options-chip"
    assert d_taifex_step["env"]["SOURCE_INGEST_BOUNDED_DATASET"] == "taifex_ssf_contracts"
    assert "openapi.taifex.com.tw,www.taifex.com.tw" in d_taifex_step["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"]
    assert d_taifex_step.get("continue-on-error") is True
    assert "--force" in d_taifex_step["run"]
    assert "--non-blocking" in d_taifex_step["run"]
    assert "if log_text:\n    print(log_text)" in d_taifex_step["run"]
    assert "always()" in taifex_upload_step["if"]
    assert taifex_upload_step["with"]["path"].endswith("/taifex-market-refresh.json")
    assert taifex_step.get("continue-on-error") is True
    assert "--non-blocking" in taifex_step["run"]
    assert "if log_text:\n    print(log_text)" in taifex_step["run"]

    # Every workflow step with SOURCE_INGEST_BOUNDED_CONNECTOR_ID must use a single fixed value per run
    for wf_steps in (refresh_steps, deploy_steps):
        for step in wf_steps:
            env_map = step.get("env") or {}
            if "SOURCE_INGEST_BOUNDED_CONNECTOR_ID" in env_map:
                cid = str(env_map["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"])
                assert cid in ("tw-twse-tpex-official-market", "tw-taifex-futures-options-chip", "tw-finmind-broker-daily-report")
                assert "," not in cid
                assert "${{" not in cid


def _assert_no_merged_twse_and_taifex_hosts_in_text(text: str, source_name: str) -> None:
    import re

    for line_no, line in enumerate(text.splitlines(), start=1):
        has_twse = "twse.com.tw" in line or "tpex.org.tw" in line
        has_taifex = "taifex.com.tw" in line
        assert not (has_twse and has_taifex), (
            f"{source_name}:{line_no} merges TWSE/TPEx and TAIFEX hosts on the same line: {line}"
        )
        if any(
            token in line
            for token in (
                "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS",
                "SOURCE_REFRESH_ALLOWED_HOSTS",
                "--allowed-hosts",
            )
        ):
            cleaned_line = re.sub(r"^\s*\*\)\s*", "", line)
            assert "*" not in cleaned_line, f"{source_name}:{line_no} contains wildcard in allowed hosts: {line}"


def test_regression_no_wildcard_no_host_widening_and_never_merged_host_lists() -> None:
    import pytest

    refresh_wf_path = ROOT / ".github" / "workflows" / "dev-tw-market-refresh.yml"
    for path in (DEPLOY_SCRIPT, DEPLOY_WORKFLOW, refresh_wf_path):
        _assert_no_merged_twse_and_taifex_hosts_in_text(path.read_text(encoding="utf-8"), path.name)

    # Verify the regression detector fails if a future change merges the two host lists
    with pytest.raises(AssertionError, match="merges TWSE/TPEx and TAIFEX hosts"):
        _assert_no_merged_twse_and_taifex_hosts_in_text(
            "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS: openapi.twse.com.tw,www.twse.com.tw,www.tpex.org.tw,openapi.taifex.com.tw,www.taifex.com.tw",
            "synthetic-merged-workflow.yml",
        )


def _run_embedded_preflight(
    *,
    now_utc: str,
    twse_snapshot: dict | None,
    taifex_readback_connector: dict | None = None,
    connector_id: str = "tw-taifex-futures-options-chip",
    dataset: str = "taifex_ssf_contracts",
    force: bool = False,
) -> tuple[dict, list[str]]:
    import http.server
    import json
    import os
    import threading
    import urllib.parse

    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    body = script.split("<<'PREFLIGHT_PY'\n", 1)[1].split("\nPREFLIGHT_PY", 1)[0]
    requested_paths: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requested_paths.append(self.path)
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/source-ingest/snapshots/latest":
                payload = json.dumps(twse_snapshot if twse_snapshot is not None else {"detail": "not found"}).encode()
                self.send_response(200 if twse_snapshot is not None else 404)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif parsed.path == "/api/source-ingest/controller/readback":
                connectors = [taifex_readback_connector] if taifex_readback_connector is not None else []
                payload = json.dumps({"connectors": connectors}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "SOURCE_INGEST_API_URL": f"http://127.0.0.1:{server.server_port}",
        "PANTHEON_REFRESH_PREFLIGHT_NOW": now_utc,
        "SOURCE_INGEST_BOUNDED_CONNECTOR_ID": connector_id,
        "SOURCE_INGEST_BOUNDED_DATASET": dataset,
    }
    try:
        proc = subprocess.run(
            ["python3", "-c", f"import sys\nexec(compile({body!r}, 'preflight', 'exec'))\n", "true" if force else "false"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        server.shutdown()
    return json.loads(proc.stdout), requested_paths


def test_taifex_preflight_does_not_skip_on_fresh_twse_snapshot_when_ssf_missing_or_stale() -> None:
    from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence

    fresh_twse_snapshot = {
        "snapshot_id": "snap-2026-11-06",
        "event_time": "2026-11-06T06:30:00Z",
        "observed_at": "2026-11-06T07:05:00Z",
        "calendar_evidence": governed_taiwan_calendar_evidence(venue="TWSE", trade_date="2026-11-06"),
        "lineage": {
            "source_ids": ["tw-official:tw_price_daily:TWSE:2330:receipt-2026-11-06"],
            "connector_ids": ["tw-twse-tpex-official-market"],
        },
    }

    # 1. Fresh TWSE snapshot + missing TAIFEX SSF readback -> must proceed, NOT noop/already_fresh
    res_missing, paths_missing = _run_embedded_preflight(
        now_utc="2026-11-06T08:00:00Z",
        twse_snapshot=fresh_twse_snapshot,
        taifex_readback_connector=None,
    )
    assert res_missing["status"] == "proceed"
    assert res_missing["reason"] == "dataset_missing"
    assert res_missing["connector_id"] == "tw-taifex-futures-options-chip"
    assert res_missing["dataset"] == "taifex_ssf_contracts"
    assert any("/api/source-ingest/controller/readback" in p for p in paths_missing)

    # 2. Fresh TWSE snapshot + TAIFEX readback has taifex_futures_chip instead of taifex_ssf_contracts -> proceed
    futures_chip_connector = {
        "connector_id": "tw-taifex-futures-options-chip",
        "freshness": {"last_success_at": "2026-11-06T07:30:00Z"},
        "latest_source_record": {
            "source_id": "tw-taifex:taifex_futures_chip:TX:foreign_investors:abc",
            "status": "normalized",
            "metadata": {"date": "2026-11-06", "dataset": "taifex_futures_chip", "schema_hash": "tw_taifex_futures_chip.v1"},
            "provenance": {"dataset": "taifex_futures_chip", "schema_hash": "tw_taifex_futures_chip.v1"},
        },
    }
    res_wrong_ds, _ = _run_embedded_preflight(
        now_utc="2026-11-06T08:00:00Z",
        twse_snapshot=fresh_twse_snapshot,
        taifex_readback_connector=futures_chip_connector,
    )
    assert res_wrong_ds["status"] == "proceed"
    assert res_wrong_ds["reason"] == "dataset_missing"

    # 3. Fresh TWSE snapshot + stale TAIFEX SSF readback from previous session -> proceed
    stale_ssf_connector = {
        "connector_id": "tw-taifex-futures-options-chip",
        "freshness": {"last_success_at": "2026-11-05T07:30:00Z"},
        "latest_source_record": {
            "source_id": "tw-taifex:taifex_ssf_contracts:QFF:202611:abc",
            "status": "normalized",
            "metadata": {"date": "2026-11-05", "dataset": "taifex_ssf_contracts", "schema_hash": "tw_taifex_ssf_contracts.v1"},
            "provenance": {"dataset": "taifex_ssf_contracts", "schema_hash": "tw_taifex_ssf_contracts.v1"},
        },
    }
    res_stale, _ = _run_embedded_preflight(
        now_utc="2026-11-06T08:00:00Z",
        twse_snapshot=fresh_twse_snapshot,
        taifex_readback_connector=stale_ssf_connector,
    )
    assert res_stale["status"] == "proceed"
    assert res_stale["reason"] == "closed_session_missing"

    # 4. Fresh TAIFEX SSF readback for closed session -> noop/already_fresh
    fresh_ssf_connector = {
        "connector_id": "tw-taifex-futures-options-chip",
        "freshness": {"last_success_at": "2026-11-06T07:30:00Z"},
        "latest_source_record": {
            "source_id": "tw-taifex:taifex_ssf_contracts:QFF:202611:abc",
            "status": "normalized",
            "metadata": {"date": "2026-11-06", "dataset": "taifex_ssf_contracts", "schema_hash": "tw_taifex_ssf_contracts.v1"},
            "provenance": {"dataset": "taifex_ssf_contracts", "schema_hash": "tw_taifex_ssf_contracts.v1"},
        },
    }
    res_fresh, _ = _run_embedded_preflight(
        now_utc="2026-11-06T08:00:00Z",
        twse_snapshot=fresh_twse_snapshot,
        taifex_readback_connector=fresh_ssf_connector,
    )
    assert res_fresh["status"] == "noop"
    assert res_fresh["reason"] == "already_fresh"
    assert res_fresh["dataset"] == "taifex_ssf_contracts"


def test_taifex_preflight_skips_weekend_holiday_and_recovers_latest_closed_session() -> None:
    from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence

    fresh_friday_twse_snapshot = {
        "snapshot_id": "snap-2026-11-06",
        "event_time": "2026-11-06T06:30:00Z",
        "observed_at": "2026-11-06T07:05:00Z",
        "calendar_evidence": governed_taiwan_calendar_evidence(venue="TWSE", trade_date="2026-11-06"),
        "lineage": {
            "source_ids": ["tw-official:tw_price_daily:TWSE:2330:receipt-2026-11-06"],
            "connector_ids": ["tw-twse-tpex-official-market"],
        },
    }

    # 1. Sunday 2026-11-08 (run 42488948670 timestamp) with missing TAIFEX dataset skips with reason=weekend
    res_sun, paths_sun = _run_embedded_preflight(
        now_utc="2026-11-08T14:21:20Z",
        twse_snapshot=fresh_friday_twse_snapshot,
        taifex_readback_connector=None,
    )
    assert res_sun["status"] == "skipped"
    assert res_sun["reason"] == "weekend"
    assert res_sun["connector_id"] == "tw-taifex-futures-options-chip"
    assert res_sun["dataset"] == "taifex_ssf_contracts"
    assert not any("/api/source-ingest/controller/readback" in p for p in paths_sun)

    # 2. Morning recovery must pull the previous closed session when its dataset is missing.
    res_open, _ = _run_embedded_preflight(
        now_utc="2026-11-06T04:00:00Z",
        twse_snapshot=fresh_friday_twse_snapshot,
        taifex_readback_connector=None,
    )
    assert res_open["status"] == "proceed"
    assert res_open["reason"] == "dataset_missing"
    assert res_open["session_date"] == "2026-11-05"

    # 3. Evidenced Taiwan holiday (2026-10-09 Double Ten observed) skips with reason=holiday
    holiday_prev_snapshot = {
        "snapshot_id": "snap-2026-10-08",
        "event_time": "2026-10-08T06:30:00Z",
        "observed_at": "2026-10-08T07:05:00Z",
        "calendar_evidence": governed_taiwan_calendar_evidence(venue="TWSE", trade_date="2026-10-08"),
        "lineage": {
            "source_ids": ["tw-official:tw_price_daily:TWSE:2330:receipt-2026-10-08"],
            "connector_ids": ["tw-twse-tpex-official-market"],
        },
    }
    res_holiday, _ = _run_embedded_preflight(
        now_utc="2026-10-09T08:00:00Z",
        twse_snapshot=holiday_prev_snapshot,
        taifex_readback_connector=None,
    )
    assert res_holiday["status"] == "skipped"
    assert res_holiday["reason"] == "holiday"


def test_prepare_bounded_source_refresh_dataset_wires_ssf_through_scheduler_and_adapter(
    tmp_path: Path,
) -> None:
    import sys

    runner_code = """
import importlib
import json
import os
import sys
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

tmp_dir = Path(sys.argv[1])
root_dir = Path(sys.argv[2])

data_dir = tmp_dir / "source-ingest-data"
data_dir.mkdir(parents=True, exist_ok=True)
token = "t" * 48
(data_dir / "controller_token").write_text(token, encoding="utf-8")

from services.source_ingestion.controller_state import ControllerStateStore
from services.source_ingestion.controller_worker import _new_state

ctrl_state = _new_state()
ctrl_state.controller_id = "bounded-test:gen"
ctrl_state.tenant_id = "tenant-dev"
ctrl_state.sequence_no = 1
ControllerStateStore(data_dir / "controller_state.json").save(ctrl_state)

os.environ["SOURCE_INGEST_DATA_DIR"] = str(data_dir)
os.environ["SOURCE_INGEST_CONTROLLER_TOKEN"] = token
os.environ["PANTHEON_TENANT_ID"] = "tenant-dev"
os.environ["PANTHEON_REMOTE_DIR"] = str(root_dir)
# Leave SOURCE_INGEST_API_URL unset to exercise default host URL resolution (http://127.0.0.1:18097)
os.environ.pop("SOURCE_INGEST_API_URL", None)

import services.source_ingestion.main as si_main
from services.source_ingestion import controller_worker
from services.source_ingestion.connectors import taiwan_official as tw_official

for attr in list(vars(si_main)):
    if not attr.startswith("__"):
        delattr(si_main, attr)
importlib.reload(si_main)
client = TestClient(si_main.app)

real_urlopen = urllib.request.urlopen
preparation_urls: list[str] = []


class _FakeHTTPResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._buf = BytesIO(body)

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def fake_urlopen(req, timeout=30):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    preparation_urls.append(url)
    if url.startswith("http://127.0.0.1:18097"):
        path = url[len("http://127.0.0.1:18097") :]
        method = getattr(req, "get_method", lambda: "GET")()
        headers = dict(req.header_items()) if hasattr(req, "header_items") else {}
        data = req.data if hasattr(req, "data") else None
        resp = client.request(method, path, headers=headers, content=data)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(
                url, resp.status_code, resp.reason_phrase, resp.headers, BytesIO(resp.content)
            )
        return _FakeHTTPResponse(resp.status_code, resp.content)
    return real_urlopen(req, timeout=timeout)


urllib.request.urlopen = fake_urlopen

deploy = (root_dir / "scripts" / "deploy_nonprod_vm.sh").read_text(encoding="utf-8")
marker = 'python3 - "${connector_id}" "${dataset}" "${active_symbols_csv}" <<\\'PREPARE_DATASET_PY\\'\\n'
start = deploy.index(marker) + len(marker)
end = deploy.index("\\nPREPARE_DATASET_PY\\n", start)
prepare_py = deploy[start:end]

old_argv = list(sys.argv)
try:
    sys.argv = ["prepare_dataset.py", "tw-taifex-futures-options-chip", "taifex_ssf_contracts", "2330.TW"]
    ns: dict = {"__name__": "__main__"}
    exec(compile(prepare_py, "prepare_bounded_source_refresh_dataset", "exec"), ns)
finally:
    sys.argv = old_argv

assert preparation_urls == [
    "http://127.0.0.1:18097/api/source-ingest/connectors",
    "http://127.0.0.1:18097/api/source-ingest/connectors/tw-taifex-futures-options-chip/schedule",
    "http://127.0.0.1:18097/api/source-ingest/active-universe/schedule",
], f"Expected all three preparation requests to target default host URL 18097, got: {preparation_urls}"

frontier_resp = client.get(
    "/api/source-ingest/frontier",
    headers={"Authorization": f"Bearer {token}"},
)
assert frontier_resp.status_code == 200
frontier_items = [
    item
    for item in (frontier_resp.json().get("frontier") or [])
    if item.get("connector_id") == "tw-taifex-futures-options-chip"
]
assert len(frontier_items) == 1
assert frontier_items[0]["job_parameters"]["dataset"] == "taifex_ssf_contracts"
assert frontier_items[0]["job_parameters"]["contracts"] == ["2330"]
assert frontier_items[0]["job_parameters"]["symbols"] == ["2330"]

ssf_fixtures = root_dir / "services" / "execution" / "lean_runtime" / "fixtures" / "taifex_20261106"
seen_urls: list[str] = []


def fake_fetch_official(request, *, caller, timeout_seconds, max_bytes, parse):
    seen_urls.append(request.full_url)
    name = request.full_url.rsplit("/", 1)[-1]
    if name == "stockLists":
        return parse((ssf_fixtures / "stockLists.html").read_text(encoding="utf-8").encode("utf-8"))
    raw = json.loads((ssf_fixtures / f"{name}.json").read_text(encoding="utf-8"))
    return parse(b"\\xef\\xbb\\xbf" + json.dumps(raw).encode("utf-8"))


tw_official._fetch_official = fake_fetch_official


def fake_controller_request_json(url, *, method="GET", payload=None, bearer_token=None, timeout_seconds=30.0):
    path = url[len("http://127.0.0.1:18097") :]
    headers = {"Accept": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    resp = client.request(method, path, headers=headers, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


controller_worker._request_json = fake_controller_request_json

tick_result = controller_worker.run_schedule_tick(
    api_url="http://127.0.0.1:18097",
    max_concurrency=1,
    timeout_seconds=30.0,
    controller_token=token,
    force_connector_ids=("tw-taifex-futures-options-chip",),
    exclusive_connector_ids=("tw-taifex-futures-options-chip",),
)
assert tick_result["summary"]["total_ran"] == 1
assert tick_result["summary"]["total_failed"] == 0

assert seen_urls == [
    "https://openapi.taifex.com.tw/v1/SingleStockFuturesMargining",
    "https://openapi.taifex.com.tw/v1/SingleStockFuturesETFMargining",
    "https://openapi.taifex.com.tw/v1/DailyMarketReportFut",
    "https://openapi.taifex.com.tw/v1/FinalSettlementPriceSSF",
    "https://www.taifex.com.tw/cht/2/stockLists",
]
assert not any("InstitutionalInvestors" in u for u in seen_urls)
assert {u.split("/")[2] for u in seen_urls} == {"openapi.taifex.com.tw", "www.taifex.com.tw"}

readback_resp = client.get(
    "/api/source-ingest/controller/readback",
    headers={"Authorization": f"Bearer {token}"},
)
assert readback_resp.status_code == 200
taifex_row = next(
    row
    for row in (readback_resp.json().get("connectors") or [])
    if row.get("connector_id") == "tw-taifex-futures-options-chip"
)
latest_rec = taifex_row["latest_source_record"]
assert latest_rec["source_id"].startswith("tw-taifex:taifex_ssf_contracts:")
assert latest_rec["provenance"]["dataset"] == "taifex_ssf_contracts"
assert latest_rec["provenance"]["schema_hash"] == "tw_taifex_ssf_contracts.v1"
assert latest_rec["provenance"]["symbol"] == "2330"
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.pop("SOURCE_INGEST_API_URL", None)
    proc = subprocess.run(
        [sys.executable, "-c", runner_code, str(tmp_path), str(ROOT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Subprocess integration test failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


def test_prepare_bounded_source_refresh_dataset_re_resolves_cid_and_fails_closed_on_exec_failures(
    tmp_path: Path,
) -> None:
    import http.server
    import json
    import threading

    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = deploy.index("prepare_bounded_source_refresh_dataset() {")
    end = deploy.index("\n}\n\n# Proves the latest closed Taiwan session", start) + 2
    func_def = deploy[start:end]

    received_requests: list[tuple[str, str, str]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _handle(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            received_requests.append((method, self.path, self.headers.get("Authorization", "")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if self.path == "/api/source-ingest/active-universe/schedule":
                self.wfile.write(
                    json.dumps(
                        {
                            "enqueued_count": 1,
                            "enqueued_items": [
                                {
                                    "connector_id": "tw-taifex-futures-options-chip",
                                    "job_parameters": {
                                        "connector_id": "tw-taifex-futures-options-chip",
                                        "dataset": "taifex_ssf_contracts",
                                        "contracts": ["2330"],
                                        "symbols": ["2330"],
                                    },
                                }
                            ],
                        }
                    ).encode("utf-8")
                )
            else:
                self.wfile.write(json.dumps({"ok": True, "echo": body}).encode("utf-8"))

        def do_POST(self) -> None:
            self._handle("POST")

        def do_PUT(self) -> None:
            self._handle("PUT")

        def log_message(self, *args) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    state_path = tmp_path / "stub_state.json"
    written_desired = tmp_path / "written_desired.json"

    mock_docker = bin_dir / "docker"
    mock_docker.write_text(
        f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path

state = json.loads(Path({str(state_path)!r}).read_text(encoding="utf-8"))
args = sys.argv[1:]
if args[0] == "compose" and "ps" in args and args[-1] == "source-ingest":
    print(state.get("live_cid", ""))
    sys.exit(0)
if args[0] == "exec":
    cid = args[2] if args[1] == "-i" else args[1]
    cmd = " ".join(args[3:] if args[1] == "-i" else args[2:])
    if cid != state.get("live_cid"):
        sys.stderr.write(f"Error: No such container: {{cid}}\\n")
        sys.exit(1)
    if "controller_token" in cmd:
        if state.get("fail_token_exec"):
            sys.stderr.write("exec token read failed\\n")
            sys.exit(1)
        print(state.get("token", ""))
        sys.exit(0)
    if "desired_state.json" in cmd:
        if state.get("fail_desired_write"):
            sys.stderr.write("exec desired_state write failed\\n")
            sys.exit(1)
        data = sys.stdin.read()
        if not data.strip():
            sys.exit(1)
        Path({str(written_desired)!r}).write_text(data, encoding="utf-8")
        sys.exit(0)
sys.exit(1)
""",
        encoding="utf-8",
    )
    mock_docker.chmod(0o755)

    def run_prepare(state_obj: dict, passed_cid: str = "obsolete-cid-before-recreate") -> subprocess.CompletedProcess[str]:
        state_path.write_text(json.dumps(state_obj), encoding="utf-8")
        script = f"""set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}
{func_def}
prepare_bounded_source_refresh_dataset "tw-taifex-futures-options-chip" "taifex_ssf_contracts" "2330.TW" "{passed_cid}"
"""
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        env["PYTHONPATH"] = str(ROOT)
        env["PANTHEON_REMOTE_DIR"] = str(ROOT)
        env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{server.server_port}"
        env.pop("SOURCE_INGEST_CONTROLLER_TOKEN", None)
        env.pop("SOURCE_INGEST_CONTROLLER_TOKEN_FILE", None)
        return subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    try:
        # 1. Obsolete passed cid is overridden by live compose ps cid; token read and desired_state write succeed
        res_ok = run_prepare({"live_cid": "new-recreated-cid", "token": "tok-live-999"})
        assert res_ok.returncode == 0, res_ok.stderr
        assert "prepared bounded source refresh dataset taifex_ssf_contracts" in res_ok.stdout
        assert written_desired.exists()
        desired_obj = json.loads(written_desired.read_text(encoding="utf-8"))
        assert any(
            r.get("dataset") == "taifex_ssf_contracts"
            for r in desired_obj["personas"][0]["required_data_sources"]
        )
        assert all(auth == "Bearer tok-live-999" for _, _, auth in received_requests)

        # 2. Token docker exec failure must fail closed (non-zero) and never report prepared
        res_tok_fail = run_prepare({"live_cid": "new-recreated-cid", "fail_token_exec": True})
        assert res_tok_fail.returncode != 0
        assert "failed to read controller_token" in res_tok_fail.stderr
        assert "prepared bounded source refresh dataset" not in res_tok_fail.stdout

        # 3. Empty controller token must fail closed
        res_tok_empty = run_prepare({"live_cid": "new-recreated-cid", "token": ""})
        assert res_tok_empty.returncode != 0
        assert "empty controller_token" in res_tok_empty.stderr
        assert "prepared bounded source refresh dataset" not in res_tok_empty.stdout

        # 4. Desired-state docker exec write failure must fail closed (no || true swallow)
        res_write_fail = run_prepare(
            {"live_cid": "new-recreated-cid", "token": "tok-live-999", "fail_desired_write": True}
        )
        assert res_write_fail.returncode != 0
        assert "failed to write bounded desired_state.json" in res_write_fail.stderr
        assert "prepared bounded source refresh dataset" not in res_write_fail.stdout
    finally:
        server.shutdown()
