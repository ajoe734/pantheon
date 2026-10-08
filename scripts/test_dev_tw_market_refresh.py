from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
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
        "DEV_DEPLOY_SSH_KNOWN_HOSTS",
        "DEV_DEPLOY_SSH_PRIVATE_KEY",
    ]
    for var in required_vars:
        assert var in content, f"Workflow must reference § 3.1 variable/secret: {var}"

    assert "scripts/dev_vm_ssh.sh prepare" in content, "Workflow must prepare SSH credentials via dev_vm_ssh.sh"
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


class _MockSourceIngestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/source-ingest/snapshots/latest"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error": "not found"}')
        elif self.path.startswith("/api/source-ingest/receipts"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "receipts": [{
                    "connector_id": "tw-twse-tpex-official-market",
                    "status": "completed",
                    "typed_failure": None,
                    "source_timestamp": "2026-10-06T12:00:00Z",
                    "source_timestamp_status": "valid",
                    "created_at": "2030-01-01T00:00:00Z",
                    "finished_at": "2030-01-01T00:00:00Z",
                    "ingest_run_id": "run-1"
                }]
            }).encode("utf-8"))
        elif self.path.startswith("/api/source-ingest/controller/readback"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "connectors": [{
                    "connector_id": "tw-twse-tpex-official-market",
                    "freshness": {
                        "latest_receipt": {"ingest_run_id": "run-1"},
                        "source_timestamp_status": "valid"
                    },
                    "latest_source_record": {
                        "provenance": {"source_ingest_run_id": "run-1"},
                        "source_id": "src-1"
                    }
                }]
            }).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def _setup_refresh_stub_docker(tmp_path: Path, initial_state: dict[str, Any]) -> tuple[Path, Path, Path, Path, int]:
    server = HTTPServer(("127.0.0.1", 0), _MockSourceIngestHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    state_file = tmp_path / "docker_state.json"
    events_file = tmp_path / "docker_events.jsonl"
    output_file = tmp_path / "refresh_output.json"

    state_file.write_text(json.dumps(initial_state))

    docker_script = f"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

state_file = Path({repr(str(state_file))})
events_file = Path({repr(str(events_file))})

def log_event(name, **kwargs):
    with events_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps({{"event": name, **kwargs}}) + "\\n")

args = sys.argv[1:]
if not args:
    sys.exit(0)

state = json.loads(state_file.read_text(encoding="utf-8"))

if args[0] == "compose":
    sub = args[5:] if len(args) > 5 else []
    if not sub:
        sys.exit(0)
    if sub[0] == "run":
        if "runtime-manager" in sub:
            print("0050.TW")
        else:
            log_event("compose_run", args=sub)
        sys.exit(0)
    elif sub[0] == "ps":
        target = sub[-1]
        print(f"cid-{{target}}")
        sys.exit(0)
    elif sub[0] == "images":
        print(state["compose_image_id"])
        sys.exit(0)
    elif sub[0] == "up":
        services = sub[4:]
        log_event("compose_up", services=services, env={{k: os.environ[k] for k in os.environ if k.startswith("PANTHEON_") or k.startswith("SOURCE_INGEST_")}})
        if "source-ingest" in services:
            if state.get("mutate_image_on_up"):
                state["image_id"] = state["mutate_image_on_up"]
            if os.environ.get("PANTHEON_EXTERNAL_EGRESS") == "deny" and "mutate_env_on_restore" in state:
                state["container_env"] = state["mutate_env_on_restore"]
            state_file.write_text(json.dumps(state))
        sys.exit(0)
    elif sub[0] == "rm":
        services = sub[4:]
        log_event("compose_rm", services=services)
        sys.exit(0)

elif args[0] == "ps":
    print("bounded-container-id")
    sys.exit(0)

elif args[0] == "rm":
    log_event("docker_rm", names=args[2:])
    sys.exit(0)

elif args[0] == "inspect":
    fmt = args[2] if len(args) > 2 and args[1] == "--format" else ""
    target = args[-1]
    if "{{.Image}}" in fmt:
        print(state["image_id"])
        sys.exit(0)
    elif "Config.Env" in fmt and target == "cid-source-ingest-scheduler":
        print(json.dumps(["PANTHEON_TENANT_ID=tenant-dev", "PANTHEON_ENV=dev", "GIT_SHA=abc123"]))
        sys.exit(0)
    elif "Config.Env" in fmt:
        print(json.dumps(state["container_env"]))
        sys.exit(0)
    elif "State.Status" in fmt:
        print("exited")
        sys.exit(0)
    elif "State.ExitCode" in fmt:
        print("0")
        sys.exit(0)

elif args[0] == "cp":
    dest = Path(args[2])
    dest.write_text(json.dumps({{
        "row1": {{
            "connectorId": "tw-twse-tpex-official-market",
            "ingestRunId": "run-1",
            "sourceId": "src-1",
            "asOf": "2026-10-06T12:00:00Z",
            "freshness": {{
                "sourceTimestamp": "2026-10-06T12:00:00Z",
                "sourceTimeStatus": "valid",
                "status": "fresh",
                "stale": False
            }}
        }}
    }}))
    sys.exit(0)

sys.exit(0)
"""
    (bin_dir / "docker").write_text(docker_script)
    (bin_dir / "docker").chmod(0o755)

    return bin_dir, state_file, events_file, output_file, port


def _deploy_script_without_readback(tmp_path: Path) -> Path:
    """Copy of the deploy script with the HTTP/Agora readback stubbed out; these tests cover restore, not readback."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for entry in DEPLOY_SCRIPT.parent.iterdir():
        if entry != DEPLOY_SCRIPT:
            (scripts / entry.name).symlink_to(entry)
    content = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    needle = '  verify_bounded_source_refresh_readback "${refresh_started_at}"\n'
    assert needle in content
    patched = scripts / DEPLOY_SCRIPT.name
    patched.write_text(content.replace(needle, ""), encoding="utf-8")
    return patched


def test_refresh_entrypoint_restores_egress_deny_and_preserves_env(tmp_path: Path):
    initial_env = [
        "PANTHEON_EXTERNAL_EGRESS=deny",
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=",
        "PORT=8097",
        "DATABASE_URL=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon",
        "CUSTOM_SECRET=foo$bar",
    ]
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "a" * 64,  # bare 64-hex to verify Compose images -q normalization
        "container_env": initial_env,
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"Refresh failed: {proc.stderr}\n{proc.stdout}"
    assert output_file.exists()
    out_data = json.loads(output_file.read_text(encoding="utf-8"))
    assert out_data.get("status") == "completed"

    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line]
    bounded_up = next(
        e for e in events
        if e.get("event") == "compose_up"
        and "source-ingest" in e.get("services", [])
        and e["env"].get("PANTHEON_EXTERNAL_EGRESS") == "allowlist"
    )
    assert bounded_up["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "openapi.twse.com.tw,www.twse.com.tw,www.tpex.org.tw"

    restore_up = next(
        e for e in events
        if e.get("event") == "compose_up"
        and "source-ingest" in e.get("services", [])
        and e["env"].get("PANTHEON_EXTERNAL_EGRESS") == "deny"
    )
    assert restore_up["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == ""
    assert "SOURCE_INGEST_CONTROLLER_TIMEOUT_SECONDS" not in restore_up["env"]
    assert "SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS" not in restore_up["env"]
    assert "SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS" not in restore_up["env"]

    steady = {"source-ingest-scheduler", "source-ingest-agora-projector"}
    assert not [e for e in events if e.get("event") in ("compose_rm", "compose_up") and steady & set(e.get("services", []))]
    assert len([e for e in events if e.get("event") == "compose_run"]) == 2
    assert any(
        n.startswith("pantheon-bounded-refresh-") for e in events if e.get("event") == "docker_rm" for n in e["names"]
    )


def test_refresh_entrypoint_image_id_guard_pre_recreate_mismatch(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "1" * 64,
        "compose_image_id": "sha256:" + "2" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0
    assert "running image ID" in proc.stderr
    assert "!= compose image ID" in proc.stderr
    assert not events_file.exists() or not any(
        e.get("event") == "compose_up"
        for e in [json.loads(line) for line in events_file.read_text().splitlines() if line]
    )


def test_refresh_entrypoint_image_id_guard_post_recreate_mismatch(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "1" * 64,
        "compose_image_id": "sha256:" + "1" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "mutate_image_on_up": "sha256:" + "3" * 64,
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0
    assert "source-ingest container image ID" in proc.stderr
    assert "!= expected" in proc.stderr


def test_refresh_entrypoint_env_equality_fails_closed_on_mismatch(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "1" * 64,
        "compose_image_id": "sha256:" + "1" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny", "PORT=8097"],
        "mutate_env_on_restore": ["PANTHEON_EXTERNAL_EGRESS=deny", "PORT=8097", "LEAKED_VAR=leaked"],
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0
    assert "restored container env mismatch" in proc.stderr


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
