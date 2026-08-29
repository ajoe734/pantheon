import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts/deploy_nonprod_vm.sh"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"


def _failure_diagnostics_function() -> str:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = deploy.index("dump_dev_root_failure_diagnostics() {")
    end = deploy.index("\n}\n\nverify_dev_evolution_daily_sweep()", start)
    return deploy[start:end]


def _bounded_wait_function() -> str:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = deploy.index("wait_for_bounded_source_refresh_service() {")
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
    assert 'required = {"openapi.twse.com.tw", "www.twse.com.tw", "www.tpex.org.tw"}' in gate
    assert 'export SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS="${SOURCE_INGEST_BOUNDED_CONNECTOR_ID}"' in gate
    assert 'export SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS="${SOURCE_INGEST_BOUNDED_CONNECTOR_ID}"' in gate
    assert "from services.external_egress import allowed_hosts" in gate

    assert 'SOURCE_REFRESH_CONNECTOR_ID}" == "tw-twse-tpex-official-market"' in deploy
    assert 'SOURCE_REFRESH_ALLOWED_HOSTS:+${SOURCE_REFRESH_ALLOWED_HOSTS},}www.twse.com.tw' in deploy

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
        "&& 'openclaw,source-ingest-scheduler' || 'openclaw' }}"
    ) in workflow
    assert (
        "PANTHEON_EXTERNAL_EGRESS: ${{ env.BOUNDED_SOURCE_REFRESH_ENABLED == 'true' "
        "&& 'allowlist' || 'deny' }}"
    ) in workflow
    assert (
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS: ${{ env.BOUNDED_SOURCE_REFRESH_ENABLED == 'true' "
        "&& 'openapi.twse.com.tw,www.tpex.org.tw' || '' }}"
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
    # The deploy gate consumes governed proof from the Source public snapshot;
    # it must not rely on a deploy-only calendar fixture or local heuristic.
    assert 'ev = snapshot.get("calendar_evidence")' in gate
    assert "calendar_evidence=ev" in gate

    root_start = deploy.index("  root)\n")
    root_end = deploy.index("\n  bff)\n", root_start)
    root_case = deploy[root_start:root_end]
    assert root_case.index("docker compose -p pantheon -f docker-compose.yml up -d") < root_case.index(
        "verify_bounded_source_refresh_readback"
    )
    assert root_case.index("docker compose -p pantheon -f docker-compose.yml build") < root_case.index(
        "resolve_bounded_source_refresh_active_symbols"
    )
    assert root_case.index("resolve_bounded_source_refresh_active_symbols") < root_case.index(
        "docker compose -p pantheon -f docker-compose.yml up -d"
    )
    assert root_case.index("verify_bounded_source_refresh_readback") < root_case.index(
        "openclaw-configure-shared-model-pool.sh"
    )


def test_bounded_refresh_resolves_symbols_from_read_only_runtime_binding_store() -> None:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = deploy.index("resolve_bounded_source_refresh_active_symbols() {")
    end = deploy.index("\n}\n\nverify_bounded_source_refresh_readback()", start)
    resolver = deploy[start:end]

    assert "runtime-manager - <<'PY'" in resolver
    assert "PANTHEON_RUNTIME_BINDING_STORE_PATH" in resolver
    assert 'mode != "paper" or status != "active"' in resolver
    assert 'binding.get("symbol") or metadata.get("symbol")' in resolver
    assert "strategy_artifact" not in resolver
    assert 'export SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS="$priority_symbols"' in resolver
    assert "SOURCE_INGEST_MAX_RECORDS" not in resolver


def test_bounded_source_refresh_wait_accepts_zero_exit(tmp_path: Path) -> None:
    result = _run_bounded_wait(tmp_path, exit_code=0)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "source-refresh-test-container"


def test_bounded_source_refresh_wait_rejects_nonzero_exit(tmp_path: Path) -> None:
    result = _run_bounded_wait(tmp_path, exit_code=17)

    assert result.returncode != 0
    assert "source-ingest-scheduler exited with code 17" in result.stderr


def test_root_failure_diagnostics_capture_search_service_and_state_without_env() -> None:
    diagnostics = _failure_diagnostics_function()

    assert 'info "search-svc service logs after failure"' in diagnostics
    assert "logs --no-color --tail=240 search-svc" in diagnostics
    assert "ps -a -q search-svc" in diagnostics
    assert 'info "search-svc container restart and health state after failure"' in diagnostics
    assert "restart_count={{.RestartCount}}" in diagnostics
    assert "health={{if .State.Health}}{{.State.Health.Status}}" in diagnostics
    assert ".Config.Env" not in diagnostics
