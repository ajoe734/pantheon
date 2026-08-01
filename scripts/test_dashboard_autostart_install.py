from __future__ import annotations

from pathlib import Path

from dashboard_autostart_install import (
    CRON_TAG,
    SERVICE_NAME,
    TIMER_NAME,
    render_cron_line,
    render_systemd_service,
    render_systemd_timer,
)


def test_systemd_service_preserves_dashboard_children() -> None:
    repo = Path("/home/lupin/pantheon repo")

    unit = render_systemd_service(repo)

    assert "Description=Pantheon dashboard recovery probe" in unit
    assert "Type=oneshot" in unit
    assert "KillMode=process" in unit
    assert 'WorkingDirectory="/home/lupin/pantheon repo"' in unit
    assert 'Environment="PANTHEON_DASHBOARD_ROOT=/home/lupin/pantheon repo"' in unit
    assert 'ExecStart="/home/lupin/pantheon repo/scripts/dashboard_autostart.sh"' in unit


def test_systemd_timer_runs_after_boot_and_every_minute() -> None:
    timer = render_systemd_timer()

    assert f"Unit={SERVICE_NAME}" in timer
    assert "OnBootSec=30s" in timer
    assert "OnCalendar=*-*-* *:*:00" in timer
    assert "Persistent=true" in timer
    assert TIMER_NAME == "pantheon-dashboard-autostart.timer"


def test_cron_line_is_idempotently_tagged_and_shell_quoted() -> None:
    repo = Path("/home/lupin/pantheon repo")

    line = render_cron_line(repo)

    assert line.startswith("* * * * * cd '/home/lupin/pantheon repo'")
    assert "env 'PANTHEON_DASHBOARD_ROOT=/home/lupin/pantheon repo'" in line
    assert "bash '/home/lupin/pantheon repo/scripts/dashboard_autostart.sh'" in line
    assert ".orchestrator/logs/dashboard-autostart-cron.log" in line
    assert line.endswith(CRON_TAG)
