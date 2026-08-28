from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts/deploy_nonprod_vm.sh"


def _failure_diagnostics_function() -> str:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = deploy.index("dump_dev_root_failure_diagnostics() {")
    end = deploy.index("\n}\n\nverify_dev_evolution_daily_sweep()", start)
    return deploy[start:end]


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


def test_root_deploy_rejects_the_second_source_controller_profile() -> None:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = deploy.index("validate_source_refresh_profile() {")
    end = deploy.index("\n}\n\ncurl_with_retry()", start)
    gate = deploy[start:end]

    assert "already the durable controller" in gate
    assert "run_bounded_source_ingest_refresh.sh" in gate
    assert 'SOURCE_INGEST_CONTROLLER_MODE:-}" == "reconcile_and_pull"' not in gate
    assert 'PANTHEON_EXTERNAL_EGRESS:-deny}" == "allowlist"' not in gate

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


def test_root_deploy_has_no_second_controller_refresh_or_readback_phase() -> None:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "wait_for_bounded_source_refresh_service" not in deploy
    assert "verify_bounded_source_refresh_readback" not in deploy
    assert "source_refresh_deploy_started_at" not in deploy


def test_root_failure_diagnostics_capture_search_service_and_state_without_env() -> None:
    diagnostics = _failure_diagnostics_function()

    assert 'info "search-svc service logs after failure"' in diagnostics
    assert "logs --no-color --tail=240 search-svc" in diagnostics
    assert "ps -a -q search-svc" in diagnostics
    assert 'info "search-svc container restart and health state after failure"' in diagnostics
    assert "restart_count={{.RestartCount}}" in diagnostics
    assert "health={{if .State.Health}}{{.State.Health.Status}}" in diagnostics
    assert ".Config.Env" not in diagnostics
