import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import yaml

from scripts import collect_dev_paper_diagnostics as diag

WRAPPER = Path(__file__).resolve().parents[1] / "scripts" / "run_dev_paper_baseline_with_diagnostics.sh"

FAKE_SSH_TEMPLATE = """#!/usr/bin/env bash
set -uo pipefail
mode="$1"
cmd="$2"
echo "$cmd" >> "${FAKE_SSH_LOG}"
cat >/dev/null
if [[ "$cmd" == *"bootstrap_dev_paper_baseline.py"* ]]; then
  [[ -n "${FAKE_SSH_BOOTSTRAP_SLEEP:-}" ]] && sleep "${FAKE_SSH_BOOTSTRAP_SLEEP}"
  exit "${FAKE_SSH_BOOTSTRAP_EXIT:-1}"
fi
[[ -n "${FAKE_SSH_COLLECTOR_SLEEP:-}" ]] && sleep "${FAKE_SSH_COLLECTOR_SLEEP}"
if [[ -n "${FAKE_SSH_COLLECTOR_STDERR:-}" ]]; then
  printf '%s' "${FAKE_SSH_COLLECTOR_STDERR}" >&2
fi
if [[ -n "${FAKE_SSH_COLLECTOR_STDOUT:-}" ]]; then
  printf '%s' "${FAKE_SSH_COLLECTOR_STDOUT}"
fi
exit "${FAKE_SSH_COLLECTOR_EXIT:-0}"
"""


def _run_wrapper(tmp_path, env_overrides, extra_env=None):
    workspace = tmp_path / "workspace"
    ssh_dir = workspace / ".agora-gate-controller" / "scripts"
    ssh_dir.mkdir(parents=True)
    ssh_path = ssh_dir / "dev_vm_ssh.sh"
    ssh_path.write_text(FAKE_SSH_TEMPLATE)
    ssh_path.chmod(ssh_path.stat().st_mode | stat.S_IEXEC)

    diagnostic_dir = tmp_path / "diagnostics"
    diagnostic_dir.mkdir()
    collector_stub = tmp_path / "collector_stub.py"
    collector_stub.write_text("# fixture only, never executed locally\n")
    fake_ssh_log = tmp_path / "fake-ssh.log"

    env = dict(os.environ)
    env.update({
        "GITHUB_WORKSPACE": str(workspace),
        "DEV_PAPER_DIAGNOSTICS_DIR": str(diagnostic_dir),
        "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(collector_stub),
        "EXPECTED_BFF_SHA": "b" * 40,
        "FAKE_SSH_LOG": str(fake_ssh_log),
    })
    env.update(extra_env or {})
    env.update(env_overrides)

    result = subprocess.run(
        ["bash", str(WRAPPER)], env=env, cwd=str(tmp_path),
        capture_output=True, text=True, timeout=30,
    )
    log_lines = fake_ssh_log.read_text().splitlines() if fake_ssh_log.exists() else []
    return result, diagnostic_dir, log_lines


def _status_document(diagnostic_dir):
    return json.loads((diagnostic_dir / "collection-status.json").read_text())


def test_exception_identifiers_and_frames_survive_without_values():
    raw = '''2026-09-07T00:17:26.123Z Traceback (most recent call last):
2026-09-07T00:17:26.123Z   File "/workspace/services/control-plane/bff/personas/service.py", line 3999, in _coordinate_persona_create
2026-09-07T00:17:26.123Z     call(password="SUPERSECRET")
2026-09-07T00:17:26.123Z NameError: name 'missing_helper' is not defined
2026-09-07T00:17:26.123Z urllib.error.HTTPError: HTTP Error 403: SECRET_RESPONSE
psycopg.errors.UndefinedTable: relation "persona.ledger" does not exist
TypeError: Store.release() got an unexpected keyword argument 'lease_seconds'
ValueError: Authorization: Bearer PRIVATE_JWT
RuntimeError: postgresql://user:DB_PASSWORD@host/db
password=OTHER_SECRET
{"token":"JSON_SECRET","body":{"secret":"NESTED_SECRET"}}
-----BEGIN PRIVATE KEY-----
PRIVATE_KEY_BYTES
-----END PRIVATE KEY-----
'''
    events = diag.log_events(raw)
    assert events[0]["file"].endswith("personas/service.py")
    assert events[1]["identifier"] == "missing_helper"
    assert events[2]["http_status"] == 403
    assert events[3]["identifier"] == "persona.ledger"
    assert events[4]["identifier"] == "lease_seconds"
    encoded = json.dumps(events)
    for secret in ("SUPERSECRET", "SECRET_RESPONSE", "PRIVATE_JWT", "DB_PASSWORD", "OTHER_SECRET", "JSON_SECRET", "NESTED_SECRET", "PRIVATE_KEY_BYTES"):
        assert secret not in encoded
    assert all("message" not in event for event in events)


def test_project_exception_allowlist_admits_named_types_only():
    raw = (
        "PersonaWriteOwnerUnavailable: persona owner call failed with SECRET_TOKEN\n"
        "ProvisioningLeaseLost: lease revoked mid PRIVATE_DETAIL\n"
        "SomeUnknownFailure: raw request body SHOULD_NOT_LEAK\n"
    )
    events = diag.log_events(raw)
    assert [event["type"] for event in events] == [
        "PersonaWriteOwnerUnavailable",
        "ProvisioningLeaseLost",
    ]
    encoded = json.dumps(events)
    for secret in ("SECRET_TOKEN", "PRIVATE_DETAIL", "SomeUnknownFailure", "SHOULD_NOT_LEAK"):
        assert secret not in encoded


def test_commands_are_bounded_in_bytes_and_time(monkeypatch):
    monkeypatch.setattr(diag, "MAX_BYTES", 1024)
    raw, status = diag.command([sys.executable, "-c", "print('x' * 4096)"])
    assert len(raw) == 1024 and status == "truncated"
    monkeypatch.setattr(diag, "COMMAND_SECONDS", 0.1)
    raw, status = diag.command([sys.executable, "-c", "import time; time.sleep(10)"])
    assert status == "timeout"


def test_fixed_services_continue_after_failure_and_bind_observed_identity(monkeypatch):
    calls = []
    def run(args):
        calls.append(args)
        if args[1] == "ps":
            if args[-1].endswith("=capital"):
                return "UNTRUSTED_ERROR", "command_failed"
            return "a" * 64, "ok"
        if args[1] == "inspect":
            assert ".Config.Env" not in args[3] and ".State.Error" not in args[3]
            return json.dumps({"source_sha": "b" * 40, "status": "running", "exit_code": 0,
                               "restart_count": 0, "oom_killed": False, "image_id": "sha256:" + "c" * 64}), "ok"
        assert args[1:5] == ["logs", "--timestamps", "--since=15m", "--tail=240"]
        return "NameError: name 'missing_helper' is not defined", "ok"
    monkeypatch.setattr(diag, "command", run)
    result = diag.collect("b" * 40, run_id="34081262894", attempt="1", phase="paper_bootstrap",
                          expected_fe_sha="e" * 40, bootstrap_exit="1")
    assert "persona" in diag.SERVICES
    assert result["identity_matches"] is True
    assert result["run_id"] == "34081262894"
    assert result["bootstrap_exit"] == "1"
    assert result["container_id"] == "a" * 64
    assert result["observed_source_sha"] == "b" * 40
    assert result["services"]["persona"]["collection_status"] == "ok"
    assert result["services"]["capital"]["collection_status"] == "command_failed"
    assert result["services"]["postgres"]["events"][0]["type"] == "NameError"
    assert result["collection_status"] == "partial"
    assert "UNTRUSTED_ERROR" not in json.dumps(result)
    assert diag.collect("d" * 40)["identity_matches"] is False
    assert all(args[0] == "docker" and args[1] in {"ps", "inspect", "logs"} for args in calls)
    with pytest.raises(ValueError):
        diag.collect("dev; unsafe")


def _paper_bootstrap_steps():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/nonprod-deploy.yml").read_text())
    steps = next(job["steps"] for job in workflow["jobs"].values() if any(step.get("id") == "paper_bootstrap" for step in job.get("steps", [])))
    names = [step.get("id") for step in steps]
    return steps, names


def test_diagnostic_collection_happens_inside_the_guarded_baseline_child():
    steps, names = _paper_bootstrap_steps()
    baseline = steps[names.index("paper_bootstrap")]
    upload = steps[names.index("paper_bootstrap_diagnostics_upload")]

    # The failure-capture ID from the broken cross-step design must not
    # reappear: a second, separate guard invocation after the guard already
    # quarantined the heartbeat can never observe a healthy lease.
    assert "paper_bootstrap_diagnostics" not in names
    assert names.index("paper_bootstrap") < names.index("deploy_compensation")

    run = baseline["run"]
    assert "run_dev_paper_baseline_with_diagnostics.sh" in run
    assert "collect_dev_paper_diagnostics.py" in run
    assert "run_with_dev_environment_lease.sh" in run
    assert "StrictHostKeyChecking=no" not in run
    # The baseline's own exit status must reach the outer step unchanged so
    # deploy_compensation still triggers on the same condition as before.
    assert 'exit "${baseline_status}"' in run
    assert "|| true" not in run

    assert upload["with"]["retention-days"] == 7
    assert upload["continue-on-error"] is True
    assert "steps.paper_bootstrap.outputs.diagnostics_file" in upload["if"]
    assert "steps.paper_bootstrap.outputs.status_file" in upload["if"]
    # Only an explicit safe-file allowlist is uploaded; never the whole
    # runner-local directory the wrapper wrote into.
    upload_path = upload["with"]["path"]
    assert "${{ steps.paper_bootstrap.outputs.diagnostics_file }}" in upload_path
    assert "${{ steps.paper_bootstrap.outputs.status_file }}" in upload_path
    assert "${{ steps.paper_bootstrap.outputs.checksum_file }}" in upload_path
    assert "artifact_path" not in upload_path

    run = baseline["run"]
    assert "EXPECTED_FE_SHA=" in run
    assert "DEV_PAPER_RUN_ID=" in run
    assert "DEV_PAPER_ATTEMPT=" in run
    assert "DEV_PAPER_PHASE=" in run


def test_diagnostics_script_preserves_baseline_exit_and_never_masks_it():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "run_dev_paper_baseline_with_diagnostics.sh").read_text()
    assert "|| true" not in script
    assert "bootstrap_status=$?" in script
    assert 'exit "${bootstrap_status}"' in script
    # A missing/failed/invalid collection must never overwrite the original
    # baseline failure, and must never be forced into a fabricated success.
    assert script.count('exit "${bootstrap_status}"') >= 1


def test_collector_aggregate_status_flags_inspect_failure(monkeypatch):
    # docker ps and docker logs can both report "ok" while docker inspect
    # itself times out; that must still surface at the aggregate level
    # instead of silently disappearing.
    monkeypatch.setattr(diag, "SERVICES", ("operator-bff",))

    def run(args):
        if args[1] == "ps":
            return "a" * 64, "ok"
        if args[1] == "inspect":
            return "", "timeout"
        return "", "ok"

    monkeypatch.setattr(diag, "command", run)
    result = diag.collect("b" * 40)
    assert result["services"]["operator-bff"]["state"]["collection_status"] == "timeout"
    assert result["collection_status"] == "partial"


def test_collector_aggregate_status_flags_identity_mismatch_even_when_all_ok(monkeypatch):
    # A fully inspectable, fully logged container that is simply running the
    # wrong source SHA must never be reported as an overall "ok" collection.
    monkeypatch.setattr(diag, "SERVICES", ("operator-bff",))

    def run(args):
        if args[1] == "ps":
            return "a" * 64, "ok"
        if args[1] == "inspect":
            return json.dumps({"source_sha": "c" * 40, "status": "running", "exit_code": 0,
                               "restart_count": 0, "oom_killed": False,
                               "image_id": "sha256:" + "d" * 64}), "ok"
        return "", "ok"

    monkeypatch.setattr(diag, "command", run)
    result = diag.collect("b" * 40)
    assert result["identity_matches"] is False
    assert result["collection_status"] == "identity_mismatch"


def test_wrapper_never_uploads_raw_unbounded_collector_stderr(tmp_path):
    secret = "Authorization: Bearer " + ("S3CRETTOKEN" * 10)
    result, diagnostic_dir, _log = _run_wrapper(tmp_path, {}, extra_env={
        "FAKE_SSH_BOOTSTRAP_EXIT": "1",
        "FAKE_SSH_COLLECTOR_STDOUT": '{"ok":true}',
        "FAKE_SSH_COLLECTOR_STDERR": secret,
        "FAKE_SSH_COLLECTOR_EXIT": "0",
    })
    assert result.returncode == 1, result.stderr

    uploaded_names = sorted(p.name for p in diagnostic_dir.iterdir())
    assert uploaded_names == ["SHA256SUMS", "collection-status.json", "diagnostics.json"]
    for path in diagnostic_dir.iterdir():
        assert secret not in path.read_text()
        assert "collector-stderr" not in path.name

    # Nothing raw was left behind as a sibling of the uploaded directory either.
    for sibling in diagnostic_dir.parent.iterdir():
        if sibling != diagnostic_dir and sibling.is_file():
            assert secret not in sibling.read_text(errors="replace")

    document = _status_document(diagnostic_dir)
    assert document["collectionStatus"] == "ok"
    # A bounded, redacted summary is allowed to survive; the raw secret text
    # must not.
    assert secret not in json.dumps(document)
    assert "[REDACTED]" in document["collectorStderrSummary"]


def test_wrapper_enforces_overall_collection_deadline(tmp_path):
    started = time.monotonic()
    result, diagnostic_dir, _log = _run_wrapper(tmp_path, {
        "DEV_PAPER_DIAGNOSTICS_TIMEOUT_SECONDS": "1",
    }, extra_env={
        "FAKE_SSH_BOOTSTRAP_EXIT": "1",
        "FAKE_SSH_COLLECTOR_SLEEP": "10",
    })
    elapsed = time.monotonic() - started
    # A per-command SSH ConnectTimeout cannot bound an already-connected,
    # stalled channel; only an overall wrapper-side deadline can.
    assert elapsed < 8, f"collection deadline was not enforced ({elapsed}s elapsed)"
    assert result.returncode == 1, result.stderr
    document = _status_document(diagnostic_dir)
    assert document["collectionStatus"] == "timeout"
    assert document["bootstrapExit"] == 1
    assert not (diagnostic_dir / "diagnostics.json").exists()


def test_wrapper_wires_run_identity_into_collector_invocation(tmp_path):
    result, diagnostic_dir, log_lines = _run_wrapper(tmp_path, {
        "EXPECTED_FE_SHA": "e" * 40,
        "DEV_PAPER_RUN_ID": "34081262894",
        "DEV_PAPER_ATTEMPT": "2",
        "DEV_PAPER_PHASE": "paper_bootstrap",
    }, extra_env={
        "FAKE_SSH_BOOTSTRAP_EXIT": "1",
        "FAKE_SSH_COLLECTOR_STDOUT": '{"ok":true}',
    })
    assert result.returncode == 1, result.stderr
    assert len(log_lines) == 2
    collector_invocation = log_lines[1]
    assert "--expected-fe-sha " + "e" * 40 in collector_invocation
    assert "--run-id 34081262894" in collector_invocation
    assert "--attempt 2" in collector_invocation
    assert "--phase paper_bootstrap" in collector_invocation
    assert "--bootstrap-exit 1" in collector_invocation
    document = _status_document(diagnostic_dir)
    assert document["bootstrapExit"] == 1


def test_wrapper_initializes_terminal_evidence_before_running_anything(tmp_path):
    # A cancellation or lease loss mid-baseline must not erase evidence:
    # the status file must already exist, with a non-terminal status, before
    # the (slow) baseline command even finishes.
    workspace_holder = {}

    def launch():
        env = dict(os.environ)
        workspace = tmp_path / "workspace"
        ssh_dir = workspace / ".agora-gate-controller" / "scripts"
        ssh_dir.mkdir(parents=True)
        ssh_path = ssh_dir / "dev_vm_ssh.sh"
        ssh_path.write_text(FAKE_SSH_TEMPLATE)
        ssh_path.chmod(ssh_path.stat().st_mode | stat.S_IEXEC)
        diagnostic_dir = tmp_path / "diagnostics"
        diagnostic_dir.mkdir()
        collector_stub = tmp_path / "collector_stub.py"
        collector_stub.write_text("# fixture only\n")
        env.update({
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diagnostic_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(collector_stub),
            "EXPECTED_BFF_SHA": "b" * 40,
            "FAKE_SSH_LOG": str(tmp_path / "fake-ssh.log"),
            "FAKE_SSH_BOOTSTRAP_SLEEP": "2",
            "FAKE_SSH_BOOTSTRAP_EXIT": "0",
        })
        workspace_holder["diagnostic_dir"] = diagnostic_dir
        return subprocess.Popen(["bash", str(WRAPPER)], env=env, cwd=str(tmp_path),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    proc = launch()
    try:
        diagnostic_dir = workspace_holder["diagnostic_dir"]
        status_path = diagnostic_dir / "collection-status.json"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not status_path.exists():
            time.sleep(0.05)
        assert status_path.exists(), "status file was not initialized before baseline completed"
        early_document = json.loads(status_path.read_text())
        assert early_document["collectionStatus"] == "not_started"
    finally:
        stdout, stderr = proc.communicate(timeout=30)
    assert proc.returncode == 0, stderr
    final_document = json.loads(status_path.read_text())
    assert final_document["collectionStatus"] == "not_required"
    assert final_document["bootstrapExit"] == 0
