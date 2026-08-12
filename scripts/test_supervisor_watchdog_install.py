from __future__ import annotations

from pathlib import Path

from supervisor_watchdog_install import (
    CRON_TAG,
    SERVICE_NAME,
    TIMER_NAME,
    render_cron_line,
    render_systemd_service,
    render_systemd_timer,
)


def test_render_systemd_service_points_at_repo_watchdog() -> None:
    repo = Path("/tmp/pantheon repo")

    unit = render_systemd_service(repo)

    assert "Description=Pantheon supervisor watchdog" in unit
    assert "Type=oneshot" in unit
    assert "KillMode=process" in unit
    assert 'WorkingDirectory="/tmp/pantheon repo"' in unit
    assert 'ExecStart="/tmp/pantheon repo/scripts/run-supervisor-watchdog.sh" --restart' in unit


def test_render_systemd_service_pins_explicit_live_config() -> None:
    repo = Path("/tmp/pantheon repo")
    config = Path("/tmp/pantheon runtime/live supervisor.json")

    unit = render_systemd_service(repo, config)

    assert (
        'ExecStart="/tmp/pantheon repo/scripts/run-supervisor-watchdog.sh" '
        '--restart --config "/tmp/pantheon runtime/live supervisor.json"'
    ) in unit


def test_render_systemd_service_loads_public_verifier_environment() -> None:
    repo = Path("/tmp/pantheon repo")
    authority = Path("/tmp/pantheon runtime/supervisor public.env")

    unit = render_systemd_service(repo, authority_env_file=authority)

    assert 'EnvironmentFile="/tmp/pantheon runtime/supervisor public.env"' in unit
    assert (
        'Environment=PANTHEON_SUPERVISOR_VERIFIER_ENV_FILE="/tmp/pantheon runtime/supervisor public.env"'
        in unit
    )


def test_render_systemd_timer_runs_every_minute() -> None:
    timer = render_systemd_timer()

    assert f"Unit={SERVICE_NAME}" in timer
    assert "OnBootSec=30s" in timer
    assert "OnUnitActiveSec=60s" in timer
    assert "Persistent=true" in timer
    assert TIMER_NAME == "pantheon-supervisor-watchdog.timer"


def test_render_cron_line_is_idempotently_tagged() -> None:
    repo = Path("/home/lupin/pantheon")

    line = render_cron_line(repo)

    assert line.startswith("* * * * * cd /home/lupin/pantheon")
    assert line.split("cd ", 1)[0].split() == ["*", "*", "*", "*", "*"]
    assert "scripts/run-supervisor-watchdog.sh --restart" in line
    assert ".orchestrator/logs/supervisor-watchdog-cron.log" in line
    assert line.endswith(CRON_TAG)


def test_render_cron_line_pins_shell_quoted_live_config() -> None:
    repo = Path("/home/lupin/pantheon dev")
    config = Path("/home/lupin/pantheon runtime/live supervisor.json")

    line = render_cron_line(repo, config)

    assert "cd '/home/lupin/pantheon dev'" in line
    assert "--config '/home/lupin/pantheon runtime/live supervisor.json'" in line


def test_render_cron_line_loads_public_verifier_environment() -> None:
    repo = Path("/home/lupin/pantheon")
    authority = Path("/home/lupin/runtime/supervisor public.env")

    line = render_cron_line(repo, authority_env_file=authority)

    assert (
        "PANTHEON_SUPERVISOR_VERIFIER_ENV_FILE='/home/lupin/runtime/supervisor public.env' "
        "bash scripts/run-supervisor-watchdog.sh"
        in line
    )
