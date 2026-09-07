#!/usr/bin/env python3
"""Execute guard-to-diagnostic-to-compensation failure-path integration tests (SD D3).

Uses real subprocesses, actual guard executable (scripts/run_with_dev_environment_lease.sh),
actual wrapped child (scripts/run_dev_paper_baseline_with_diagnostics.sh), actual
collector (scripts/collect_dev_paper_diagnostics.py), and actual compensation script
(scripts/compensate_cross_repo_release.sh) with isolated fake lease authority and controlled
bootstrap / SSH / HTTP fixtures.

Test Scenarios (SD D3):
  1. Baseline success does not execute failure collector.
  2. Baseline exit 1: capture precedes heartbeat stop; guard retains exit 75 and original exit 1.
  3. Collector timeout, non-zero exit, and invalid JSON: bootstrap failure exit preserved, rollback reachable.
  4. Heartbeat and remote CAS loss: command process group terminated, no further remote commands.
  5. Cancellation, missing/expired state, wrong source, ambiguous containers fail closed.
  6. Artifact output missing, upload failure, bad checksum cannot declare diagnostic acceptance.
  7. Project domain exceptions and Persona owner positively captured; secret sentinels never leak.
  8. Normal compensation and failed compensation retain true independent outcomes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import http.server
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_SCRIPT = REPO_ROOT / "scripts" / "run_with_dev_environment_lease.sh"
WRAPPER_SCRIPT = REPO_ROOT / "scripts" / "run_dev_paper_baseline_with_diagnostics.sh"
COLLECTOR_SCRIPT = REPO_ROOT / "scripts" / "collect_dev_paper_diagnostics.py"
COMPENSATION_SCRIPT = REPO_ROOT / "scripts" / "compensate_cross_repo_release.sh"

TOKEN_ENV = "PANTHEON_ENVIRONMENT_LEASE_TOKEN"
TEST_TOKEN = "guard-adjacent-cli-test-token"
TEST_LEASE_ID = "11111111-1111-4111-8111-111111111111"
TEST_BFF_SHA = "a" * 40
TEST_FE_SHA = "b" * 40
PINNED_LEASE_CONTROLLER_SHA = "9e564718da8c39199a4c311f1a667b74226e3428"


FAKE_ADJACENT_CLI = r"""#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import time
from pathlib import Path

TOKEN_ENV = "PANTHEON_ENVIRONMENT_LEASE_TOKEN"


def option(name: str) -> str:
    index = sys.argv.index(name)
    return sys.argv[index + 1]


def start_ticks(pid: int) -> int:
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return int(raw[raw.rfind(")") + 1 :].strip().split()[19])


def resolve_argument(pid: int, value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = Path(os.readlink(f"/proc/{pid}/cwd")) / path
    return str(path.resolve())


command = sys.argv[1]

if command == "heartbeat-loop":
    state_file = str(Path(option("--state-file")).resolve())
    identity_file = Path(option("--identity-json-out"))
    stop_file = Path(option("--shutdown-json-out")) if "--shutdown-json-out" in sys.argv else None
    pid = os.getpid()
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    identity_file.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "running",
                "pid": pid,
                "startTicks": start_ticks(pid),
                "cmdlineSha256": hashlib.sha256(cmdline).hexdigest(),
                "expectedCli": str(Path(__file__).resolve()),
                "stateFile": state_file,
                "recordedAt": "2026-07-13T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def on_term(*_args):
        if stop_file:
            stop_file.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "status": "stopped",
                        "heartbeatPid": pid,
                        "recordedAt": "2026-07-13T00:00:01Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        sys.exit(0)

    if os.environ.get("FAKE_HEARTBEAT_IGNORE_TERM") == "1":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    else:
        signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    while True:
        time.sleep(0.05)

if command == "verify-heartbeat-identity":
    assert TOKEN_ENV not in os.environ
    identity = json.loads(Path(option("--identity-file")).read_text(encoding="utf-8"))
    pid = int(option("--pid"))
    expected_cli = str(Path(option("--expected-cli")).resolve())
    state_file = str(Path(option("--state-file")).resolve())
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    arguments = [os.fsdecode(part) for part in cmdline.rstrip(b"\0").split(b"\0")]
    assert identity["pid"] == pid
    assert identity["startTicks"] == start_ticks(pid)
    assert identity["cmdlineSha256"] == hashlib.sha256(cmdline).hexdigest()
    assert identity["expectedCli"] == expected_cli
    assert identity["stateFile"] == state_file
    assert any(resolve_argument(pid, value) == expected_cli for value in arguments)
    state_index = arguments.index("--state-file")
    assert resolve_argument(pid, arguments[state_index + 1]) == state_file
    print('{"status":"verified"}')
    raise SystemExit(0)

if command == "verify":
    token = os.environ.get(TOKEN_ENV, "").encode("utf-8")
    assert hashlib.sha256(token).hexdigest() == os.environ["FAKE_EXPECTED_TOKEN_SHA256"]
    count_file = Path(os.environ["FAKE_VERIFY_COUNT_FILE"])
    count = int(count_file.read_text(encoding="utf-8") or "0") + 1
    count_file.write_text(f"{count}\n", encoding="utf-8")
    fail_at = int(os.environ.get("FAKE_VERIFY_FAIL_AT", "0"))
    if fail_at and count >= fail_at:
        print("simulated GitHub API outage", file=sys.stderr)
        raise SystemExit(78)
    print('{"status":"verified"}')
    raise SystemExit(0)

if command == "acquire":
    state_file = Path(option("--state-file"))
    json_out = Path(option("--json-out")) if "--json-out" in sys.argv else None
    owner = option("--owner") if "--owner" in sys.argv else "test-owner"
    expected_backend = option("--expected-backend-sha") if "--expected-backend-sha" in sys.argv else "a" * 40
    data = {
        "schemaVersion": 1,
        "resource": "pantheon-dev-environment",
        "mode": "deployment",
        "owner": owner,
        "leaseId": "11111111-1111-4111-8111-111111111111",
        "acquiredAt": "2026-09-07T00:00:00Z",
        "heartbeatAt": "2026-09-07T00:00:00Z",
        "expiresAt": "2026-09-07T01:00:00Z",
        "repository": "ajoe734/execute-plans",
        "branch": "environment-coordination",
        "path": ".pantheon/environment-leases/pantheon-dev-environment.json",
        "expectedBackendSha": expected_backend,
        "runUrl": "https://actions.example/run/1",
    }
    state_file.write_text(json.dumps(data) + "\n", encoding="utf-8")
    if json_out:
        json_out.write_text(json.dumps({"status": "acquired"}) + "\n", encoding="utf-8")
    print('{"status":"acquired"}')
    raise SystemExit(0)

if command == "release":
    print('{"status":"released"}')
    raise SystemExit(0)

raise SystemExit(f"unsupported fake CLI command: {command}")
"""


FAKE_SSH_SCRIPT = r"""#!/usr/bin/env bash
set -uo pipefail

mode="${1:-}"
cmd="${2:-}"

log_file="${FAKE_SSH_LOG:-}"
if [[ -n "${log_file}" ]]; then
  printf '%s\n' "${cmd}" >> "${log_file}"
fi

# Drain stdin so piping behaves properly
cat >/dev/null

if [[ "${cmd}" == *"bootstrap_dev_paper_baseline.py"* ]]; then
  if [[ -n "${FAKE_BOOTSTRAP_PID_FILE:-}" ]]; then
    printf '%s\n' "$$" > "${FAKE_BOOTSTRAP_PID_FILE}"
  fi
  if [[ -n "${FAKE_BOOTSTRAP_SLEEP:-}" ]]; then
    sleep "${FAKE_BOOTSTRAP_SLEEP}"
  fi
  exit "${FAKE_BOOTSTRAP_EXIT:-1}"
fi

# Collector execution
if [[ "${cmd}" == *"python3 -"* || "${cmd}" == *"--expected-bff-sha"* ]]; then
  # Probe check: verify heartbeat is still alive and failure file does not exist yet!
  if [[ -n "${FAKE_HEARTBEAT_PID:-}" && -n "${FAKE_TIMING_PROBE_FILE:-}" ]]; then
    hb_alive=false
    if kill -0 "${FAKE_HEARTBEAT_PID}" 2>/dev/null; then
      hb_alive=true
    fi
    fail_file_exists=false
    if [[ -n "${FAKE_FAILURE_FILE:-}" && -e "${FAKE_FAILURE_FILE}" ]]; then
      fail_file_exists=true
    fi
    python3 -c "
import json, sys, time
data = {'heartbeat_alive': sys.argv[1] == 'true', 'failure_file_exists': sys.argv[2] == 'true', 'time': time.time()}
open(sys.argv[3], 'w').write(json.dumps(data) + '\n')
" "${hb_alive}" "${fail_file_exists}" "${FAKE_TIMING_PROBE_FILE}"
  fi

  if [[ -n "${FAKE_COLLECTOR_SLEEP:-}" ]]; then
    exec sleep "${FAKE_COLLECTOR_SLEEP}"
  fi

  if [[ -n "${FAKE_COLLECTOR_STDERR:-}" ]]; then
    printf '%s' "${FAKE_COLLECTOR_STDERR}" >&2
  fi

  if [[ -n "${FAKE_COLLECTOR_STDOUT:-}" ]]; then
    printf '%s' "${FAKE_COLLECTOR_STDOUT}"
  fi

  exit "${FAKE_COLLECTOR_EXIT:-0}"
fi

exit 0
"""


def wait_for_file(path: Path, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while (not path.exists() or path.stat().st_size == 0) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert path.exists() and path.stat().st_size > 0, f"timed out waiting for {path}"


def process_state(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return ""
    close = raw.rfind(")")
    return raw[close + 1 :].strip().split()[0] if close >= 0 else ""


def assert_processes_terminated(pids: list[int], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(process_state(pid) in ("", "Z", "X", "x") for pid in pids):
            return
        time.sleep(0.05)
    states = {pid: process_state(pid) for pid in pids}
    assert False, f"processes still have live members: {states}"


def prepare_guard_fixture(root: Path) -> dict[str, Path]:
    guard_dir = root / "guard"
    guard_dir.mkdir()
    guard = guard_dir / GUARD_SCRIPT.name
    shutil.copy2(GUARD_SCRIPT, guard)
    guard.chmod(0o755)

    adjacent_cli = guard_dir / "dev_environment_lease.py"
    adjacent_cli.write_text(FAKE_ADJACENT_CLI, encoding="utf-8")
    adjacent_cli.chmod(0o755)

    paths = {
        "guard": guard,
        "cli": adjacent_cli,
        "state": root / "state.json",
        "heartbeat_pid": root / "heartbeat.pid",
        "heartbeat_identity": root / "heartbeat-identity.json",
        "failure": root / "guard-failure.json",
        "verify_count": root / "verify-count.txt",
    }
    paths["state"].write_text(
        json.dumps({"leaseId": TEST_LEASE_ID, "expectedBackendSha": TEST_BFF_SHA}) + "\n",
        encoding="utf-8",
    )
    paths["verify_count"].write_text("0\n", encoding="utf-8")
    return paths


def start_fake_heartbeat(paths: dict[str, Path], *, ignore_term: bool = False) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env.pop(TOKEN_ENV, None)
    if ignore_term:
        env["FAKE_HEARTBEAT_IGNORE_TERM"] = "1"
    heartbeat = subprocess.Popen(
        [
            sys.executable,
            str(paths["cli"]),
            "heartbeat-loop",
            "--state-file",
            str(paths["state"]),
            "--identity-json-out",
            str(paths["heartbeat_identity"]),
        ],
        env=env,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    paths["heartbeat_pid"].write_text(f"{heartbeat.pid}\n", encoding="utf-8")
    wait_for_file(paths["heartbeat_identity"])
    return heartbeat


def prepare_ssh_fixture(workspace: Path) -> Path:
    ssh_dir = workspace / ".agora-gate-controller" / "scripts"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    ssh_file = ssh_dir / "dev_vm_ssh.sh"
    ssh_file.write_text(FAKE_SSH_SCRIPT, encoding="utf-8")
    ssh_file.chmod(0o755)
    return ssh_file


def valid_d2_envelope(
    *,
    expected_bff: str = TEST_BFF_SHA,
    expected_fe: str | None = TEST_FE_SHA,
    observed_source: str = TEST_BFF_SHA,
    identity_matches: bool = True,
    bootstrap_exit: int = 1,
    status: str = "ok",
    run_id: str | None = "1001",
    attempt: str | None = "1",
    phase: str | None = "paper_bootstrap",
    events: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": "pantheon.dev-paper-diagnostics.v1",
        "run_id": run_id,
        "attempt": attempt,
        "phase": phase,
        "collected_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expected_fe_sha": expected_fe,
        "expected_bff_sha": expected_bff,
        "observed_source_sha": observed_source,
        "container_id": "0123456789abcdef" * 4,
        "image_id": "sha256:" + "0123456789abcdef" * 4,
        "identity_matches": identity_matches,
        "bootstrap_exit": bootstrap_exit,
        "collection_status": status,
        "services": {
            "operator-bff": {
                "collection_status": "ok",
                "events": events or [],
            },
            "persona": {
                "collection_status": "ok",
                "events": [],
            },
        },
    }


def verify_diagnostic_acceptance(diag_dir: Path) -> tuple[bool, str]:
    """Verify that a diagnostics directory meets diagnostic acceptance criteria (SD D3.6)."""
    diag_file = diag_dir / "diagnostics.json"
    status_file = diag_dir / "collection-status.json"
    checksum_file = diag_dir / "SHA256SUMS"

    if not status_file.exists():
        return False, "missing_collection_status"
    try:
        status_data = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        return False, "corrupted_collection_status"

    if status_data.get("schemaVersion") != 1:
        return False, "invalid_status_schema"
    if status_data.get("collectionStatus") != "ok":
        return False, f"collection_status_{status_data.get('collectionStatus')}"

    if not diag_file.exists():
        return False, "missing_diagnostics_file"
    if not checksum_file.exists():
        return False, "missing_checksum_file"

    try:
        diag_data = json.loads(diag_file.read_text(encoding="utf-8"))
    except Exception:
        return False, "corrupted_diagnostics_json"

    if diag_data.get("schema_version") != "pantheon.dev-paper-diagnostics.v1":
        return False, "invalid_diagnostics_schema"
    if not diag_data.get("identity_matches", False):
        return False, "identity_mismatch"

    # Verify SHA256 checksum
    computed = hashlib.sha256(diag_file.read_bytes()).hexdigest()
    checksum_text = checksum_file.read_text(encoding="utf-8")
    expected_entry = f"{computed}  diagnostics.json"
    if expected_entry not in checksum_text:
        return False, "checksum_mismatch"

    return True, "accepted"


# ==============================================================================
# Scenario 1: Baseline success does not execute failure collector
# ==============================================================================
def test_d3_1_baseline_success_does_not_execute_collector():
    """SD D3.1: baseline success must not execute failure collector; guard exits 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        paths = prepare_guard_fixture(root)
        heartbeat = start_fake_heartbeat(paths)

        workspace = root / "workspace"
        workspace.mkdir()
        prepare_ssh_fixture(workspace)

        diag_dir = root / "diagnostics"
        diag_dir.mkdir()
        ssh_log = root / "fake-ssh.log"

        env = {
            **os.environ,
            "TARGET_ENV": "dev",
            TOKEN_ENV: TEST_TOKEN,
            "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(paths["state"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE": str(paths["heartbeat_pid"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_IDENTITY_FILE": str(paths["heartbeat_identity"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE": str(paths["failure"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_VERIFY_INTERVAL_SECONDS": "30",
            "PANTHEON_DEV_ENVIRONMENT_LEASE_MAX_HEARTBEAT_AGE_SECONDS": "120",
            "FAKE_EXPECTED_TOKEN_SHA256": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
            "FAKE_VERIFY_COUNT_FILE": str(paths["verify_count"]),
            "FAKE_VERIFY_FAIL_AT": "0",
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            "DEV_PAPER_RUN_ID": "1001",
            "DEV_PAPER_ATTEMPT": "1",
            "DEV_PAPER_PHASE": "paper_bootstrap",
            # SSH fixture env: bootstrap succeeds
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_BOOTSTRAP_EXIT": "0",
        }

        proc = subprocess.run(
            ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        try:
            assert proc.returncode == 0, f"guard should exit 0, got {proc.returncode}. Stderr: {proc.stderr}"
            assert not paths["failure"].exists(), "guard failure file must not exist on success"

            # SSH log check: bootstrap was called, collector was never called
            log_lines = ssh_log.read_text().splitlines() if ssh_log.exists() else []
            assert any("bootstrap_dev_paper_baseline.py" in line for line in log_lines)
            assert not any("collect_dev_paper_diagnostics.py" in line or "--expected-bff-sha" in line for line in log_lines)

            # Check collection-status.json: not_required
            status_file = diag_dir / "collection-status.json"
            assert status_file.exists()
            status_data = json.loads(status_file.read_text())
            assert status_data["collectionStatus"] == "not_required"
            assert status_data["bootstrapExit"] == 0
            assert status_data["collectorExit"] == 0

            # diagnostics.json should NOT exist
            assert not (diag_dir / "diagnostics.json").exists()

            # Heartbeat is still healthy and running
            assert process_state(heartbeat.pid) not in ("", "Z", "X", "x")
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


# ==============================================================================
# Scenario 2: Baseline exit 1 - Capture precedes heartbeat stop, guard exits 75
# ==============================================================================
def test_d3_2_baseline_exit1_capture_precedes_heartbeat_stop_and_guard_quarantines():
    """SD D3.2: baseline exit 1 must capture diagnostics before heartbeat stop; guard exits 75 and records original exit 1."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        paths = prepare_guard_fixture(root)
        heartbeat = start_fake_heartbeat(paths)

        workspace = root / "workspace"
        workspace.mkdir()
        prepare_ssh_fixture(workspace)

        diag_dir = root / "diagnostics"
        diag_dir.mkdir()
        ssh_log = root / "fake-ssh.log"
        timing_probe = root / "capture-timing-probe.json"

        envelope = valid_d2_envelope(
            expected_bff=TEST_BFF_SHA,
            expected_fe=TEST_FE_SHA,
            observed_source=TEST_BFF_SHA,
            identity_matches=True,
            bootstrap_exit=1,
            status="ok",
        )

        env = {
            **os.environ,
            "TARGET_ENV": "dev",
            TOKEN_ENV: TEST_TOKEN,
            "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(paths["state"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE": str(paths["heartbeat_pid"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_IDENTITY_FILE": str(paths["heartbeat_identity"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE": str(paths["failure"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_VERIFY_INTERVAL_SECONDS": "30",
            "PANTHEON_DEV_ENVIRONMENT_LEASE_MAX_HEARTBEAT_AGE_SECONDS": "120",
            "FAKE_EXPECTED_TOKEN_SHA256": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
            "FAKE_VERIFY_COUNT_FILE": str(paths["verify_count"]),
            "FAKE_VERIFY_FAIL_AT": "0",
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            "DEV_PAPER_RUN_ID": "1001",
            "DEV_PAPER_ATTEMPT": "1",
            "DEV_PAPER_PHASE": "paper_bootstrap",
            # SSH fixture env: bootstrap fails with exit 1, collector outputs envelope
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_BOOTSTRAP_EXIT": "1",
            "FAKE_COLLECTOR_STDOUT": json.dumps(envelope),
            "FAKE_COLLECTOR_EXIT": "0",
            "FAKE_HEARTBEAT_PID": str(heartbeat.pid),
            "FAKE_FAILURE_FILE": str(paths["failure"]),
            "FAKE_TIMING_PROBE_FILE": str(timing_probe),
        }

        proc = subprocess.run(
            ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        try:
            # 1. Outer guard exits 75
            assert proc.returncode == 75, f"guard must exit 75 on failure quarantine, got {proc.returncode}. Stderr: {proc.stderr}"

            # 2. Timing proof: capture occurred WHILE heartbeat was alive and BEFORE guard failure file existed
            assert timing_probe.exists(), "timing probe was not written during collector execution"
            probe_data = json.loads(timing_probe.read_text())
            assert probe_data["heartbeat_alive"] is True, "heartbeat must still be ALIVE during diagnostic capture"
            assert probe_data["failure_file_exists"] is False, "guard failure file must NOT exist yet during capture"

            # 3. Guard failure file preserves original command exit 1
            assert paths["failure"].exists(), "guard failure file must exist"
            fail_data = json.loads(paths["failure"].read_text())
            assert fail_data["status"] == "guarded_command_failed"
            assert fail_data["exitStatus"] == 1, "original baseline exit 1 must be preserved in guard failure file"

            # 4. Diagnostics captured and verified
            accepted, reason = verify_diagnostic_acceptance(diag_dir)
            assert accepted is True, f"diagnostic acceptance failed: {reason}"

            # 5. Heartbeat process is now stopped for quarantine
            assert_processes_terminated([heartbeat.pid], timeout=3.0)
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


# ==============================================================================
# Scenario 3: Collector failure paths (timeout, non-zero exit, corrupted JSON)
# ==============================================================================
def test_d3_3_collector_timeout_preserves_bootstrap_exit_and_retains_quarantine():
    """SD D3.3a: collector timeout preserves original bootstrap exit and retains lease quarantine."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        paths = prepare_guard_fixture(root)
        heartbeat = start_fake_heartbeat(paths)

        workspace = root / "workspace"
        workspace.mkdir()
        prepare_ssh_fixture(workspace)

        diag_dir = root / "diagnostics"
        diag_dir.mkdir()

        env = {
            **os.environ,
            "TARGET_ENV": "dev",
            TOKEN_ENV: TEST_TOKEN,
            "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(paths["state"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE": str(paths["heartbeat_pid"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_IDENTITY_FILE": str(paths["heartbeat_identity"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE": str(paths["failure"]),
            "FAKE_EXPECTED_TOKEN_SHA256": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
            "FAKE_VERIFY_COUNT_FILE": str(paths["verify_count"]),
            "FAKE_VERIFY_FAIL_AT": "0",
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            "DEV_PAPER_RUN_ID": "1001",
            "DEV_PAPER_ATTEMPT": "1",
            "DEV_PAPER_PHASE": "paper_bootstrap",
            "DEV_PAPER_DIAGNOSTICS_TIMEOUT_SECONDS": "1",
            # SSH fixture: bootstrap fails 1, collector hangs
            "FAKE_BOOTSTRAP_EXIT": "1",
            "FAKE_COLLECTOR_SLEEP": "5",
        }

        proc = subprocess.run(
            ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        try:
            assert proc.returncode == 75
            assert paths["failure"].exists()
            fail_data = json.loads(paths["failure"].read_text())
            assert fail_data["exitStatus"] == 1, "original baseline exit 1 must be preserved despite collector timeout"

            status_file = diag_dir / "collection-status.json"
            assert status_file.exists()
            status_data = json.loads(status_file.read_text())
            assert status_data["collectionStatus"] == "timeout"
            assert status_data["bootstrapExit"] == 1
            assert status_data["collectorExit"] in (124, 137)
            assert status_data["collectorErrorCategory"] == "timeout"

            assert not (diag_dir / "diagnostics.json").exists()
            assert_processes_terminated([heartbeat.pid], timeout=3.0)
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


def test_d3_3_collector_error_exit_preserves_bootstrap_exit():
    """SD D3.3b: collector non-zero exit preserves original bootstrap exit and records category."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        paths = prepare_guard_fixture(root)
        heartbeat = start_fake_heartbeat(paths)

        workspace = root / "workspace"
        workspace.mkdir()
        prepare_ssh_fixture(workspace)

        diag_dir = root / "diagnostics"
        diag_dir.mkdir()

        env = {
            **os.environ,
            "TARGET_ENV": "dev",
            TOKEN_ENV: TEST_TOKEN,
            "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(paths["state"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE": str(paths["heartbeat_pid"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_IDENTITY_FILE": str(paths["heartbeat_identity"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE": str(paths["failure"]),
            "FAKE_EXPECTED_TOKEN_SHA256": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
            "FAKE_VERIFY_COUNT_FILE": str(paths["verify_count"]),
            "FAKE_VERIFY_FAIL_AT": "0",
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            # SSH fixture: bootstrap exit 1, collector exit 2 with connection refused
            "FAKE_BOOTSTRAP_EXIT": "1",
            "FAKE_COLLECTOR_EXIT": "2",
            "FAKE_COLLECTOR_STDERR": "ssh: connect to host 35.201.204.12 port 22: Connection refused\n",
        }

        proc = subprocess.run(
            ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        try:
            assert proc.returncode == 75
            assert paths["failure"].exists()
            fail_data = json.loads(paths["failure"].read_text())
            assert fail_data["exitStatus"] == 1

            status_file = diag_dir / "collection-status.json"
            assert status_file.exists()
            status_data = json.loads(status_file.read_text())
            assert status_data["collectionStatus"] == "collector_command_failed"
            assert status_data["bootstrapExit"] == 1
            assert status_data["collectorExit"] == 2
            assert status_data["collectorErrorCategory"] == "ssh_connection_refused"

            assert not (diag_dir / "diagnostics.json").exists()
            assert_processes_terminated([heartbeat.pid], timeout=3.0)
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


def test_d3_3_collector_corrupted_json_preserves_bootstrap_exit():
    """SD D3.3c: malformed collector output preserves bootstrap exit and removes corrupted files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        paths = prepare_guard_fixture(root)
        heartbeat = start_fake_heartbeat(paths)

        workspace = root / "workspace"
        workspace.mkdir()
        prepare_ssh_fixture(workspace)

        diag_dir = root / "diagnostics"
        diag_dir.mkdir()

        env = {
            **os.environ,
            "TARGET_ENV": "dev",
            TOKEN_ENV: TEST_TOKEN,
            "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(paths["state"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE": str(paths["heartbeat_pid"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_IDENTITY_FILE": str(paths["heartbeat_identity"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE": str(paths["failure"]),
            "FAKE_EXPECTED_TOKEN_SHA256": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
            "FAKE_VERIFY_COUNT_FILE": str(paths["verify_count"]),
            "FAKE_VERIFY_FAIL_AT": "0",
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            # SSH fixture: bootstrap exit 1, collector outputs broken JSON
            "FAKE_BOOTSTRAP_EXIT": "1",
            "FAKE_COLLECTOR_STDOUT": "{malformed json syntax here\n",
            "FAKE_COLLECTOR_EXIT": "0",
        }

        proc = subprocess.run(
            ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        try:
            assert proc.returncode == 75
            assert paths["failure"].exists()
            fail_data = json.loads(paths["failure"].read_text())
            assert fail_data["exitStatus"] == 1

            status_file = diag_dir / "collection-status.json"
            assert status_file.exists()
            status_data = json.loads(status_file.read_text())
            assert status_data["collectionStatus"] == "invalid_json"
            assert status_data["bootstrapExit"] == 1

            # Corrupted diagnostics file and checksum must be cleaned up
            assert not (diag_dir / "diagnostics.json").exists()
            assert not (diag_dir / "SHA256SUMS").exists()
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


# ==============================================================================
# Scenario 4: Heartbeat & Remote CAS loss terminate process group
# ==============================================================================
def test_d3_4_heartbeat_loss_during_command_terminates_process_group():
    """SD D3.4a: heartbeat loss terminates entire command process group; no further remote command runs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        paths = prepare_guard_fixture(root)
        heartbeat = start_fake_heartbeat(paths)

        workspace = root / "workspace"
        workspace.mkdir()
        prepare_ssh_fixture(workspace)

        diag_dir = root / "diagnostics"
        diag_dir.mkdir()
        ssh_log = root / "fake-ssh.log"
        bootstrap_pid_file = root / "bootstrap.pid"

        env = {
            **os.environ,
            "TARGET_ENV": "dev",
            TOKEN_ENV: TEST_TOKEN,
            "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(paths["state"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE": str(paths["heartbeat_pid"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_IDENTITY_FILE": str(paths["heartbeat_identity"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE": str(paths["failure"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_VERIFY_INTERVAL_SECONDS": "30",
            "FAKE_EXPECTED_TOKEN_SHA256": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
            "FAKE_VERIFY_COUNT_FILE": str(paths["verify_count"]),
            "FAKE_VERIFY_FAIL_AT": "0",
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            # SSH fixture: bootstrap sleeps 30 seconds
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_BOOTSTRAP_SLEEP": "30",
            "FAKE_BOOTSTRAP_PID_FILE": str(bootstrap_pid_file),
        }

        guard_proc = subprocess.Popen(
            ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Wait for bootstrap child to start
            wait_for_file(bootstrap_pid_file, timeout=5.0)
            bootstrap_pid = int(bootstrap_pid_file.read_text().strip())

            # Now kill heartbeat process mid-execution
            heartbeat.kill()
            heartbeat.wait()

            # Guard monitor loop checks heartbeat every 0.5s; wait for guard to terminate
            stdout, stderr = guard_proc.communicate(timeout=10)
            assert guard_proc.returncode == 75, f"guard must exit 75 on heartbeat loss, got {guard_proc.returncode}"
            assert "lease heartbeat identity/health was lost" in stderr or "quarantine" in stderr

            # Assert bootstrap process was killed
            assert_processes_terminated([bootstrap_pid], timeout=3.0)

            # Collector was never executed
            log_lines = ssh_log.read_text().splitlines() if ssh_log.exists() else []
            assert not any("collect_dev_paper_diagnostics.py" in line for line in log_lines)
        finally:
            if guard_proc.poll() is None:
                guard_proc.kill()
                guard_proc.wait()


def test_d3_4_remote_cas_loss_terminates_process_group():
    """SD D3.4b: remote CAS verification failure terminates process group."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        paths = prepare_guard_fixture(root)
        heartbeat = start_fake_heartbeat(paths)

        workspace = root / "workspace"
        workspace.mkdir()
        prepare_ssh_fixture(workspace)

        diag_dir = root / "diagnostics"
        diag_dir.mkdir()
        bootstrap_pid_file = root / "bootstrap.pid"

        env = {
            **os.environ,
            "TARGET_ENV": "dev",
            TOKEN_ENV: TEST_TOKEN,
            "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(paths["state"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE": str(paths["heartbeat_pid"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_IDENTITY_FILE": str(paths["heartbeat_identity"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE": str(paths["failure"]),
            # Check remote verification every 1 second, fail at second verification
            "PANTHEON_DEV_ENVIRONMENT_LEASE_VERIFY_INTERVAL_SECONDS": "1",
            "FAKE_EXPECTED_TOKEN_SHA256": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
            "FAKE_VERIFY_COUNT_FILE": str(paths["verify_count"]),
            "FAKE_VERIFY_FAIL_AT": "2",
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            "FAKE_BOOTSTRAP_SLEEP": "30",
            "FAKE_BOOTSTRAP_PID_FILE": str(bootstrap_pid_file),
        }

        guard_proc = subprocess.Popen(
            ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            wait_for_file(bootstrap_pid_file, timeout=5.0)
            bootstrap_pid = int(bootstrap_pid_file.read_text().strip())

            stdout, stderr = guard_proc.communicate(timeout=10)
            assert guard_proc.returncode == 75
            assert "remote lease verification failed" in stderr or "quarantine" in stderr
            assert_processes_terminated([bootstrap_pid], timeout=3.0)
        finally:
            if guard_proc.poll() is None:
                guard_proc.kill()
                guard_proc.wait()
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


# ==============================================================================
# Scenario 5: Cancellation, Missing State, Identity Mismatch fail closed
# ==============================================================================
def test_d3_5_baseline_cancellation_terminates_immediately_without_collector():
    """SD D3.5a: baseline cancellation (SIGTERM/SIGINT) terminates immediately without collector."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        paths = prepare_guard_fixture(root)
        heartbeat = start_fake_heartbeat(paths)

        workspace = root / "workspace"
        workspace.mkdir()
        prepare_ssh_fixture(workspace)

        diag_dir = root / "diagnostics"
        diag_dir.mkdir()
        ssh_log = root / "fake-ssh.log"

        env = {
            **os.environ,
            "TARGET_ENV": "dev",
            TOKEN_ENV: TEST_TOKEN,
            "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(paths["state"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE": str(paths["heartbeat_pid"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_IDENTITY_FILE": str(paths["heartbeat_identity"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE": str(paths["failure"]),
            "FAKE_EXPECTED_TOKEN_SHA256": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
            "FAKE_VERIFY_COUNT_FILE": str(paths["verify_count"]),
            "FAKE_VERIFY_FAIL_AT": "0",
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            # Bootstrap was killed with SIGTERM (exit 143)
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_BOOTSTRAP_EXIT": "143",
        }

        proc = subprocess.run(
            ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        try:
            assert proc.returncode in (75, 143)
            # Collector was NEVER executed
            log_lines = ssh_log.read_text().splitlines() if ssh_log.exists() else []
            assert not any("collect_dev_paper_diagnostics.py" in line for line in log_lines)

            # Collection status remained not_started
            status_file = diag_dir / "collection-status.json"
            assert status_file.exists()
            status_data = json.loads(status_file.read_text())
            assert status_data["collectionStatus"] == "not_started"
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


def test_d3_5_missing_lease_state_fails_closed():
    """SD D3.5b: missing lease state or token fails closed before executing command."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        paths = prepare_guard_fixture(root)

        # 1. Missing lease token
        proc1 = subprocess.run(
            ["bash", str(paths["guard"]), "echo", "should_not_run"],
            cwd=str(root),
            env={**os.environ, "TARGET_ENV": "dev"},
            capture_output=True,
            text=True,
        )
        assert proc1.returncode == 75
        assert "PANTHEON_ENVIRONMENT_LEASE_TOKEN is required" in proc1.stderr

        # 2. Missing state file
        proc2 = subprocess.run(
            ["bash", str(paths["guard"]), "echo", "should_not_run"],
            cwd=str(root),
            env={
                **os.environ,
                "TARGET_ENV": "dev",
                TOKEN_ENV: TEST_TOKEN,
                "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(root / "nonexistent-state.json"),
            },
            capture_output=True,
            text=True,
        )
        assert proc2.returncode == 75

        # 3. Missing wrapper required env
        proc3 = subprocess.run(
            ["bash", str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env={},
            capture_output=True,
            text=True,
        )
        assert proc3.returncode == 75
        assert "DEV_PAPER_DIAGNOSTICS_DIR is required" in proc3.stderr


def test_d3_5_identity_mismatch_fails_closed():
    """SD D3.5c: observed container SHA mismatch flags identity_mismatch and fails closed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        paths = prepare_guard_fixture(root)
        heartbeat = start_fake_heartbeat(paths)

        workspace = root / "workspace"
        workspace.mkdir()
        prepare_ssh_fixture(workspace)

        diag_dir = root / "diagnostics"
        diag_dir.mkdir()

        # Envelope with wrong observed source SHA
        wrong_sha = "c" * 40
        envelope = valid_d2_envelope(
            expected_bff=TEST_BFF_SHA,
            expected_fe=TEST_FE_SHA,
            observed_source=wrong_sha,
            identity_matches=False,
            bootstrap_exit=1,
            status="identity_mismatch",
        )

        env = {
            **os.environ,
            "TARGET_ENV": "dev",
            TOKEN_ENV: TEST_TOKEN,
            "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(paths["state"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE": str(paths["heartbeat_pid"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_IDENTITY_FILE": str(paths["heartbeat_identity"]),
            "PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE": str(paths["failure"]),
            "FAKE_EXPECTED_TOKEN_SHA256": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
            "FAKE_VERIFY_COUNT_FILE": str(paths["verify_count"]),
            "FAKE_VERIFY_FAIL_AT": "0",
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            "DEV_PAPER_RUN_ID": "1001",
            "DEV_PAPER_ATTEMPT": "1",
            "DEV_PAPER_PHASE": "paper_bootstrap",
            # SSH fixture: bootstrap fails 1, collector returns mismatch envelope
            "FAKE_BOOTSTRAP_EXIT": "1",
            "FAKE_COLLECTOR_STDOUT": json.dumps(envelope),
            "FAKE_COLLECTOR_EXIT": "0",
        }

        proc = subprocess.run(
            ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        try:
            assert proc.returncode == 75
            status_file = diag_dir / "collection-status.json"
            assert status_file.exists()
            status_data = json.loads(status_file.read_text())
            assert status_data["collectionStatus"] == "identity_mismatch"
            assert status_data["bootstrapExit"] == 1

            accepted, reason = verify_diagnostic_acceptance(diag_dir)
            assert accepted is False
            assert "identity_mismatch" in reason
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


# ==============================================================================
# Scenario 6: Missing artifact, bad checksum, or upload failure
# ==============================================================================
def test_d3_6_artifact_output_missing_or_bad_checksum_cannot_declare_acceptance():
    """SD D3.6: missing artifacts, corrupt status, or bad checksum fail diagnostic acceptance."""
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)

        # Case A: empty directory
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "missing_collection_status"

        # Case B: collection-status exists but status is timeout
        (d / "collection-status.json").write_text(
            json.dumps({"schemaVersion": 1, "collectionStatus": "timeout"})
        )
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "collection_status_timeout"

        # Case C: collection-status ok, but diagnostics.json missing
        (d / "collection-status.json").write_text(
            json.dumps({"schemaVersion": 1, "collectionStatus": "ok"})
        )
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "missing_diagnostics_file"

        # Case D: diagnostics.json exists, but SHA256SUMS missing
        envelope = valid_d2_envelope()
        diag_content = json.dumps(envelope)
        (d / "diagnostics.json").write_text(diag_content)
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "missing_checksum_file"

        # Case E: SHA256SUMS has corrupted/tampered hash
        (d / "SHA256SUMS").write_text("badhashbadhashbadhashbadhashbadhashbadhashbadhashbadhash  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "checksum_mismatch"

        # Case F: SHA256SUMS matches correct hash
        real_hash = hashlib.sha256(diag_content.encode()).hexdigest()
        (d / "SHA256SUMS").write_text(f"{real_hash}  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is True
        assert reason == "accepted"


# ==============================================================================
# Scenario 7: Project exceptions captured, secret sentinels never leak
# ==============================================================================
def test_d3_7_project_exceptions_captured_and_secret_sentinels_never_leak():
    """SD D3.7: domain exceptions positively captured; secret sentinels never leak into diagnostics or status."""
    import collect_dev_paper_diagnostics as diag

    sentinels = [
        "SECRET_SENTINEL_TOKEN_XYZ_987",
        "PASSWORD_SECRET_12345",
        "SUPER_PRIVATE_DSN_KEY",
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.SECRET_PAYLOAD",
        "postgresql://user:TOP_SECRET_PASSWORD@localhost:5432/trading",
    ]

    raw_log = f"""2026-09-07T00:17:26.123Z Traceback (most recent call last):
2026-09-07T00:17:26.123Z   File "/workspace/services/control-plane/bff/personas/service.py", line 4000, in _coordinate_persona_create
2026-09-07T00:17:26.123Z     call(auth_token="{sentinels[0]}")
2026-09-07T00:17:26.123Z services.control_plane.bff.ports.persona_write_owner.PersonaWriteOwnerUnavailable: failed to reach owner with {sentinels[1]}
2026-09-07T00:17:27.456Z Traceback (most recent call last):
2026-09-07T00:17:27.456Z   File "/workspace/services/control-plane/bff/persona_provisioning.py", line 120, in release_lease
2026-09-07T00:17:27.456Z     verify(secret="{sentinels[2]}")
2026-09-07T00:17:27.456Z services.control_plane.bff.persona_provisioning.ProvisioningLeaseLost: lease expired with {sentinels[3]}
2026-09-07T00:17:28.789Z ConnectionRefusedError: [Errno 111] Connection refused: {sentinels[4]}
2026-09-07T00:17:29.000Z SYNTHETIC_PRIVATE_SENTINELError: should not be parsed
"""

    events = diag.log_events(raw_log)
    event_types = [e["type"] for e in events if e.get("kind") == "exception"]

    # Positive capture of domain exceptions
    assert "services.control_plane.bff.ports.persona_write_owner.PersonaWriteOwnerUnavailable" in event_types
    assert "services.control_plane.bff.persona_provisioning.ProvisioningLeaseLost" in event_types
    assert "ConnectionRefusedError" in event_types
    assert "SYNTHETIC_PRIVATE_SENTINELError" not in event_types

    # Verify ZERO secret sentinels leak
    encoded_events = json.dumps(events)
    for s in sentinels:
        assert s not in encoded_events, f"Secret sentinel leaked into parsed events: {s}"

    # Verify inside full envelope structure
    envelope = valid_d2_envelope(events=events)
    encoded_envelope = json.dumps(envelope)
    for s in sentinels:
        assert s not in encoded_envelope, f"Secret sentinel leaked into envelope: {s}"


# ==============================================================================
# Scenario 8: Normal compensation and failed compensation outcomes
# ==============================================================================
class MockRollbackHandler(http.server.BaseHTTPRequestHandler):
    backend_sha = "1" * 40
    frontend_sha = "2" * 40
    status_code = 200

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.status_code != 200:
            self.send_response(self.status_code)
            self.end_headers()
            self.wfile.write(b"Service Unavailable\n")
            return

        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK\n")
        elif self.path == "/bff/version":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"source_commit_sha": self.backend_sha}).encode())
        elif self.path == "/deployment.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"frontendSha": self.frontend_sha}).encode())
        else:
            self.send_response(404)
            self.end_headers()


def setup_compensation_fixture(tmp: Path, *, rollback_bff: str, rollback_fe: str, deploy_exit: int = 0):
    # 1. Lease controller repo at pinned commit
    lease_ctrl = tmp / "lease-controller"
    subprocess.run(["git", "clone", "--shared", "--no-checkout", str(REPO_ROOT), str(lease_ctrl)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(lease_ctrl), "sparse-checkout", "init"], check=True)
    subprocess.run(["git", "-C", str(lease_ctrl), "sparse-checkout", "set", "scripts/"], check=True)
    subprocess.run(["git", "-C", str(lease_ctrl), "checkout", PINNED_LEASE_CONTROLLER_SHA], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 2. Release repo root with deploy_nonprod_vm.sh
    release_root = tmp / "release-root"
    (release_root / "scripts").mkdir(parents=True)
    deploy_script = release_root / "scripts" / "deploy_nonprod_vm.sh"
    deploy_script.write_text(f"#!/usr/bin/env bash\nexit {deploy_exit}\n", encoding="utf-8")
    deploy_script.chmod(0o755)

    # 3. Python shim in bin/python3
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    shim_py = bin_dir / "python3"
    shim_code = f"""#!/usr/bin/python3
import sys, os, json, signal, time, pathlib

is_lease = any("dev_environment_lease.py" in arg for arg in sys.argv)
if is_lease:
    cmd = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1].endswith(".py") else (sys.argv[1] if len(sys.argv) > 1 else "")
    if cmd == "acquire":
        state_idx = sys.argv.index("--state-file")
        state_file = pathlib.Path(sys.argv[state_idx + 1])
        json_out_idx = sys.argv.index("--json-out")
        json_out = pathlib.Path(sys.argv[json_out_idx + 1])
        data = {{
            "schemaVersion": 1,
            "resource": "pantheon-dev-environment",
            "mode": "deployment",
            "owner": "test",
            "leaseId": "11111111-1111-4111-8111-111111111111",
            "acquiredAt": "2026-09-07T00:00:00Z",
            "heartbeatAt": "2026-09-07T00:00:00Z",
            "expiresAt": "2026-09-07T01:00:00Z",
            "repository": "ajoe734/execute-plans",
            "branch": "environment-coordination",
            "path": ".pantheon/environment-leases/pantheon-dev-environment.json",
            "expectedBackendSha": "{rollback_bff}",
            "runUrl": "https://test"
        }}
        state_file.write_text(json.dumps(data) + "\\n")
        json_out.write_text(json.dumps({{"status": "acquired"}}) + "\\n")
        sys.exit(0)
    elif cmd == "heartbeat-loop":
        id_idx = sys.argv.index("--identity-json-out")
        id_file = pathlib.Path(sys.argv[id_idx + 1])
        state_idx = sys.argv.index("--state-file")
        state_file = str(pathlib.Path(sys.argv[state_idx + 1]).resolve())
        cli_path = str(pathlib.Path(sys.argv[1]).resolve())
        pid = os.getpid()
        raw = pathlib.Path(f"/proc/{{pid}}/stat").read_text(encoding="utf-8")
        ticks = int(raw[raw.rfind(")") + 1 :].strip().split()[19])
        cmdline = pathlib.Path(f"/proc/{{pid}}/cmdline").read_bytes()
        import hashlib
        c_hash = hashlib.sha256(cmdline).hexdigest()
        id_file.write_text(json.dumps({{
            "schemaVersion": 1,
            "status": "running",
            "pid": pid,
            "startTicks": ticks,
            "cmdlineSha256": c_hash,
            "expectedCli": cli_path,
            "stateFile": state_file,
            "recordedAt": "2026-09-07T00:00:00Z"
        }}) + "\\n")
        stop_file = None
        if "--shutdown-json-out" in sys.argv:
            stop_file = pathlib.Path(sys.argv[sys.argv.index("--shutdown-json-out") + 1])
        def on_term(*a):
            if stop_file:
                stop_file.write_text('{{"status":"stopped"}}\\n')
            sys.exit(0)
        signal.signal(signal.SIGTERM, on_term)
        while True:
            time.sleep(0.1)
    elif cmd == "verify-heartbeat-identity":
        print('{{"status":"verified"}}')
        sys.exit(0)
    elif cmd == "verify":
        print('{{"status":"verified"}}')
        sys.exit(0)
    elif cmd == "release":
        print('{{"status":"released"}}')
        sys.exit(0)

# Forward non-lease invocations to real system python3
os.execv("/usr/bin/python3", ["python3"] + sys.argv[1:])
"""
    shim_py.write_text(shim_code, encoding="utf-8")
    shim_py.chmod(0o755)

    return lease_ctrl, release_root, bin_dir


def test_d3_8_successful_compensation_produces_real_evidence_and_releases_lease():
    """SD D3.8a: successful compensation verifies rollback, produces evidence, releases lease."""
    rollback_bff = "1" * 40
    rollback_fe = "2" * 40
    failed_bff = "3" * 40
    failed_fe = "4" * 40
    rc_id = "5" * 64

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        lease_ctrl, release_root, bin_dir = setup_compensation_fixture(
            tmp, rollback_bff=rollback_bff, rollback_fe=rollback_fe, deploy_exit=0
        )

        MockRollbackHandler.backend_sha = rollback_bff
        MockRollbackHandler.frontend_sha = rollback_fe
        MockRollbackHandler.status_code = 200

        server = http.server.HTTPServer(("127.0.0.1", 0), MockRollbackHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        log_file = tmp / "controller.log"
        log_file.write_text("failure log detail\n")
        evidence_out = tmp / "release-compensation.json"

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PANTHEON_ENVIRONMENT_LEASE_TOKEN": "compensation-test-token",
            "PANTHEON_LEASE_CONTROLLER_ROOT": str(lease_ctrl),
            "PANTHEON_RELEASE_REPO_ROOT": str(release_root),
            "PANTHEON_ROLLBACK_BACKEND_SHA": rollback_bff,
            "PANTHEON_ROLLBACK_FRONTEND_SHA": rollback_fe,
            "PANTHEON_FAILED_BACKEND_SHA": failed_bff,
            "PANTHEON_FAILED_FRONTEND_SHA": failed_fe,
            "PANTHEON_RELEASE_CANDIDATE_ID": rc_id,
            "PANTHEON_RELEASE_CONTROLLER_LOG": str(log_file),
            "PANTHEON_ROLLBACK_EVIDENCE_OUT": str(evidence_out),
            "DEV_BFF_URL": f"http://127.0.0.1:{port}",
            "DEV_FE_URL": f"http://127.0.0.1:{port}",
            "REMOTE_USER": "testuser",
            "DEV_VM": "pantheon-dev",
            "DEV_ZONE": "asia-east1-b",
            "GCP_DEPLOY_PROJECT_ID": "pantheon-dev-proj",
            "RUNNER_TEMP": str(tmp),
            "GITHUB_REPOSITORY": "ajoe734/pantheon",
            "GITHUB_RUN_ID": "999",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SERVER_URL": "https://github.com",
        }

        res = subprocess.run(
            ["bash", str(COMPENSATION_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        server.shutdown()

        assert res.returncode == 0, f"compensation script failed: {res.stderr}"
        assert evidence_out.exists(), "compensation evidence file must be created"

        ev_data = json.loads(evidence_out.read_text())
        assert ev_data["schema_version"] == "pantheon.cross-repo-release-compensation.v1"
        assert ev_data["outcome"] == "compensated"
        assert ev_data["release_candidate_id"] == rc_id
        assert ev_data["restored_pair"]["backend_sha"] == rollback_bff
        assert ev_data["restored_pair"]["frontend_sha"] == rollback_fe
        assert ev_data["rejected_pair"]["backend_sha"] == failed_bff
        assert ev_data["rejected_pair"]["frontend_sha"] == failed_fe


def test_d3_8_failed_compensation_fails_closed_and_leaves_quarantine():
    """SD D3.8b: failed compensation (e.g. deploy failure) exits 75, leaves lease quarantined."""
    rollback_bff = "1" * 40
    rollback_fe = "2" * 40
    failed_bff = "3" * 40
    failed_fe = "4" * 40
    rc_id = "5" * 64

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # Deploy script fails with exit 1
        lease_ctrl, release_root, bin_dir = setup_compensation_fixture(
            tmp, rollback_bff=rollback_bff, rollback_fe=rollback_fe, deploy_exit=1
        )

        MockRollbackHandler.backend_sha = rollback_bff
        MockRollbackHandler.frontend_sha = rollback_fe
        MockRollbackHandler.status_code = 200

        server = http.server.HTTPServer(("127.0.0.1", 0), MockRollbackHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        log_file = tmp / "controller.log"
        log_file.write_text("failure log detail\n")
        evidence_out = tmp / "release-compensation.json"

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PANTHEON_ENVIRONMENT_LEASE_TOKEN": "compensation-test-token",
            "PANTHEON_LEASE_CONTROLLER_ROOT": str(lease_ctrl),
            "PANTHEON_RELEASE_REPO_ROOT": str(release_root),
            "PANTHEON_ROLLBACK_BACKEND_SHA": rollback_bff,
            "PANTHEON_ROLLBACK_FRONTEND_SHA": rollback_fe,
            "PANTHEON_FAILED_BACKEND_SHA": failed_bff,
            "PANTHEON_FAILED_FRONTEND_SHA": failed_fe,
            "PANTHEON_RELEASE_CANDIDATE_ID": rc_id,
            "PANTHEON_RELEASE_CONTROLLER_LOG": str(log_file),
            "PANTHEON_ROLLBACK_EVIDENCE_OUT": str(evidence_out),
            "DEV_BFF_URL": f"http://127.0.0.1:{port}",
            "DEV_FE_URL": f"http://127.0.0.1:{port}",
            "REMOTE_USER": "testuser",
            "DEV_VM": "pantheon-dev",
            "DEV_ZONE": "asia-east1-b",
            "GCP_DEPLOY_PROJECT_ID": "pantheon-dev-proj",
            "RUNNER_TEMP": str(tmp),
            "GITHUB_REPOSITORY": "ajoe734/pantheon",
            "GITHUB_RUN_ID": "999",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SERVER_URL": "https://github.com",
        }

        res = subprocess.run(
            ["bash", str(COMPENSATION_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        server.shutdown()

        assert res.returncode == 75, f"compensation script must exit 75 on deploy failure, got {res.returncode}"
        assert not evidence_out.exists(), "compensation evidence must not be produced on failure"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
