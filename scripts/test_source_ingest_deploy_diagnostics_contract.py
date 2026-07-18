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


def test_root_failure_diagnostics_capture_search_service_and_state_without_env() -> None:
    diagnostics = _failure_diagnostics_function()

    assert 'info "search-svc service logs after failure"' in diagnostics
    assert "logs --no-color --tail=240 search-svc" in diagnostics
    assert "ps -a -q search-svc" in diagnostics
    assert 'info "search-svc container restart and health state after failure"' in diagnostics
    assert "restart_count={{.RestartCount}}" in diagnostics
    assert "health={{if .State.Health}}{{.State.Health.Status}}" in diagnostics
    assert ".Config.Env" not in diagnostics
