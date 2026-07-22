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


def test_bounded_source_refresh_profile_is_fail_closed() -> None:
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = deploy.index("validate_source_refresh_profile() {")
    end = deploy.index("\n}\n\ncurl_with_retry()", start)
    gate = deploy[start:end]

    assert 'PANTHEON_EXTERNAL_EGRESS:-deny}" == "allowlist"' in gate
    assert "requires a reviewed exact host allowlist" in gate
    assert "SOURCE_INGEST_CONTROLLER_MAX_TICKS >= 1" in gate
    assert "SOURCE_INGEST_CONTROLLER_MAX_TICKS <= 24" in gate
    assert "SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY <= 4" in gate
    assert "SOURCE_INGEST_MAX_RECORDS <= 500" in gate
    assert "from services.external_egress import allowed_hosts" in gate

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


def test_root_failure_diagnostics_capture_search_service_and_state_without_env() -> None:
    diagnostics = _failure_diagnostics_function()

    assert 'info "search-svc service logs after failure"' in diagnostics
    assert "logs --no-color --tail=240 search-svc" in diagnostics
    assert "ps -a -q search-svc" in diagnostics
    assert 'info "search-svc container restart and health state after failure"' in diagnostics
    assert "restart_count={{.RestartCount}}" in diagnostics
    assert "health={{if .State.Health}}{{.State.Health.Status}}" in diagnostics
    assert ".Config.Env" not in diagnostics
