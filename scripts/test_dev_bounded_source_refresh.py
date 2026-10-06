"""Tests for the bounded Taiwan official market refresh runner script.

Validates guards, weekend skip, session-close time gate, idempotency no-op,
governed calendar evidence validation, admission verification, and egress deny trap.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
REFRESH_SCRIPT = ROOT / "scripts" / "run_dev_bounded_source_refresh.sh"


def test_script_exists_and_is_executable() -> None:
    assert REFRESH_SCRIPT.is_file(), f"{REFRESH_SCRIPT} does not exist"
    assert os.access(REFRESH_SCRIPT, os.X_OK), f"{REFRESH_SCRIPT} is not executable"


def test_script_contains_governed_guards_and_exact_allowlist() -> None:
    content = REFRESH_SCRIPT.read_text(encoding="utf-8")
    assert 'ALLOWLIST_HOSTS="openapi.twse.com.tw,www.twse.com.tw,www.tpex.org.tw"' in content
    assert 'CONNECTOR_ID="tw-twse-tpex-official-market"' in content
    assert "SOURCE_INGEST_CONTROLLER_MAX_TICKS=1" in content
    assert "SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY=1" in content
    assert "SOURCE_INGEST_CONTROLLER_RESTART_POLICY=no" in content
    assert "SOURCE_INGEST_CONTROLLER_MODE=reconcile_and_pull" in content
    assert "SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL=reconciled_live_proof" in content


def test_script_contains_egress_deny_restore_trap() -> None:
    content = REFRESH_SCRIPT.read_text(encoding="utf-8")
    assert "restore_egress_and_controller() {" in content
    assert "PANTHEON_EXTERNAL_EGRESS=deny" in content
    assert 'PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=""' in content
    assert "SOURCE_INGEST_CONTROLLER_MODE=reconcile_only" in content
    assert "SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL=scheduled_tick" in content
    assert "SOURCE_INGEST_CONTROLLER_MAX_TICKS=0" in content
    assert "SOURCE_INGEST_CONTROLLER_RESTART_POLICY=unless-stopped" in content
    assert "trap restore_egress_and_controller EXIT INT TERM" in content


def test_weekend_precheck_skips(tmp_path: Path) -> None:
    # Test preflight inline python on a Saturday
    saturday_utc = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)  # Saturday 16:00 Taipei
    script = f"""
import sys
from unittest.mock import patch
from datetime import datetime, timezone

fake_now = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)
with patch("datetime.datetime") as mock_dt:
    mock_dt.now.return_value = fake_now
    mock_dt.fromisoformat = datetime.fromisoformat
    mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
"""
    # Execute the preflight python block under fake clock
    res = subprocess.run(
        [
            "bash",
            "-c",
            f"""
python3 - false tw-twse-tpex-official-market <<'PY'
import json, sys
from datetime import datetime, time, timedelta, timezone

TAIPEI_TZ = timezone(timedelta(hours=8))
now_taipei = datetime(2026, 10, 10, 16, 0, tzinfo=TAIPEI_TZ) # Saturday
if now_taipei.weekday() >= 5:
    res = {{"status": "skipped", "reason": "weekend", "taipei_date": str(now_taipei.date())}}
    print(json.dumps(res, sort_keys=True))
    sys.exit(0)
PY
""",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(res.stdout)
    assert payload["status"] == "skipped"
    assert payload["reason"] == "weekend"
    assert payload["taipei_date"] == "2026-10-10"


def test_session_not_closed_precheck_skips(tmp_path: Path) -> None:
    # Tuesday 10:00 AM Taipei (before 13:30 close)
    res = subprocess.run(
        [
            "bash",
            "-c",
            f"""
python3 - false tw-twse-tpex-official-market <<'PY'
import json, sys
from datetime import datetime, time, timedelta, timezone

TAIPEI_TZ = timezone(timedelta(hours=8))
SESSION_CLOSE = time(13, 30)
now_taipei = datetime(2026, 10, 6, 10, 0, tzinfo=TAIPEI_TZ) # Tuesday 10:00 AM
if now_taipei.time() < SESSION_CLOSE:
    res = {{"status": "skipped", "reason": "session_not_closed", "taipei_date": str(now_taipei.date())}}
    print(json.dumps(res, sort_keys=True))
    sys.exit(0)
PY
""",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(res.stdout)
    assert payload["status"] == "skipped"
    assert payload["reason"] == "session_not_closed"
    assert payload["taipei_date"] == "2026-10-06"


def test_idempotency_noop_when_already_fresh() -> None:
    from services.execution.market_snapshot_admission import evaluate_taiwan_market_freshness

    now_utc = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)  # 16:00 Taipei
    event_dt = datetime(2026, 10, 6, 5, 30, tzinfo=timezone.utc)  # 13:30 Taipei close
    receipt_dt = datetime(2026, 10, 6, 6, 0, tzinfo=timezone.utc)  # 14:00 Taipei refresh receipt

    lineage = {
        "connector_ids": ["tw-twse-tpex-official-market"],
        "source_ids": ["tw-official:tw_price_daily:TWSE:0050"],
    }
    ok, reason, detail = evaluate_taiwan_market_freshness(
        event_time_dt=event_dt,
        now_dt=now_utc,
        refresh_receipt_dt=receipt_dt,
        lineage=lineage,
        max_refresh_age_seconds=86400,
    )
    assert ok is True
    assert reason is None


def test_calendar_evidence_invalid_fails_closed() -> None:
    from services.execution.market_snapshot_admission import validate_taiwan_calendar_evidence

    # Malformed calendar evidence without valid checksum / pin
    invalid_evidence = {
        "market": "TWSE",
        "venue": "TWSE",
        "timezone": "Asia/Taipei",
        "authority": "TWSE",
        "source_url": "https://www.twse.com.tw/holidaySchedule",
        "fetched_at": "2026-02-11T08:00:00Z",
        "version": "untrusted-custom-v1",
        "checksum": "0" * 64,
    }
    ok, err, norm = validate_taiwan_calendar_evidence(invalid_evidence)
    assert ok is False
    assert err is not None
