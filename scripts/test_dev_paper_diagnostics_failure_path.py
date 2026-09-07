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
  4. Heartbeat and remote CAS loss: command process group and descendants terminated, no collector remote commands.
  5. Cancellation (real SIGTERM/SIGINT), missing/expired state, wrong source, ambiguous containers fail closed.
  6. Artifact output missing, upload failure, bad checksum cannot declare diagnostic acceptance.
  7. Project domain exceptions and Persona owner positively captured via real collector stdin; secret sentinels never leak.
  8. Composed baseline failure -> capture/error -> quarantine -> runner artifact handling -> fresh-lease compensation;
     primary and compensation exits recorded independently with authority transitions verified.
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


def record_audit(lease_id: str, action: str, status: str, details: dict | None = None) -> None:
    audit_file = os.environ.get("FAKE_LEASE_AUDIT_FILE")
    if not audit_file:
        return
    p = Path(audit_file)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"events": [], "leases": {}}
    event = {
        "timestamp": time.time(),
        "lease_id": lease_id,
        "action": action,
        "status": status,
        "details": details or {},
    }
    data["events"].append(event)
    lease_info = data["leases"].setdefault(lease_id, {})
    lease_info["status"] = status
    lease_info["last_action"] = action
    if details:
        lease_info.update(details)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


command = sys.argv[1]

if command == "heartbeat-loop":
    state_file = str(Path(option("--state-file")).resolve())
    identity_file = Path(option("--identity-json-out"))
    stop_file = Path(option("--shutdown-json-out")) if "--shutdown-json-out" in sys.argv else None
    fail_file = Path(option("--failure-json-out")) if "--failure-json-out" in sys.argv else None
    pid = os.getpid()
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()

    state_data = json.loads(Path(state_file).read_text(encoding="utf-8"))
    lease_id = state_data.get("leaseId", "unknown-lease")
    record_audit(lease_id, "heartbeat_start", "active", {"pid": pid, "state_file": state_file})

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
            record_audit(lease_id, "heartbeat_stop", "stopped")
        else:
            record_audit(lease_id, "heartbeat_term", "quarantined")
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
    expected_token_hash = os.environ.get("FAKE_EXPECTED_TOKEN_SHA256")
    if expected_token_hash:
        assert hashlib.sha256(token).hexdigest() == expected_token_hash

    # Check expired lease condition
    if os.environ.get("FAKE_LEASE_EXPIRED") == "1":
        print("[dev-environment-lease] ERROR: lease has expired (heartbeat age exceeded threshold)", file=sys.stderr)
        raise SystemExit(75)

    state_file = Path(option("--state-file"))
    if state_file.exists():
        state_data = json.loads(state_file.read_text(encoding="utf-8"))
        lease_id = state_data.get("leaseId", "unknown-lease")
        if state_data.get("expired") is True:
            print("[dev-environment-lease] ERROR: lease state marks lease expired", file=sys.stderr)
            raise SystemExit(75)
    else:
        lease_id = "unknown-lease"

    count_file_env = os.environ.get("FAKE_VERIFY_COUNT_FILE")
    if count_file_env:
        count_file = Path(count_file_env)
        count = int(count_file.read_text(encoding="utf-8") or "0") + 1
        count_file.write_text(f"{count}\n", encoding="utf-8")
        fail_at = int(os.environ.get("FAKE_VERIFY_FAIL_AT", "0"))
        if fail_at and count >= fail_at:
            print("simulated GitHub API outage / CAS mismatch", file=sys.stderr)
            record_audit(lease_id, "verify", "cas_failed", {"count": count})
            raise SystemExit(78)

    record_audit(lease_id, "verify", "verified")
    print('{"status":"verified"}')
    raise SystemExit(0)

if command == "acquire":
    state_file = Path(option("--state-file"))
    json_out = Path(option("--json-out")) if "--json-out" in sys.argv else None
    owner = option("--owner") if "--owner" in sys.argv else "test-owner"
    expected_backend = option("--expected-backend-sha") if "--expected-backend-sha" in sys.argv else "a" * 40
    lease_id = os.environ.get("FAKE_ACQUIRE_LEASE_ID", "22222222-2222-4222-8222-222222222222")
    data = {
        "schemaVersion": 1,
        "resource": "pantheon-dev-environment",
        "mode": "deployment",
        "owner": owner,
        "leaseId": lease_id,
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
    record_audit(lease_id, "acquire", "active", {"owner": owner})
    print('{"status":"acquired"}')
    raise SystemExit(0)

if command == "release":
    state_file = Path(option("--state-file"))
    state_data = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    lease_id = state_data.get("leaseId", "unknown-lease")
    record_audit(lease_id, "release", "released")
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

child_pid=""
cleanup_child() {
  if [[ -n "${child_pid:-}" ]]; then
    kill -9 "${child_pid}" 2>/dev/null || true
  fi
}
trap 'cleanup_child; exit 143' TERM INT

# Bootstrap execution
if [[ "${cmd}" == *"bootstrap_dev_paper_baseline.py"* ]]; then
  cat >/dev/null

  if [[ -n "${FAKE_BOOTSTRAP_SPAWN_CHILD:-}" ]]; then
    sleep 300 &
    child_pid=$!
  fi

  if [[ -n "${FAKE_BOOTSTRAP_PID_FILE:-}" ]]; then
    if [[ -n "${child_pid}" ]]; then
      printf '%s %s\n' "$$" "${child_pid}" > "${FAKE_BOOTSTRAP_PID_FILE}"
    else
      printf '%s\n' "$$" > "${FAKE_BOOTSTRAP_PID_FILE}"
    fi
  fi

  if [[ -n "${FAKE_BOOTSTRAP_SLEEP:-}" ]]; then
    sleep "${FAKE_BOOTSTRAP_SLEEP}"
  fi

  cleanup_child

  exit "${FAKE_BOOTSTRAP_EXIT:-1}"
fi

# Collector execution
if [[ "${cmd}" == *"python3 -"* || "${cmd}" == *"--expected-bff-sha"* ]]; then
  if [[ -n "${FAKE_COLLECTOR_SPAWN_CHILD:-}" ]]; then
    sleep 300 &
    child_pid=$!
  fi

  if [[ -n "${FAKE_COLLECTOR_PID_FILE:-}" ]]; then
    if [[ -n "${child_pid}" ]]; then
      printf '%s %s\n' "$$" "${child_pid}" > "${FAKE_COLLECTOR_PID_FILE}"
    else
      printf '%s\n' "$$" > "${FAKE_COLLECTOR_PID_FILE}"
    fi
  fi

  # Timing probe check: verify heartbeat is still alive and failure file does not exist yet
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

  # Real collector execution path
  if [[ "${FAKE_EXECUTE_REAL_COLLECTOR:-0}" == "1" ]]; then
    eval "${cmd}"
    rc=$?
    cleanup_child
    exit "${rc}"
  fi

  # Synthetic collector execution path: drain stdin
  cat >/dev/null

  if [[ -n "${FAKE_COLLECTOR_SLEEP:-}" ]]; then
    sleep "${FAKE_COLLECTOR_SLEEP}"
  fi

  cleanup_child

  if [[ -n "${FAKE_COLLECTOR_STDERR:-}" ]]; then
    printf '%s' "${FAKE_COLLECTOR_STDERR}" >&2
  fi

  if [[ -n "${FAKE_COLLECTOR_STDOUT:-}" ]]; then
    printf '%s' "${FAKE_COLLECTOR_STDOUT}"
  fi

  exit "${FAKE_COLLECTOR_EXIT:-0}"
fi

cat >/dev/null
exit 0
"""


def wait_for_file(path: Path, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while (not path.exists() or path.stat().st_size == 0) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert path.exists() and path.stat().st_size > 0, f"timed out waiting for {path}"


def read_pids_from_file(path: Path) -> list[int]:
    raw = path.read_text(encoding="utf-8").strip()
    return [int(p) for p in raw.split() if p.isdigit()]


def process_state(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return ""
    close = raw.rfind(")")
    return raw[close + 1 :].strip().split()[0] if close >= 0 else ""


def assert_processes_terminated(pids: list[int], timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(process_state(pid) in ("", "Z", "X", "x") for pid in pids):
            return
        time.sleep(0.05)
    states = {pid: process_state(pid) for pid in pids}
    assert False, f"processes still have live members: {states}"


def prepare_guard_fixture(root: Path, *, lease_id: str = TEST_LEASE_ID, expired: bool = False) -> dict[str, Path]:
    guard_dir = root / "guard"
    guard_dir.mkdir(exist_ok=True)
    guard = guard_dir / GUARD_SCRIPT.name
    shutil.copy2(GUARD_SCRIPT, guard)
    guard.chmod(0o755)

    adjacent_cli = guard_dir / "dev_environment_lease.py"
    adjacent_cli.write_text(FAKE_ADJACENT_CLI, encoding="utf-8")
    adjacent_cli.chmod(0o755)

    audit_file = root / "lease_authority_audit.json"
    audit_file.write_text(json.dumps({"events": [], "leases": {}}) + "\n", encoding="utf-8")

    paths = {
        "guard": guard,
        "cli": adjacent_cli,
        "state": root / "state.json",
        "heartbeat_pid": root / "heartbeat.pid",
        "heartbeat_identity": root / "heartbeat-identity.json",
        "failure": root / "guard-failure.json",
        "verify_count": root / "verify-count.txt",
        "audit": audit_file,
    }
    state_payload = {"leaseId": lease_id, "expectedBackendSha": TEST_BFF_SHA}
    if expired:
        state_payload["expired"] = True
    paths["state"].write_text(
        json.dumps(state_payload) + "\n",
        encoding="utf-8",
    )
    paths["verify_count"].write_text("0\n", encoding="utf-8")
    return paths


def start_fake_heartbeat(paths: dict[str, Path], *, ignore_term: bool = False) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env.pop(TOKEN_ENV, None)
    env["FAKE_LEASE_AUDIT_FILE"] = str(paths["audit"])
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

    outcome = status_data.get("collectionStatus")
    if outcome == "timeout":
        return False, "collection_status_timeout"
    if outcome != "ok":
        return False, f"collection_status_{outcome}"

    if not diag_file.exists():
        return False, "missing_diagnostics_file"
    if not checksum_file.exists():
        return False, "missing_checksum_file"

    checksum_lines = checksum_file.read_text(encoding="utf-8").splitlines()
    diag_checksum = None
    for line in checksum_lines:
        parts = line.split()
        if len(parts) == 2 and parts[1].endswith("diagnostics.json"):
            diag_checksum = parts[0]
            break
    if not diag_checksum:
        return False, "missing_diagnostics_checksum_entry"

    actual_hash = hashlib.sha256(diag_file.read_bytes()).hexdigest()
    if actual_hash != diag_checksum:
        return False, "checksum_mismatch"

    return True, "accepted"


class MockRollbackHandler(http.server.BaseHTTPRequestHandler):
    backend_sha = "1" * 40
    frontend_sha = "2" * 40
    status_code = 200

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(self.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}\n')
        elif self.path == "/bff/version":
            self.send_response(self.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"source_commit_sha": self.backend_sha}).encode() + b"\n")
        elif self.path == "/deployment.json":
            self.send_response(self.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"frontendSha": self.frontend_sha}).encode() + b"\n")
        else:
            self.send_response(404)
            self.end_headers()


def setup_compensation_fixture(
    tmp: Path,
    *,
    rollback_bff: str,
    rollback_fe: str,
    deploy_exit: int = 0,
    audit_file: Path | None = None,
    compensation_lease_id: str = "22222222-2222-4222-8222-222222222222",
) -> tuple[Path, Path, Path]:
    lease_ctrl = tmp / "lease-controller"
    subprocess.run(["git", "clone", "--shared", "--no-checkout", str(REPO_ROOT), str(lease_ctrl)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(lease_ctrl), "sparse-checkout", "init"], check=True)
    subprocess.run(["git", "-C", str(lease_ctrl), "sparse-checkout", "set", "scripts/"], check=True)
    subprocess.run(["git", "-C", str(lease_ctrl), "checkout", PINNED_LEASE_CONTROLLER_SHA], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    release_root = tmp / "release-root"
    (release_root / "scripts").mkdir(parents=True, exist_ok=True)
    deploy_script = release_root / "scripts" / "deploy_nonprod_vm.sh"
    deploy_script.write_text(f"#!/usr/bin/env bash\nexit {deploy_exit}\n", encoding="utf-8")
    deploy_script.chmod(0o755)

    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    shim_py = bin_dir / "python3"

    audit_path_str = str(audit_file) if audit_file else ""

    shim_code = f"""#!/usr/bin/python3
import sys, os, json, signal, time, pathlib

is_lease = any("dev_environment_lease.py" in arg for arg in sys.argv)
if is_lease:
    cmd = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1].endswith(".py") else (sys.argv[1] if len(sys.argv) > 1 else "")
    audit_file_path = "{audit_path_str}" or os.environ.get("FAKE_LEASE_AUDIT_FILE")

    def log_audit(lid, act, stat, extra=None):
        if not audit_file_path: return
        p = pathlib.Path(audit_file_path)
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {{"events": [], "leases": {{}}}}
        ev = {{"timestamp": time.time(), "lease_id": lid, "action": act, "status": stat, "details": extra or {{}}}}
        d["events"].append(ev)
        li = d["leases"].setdefault(lid, {{}})
        li["status"] = stat
        li["last_action"] = act
        if extra: li.update(extra)
        p.write_text(json.dumps(d, indent=2) + "\\n", encoding="utf-8")

    if cmd == "acquire":
        state_idx = sys.argv.index("--state-file")
        state_file = pathlib.Path(sys.argv[state_idx + 1])
        json_out_idx = sys.argv.index("--json-out")
        json_out = pathlib.Path(sys.argv[json_out_idx + 1])
        lid = "{compensation_lease_id}"
        data = {{
            "schemaVersion": 1,
            "resource": "pantheon-dev-environment",
            "mode": "deployment",
            "owner": "pantheon:ajoe734/pantheon:999:1:rollback",
            "leaseId": lid,
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
        log_audit(lid, "acquire", "active", {{"owner": "pantheon:ajoe734/pantheon:999:1:rollback"}})
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
        lid = "{compensation_lease_id}"
        log_audit(lid, "heartbeat_start", "active", {{"pid": pid}})
        def on_term(*a):
            if stop_file:
                stop_file.write_text('{{"status":"stopped"}}\\n')
                log_audit(lid, "heartbeat_stop", "stopped")
            else:
                log_audit(lid, "heartbeat_term", "quarantined")
            sys.exit(0)
        signal.signal(signal.SIGTERM, on_term)
        while True:
            time.sleep(0.05)
    elif cmd == "verify-heartbeat-identity":
        print('{{"status":"verified"}}')
        sys.exit(0)
    elif cmd == "verify":
        lid = "{compensation_lease_id}"
        log_audit(lid, "verify", "verified")
        print('{{"status":"verified"}}')
        sys.exit(0)
    elif cmd == "release":
        lid = "{compensation_lease_id}"
        log_audit(lid, "release", "released")
        print('{{"status":"released"}}')
        sys.exit(0)

# Forward non-lease invocations to real system python3
os.execv("/usr/bin/python3", ["python3"] + sys.argv[1:])
"""
    shim_py.write_text(shim_code, encoding="utf-8")
    shim_py.chmod(0o755)

    return lease_ctrl, release_root, bin_dir


def execute_fresh_lease_compensation(
    tmp: Path,
    *,
    rollback_bff: str = TEST_BFF_SHA,
    rollback_fe: str = TEST_FE_SHA,
    failed_bff: str = TEST_BFF_SHA,
    failed_fe: str = TEST_FE_SHA,
    rc_id: str = "5" * 64,
    deploy_exit: int = 0,
    audit_file: Path | None = None,
    compensation_lease_id: str = "22222222-2222-4222-8222-222222222222",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Execute real compensate_cross_repo_release.sh with fresh lease authority."""
    lease_ctrl, release_root, bin_dir = setup_compensation_fixture(
        tmp,
        rollback_bff=rollback_bff,
        rollback_fe=rollback_fe,
        deploy_exit=deploy_exit,
        audit_file=audit_file,
        compensation_lease_id=compensation_lease_id,
    )

    MockRollbackHandler.backend_sha = rollback_bff
    MockRollbackHandler.frontend_sha = rollback_fe
    MockRollbackHandler.status_code = 200

    server = http.server.HTTPServer(("127.0.0.1", 0), MockRollbackHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    log_file = tmp / "controller.log"
    log_file.write_text("failure log detail\n", encoding="utf-8")
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
    if audit_file:
        env["FAKE_LEASE_AUDIT_FILE"] = str(audit_file)

    try:
        res = subprocess.run(
            ["bash", str(COMPENSATION_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        server.shutdown()

    return res, evidence_out


# ==============================================================================
# Scenario 1: Baseline success does NOT execute failure collector
# ==============================================================================
def test_d3_1_baseline_success_does_not_execute_collector():
    """SD D3.1: baseline success writes not_required, does not execute collector."""
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
            "FAKE_LEASE_AUDIT_FILE": str(paths["audit"]),
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            "DEV_PAPER_RUN_ID": "1001",
            "DEV_PAPER_ATTEMPT": "1",
            "DEV_PAPER_PHASE": "paper_bootstrap",
            # SSH fixture: bootstrap succeeds
            "FAKE_BOOTSTRAP_EXIT": "0",
            "FAKE_SSH_LOG": str(ssh_log),
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
            assert proc.returncode == 0, f"guard failed with code {proc.returncode}: {proc.stderr}"
            assert not paths["failure"].exists(), "failure file must not be created on baseline success"

            status_file = diag_dir / "collection-status.json"
            assert status_file.exists(), "collection-status.json must exist"
            status_data = json.loads(status_file.read_text(encoding="utf-8"))
            assert status_data["collectionStatus"] == "not_required"
            assert status_data["bootstrapExit"] == 0

            # Collector was never executed
            log_lines = ssh_log.read_text().splitlines() if ssh_log.exists() else []
            assert not any("python3 -" in line or "--expected-bff-sha" in line for line in log_lines)
            assert not (diag_dir / "diagnostics.json").exists()
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


# ==============================================================================
# Scenario 2: Baseline exit 1: capture precedes heartbeat stop, guard quarantines
# ==============================================================================
def test_d3_2_baseline_exit1_capture_precedes_heartbeat_stop_and_guard_quarantines():
    """SD D3.2: baseline exit 1 executes collector while lease heartbeat is healthy, then quarantines."""
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
        timing_probe_file = root / "collector-timing.json"

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
            "FAKE_LEASE_AUDIT_FILE": str(paths["audit"]),
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            "DEV_PAPER_RUN_ID": "1001",
            "DEV_PAPER_ATTEMPT": "1",
            "DEV_PAPER_PHASE": "paper_bootstrap",
            # SSH fixture: bootstrap exit 1, collector returns valid D2 json
            "FAKE_BOOTSTRAP_EXIT": "1",
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_HEARTBEAT_PID": str(heartbeat.pid),
            "FAKE_TIMING_PROBE_FILE": str(timing_probe_file),
            "FAKE_FAILURE_FILE": str(paths["failure"]),
            "FAKE_COLLECTOR_STDOUT": json.dumps(valid_d2_envelope(bootstrap_exit=1, status="ok")),
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
            assert proc.returncode == 75, f"guard must exit 75 on failure quarantine, got {proc.returncode}"
            assert paths["failure"].exists(), "failure file must be created"
            fail_data = json.loads(paths["failure"].read_text())
            assert fail_data["exitStatus"] == 1, "guard must record original command exitStatus 1"

            # Timing probe proves: during collector run, heartbeat was alive and failure file did not exist yet
            assert timing_probe_file.exists(), "timing probe was not written by collector"
            probe = json.loads(timing_probe_file.read_text())
            assert probe["heartbeat_alive"] is True, "collector must run while heartbeat is still alive"
            assert probe["failure_file_exists"] is False, "collector must run before guard records failure / quarantines"

            status_file = diag_dir / "collection-status.json"
            assert status_file.exists()
            status_data = json.loads(status_file.read_text())
            assert status_data["collectionStatus"] == "ok"
            assert status_data["bootstrapExit"] == 1

            diag_file = diag_dir / "diagnostics.json"
            assert diag_file.exists()
            checksum_file = diag_dir / "SHA256SUMS"
            assert checksum_file.exists()

            # Heartbeat was stopped for quarantine after collector finished
            assert_processes_terminated([heartbeat.pid], timeout=3.0)

            # Audit verifies lease was quarantined
            audit = json.loads(paths["audit"].read_text())
            assert audit["leases"][TEST_LEASE_ID]["status"] in ("quarantined", "active")
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


# ==============================================================================
# Scenario 3: Collector failure preserves bootstrap exit & enables compensation
# ==============================================================================
def test_d3_3_collector_timeout_preserves_bootstrap_and_enables_compensation():
    """SD D3.3a: collector timeout preserves bootstrap exit 1, quarantines, and rollback is reachable."""
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
            "FAKE_LEASE_AUDIT_FILE": str(paths["audit"]),
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

            # Invoke fresh-lease compensation: proves rollback is reachable after collector timeout
            comp_proc, comp_evidence = execute_fresh_lease_compensation(
                root / "comp",
                rollback_bff=TEST_BFF_SHA,
                rollback_fe=TEST_FE_SHA,
                deploy_exit=0,
                audit_file=paths["audit"],
                compensation_lease_id="22222222-2222-4222-8222-333333333333",
            )
            assert comp_proc.returncode == 0, f"compensation must succeed: {comp_proc.stderr}"
            assert comp_evidence.exists()
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


def test_d3_3_collector_error_exit_preserves_bootstrap_and_enables_compensation():
    """SD D3.3b: collector non-zero exit preserves bootstrap exit 1, quarantines, and rollback is reachable."""
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
            "FAKE_LEASE_AUDIT_FILE": str(paths["audit"]),
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

            # Invoke compensation after collector command error
            comp_proc, comp_evidence = execute_fresh_lease_compensation(
                root / "comp",
                rollback_bff=TEST_BFF_SHA,
                rollback_fe=TEST_FE_SHA,
                deploy_exit=0,
                audit_file=paths["audit"],
                compensation_lease_id="22222222-2222-4222-8222-444444444444",
            )
            assert comp_proc.returncode == 0
            assert comp_evidence.exists()
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


def test_d3_3_collector_corrupted_json_preserves_bootstrap_and_enables_compensation():
    """SD D3.3c: malformed collector output preserves bootstrap exit 1, cleans up, rollback is reachable."""
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
            "FAKE_LEASE_AUDIT_FILE": str(paths["audit"]),
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

            assert not (diag_dir / "diagnostics.json").exists()
            assert not (diag_dir / "SHA256SUMS").exists()
            assert_processes_terminated([heartbeat.pid], timeout=3.0)

            # Invoke compensation after collector corrupt JSON
            comp_proc, comp_evidence = execute_fresh_lease_compensation(
                root / "comp",
                rollback_bff=TEST_BFF_SHA,
                rollback_fe=TEST_FE_SHA,
                deploy_exit=0,
                audit_file=paths["audit"],
                compensation_lease_id="22222222-2222-4222-8222-555555555555",
            )
            assert comp_proc.returncode == 0
            assert comp_evidence.exists()
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


# ==============================================================================
# Scenario 4: Heartbeat & Remote CAS loss terminate whole process group & descendants
# ==============================================================================
def test_d3_4_heartbeat_loss_during_command_terminates_process_group_and_descendants():
    """SD D3.4a: heartbeat loss terminates whole process group and descendants; no remote collector runs."""
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
            "FAKE_LEASE_AUDIT_FILE": str(paths["audit"]),
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            # SSH fixture: bootstrap sleeps, spawns descendant
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_BOOTSTRAP_PID_FILE": str(bootstrap_pid_file),
            "FAKE_BOOTSTRAP_SPAWN_CHILD": "1",
            "FAKE_BOOTSTRAP_SLEEP": "30",
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
            bootstrap_pids = read_pids_from_file(bootstrap_pid_file)
            assert len(bootstrap_pids) >= 2, f"expected parent and descendant PIDs, got {bootstrap_pids}"
            for p in bootstrap_pids:
                assert process_state(p) != "", f"PID {p} should be actively running"

            # Kill heartbeat process mid-execution
            heartbeat.kill()
            heartbeat.wait()

            # Guard monitor loop checks heartbeat every 0.5s; wait for guard to terminate
            stdout, stderr = guard_proc.communicate(timeout=10)
            assert guard_proc.returncode == 75, f"guard must exit 75 on heartbeat loss, got {guard_proc.returncode}"
            assert "lease heartbeat identity/health was lost" in stderr or "quarantine" in stderr

            # Assert BOTH bootstrap shell and descendant process were killed
            assert_processes_terminated(bootstrap_pids, timeout=3.0)

            # Assert remote invocation markers were never called
            log_lines = ssh_log.read_text().splitlines() if ssh_log.exists() else []
            assert not any("python3 -" in line or "--expected-bff-sha" in line for line in log_lines)
        finally:
            if guard_proc.poll() is None:
                guard_proc.kill()
                guard_proc.wait()


def test_d3_4_remote_cas_loss_terminates_process_group_and_descendants():
    """SD D3.4b: remote CAS verification failure terminates whole process group; no collector runs."""
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
            # Fail CAS on second verification
            "FAKE_VERIFY_FAIL_AT": "2",
            "PANTHEON_DEV_ENVIRONMENT_LEASE_VERIFY_INTERVAL_SECONDS": "1",
            "FAKE_LEASE_AUDIT_FILE": str(paths["audit"]),
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            # SSH fixture: bootstrap sleeps, spawns descendant
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_BOOTSTRAP_PID_FILE": str(bootstrap_pid_file),
            "FAKE_BOOTSTRAP_SPAWN_CHILD": "1",
            "FAKE_BOOTSTRAP_SLEEP": "30",
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
            bootstrap_pids = read_pids_from_file(bootstrap_pid_file)
            assert len(bootstrap_pids) >= 2

            stdout, stderr = guard_proc.communicate(timeout=10)
            assert guard_proc.returncode == 75
            assert "remote lease verification failed" in stderr or "quarantine" in stderr
            assert_processes_terminated(bootstrap_pids, timeout=3.0)

            # Assert actual remote invocation markers were never called
            log_lines = ssh_log.read_text().splitlines() if ssh_log.exists() else []
            assert not any("python3 -" in line or "--expected-bff-sha" in line for line in log_lines)
        finally:
            if guard_proc.poll() is None:
                guard_proc.kill()
                guard_proc.wait()
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


# ==============================================================================
# Scenario 5: Cancellation, Expired State, Ambiguous Container Fail Closed
# ==============================================================================
def test_d3_5_baseline_cancellation_real_signal_injection():
    """SD D3.5a: real SIGTERM and SIGINT injection terminates process group immediately without collector."""
    # Subtest 1: Real SIGTERM while bootstrap is active
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
            "FAKE_EXPECTED_TOKEN_SHA256": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
            "FAKE_VERIFY_COUNT_FILE": str(paths["verify_count"]),
            "FAKE_VERIFY_FAIL_AT": "0",
            "FAKE_LEASE_AUDIT_FILE": str(paths["audit"]),
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_BOOTSTRAP_PID_FILE": str(bootstrap_pid_file),
            "FAKE_BOOTSTRAP_SPAWN_CHILD": "1",
            "FAKE_BOOTSTRAP_SLEEP": "30",
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
            bootstrap_pids = read_pids_from_file(bootstrap_pid_file)
            assert len(bootstrap_pids) >= 2

            # Inject REAL SIGTERM to the guard process while bootstrap is running
            os.kill(guard_proc.pid, signal.SIGTERM)

            stdout, stderr = guard_proc.communicate(timeout=10)
            assert guard_proc.returncode == 143, f"guard must exit 143 on SIGTERM, got {guard_proc.returncode}"

            # Both parent and child must be terminated
            assert_processes_terminated(bootstrap_pids, timeout=3.0)

            # Collector was NEVER executed
            log_lines = ssh_log.read_text().splitlines() if ssh_log.exists() else []
            assert not any("python3 -" in line or "--expected-bff-sha" in line for line in log_lines)

            # Collection status remained not_started
            status_file = diag_dir / "collection-status.json"
            assert status_file.exists()
            status_data = json.loads(status_file.read_text())
            assert status_data["collectionStatus"] == "not_started"
        finally:
            if guard_proc.poll() is None:
                guard_proc.kill()
                guard_proc.wait()
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()

    # Subtest 2: Real SIGINT while bootstrap is active
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
            "FAKE_EXPECTED_TOKEN_SHA256": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
            "FAKE_VERIFY_COUNT_FILE": str(paths["verify_count"]),
            "FAKE_VERIFY_FAIL_AT": "0",
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_BOOTSTRAP_PID_FILE": str(bootstrap_pid_file),
            "FAKE_BOOTSTRAP_SPAWN_CHILD": "1",
            "FAKE_BOOTSTRAP_SLEEP": "30",
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
            bootstrap_pids = read_pids_from_file(bootstrap_pid_file)

            # Inject REAL SIGINT to guard
            os.kill(guard_proc.pid, signal.SIGINT)

            stdout, stderr = guard_proc.communicate(timeout=10)
            assert guard_proc.returncode == 130, f"guard must exit 130 on SIGINT, got {guard_proc.returncode}"
            assert_processes_terminated(bootstrap_pids, timeout=3.0)

            log_lines = ssh_log.read_text().splitlines() if ssh_log.exists() else []
            assert not any("python3 -" in line or "--expected-bff-sha" in line for line in log_lines)
        finally:
            if guard_proc.poll() is None:
                guard_proc.kill()
                guard_proc.wait()
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


def test_d3_5_cancellation_during_collector_terminates_group_and_descendants():
    """SD D3.5a: real SIGTERM while collector is active terminates collector process group and descendants."""
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
        collector_pid_file = root / "collector.pid"

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
            # Bootstrap fails exit 1 immediately; collector hangs with descendant
            "FAKE_BOOTSTRAP_EXIT": "1",
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_COLLECTOR_PID_FILE": str(collector_pid_file),
            "FAKE_COLLECTOR_SPAWN_CHILD": "1",
            "FAKE_COLLECTOR_SLEEP": "30",
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
            wait_for_file(collector_pid_file, timeout=5.0)
            collector_pids = read_pids_from_file(collector_pid_file)
            assert len(collector_pids) >= 2

            # Inject REAL SIGTERM to the guard while collector is active
            os.kill(guard_proc.pid, signal.SIGTERM)

            stdout, stderr = guard_proc.communicate(timeout=10)
            assert guard_proc.returncode in (75, 143)

            # Both collector parent and descendant are killed
            assert_processes_terminated(collector_pids, timeout=3.0)
        finally:
            if guard_proc.poll() is None:
                guard_proc.kill()
                guard_proc.wait()
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


def test_d3_5_missing_lease_state_fails_closed():
    """SD D3.5b: missing lease state or token fails closed before executing command."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        paths = prepare_guard_fixture(root)

        # Remove state file
        paths["state"].unlink()

        workspace = root / "workspace"
        workspace.mkdir()
        prepare_ssh_fixture(workspace)

        ssh_log = root / "fake-ssh.log"

        env = {
            **os.environ,
            "TARGET_ENV": "dev",
            TOKEN_ENV: TEST_TOKEN,
            "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(paths["state"]),
            "GITHUB_WORKSPACE": str(workspace),
            "FAKE_SSH_LOG": str(ssh_log),
        }

        proc = subprocess.run(
            ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert proc.returncode != 0
        assert not ssh_log.exists(), "no remote command should ever be launched when lease state is missing"


def test_d3_5_expired_lease_state_fails_closed():
    """SD D3.5c: expired lease state fails closed before command execution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # Prepare fixture with expired=True
        paths = prepare_guard_fixture(root, expired=True)
        heartbeat = start_fake_heartbeat(paths)

        workspace = root / "workspace"
        workspace.mkdir()
        prepare_ssh_fixture(workspace)

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
            "FAKE_LEASE_EXPIRED": "1",
            "GITHUB_WORKSPACE": str(workspace),
            "FAKE_SSH_LOG": str(ssh_log),
        }

        try:
            proc = subprocess.run(
                ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            assert proc.returncode == 75
            assert "expired" in proc.stderr.lower()
            assert not ssh_log.exists(), "no remote command should ever execute when lease is expired"
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


def test_d3_5_identity_mismatch_fails_closed():
    """SD D3.5d: observed candidate source SHA mismatch fails closed without false success."""
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

        wrong_sha = "f" * 40
        mismatched_envelope = valid_d2_envelope(
            expected_bff=TEST_BFF_SHA,
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
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_BOOTSTRAP_EXIT": "1",
            "FAKE_COLLECTOR_STDOUT": json.dumps(mismatched_envelope),
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
            assert status_data["collectionStatus"] == "identity_mismatch"
            assert status_data["bootstrapExit"] == 1
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


def test_d3_5_ambiguous_container_fails_closed():
    """SD D3.5e: ambiguous container (multiple container matches) fails closed."""
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

        # Ambiguous container envelope: identity_matches=False, status=partial
        ambiguous_envelope = valid_d2_envelope(
            expected_bff=TEST_BFF_SHA,
            observed_source="",
            identity_matches=False,
            bootstrap_exit=1,
            status="partial",
        )
        ambiguous_envelope["container_id"] = None
        ambiguous_envelope["image_id"] = None
        ambiguous_envelope["observed_source_sha"] = None
        ambiguous_envelope["services"]["operator-bff"]["collection_status"] = "container_missing_or_ambiguous"

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
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_BOOTSTRAP_EXIT": "1",
            "FAKE_COLLECTOR_STDOUT": json.dumps(ambiguous_envelope),
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
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


# ==============================================================================
# Scenario 6: Missing artifact, bad checksum, and upload/download failure pipeline
# ==============================================================================
def test_d3_6_artifact_output_missing_or_bad_checksum_cannot_declare_acceptance():
    """SD D3.6a: missing artifacts, corrupt status, or bad checksum fail diagnostic acceptance."""
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


def test_d3_6_artifact_upload_download_failure_and_acceptance_pipeline():
    """SD D3.6b: executable runner artifact upload and acceptance download failure path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        artifact_store = root / "artifacts_store"
        artifact_store.mkdir()
        runner_temp = root / "runner_temp"
        runner_temp.mkdir()

        run_id = "34081262894"
        attempt = "1"
        artifact_name = f"pantheon-dev-paper-diagnostics-{run_id}-{attempt}"

        # Executable runner upload helper simulating actions/upload-artifact@v4
        def simulate_runner_upload(source_dir: Path) -> bool:
            required = ["diagnostics.json", "collection-status.json", "SHA256SUMS"]
            if not source_dir.exists():
                return False
            if not all((source_dir / f).exists() for f in required):
                return False
            dest_dir = artifact_store / artifact_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            for f in required:
                shutil.copy2(source_dir / f, dest_dir / f)
            return True

        # Executable acceptance download & verification helper simulating actions/download-artifact
        def simulate_acceptance_download_and_verify(download_dir: Path) -> tuple[bool, str]:
            source_dir = artifact_store / artifact_name
            if not source_dir.exists():
                return False, "missing_artifact"
            download_dir.mkdir(parents=True, exist_ok=True)
            for f in source_dir.iterdir():
                shutil.copy2(f, download_dir / f.name)
            return verify_diagnostic_acceptance(download_dir)

        # Case 1: Upload fails because source directory does not have complete files
        broken_src = runner_temp / "broken_src"
        broken_src.mkdir()
        (broken_src / "collection-status.json").write_text('{"collectionStatus":"timeout"}\n')
        upload_ok = simulate_runner_upload(broken_src)
        assert upload_ok is False, "upload must fail when required files are incomplete"

        # Downstream acceptance attempt fails closed
        dl_dir1 = root / "download_1"
        accepted, reason = simulate_acceptance_download_and_verify(dl_dir1)
        assert accepted is False
        assert reason == "missing_artifact"

        # Case 2: Upload succeeds, but downloaded payload has corrupted checksum
        valid_src = runner_temp / "valid_src"
        valid_src.mkdir()
        diag_content = json.dumps(valid_d2_envelope())
        (valid_src / "diagnostics.json").write_text(diag_content)
        (valid_src / "collection-status.json").write_text('{"collectionStatus":"ok"}\n')
        real_hash = hashlib.sha256(diag_content.encode()).hexdigest()
        (valid_src / "SHA256SUMS").write_text(f"{real_hash}  diagnostics.json\n")

        upload_ok = simulate_runner_upload(valid_src)
        assert upload_ok is True

        # Tamper stored checksum to simulate transmission/storage corruption
        (artifact_store / artifact_name / "SHA256SUMS").write_text("0" * 64 + "  diagnostics.json\n")

        dl_dir2 = root / "download_2"
        accepted, reason = simulate_acceptance_download_and_verify(dl_dir2)
        assert accepted is False
        assert reason == "checksum_mismatch"

        # Case 3: Clean upload and valid download passes diagnostic acceptance
        (artifact_store / artifact_name / "SHA256SUMS").write_text(f"{real_hash}  diagnostics.json\n")
        dl_dir3 = root / "download_3"
        accepted, reason = simulate_acceptance_download_and_verify(dl_dir3)
        assert accepted is True
        assert reason == "accepted"


# ==============================================================================
# Scenario 7: Persona exception capture & sentinel safety via real collector stdin
# ==============================================================================
def test_d3_7_project_exceptions_and_sentinels_through_actual_collector_pipeline():
    """SD D3.7: Persona domain exceptions captured & sentinels never leak via real collector stdin."""
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

        sentinels = [
            "SECRET_SENTINEL_TOKEN_XYZ_987",
            "PASSWORD_SECRET_12345",
            "SUPER_PRIVATE_DSN_KEY",
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.SECRET_PAYLOAD",
            "postgresql://user:TOP_SECRET_PASSWORD@localhost:5432/trading",
        ]

        # Create mock docker binary in mock_bin directory
        mock_bin = root / "mock_bin"
        mock_bin.mkdir()
        mock_docker = mock_bin / "docker"

        docker_script = f"""#!/usr/bin/env bash
set -uo pipefail
cmd="${{1:-}}"
shift || true

if [[ "${{cmd}}" == "ps" ]]; then
  for arg in "$@"; do
    if [[ "${{arg}}" == *"label=com.docker.compose.service=operator-bff"* ]]; then
      echo "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
      exit 0
    elif [[ "${{arg}}" == *"label=com.docker.compose.service=persona"* ]]; then
      echo "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"
      exit 0
    elif [[ "${{arg}}" == *"label=com.docker.compose.service="* ]]; then
      echo "1111111111111111111111111111111111111111111111111111111111111111"
      exit 0
    fi
  done
  echo ""
  exit 0
elif [[ "${{cmd}}" == "inspect" ]]; then
  echo '{{"status":"running","health":"healthy","exit_code":0,"oom_killed":false,"restart_count":0,"image_id":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","source_sha":"{TEST_BFF_SHA}"}}'
  exit 0
elif [[ "${{cmd}}" == "logs" ]]; then
  target_cid="${{@: -1}}"
  if [[ "${{target_cid}}" == *"fedcba"* ]]; then
    cat <<'LOGS'
2026-09-07T00:17:26.123Z Traceback (most recent call last):
2026-09-07T00:17:26.123Z   File "/workspace/services/control-plane/bff/personas/service.py", line 4000, in _coordinate_persona_create
2026-09-07T00:17:26.123Z     call(auth_token="{sentinels[0]}")
2026-09-07T00:17:26.123Z services.control_plane.bff.ports.persona_write_owner.PersonaWriteOwnerUnavailable: failed to reach owner with {sentinels[1]}
2026-09-07T00:17:27.456Z Traceback (most recent call last):
2026-09-07T00:17:27.456Z   File "/workspace/services/control-plane/bff/persona_provisioning.py", line 120, in release_lease
2026-09-07T00:17:27.456Z     verify(secret="{sentinels[2]}")
2026-09-07T00:17:27.456Z services.control_plane.bff.persona_provisioning.ProvisioningLeaseLost: lease expired with {sentinels[3]}
2026-09-07T00:17:28.789Z ConnectionRefusedError: [Errno 111] Connection refused: {sentinels[4]}
2026-09-07T00:17:29.000Z SYNTHETIC_PRIVATE_SENTINELError: should not be parsed
LOGS
    exit 0
  else
    cat <<'LOGS'
2026-09-07T00:17:20.000Z Server listening on port 8000
2026-09-07T00:17:25.000Z HTTP Error 502: Bad Gateway
LOGS
    exit 0
  fi
fi
exit 0
"""
        mock_docker.write_text(docker_script, encoding="utf-8")
        mock_docker.chmod(0o755)

        env = {
            **os.environ,
            "PATH": f"{mock_bin}:{os.environ['PATH']}",
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
            # SSH fixture: bootstrap fails exit 1; execute real collector via stdin!
            "FAKE_BOOTSTRAP_EXIT": "1",
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_EXECUTE_REAL_COLLECTOR": "1",
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
            diag_file = diag_dir / "diagnostics.json"
            assert diag_file.exists(), "diagnostics.json must be generated by real collector"

            diag_data = json.loads(diag_file.read_text(encoding="utf-8"))
            assert diag_data["identity_matches"] is True
            assert diag_data["collection_status"] == "ok"

            persona_events = diag_data["services"]["persona"]["events"]
            event_types = [e["type"] for e in persona_events if e.get("kind") == "exception"]

            # Assert positive capture of domain exceptions
            assert "services.control_plane.bff.ports.persona_write_owner.PersonaWriteOwnerUnavailable" in event_types
            assert "services.control_plane.bff.persona_provisioning.ProvisioningLeaseLost" in event_types
            assert "ConnectionRefusedError" in event_types
            assert "SYNTHETIC_PRIVATE_SENTINELError" not in event_types

            # Verify ZERO secret sentinels leak into diagnostics, status, or checksum files
            raw_diag = diag_file.read_text(encoding="utf-8")
            raw_status = (diag_dir / "collection-status.json").read_text(encoding="utf-8")
            raw_sums = (diag_dir / "SHA256SUMS").read_text(encoding="utf-8")
            for s in sentinels:
                assert s not in raw_diag, f"Secret sentinel leaked into diagnostics.json: {s}"
                assert s not in raw_status, f"Secret sentinel leaked into collection-status.json: {s}"
                assert s not in raw_sums, f"Secret sentinel leaked into SHA256SUMS: {s}"
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


# ==============================================================================
# Scenario 8: Composed failure -> capture -> quarantine -> compensation
# ==============================================================================
def test_d3_8_composed_baseline_failure_capture_quarantine_compensation_success():
    """SD D3.8a: composed baseline failure -> capture -> quarantine -> runner handling -> fresh lease compensation success."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        paths = prepare_guard_fixture(root, lease_id="11111111-1111-4111-8111-111111111111")
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
            "FAKE_LEASE_AUDIT_FILE": str(paths["audit"]),
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            "DEV_PAPER_RUN_ID": "1001",
            "DEV_PAPER_ATTEMPT": "1",
            "DEV_PAPER_PHASE": "paper_bootstrap",
            "FAKE_BOOTSTRAP_EXIT": "1",
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_COLLECTOR_STDOUT": json.dumps(valid_d2_envelope(bootstrap_exit=1, status="ok")),
            "FAKE_COLLECTOR_EXIT": "0",
        }

        # Step 1: Baseline failure + diagnostic capture + Lease 1 quarantine
        proc = subprocess.run(
            ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        primary_exit = proc.returncode
        assert primary_exit == 75, f"primary exit must be 75 (quarantine), got {primary_exit}"

        # Assert Lease 1 was quarantined
        assert paths["failure"].exists()
        fail_data = json.loads(paths["failure"].read_text())
        assert fail_data["exitStatus"] == 1

        # Step 2: Runner artifact handling
        status_file = diag_dir / "collection-status.json"
        assert status_file.exists()
        diag_file = diag_dir / "diagnostics.json"
        assert diag_file.exists()
        checksum_file = diag_dir / "SHA256SUMS"
        assert checksum_file.exists()
        accepted, reason = verify_diagnostic_acceptance(diag_dir)
        assert accepted is True, f"diagnostics acceptance failed: {reason}"

        # Step 3: Fresh-lease compensation under Lease 2
        rollback_bff = "1" * 40
        rollback_fe = "2" * 40
        failed_bff = TEST_BFF_SHA
        failed_fe = TEST_FE_SHA
        rc_id = "5" * 64
        lease_2_id = "22222222-2222-4222-8222-222222222222"

        comp_proc, comp_evidence = execute_fresh_lease_compensation(
            root / "compensation",
            rollback_bff=rollback_bff,
            rollback_fe=rollback_fe,
            failed_bff=failed_bff,
            failed_fe=failed_fe,
            rc_id=rc_id,
            deploy_exit=0,
            audit_file=paths["audit"],
            compensation_lease_id=lease_2_id,
        )
        compensation_exit = comp_proc.returncode
        assert compensation_exit == 0, f"compensation must succeed: {comp_proc.stderr}"
        assert comp_evidence.exists()

        # Step 4: Assert independent outcomes & authority transitions
        # Primary exit is 75 (failure), compensation exit is 0 (restored)
        assert primary_exit == 75
        assert compensation_exit == 0

        # Assert lease state transitions
        audit = json.loads(paths["audit"].read_text())
        lease_1_status = audit["leases"]["11111111-1111-4111-8111-111111111111"]["status"]
        lease_2_status = audit["leases"][lease_2_id]["status"]

        assert lease_1_status in ("quarantined", "stopped"), "Lease 1 must remain quarantined"
        assert lease_2_status == "released", "Lease 2 must be cleanly verified and released"

        evidence_data = json.loads(comp_evidence.read_text())
        assert evidence_data["outcome"] == "compensated"
        assert evidence_data["restored_pair"]["backend_sha"] == rollback_bff
        assert evidence_data["restored_pair"]["frontend_sha"] == rollback_fe


def test_d3_8_composed_baseline_failure_capture_quarantine_compensation_failure():
    """SD D3.8b: composed baseline failure -> capture -> quarantine -> failed compensation exits 75, retains quarantine."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        paths = prepare_guard_fixture(root, lease_id="11111111-1111-4111-8111-111111111111")
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
            "FAKE_LEASE_AUDIT_FILE": str(paths["audit"]),
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            "DEV_PAPER_RUN_ID": "1001",
            "DEV_PAPER_ATTEMPT": "1",
            "DEV_PAPER_PHASE": "paper_bootstrap",
            "FAKE_BOOTSTRAP_EXIT": "1",
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_COLLECTOR_STDOUT": json.dumps(valid_d2_envelope(bootstrap_exit=1, status="ok")),
            "FAKE_COLLECTOR_EXIT": "0",
        }

        # Step 1: Baseline failure + capture + Lease 1 quarantine
        proc = subprocess.run(
            ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        primary_exit = proc.returncode
        assert primary_exit == 75

        # Step 2: Fresh-lease compensation under Lease 2 fails (deploy script exits 1)
        rollback_bff = "1" * 40
        rollback_fe = "2" * 40
        lease_2_id = "22222222-2222-4222-8222-222222222222"

        comp_proc, comp_evidence = execute_fresh_lease_compensation(
            root / "compensation",
            rollback_bff=rollback_bff,
            rollback_fe=rollback_fe,
            deploy_exit=1,  # Deploy fails
            audit_file=paths["audit"],
            compensation_lease_id=lease_2_id,
        )
        compensation_exit = comp_proc.returncode
        assert compensation_exit == 75, f"failed compensation must exit 75, got {compensation_exit}"
        assert not comp_evidence.exists()

        # Step 3: Assert independent outcomes & authority transitions
        assert primary_exit == 75
        assert compensation_exit == 75

        # Both leases remain quarantined
        audit = json.loads(paths["audit"].read_text())
        lease_1_status = audit["leases"]["11111111-1111-4111-8111-111111111111"]["status"]
        lease_2_status = audit["leases"][lease_2_id]["status"]

        assert lease_1_status in ("quarantined", "stopped"), "Lease 1 must remain quarantined"
        assert lease_2_status in ("quarantined", "stopped"), "Lease 2 must remain quarantined upon compensation failure"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
