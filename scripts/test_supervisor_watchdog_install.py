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
    assert 'WorkingDirectory="/tmp/pantheon repo"' in unit
    assert 'ExecStart="/tmp/pantheon repo/scripts/run-supervisor-watchdog.sh" --restart' in unit


def test_render_systemd_timer_runs_every_minute() -> None:
    timer = render_systemd_timer()

    assert f"Unit={SERVICE_NAME}" in timer
    assert "OnBootSec=30s" in timer
    assert "OnUnitActiveSec=60s" in timer
    assert "Persistent=true" in timer
    assert TIMER_NAME == "pantheon-supervisor-watchdog.timer"


def test_render_cron_line_is_idempotently_tagged() -> None:
    repo = Path("/home/lupin/code/pantheon")

    line = render_cron_line(repo)

    # 5 cron time fields (minute hour dom month dow); the missing field made
    # the entry malformed and cron rejected it.
    assert line.startswith("* * * * * cd /home/lupin/code/pantheon")
    assert line.split("cd ", 1)[0].split() == ["*", "*", "*", "*", "*"]
    assert "scripts/run-supervisor-watchdog.sh --restart" in line
    assert ".orchestrator/logs/supervisor-watchdog-cron.log" in line
    assert line.endswith(CRON_TAG)
