from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "dev-finmind-broker-backfill.yml"
CONTROLLER_SHA = "a4b0cdca261b714c386341534b07fc43badd1a7e"
CONTROLLER_SCRIPT_SHA256 = "d0d9cc63a21bb1fbf4f5e2dc14c60a2418a598cfe608e7d0df0189818e3d74ac"
CONTROLLER_WRAPPER_SHA256 = "6c82021b93621f16776d5d67a9e20cb9d690f7ebfa257ebf8c329f7d158fb2c2"


def _load_workflow() -> dict[str, Any]:
    assert WORKFLOW_PATH.is_file(), f"Workflow file not found: {WORKFLOW_PATH}"
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _steps() -> list[dict[str, Any]]:
    return _load_workflow()["jobs"]["backfill"]["steps"]


def _step_by_id(step_id: str) -> dict[str, Any]:
    return next(s for s in _steps() if s.get("id") == step_id)


def _step_by_name(name_part: str) -> dict[str, Any]:
    return next(s for s in _steps() if name_part in s.get("name", ""))


def test_workflow_is_dispatchable_with_bounded_defaults_and_dev_environment() -> None:
    wf = _load_workflow()
    on = wf.get("on") or wf.get(True)
    assert "workflow_dispatch" in on
    assert "schedule" not in on
    inputs = on["workflow_dispatch"]["inputs"]
    assert set(inputs) == {
        "start_date",
        "end_date",
        "max_requests",
        "max_wall_seconds",
        "min_interval_seconds",
        "verify_only",
    }
    assert inputs["max_requests"]["default"] == "180"
    assert inputs["max_wall_seconds"]["default"] == "2100"
    assert inputs["min_interval_seconds"]["default"] == "1.5"
    assert inputs["verify_only"]["default"] is False

    job = wf["jobs"]["backfill"]
    assert job["environment"] == "dev"
    assert job["timeout-minutes"] == 45
    backfill_step = _step_by_id("broker_backfill")
    assert backfill_step["timeout-minutes"] == 38
    assert backfill_step["timeout-minutes"] < job["timeout-minutes"]


def test_workflow_uses_pinned_direct_ssh_and_dev_environment_lease() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    wf = _load_workflow()
    job = wf["jobs"]["backfill"]
    assert job["env"]["DEV_DEPLOY_SSH_HOST"] == "${{ vars.DEV_DEPLOY_SSH_HOST }}"
    assert job["env"]["DEV_DEPLOY_SSH_USER"] == "${{ vars.NONPROD_REMOTE_USER }}"

    for retired in ("'lupin'", "/home/lupin/", "35.201.204.12", "35.201.239.38", "34.81.75.241", "gcloud compute ssh"):
        assert retired not in text

    assert CONTROLLER_SHA in text
    assert CONTROLLER_SCRIPT_SHA256 in text
    assert CONTROLLER_WRAPPER_SHA256 in text
    assert re.search(r"dev_environment_lease\.py[\"']?\s+acquire", text)
    assert "heartbeat-loop" in text
    assert "verify-heartbeat-identity" in text
    assert "--initial-visibility-wait-seconds 15" in text
    assert "run_with_dev_environment_lease.sh" in text
    assert re.search(r"dev_environment_lease\.py[\"']?\s+release", text)


def test_workflow_enforces_exact_finmind_hosts_and_never_prints_token() -> None:
    step = _step_by_id("broker_backfill")
    env = step["env"]
    assert env["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "api.finmindtrade.com,storage.googleapis.com"
    assert env["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] == "tw-finmind-broker-daily-report"
    assert env["SOURCE_INGEST_BOUNDED_DATASET"] == "tw_broker_top"
    assert env["FINMIND_API_TOKEN"] == "${{ secrets.FINMIND_API_TOKEN }}"
    assert "--allowed-hosts api.finmindtrade.com,storage.googleapis.com" in step["run"]

    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # Token value or length must never be echoed.
    assert "echo" not in "\n".join(
        line for line in text.splitlines() if "FINMIND_API_TOKEN" in line
    )


def test_all_workflow_shell_steps_parse_cleanly() -> None:
    wf = _load_workflow()
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            if "run" not in step:
                continue
            body = re.sub(r"\$\{\{.*?\}\}", "fixture-value", step["run"])
            res = subprocess.run(["bash", "-n"], input=body, text=True, capture_output=True)
            assert res.returncode == 0, (step.get("name"), res.stderr)


def _setup_fake_backfill_layout(tmp_path: Path, deploy_outcome: dict[str, Any], deploy_rc: int = 0) -> tuple[Path, Path, Path]:
    wrapper = tmp_path / ".lease-controller" / "scripts" / "run_with_dev_environment_lease.sh"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(
        """#!/usr/bin/env bash
if [[ -f "${GUARD_FAILED:-}" ]]; then
  echo "lease heartbeat quarantined" >&2
  exit 75
fi
printf '%s %s\\n' "$1" "$2" >> "${GUARD_CALLS}"
"$@"
rc=$?
if (( rc != 0 )) && [[ -n "${GUARD_FAILED:-}" ]]; then
  touch "${GUARD_FAILED}"
fi
exit "${rc}"
""",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    deploy = tmp_path / "scripts" / "deploy_nonprod_vm.sh"
    deploy.parent.mkdir(parents=True, exist_ok=True)
    deploy.write_text(
        f"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["CALLS"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")
out_path = args[args.index("--output") + 1]
with open(out_path, "w", encoding="utf-8") as fh:
    fh.write({json.dumps(deploy_outcome)!r})
sys.exit({deploy_rc})
""",
        encoding="utf-8",
    )
    deploy.chmod(0o755)
    return tmp_path / "calls", tmp_path / "guard_calls", tmp_path / "guard_failed"


def test_backfill_step_and_summary_succeed_on_bounded_run(tmp_path: Path) -> None:
    step = _step_by_id("broker_backfill")
    summary_step = _step_by_name("Summarize backfill outcome")
    outcome_data = {
        "connector_id": "tw-finmind-broker-daily-report",
        "dataset": "tw_broker_top",
        "mode": "two_year_backfill",
        "status": "ok",
        "reason": None,
        "window_start": "2024-11-26",
        "window_end": "2026-11-26",
        "completed_count": 180,
        "skipped_completed_count": 12,
        "remaining_dates_count": 330,
        "stopped_reason": "max_requests_reached",
        "requests_made": 180,
        "plan_hourly_cap": 6000,
        "top_records": 14400000,
        "bytes_written": 387000000,
        "total_stored_sessions": 192,
        "total_stored_rows": 15360000,
        "total_stored_bytes": 412800000,
        "max_total_bytes": 3000000000,
        "max_total_rows": 60000000,
        "coverage": {"passed": False, "two_sided_sessions_count": 192},
    }
    calls_file, guard_calls, guard_failed = _setup_fake_backfill_layout(tmp_path, outcome_data, deploy_rc=0)
    out_file = tmp_path / "finmind-broker-backfill-outcome.json"
    summary_file = tmp_path / "summary.md"

    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "REFRESH_OUTPUT_PATH": str(out_file),
        "FINMIND_API_TOKEN": "tok-secret-never-print",
        "BACKFILL_START_DATE": "2024-11-26",
        "BACKFILL_END_DATE": "2026-11-26",
        "BACKFILL_MAX_REQUESTS": "180",
        "BACKFILL_MAX_WALL_SECONDS": "2100",
        "BACKFILL_MIN_INTERVAL_SECONDS": "1.5",
        "BACKFILL_VERIFY_ONLY": "false",
        "CALLS": str(calls_file),
        "GUARD_CALLS": str(guard_calls),
        "GUARD_FAILED": str(guard_failed),
    }
    proc = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert not guard_failed.exists()
    assert "tok-secret-never-print" not in proc.stdout + proc.stderr
    recorded = [json.loads(l) for l in calls_file.read_text().splitlines()][0]
    assert "--broker-backfill" in recorded
    assert "--allowed-hosts" in recorded and recorded[recorded.index("--allowed-hosts") + 1] == "api.finmindtrade.com,storage.googleapis.com"
    assert "--backfill-start" in recorded and recorded[recorded.index("--backfill-start") + 1] == "2024-11-26"
    assert "--backfill-end" in recorded and recorded[recorded.index("--backfill-end") + 1] == "2026-11-26"

    sum_proc = subprocess.run(
        ["bash", "-e", "-c", summary_step["run"]],
        cwd=tmp_path,
        env={**os.environ, "OUTCOME_FILE": str(out_file), "GITHUB_STEP_SUMMARY": str(summary_file), "STEP_OUTCOME": "success"},
        text=True,
        capture_output=True,
    )
    assert sum_proc.returncode == 0, sum_proc.stderr
    summary_text = summary_file.read_text(encoding="utf-8")
    assert "`180` / `6000`" in summary_text
    assert "`max_requests_reached`" in summary_text


def test_backfill_step_missing_credential_and_cap_exceeded_do_not_quarantine_lease_and_fail_summary(tmp_path: Path) -> None:
    step = _step_by_id("broker_backfill")
    summary_step = _step_by_name("Summarize backfill outcome")

    # 1. Missing credential
    no_cred_dir = tmp_path / "no_cred"
    no_cred_dir.mkdir()
    calls_file, guard_calls, guard_failed = _setup_fake_backfill_layout(no_cred_dir, {}, deploy_rc=0)
    out_file = no_cred_dir / "outcome.json"
    summary_file = no_cred_dir / "summary.md"
    env = {
        **os.environ,
        "RUNNER_TEMP": str(no_cred_dir),
        "REFRESH_OUTPUT_PATH": str(out_file),
        "FINMIND_API_TOKEN": "",
        "BACKFILL_START_DATE": "",
        "BACKFILL_END_DATE": "",
        "BACKFILL_MAX_REQUESTS": "180",
        "BACKFILL_MAX_WALL_SECONDS": "2100",
        "BACKFILL_MIN_INTERVAL_SECONDS": "1.5",
        "BACKFILL_VERIFY_ONLY": "false",
        "CALLS": str(calls_file),
        "GUARD_CALLS": str(guard_calls),
        "GUARD_FAILED": str(guard_failed),
    }
    proc = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=no_cred_dir, env=env, text=True, capture_output=True)
    assert proc.returncode == 0
    assert not calls_file.exists()
    sum_proc = subprocess.run(
        ["bash", "-e", "-c", summary_step["run"]],
        cwd=no_cred_dir,
        env={**os.environ, "OUTCOME_FILE": str(out_file), "GITHUB_STEP_SUMMARY": str(summary_file), "STEP_OUTCOME": "success"},
        text=True,
        capture_output=True,
    )
    assert sum_proc.returncode != 0
    assert "credential_unavailable" in sum_proc.stderr

    # 2. Cap exceeded on remote run -> guarded command exits 0 (no lease quarantine), step exits 1, summary fails
    cap_dir = tmp_path / "cap_dir"
    cap_dir.mkdir()
    cap_outcome = {
        "status": "cap_exceeded",
        "reason": "total_bytes_cap_exceeded on 2026-06-09: 3000000001 > 3000000000",
        "requests_made": 1,
        "plan_hourly_cap": 6000,
        "total_stored_bytes": 2998000000,
        "max_total_bytes": 3000000000,
    }
    calls_file2, guard_calls2, guard_failed2 = _setup_fake_backfill_layout(cap_dir, cap_outcome, deploy_rc=1)
    out_file2 = cap_dir / "outcome.json"
    summary_file2 = cap_dir / "summary.md"
    env2 = {**env, "RUNNER_TEMP": str(cap_dir), "REFRESH_OUTPUT_PATH": str(out_file2), "FINMIND_API_TOKEN": "tok",
            "CALLS": str(calls_file2), "GUARD_CALLS": str(guard_calls2), "GUARD_FAILED": str(guard_failed2)}
    proc2 = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=cap_dir, env=env2, text=True, capture_output=True)
    assert proc2.returncode == 1
    assert not guard_failed2.exists(), "Lease heartbeat must not be quarantined when backfill exits non-zero"
    sum_proc2 = subprocess.run(
        ["bash", "-e", "-c", summary_step["run"]],
        cwd=cap_dir,
        env={**os.environ, "OUTCOME_FILE": str(out_file2), "GITHUB_STEP_SUMMARY": str(summary_file2), "STEP_OUTCOME": "failure"},
        text=True,
        capture_output=True,
    )
    assert sum_proc2.returncode != 0
    assert "cap_exceeded" in sum_proc2.stderr


@pytest.mark.parametrize(
    "overrides,expected_err",
    [
        ({"BACKFILL_MAX_REQUESTS": "601"}, "max_requests must be between 1 and 600"),
        ({"BACKFILL_MAX_WALL_SECONDS": "2400"}, "max_wall_seconds must be between 60 and 2100"),
        ({"BACKFILL_MIN_INTERVAL_SECONDS": "0.5"}, "min_interval_seconds must be >= 1.0"),
    ],
)
def test_backfill_step_rejects_out_of_bound_dispatch_inputs(
    tmp_path: Path, overrides: dict[str, str], expected_err: str
) -> None:
    step = _step_by_id("broker_backfill")
    calls_file, guard_calls, guard_failed = _setup_fake_backfill_layout(tmp_path, {"status": "ok"}, deploy_rc=0)
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "REFRESH_OUTPUT_PATH": str(tmp_path / "outcome.json"),
        "FINMIND_API_TOKEN": "tok",
        "BACKFILL_START_DATE": "",
        "BACKFILL_END_DATE": "",
        "BACKFILL_MAX_REQUESTS": "180",
        "BACKFILL_MAX_WALL_SECONDS": "2100",
        "BACKFILL_MIN_INTERVAL_SECONDS": "1.5",
        "BACKFILL_VERIFY_ONLY": "false",
        "CALLS": str(calls_file),
        "GUARD_CALLS": str(guard_calls),
        "GUARD_FAILED": str(guard_failed),
        **overrides,
    }
    proc = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert proc.returncode != 0
    assert expected_err in proc.stderr
    assert not calls_file.exists()
