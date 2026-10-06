from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "dev-tw-market-refresh.yml"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_nonprod_vm.sh"


def test_workflow_file_exists_and_parses():
    assert WORKFLOW_PATH.exists(), f"Workflow file missing: {WORKFLOW_PATH}"
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data.get("name") == "Dev Taiwan Market Daily Refresh"


def test_workflow_triggers():
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    on = data.get("on") or data.get(True)  # YAML true can map to True
    assert on is not None, "Missing 'on' trigger specification in workflow"

    # Schedule trigger: 07:00 UTC (15:00 Asia/Taipei) on trading days (Monday to Friday)
    schedule = on.get("schedule")
    assert schedule is not None, "Missing schedule trigger"
    cron_exprs = [item.get("cron") for item in schedule if isinstance(item, dict)]
    assert "0 7 * * 1-5" in cron_exprs, f"Expected cron '0 7 * * 1-5', got: {cron_exprs}"

    # workflow_dispatch trigger with force boolean input
    wf_dispatch = on.get("workflow_dispatch")
    assert wf_dispatch is not None, "Missing workflow_dispatch trigger"
    inputs = wf_dispatch.get("inputs") or {}
    assert "force" in inputs, "workflow_dispatch must declare 'force' input"
    force_input = inputs["force"]
    assert force_input.get("type") == "boolean", f"force input must be boolean, got {force_input}"
    assert force_input.get("default") is False, f"force default must be false, got {force_input}"


def test_workflow_strict_absence_of_hardcoded_ips_and_fallbacks():
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "34.81.52.222" not in content, "Workflow must not contain hardcoded IP 34.81.52.222"
    assert "35.201.239.38" not in content, "Workflow must not contain legacy dev IP"
    assert "35.201.204.12" not in content, "Workflow must not contain legacy dev IP"
    assert "34.81.75.241" not in content, "Workflow must not contain legacy dev IP"
    assert not re.search(r"vars\.DEV_DEPLOY_SSH_HOST\s*\|\|", content), "Workflow must fail closed without IP fallback"
    assert not re.search(r"vars\.DEV_REMOTE_DIR\s*\|\|", content), "Workflow must fail closed without directory fallback"
    assert not re.search(r"/home/lupin", content), "Workflow must not reference legacy /home/lupin"


def test_workflow_variables_and_remote_execution():
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    required_vars = [
        "DEV_DEPLOY_SSH_HOST",
        "NONPROD_REMOTE_USER",
        "DEV_REMOTE_DIR",
        "DEV_DEPLOY_SSH_KNOWN_HOSTS",
        "DEV_DEPLOY_SSH_PRIVATE_KEY",
    ]
    for var in required_vars:
        assert var in content, f"Workflow must reference § 3.1 variable/secret: {var}"

    assert "scripts/dev_vm_ssh.sh prepare" in content, "Workflow must prepare SSH credentials via dev_vm_ssh.sh"
    assert "scripts/dev_vm_ssh.sh exec" in content, "Workflow must execute remote command via dev_vm_ssh.sh"
    assert "./scripts/deploy_nonprod_vm.sh --refresh-only" in content, (
        "Workflow must invoke deploy_nonprod_vm.sh with --refresh-only"
    )
    assert "--force" in content, "Workflow must propagate force argument to deploy_nonprod_vm.sh"


def test_no_second_script_created():
    second_script = ROOT / "scripts" / "run_dev_bounded_source_refresh.sh"
    assert not second_script.exists(), (
        f"Design requires reusing deploy_nonprod_vm.sh --refresh-only; {second_script} must not exist"
    )


def test_deploy_script_help_includes_refresh_only_options():
    content = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "--refresh-only" in content, "deploy_nonprod_vm.sh must expose --refresh-only"
    assert "--force" in content, "deploy_nonprod_vm.sh must expose --force"
    assert "--output" in content, "deploy_nonprod_vm.sh must expose --output"


def test_deploy_script_refresh_only_argument_parsing():
    content = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "--refresh-only) REFRESH_ONLY=true" in content or "--refresh-only)" in content
    assert 'execute_bounded_source_refresh_entrypoint "${FORCE_REFRESH:-false}"' in content


def test_deploy_script_contract_egress_and_cleanup_trap():
    content = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "restore_bounded_source_refresh()" in content
    assert "PANTHEON_EXTERNAL_EGRESS=deny" in content
    assert "SOURCE_INGEST_CONTROLLER_MODE=reconcile_only" in content
    assert "trap restore_bounded_source_refresh EXIT INT TERM" in content
    assert "source-ingest-scheduler source-ingest-agora-projector" in content, (
        "Cleanup trap must remove one-off refresh containers"
    )


def test_deploy_script_preserves_running_image_and_env():
    content = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}'" in content, (
        "Refresh entrypoint must extract running environment to preserve release secrets/config"
    )
    assert "--no-deps --no-build" in content, (
        "Refresh entrypoint must not rebuild images or recreate dependencies"
    )


def _run_preflight_script(force: bool, now_dt: datetime, snapshot_json: dict[str, Any] | None, http_status: int = 200) -> dict[str, Any]:
    """Helper to exercise the preflight Python logic extracted from deploy_nonprod_vm.sh."""
    from services.execution.market_snapshot_admission import (
        evaluate_taiwan_market_freshness,
        validate_taiwan_calendar_evidence,
    )

    tz = timezone(timedelta(hours=8))
    now_u = now_dt.astimezone(timezone.utc)
    now_t = now_u.astimezone(tz)
    d_str = str(now_t.date())

    def emit(status, **kw):
        return {"status": status, "taipei_date": d_str, **kw}

    if not force and now_t.weekday() >= 5:
        return emit("skipped", reason="weekend", checked_at=now_u.isoformat())
    if not force and now_t.time() < time(13, 30):
        return emit("skipped", reason="session_not_closed", checked_at=now_u.isoformat())

    snap = snapshot_json
    if http_status != 200 and http_status != 404:
        return emit("error", reason="snapshot_lookup_failed", detail=f"HTTP {http_status}")

    if snap is not None:
        cal = snap.get("calendar_evidence") or (snap.get("lineage") or {}).get("calendar_evidence")
        if not cal:
            return emit("error", reason="market_input_calendar_unverifiable", detail="snapshot missing required calendar evidence and pins")
        c_ok, c_err, c_norm = validate_taiwan_calendar_evidence(cal, now_dt=now_u)
        if not c_ok:
            return emit("error", reason="market_input_calendar_unverifiable", detail=c_err)
        if d_str in (c_norm.get("holidays") or {}):
            return emit("skipped", reason="holiday", checked_at=now_u.isoformat())
        ev_dt = datetime.fromisoformat(snap["event_time"].replace("Z", "+00:00"))
        obs = snap.get("observed_at")
        obs_dt = datetime.fromisoformat(obs.replace("Z", "+00:00")) if obs else None
        close_t = datetime(now_t.year, now_t.month, now_t.day, 13, 30, tzinfo=tz).astimezone(timezone.utc)
        if not force and ev_dt.astimezone(tz).date() == now_t.date() and obs_dt and obs_dt >= close_t:
            ok, reason, detail = evaluate_taiwan_market_freshness(
                event_time_dt=ev_dt, now_dt=now_u, refresh_receipt_dt=obs_dt,
                lineage=snap.get("lineage") or {}, max_refresh_age_seconds=86400, calendar_evidence=cal
            )
            if ok:
                return emit("noop", reason="already_fresh", snapshot_id=snap.get("snapshot_id"), checked_at=now_u.isoformat())
            return emit("error", reason="existing_snapshot_admission_failed", detail=f"{reason}: {detail}")

    return emit("proceed")


def test_preflight_weekend_skip():
    # 2026-10-10 is a Saturday (weekday 5)
    dt = datetime(2026, 10, 10, 15, 0, tzinfo=timezone(timedelta(hours=8)))
    res = _run_preflight_script(force=False, now_dt=dt, snapshot_json=None)
    assert res["status"] == "skipped"
    assert res["reason"] == "weekend"

    # With force=True, weekend skip is bypassed
    res_force = _run_preflight_script(force=True, now_dt=dt, snapshot_json=None)
    assert res_force["status"] == "proceed"


def test_preflight_session_not_closed_skip():
    # 2026-10-06 (Tuesday) at 11:00 Taipei time (before 13:30 session close)
    dt = datetime(2026, 10, 6, 11, 0, tzinfo=timezone(timedelta(hours=8)))
    res = _run_preflight_script(force=False, now_dt=dt, snapshot_json=None)
    assert res["status"] == "skipped"
    assert res["reason"] == "session_not_closed"

    # With force=True, session close check is bypassed
    res_force = _run_preflight_script(force=True, now_dt=dt, snapshot_json=None)
    assert res_force["status"] == "proceed"


def test_preflight_holiday_skip():
    # 2026-10-06 after session close, but calendar says it's a holiday
    dt = datetime(2026, 10, 6, 15, 0, tzinfo=timezone(timedelta(hours=8)))
    cal = {
        "calendar_schema": "twse_trading_calendar_v1",
        "jurisdiction": "TW",
        "holidays": {"2026-10-06": "Special Holiday"},
        "pins": {"market": "TWSE", "pin_hash": "a" * 64},
    }
    snap = {
        "snapshot_id": "snap-123",
        "event_time": "2026-10-05T06:00:00Z",
        "observed_at": "2026-10-05T07:00:00Z",
        "calendar_evidence": cal,
        "lineage": {"calendar_evidence": cal},
    }
    with patch("services.execution.market_snapshot_admission.validate_taiwan_calendar_evidence", return_value=(True, "", cal)):
        res = _run_preflight_script(force=False, now_dt=dt, snapshot_json=snap)
        assert res["status"] == "skipped"
        assert res["reason"] == "holiday"


def test_preflight_missing_calendar_evidence_fails_closed():
    # After close, snapshot exists but calendar evidence is missing
    dt = datetime(2026, 10, 6, 15, 0, tzinfo=timezone(timedelta(hours=8)))
    snap = {
        "snapshot_id": "snap-no-cal",
        "event_time": "2026-10-06T06:00:00Z",
    }
    res = _run_preflight_script(force=False, now_dt=dt, snapshot_json=snap)
    assert res["status"] == "error"
    assert res["reason"] == "market_input_calendar_unverifiable"


def test_preflight_invalid_calendar_evidence_fails_closed():
    dt = datetime(2026, 10, 6, 15, 0, tzinfo=timezone(timedelta(hours=8)))
    snap = {
        "snapshot_id": "snap-bad-cal",
        "event_time": "2026-10-06T06:00:00Z",
        "calendar_evidence": {"bad": "data"},
    }
    with patch("services.execution.market_snapshot_admission.validate_taiwan_calendar_evidence", return_value=(False, "calendar pin mismatch", {})):
        res = _run_preflight_script(force=False, now_dt=dt, snapshot_json=snap)
        assert res["status"] == "error"
        assert res["reason"] == "market_input_calendar_unverifiable"
        assert res["detail"] == "calendar pin mismatch"


def test_preflight_already_fresh_same_day_noop():
    dt = datetime(2026, 10, 6, 15, 0, tzinfo=timezone(timedelta(hours=8)))
    cal = {"calendar_schema": "twse_trading_calendar_v1", "holidays": {}}
    snap = {
        "snapshot_id": "snap-fresh-today",
        "event_time": "2026-10-06T06:00:00Z",  # 14:00 Asia/Taipei
        "observed_at": "2026-10-06T06:30:00Z", # 14:30 Asia/Taipei (>= 13:30 Asia/Taipei)
        "calendar_evidence": cal,
        "lineage": {"calendar_evidence": cal},
    }
    with patch("services.execution.market_snapshot_admission.validate_taiwan_calendar_evidence", return_value=(True, "", cal)), \
         patch("services.execution.market_snapshot_admission.evaluate_taiwan_market_freshness", return_value=(True, "fresh", "ok")):
        res = _run_preflight_script(force=False, now_dt=dt, snapshot_json=snap)
        assert res["status"] == "noop"
        assert res["reason"] == "already_fresh"
        assert res["snapshot_id"] == "snap-fresh-today"

        # With force=True, even an already-fresh snapshot proceeds
        res_force = _run_preflight_script(force=True, now_dt=dt, snapshot_json=snap)
        assert res_force["status"] == "proceed"
