"""Contract tests for the one-owner bounded Source Ingestion refresh command."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_bounded_source_ingest_refresh.sh"


def test_refresh_is_sent_through_the_running_controller_and_restores_egress() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'SCHEDULER_SERVICE="source-ingest-scheduler"' in text
    assert "compose exec -T" in text
    assert "run_schedule_tick" in text
    assert "docker compose run" not in text
    assert "PANTHEON_EXTERNAL_EGRESS=allowlist" in text
    assert "PANTHEON_EXTERNAL_EGRESS=deny" in text
    assert "trap cleanup EXIT INT TERM" in text
    assert "start_egress_failsafe" in text
    assert "cancel_egress_failsafe" in text
    assert "run_with_dev_environment_lease.sh" in text


def test_refresh_refuses_to_run_without_the_dev_lease_context() -> None:
    env = dict(os.environ)
    env["TARGET_ENV"] = "dev"
    env.pop("PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE", None)
    env.pop("PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE", None)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "run this command through run_with_dev_environment_lease.sh" in result.stderr
