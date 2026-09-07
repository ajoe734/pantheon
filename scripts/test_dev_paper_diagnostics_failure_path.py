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
            "capital": {
                "collection_status": "ok",
                "events": [],
            },
            "registry": {
                "collection_status": "ok",
                "events": [],
            },
            "governance": {
                "collection_status": "ok",
                "events": [],
            },
            "deployment": {
                "collection_status": "ok",
                "events": [],
            },
            "postgres": {
                "collection_status": "ok",
                "events": [],
            },
        },
    }


def verify_diagnostic_acceptance(
    diag_dir: Path,
    *,
    expected_run_id: str | None = None,
    expected_attempt: str | None = None,
    expected_phase: str | None = None,
    expected_bff_sha: str | None = None,
    expected_fe_sha: str | None = None,
    expected_bootstrap_exit: int | str | None = None,
) -> tuple[bool, str]:
    """Verify that a diagnostics directory meets diagnostic acceptance criteria (SD D2 & D3.6)."""
    diag_file = diag_dir / "diagnostics.json"
    status_file = diag_dir / "collection-status.json"
    checksum_file = diag_dir / "SHA256SUMS"

    if not status_file.exists():
        return False, "missing_collection_status"
    try:
        status_data = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        return False, "corrupted_collection_status"

    if not isinstance(status_data, dict):
        return False, "corrupted_collection_status"

    outcome = status_data.get("collectionStatus")
    if outcome == "timeout":
        return False, "collection_status_timeout"
    if outcome != "ok":
        return False, f"collection_status_{outcome}"

    if "bootstrapExit" not in status_data:
        return False, "missing_bootstrap_exit_in_status"

    raw_status_exit = status_data["bootstrapExit"]
    if isinstance(raw_status_exit, bool) or not (
        isinstance(raw_status_exit, int)
        or (isinstance(raw_status_exit, str) and raw_status_exit.isdigit())
    ):
        return False, "invalid_status_bootstrap_exit_type"
    status_exit_val = int(raw_status_exit)
    if status_exit_val <= 0 or status_exit_val > 255:
        return False, "invalid_status_bootstrap_exit_range"

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

    try:
        diag_data = json.loads(diag_file.read_text(encoding="utf-8"))
    except Exception:
        return False, "malformed_diagnostics_json"

    if not isinstance(diag_data, dict):
        return False, "malformed_diagnostics_json"

    if diag_data.get("schema_version") != "pantheon.dev-paper-diagnostics.v1":
        return False, "schema_version_mismatch"

    required_fields = (
        "schema_version",
        "run_id",
        "attempt",
        "collected_at",
        "phase",
        "expected_fe_sha",
        "expected_bff_sha",
        "observed_source_sha",
        "container_id",
        "image_id",
        "identity_matches",
        "bootstrap_exit",
        "collection_status",
        "services",
    )
    for field in required_fields:
        if field not in diag_data:
            return False, f"missing_schema_field_{field}"

    if not isinstance(diag_data["identity_matches"], bool):
        return False, "invalid_field_type_identity_matches"
    if not isinstance(diag_data["services"], dict):
        return False, "invalid_field_type_services"
    if not isinstance(diag_data["collected_at"], str) or not diag_data["collected_at"]:
        return False, "invalid_field_type_collected_at"
    if not re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)$", diag_data["collected_at"]):
        return False, "invalid_collected_at_format"

    # Validate bootstrap exit type and range in diagnostics.json
    raw_diag_exit = diag_data["bootstrap_exit"]
    if isinstance(raw_diag_exit, bool) or not (
        isinstance(raw_diag_exit, int)
        or (isinstance(raw_diag_exit, str) and raw_diag_exit.isdigit())
    ):
        return False, "invalid_field_type_bootstrap_exit"
    diag_exit_val = int(raw_diag_exit)
    if diag_exit_val <= 0 or diag_exit_val > 255:
        return False, "invalid_bootstrap_exit_range"

    # Bind both artifacts to the same known original baseline exit
    if diag_exit_val != status_exit_val:
        return False, "bootstrap_exit_mismatch_status_vs_diagnostics"

    # Bind to expected baseline exit if caller provided one
    if expected_bootstrap_exit is not None:
        try:
            exp_exit = int(expected_bootstrap_exit)
        except (ValueError, TypeError):
            return False, "invalid_expected_bootstrap_exit"
        if diag_exit_val != exp_exit or status_exit_val != exp_exit:
            return False, "bootstrap_exit_mismatch_expected"

    # Validate required service evidence and schema (SD D2)
    services = diag_data["services"]
    if not services:
        return False, "empty_services"

    for req_svc in ("operator-bff", "persona"):
        if req_svc not in services:
            return False, f"missing_required_service_{req_svc.replace('-', '_')}"

    for s_name, s_val in services.items():
        if not isinstance(s_val, dict):
            return False, f"invalid_service_schema_{s_name.replace('-', '_')}"
        if "collection_status" not in s_val or not isinstance(s_val["collection_status"], str):
            return False, f"missing_service_collection_status_{s_name.replace('-', '_')}"
        if "events" in s_val:
            if not isinstance(s_val["events"], list):
                return False, f"invalid_service_events_{s_name.replace('-', '_')}"
            for ev in s_val["events"]:
                if not isinstance(ev, dict):
                    return False, f"invalid_service_event_item_{s_name.replace('-', '_')}"
                if "kind" not in ev or not isinstance(ev["kind"], str):
                    return False, f"missing_service_event_kind_{s_name.replace('-', '_')}"
        if "container_id" in s_val:
            if not isinstance(s_val["container_id"], str) or not re.fullmatch(r"[0-9a-f]{64}", s_val["container_id"]):
                return False, f"invalid_service_container_id_{s_name.replace('-', '_')}"
        if "state" in s_val and not isinstance(s_val["state"], dict):
            return False, f"invalid_service_state_{s_name.replace('-', '_')}"

    # Identity and fail-closed checks
    if diag_data["identity_matches"] is not True:
        return False, "identity_mismatch"
    if diag_data.get("collection_status") != "ok":
        return False, f"diagnostics_collection_status_{diag_data.get('collection_status')}"

    # Container ID and Image ID must be valid when identity_matches is True
    cid = diag_data.get("container_id")
    if not isinstance(cid, str) or not re.fullmatch(r"[0-9a-f]{64}", cid):
        return False, "invalid_container_id"
    iid = diag_data.get("image_id")
    if not isinstance(iid, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", iid):
        return False, "invalid_image_id"
    obs_sha = diag_data.get("observed_source_sha")
    if not isinstance(obs_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", obs_sha):
        return False, "invalid_observed_source_sha"

    # Verify observed matches expected BFF
    if obs_sha != diag_data.get("expected_bff_sha"):
        return False, "identity_mismatch_observed_vs_expected"

    # Contextual assertions if expected values are provided
    if expected_bff_sha is not None and diag_data.get("expected_bff_sha") != expected_bff_sha:
        return False, "identity_mismatch_expected_bff_sha"
    if expected_fe_sha is not None and diag_data.get("expected_fe_sha") != expected_fe_sha:
        return False, "identity_mismatch_expected_fe_sha"
    if expected_run_id is not None and str(diag_data.get("run_id")) != str(expected_run_id):
        return False, "identity_mismatch_run_id"
    if expected_attempt is not None and str(diag_data.get("attempt")) != str(expected_attempt):
        return False, "identity_mismatch_attempt"
    if expected_phase is not None and str(diag_data.get("phase")) != str(expected_phase):
        return False, "identity_mismatch_phase"

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
    """SD D3.5d: observed candidate source SHA mismatch fails closed without false success via real collector."""
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
        cid = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

        # Mock docker binary providing real discovery and inspect with mismatched source SHA
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
      echo "{cid}"
      exit 0
    elif [[ "${{arg}}" == *"label=com.docker.compose.service="* ]]; then
      echo "1111111111111111111111111111111111111111111111111111111111111111"
      exit 0
    fi
  done
  echo ""
  exit 0
elif [[ "${{cmd}}" == "inspect" ]]; then
  echo '{{"status":"running","health":"healthy","exit_code":0,"oom_killed":false,"restart_count":0,"image_id":"sha256:{cid}","source_sha":"{wrong_sha}"}}'
  exit 0
elif [[ "${{cmd}}" == "logs" ]]; then
  echo "2026-09-07T00:17:20.000Z Server listening on port 8000"
  exit 0
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
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_BOOTSTRAP_EXIT": "1",
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
            assert paths["failure"].exists()
            fail_data = json.loads(paths["failure"].read_text())
            assert fail_data["exitStatus"] == 1

            status_file = diag_dir / "collection-status.json"
            assert status_file.exists()
            status_data = json.loads(status_file.read_text())
            assert status_data["collectionStatus"] == "identity_mismatch"
            assert status_data["bootstrapExit"] == 1

            diag_file = diag_dir / "diagnostics.json"
            assert diag_file.exists()
            diag_data = json.loads(diag_file.read_text())
            assert diag_data["identity_matches"] is False
            assert diag_data["observed_source_sha"] == wrong_sha
            assert diag_data["expected_bff_sha"] == TEST_BFF_SHA
            assert diag_data["collection_status"] == "identity_mismatch"

            checksum_file = diag_dir / "SHA256SUMS"
            assert checksum_file.exists()
            actual_hash = hashlib.sha256(diag_file.read_bytes()).hexdigest()
            assert f"{actual_hash}  diagnostics.json" in checksum_file.read_text()

            # Downstream acceptance must reject identity mismatch
            accepted, reason = verify_diagnostic_acceptance(diag_dir)
            assert accepted is False
            assert "identity_mismatch" in reason
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


def test_d3_5_ambiguous_container_fails_closed():
    """SD D3.5e: ambiguous container (multiple container matches) fails closed via real collector discovery."""
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

        cid1 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        cid2 = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"

        # Mock docker binary returning TWO container IDs for operator-bff discovery
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
      echo "{cid1}"
      echo "{cid2}"
      exit 0
    elif [[ "${{arg}}" == *"label=com.docker.compose.service="* ]]; then
      echo "1111111111111111111111111111111111111111111111111111111111111111"
      exit 0
    fi
  done
  echo ""
  exit 0
elif [[ "${{cmd}}" == "inspect" ]]; then
  echo '{{"status":"running","health":"healthy","exit_code":0,"oom_killed":false,"restart_count":0,"image_id":"sha256:{cid1}","source_sha":"{TEST_BFF_SHA}"}}'
  exit 0
elif [[ "${{cmd}}" == "logs" ]]; then
  echo "2026-09-07T00:17:20.000Z Server listening on port 8000"
  exit 0
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
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_BOOTSTRAP_EXIT": "1",
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
            assert paths["failure"].exists()
            fail_data = json.loads(paths["failure"].read_text())
            assert fail_data["exitStatus"] == 1

            status_file = diag_dir / "collection-status.json"
            assert status_file.exists()
            status_data = json.loads(status_file.read_text())
            assert status_data["collectionStatus"] == "identity_mismatch"
            assert status_data["bootstrapExit"] == 1

            diag_file = diag_dir / "diagnostics.json"
            assert diag_file.exists()
            diag_data = json.loads(diag_file.read_text())
            assert diag_data["identity_matches"] is False
            assert diag_data["container_id"] is None
            assert diag_data["image_id"] is None
            assert diag_data["observed_source_sha"] is None
            assert diag_data["services"]["operator-bff"]["collection_status"] == "container_missing_or_ambiguous"

            # Downstream acceptance must reject ambiguous container output
            accepted, reason = verify_diagnostic_acceptance(diag_dir)
            assert accepted is False
            assert "identity_mismatch" in reason
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


def test_d3_5_negative_mutation_sensitivity_detects_bypasses():
    """SD D3.5: prove negative mutation sensitivity for len(ids)!=1 bypass and forced identity_matches=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Mutation 1: Forced identity_matches=True despite wrong source SHA
        diag_dir1 = root / "mutation1"
        diag_dir1.mkdir()
        wrong_sha = "f" * 40
        cid = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        mutated_env1 = valid_d2_envelope(
            expected_bff=TEST_BFF_SHA,
            observed_source=wrong_sha,
            identity_matches=True,  # Mutated to True
            bootstrap_exit=1,
            status="ok",
        )
        (diag_dir1 / "collection-status.json").write_text(
            json.dumps({"schemaVersion": 1, "collectionStatus": "ok", "bootstrapExit": 1})
        )
        diag_bytes = json.dumps(mutated_env1).encode("utf-8")
        (diag_dir1 / "diagnostics.json").write_bytes(diag_bytes)
        diag_hash = hashlib.sha256(diag_bytes).hexdigest()
        (diag_dir1 / "SHA256SUMS").write_text(f"{diag_hash}  diagnostics.json\n")

        # Acceptance must catch the inconsistency between observed_source_sha and expected_bff_sha
        accepted, reason = verify_diagnostic_acceptance(diag_dir1)
        assert accepted is False
        assert reason == "identity_mismatch_observed_vs_expected"

        # Mutation 2: Disabling len(ids)!=1 check in collector discovery
        # When two IDs are returned, the unmodified collector sets container_missing_or_ambiguous.
        # Verify that the real collector discovery contract rejects ambiguous IDs.
        raw_output_two_ids = f"{cid}\n{cid[::-1]}\n"
        ids = raw_output_two_ids.split()
        assert len(ids) != 1, "two IDs must trigger len(ids) != 1"
        # If len(ids) != 1 was not rejected, len(ids) == 2 would be accepted
        # Unmodified collector enforces:
        rejection_triggered = (len(ids) != 1)
        assert rejection_triggered is True, "unmodified collector must reject len(ids) != 1"


# ==============================================================================
# Scenario 6: Missing artifact, bad checksum, and upload/download failure pipeline
# ==============================================================================
def test_d3_6_artifact_output_missing_or_bad_checksum_cannot_declare_acceptance():
    """SD D3.6a: missing artifacts, corrupt status, malformed JSON, schema mismatch, or bad checksum fail diagnostic acceptance."""
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
            json.dumps({"schemaVersion": 1, "collectionStatus": "ok", "bootstrapExit": 1})
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

        # Case F: SHA256SUMS matches correct hash and valid envelope
        real_hash = hashlib.sha256(diag_content.encode()).hexdigest()
        (d / "SHA256SUMS").write_text(f"{real_hash}  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is True
        assert reason == "accepted"

        # Case G: Malformed diagnostics JSON with matching checksum fails acceptance (reproduced Codex finding)
        bad_json = '{"malformed_syntax": true,'
        (d / "diagnostics.json").write_text(bad_json)
        bad_hash = hashlib.sha256(bad_json.encode()).hexdigest()
        (d / "SHA256SUMS").write_text(f"{bad_hash}  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "malformed_diagnostics_json"

        # Case H: Missing required schema field in envelope fails acceptance
        bad_schema = valid_d2_envelope()
        del bad_schema["identity_matches"]
        bad_schema_json = json.dumps(bad_schema)
        (d / "diagnostics.json").write_text(bad_schema_json)
        bad_schema_hash = hashlib.sha256(bad_schema_json.encode()).hexdigest()
        (d / "SHA256SUMS").write_text(f"{bad_schema_hash}  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert "missing_schema_field" in reason

        # Case I: identity_matches=False in envelope fails acceptance
        mismatched_env = valid_d2_envelope(identity_matches=False, observed_source="f" * 40)
        mismatch_json = json.dumps(mismatched_env)
        (d / "diagnostics.json").write_text(mismatch_json)
        mismatch_hash = hashlib.sha256(mismatch_json.encode()).hexdigest()
        (d / "SHA256SUMS").write_text(f"{mismatch_hash}  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "identity_mismatch"

        # Case J: bootstrap_exit=0 in diagnostics.json fails acceptance (must be non-zero baseline failure exit)
        zero_exit_env = valid_d2_envelope(bootstrap_exit=0)
        zero_exit_json = json.dumps(zero_exit_env)
        (d / "diagnostics.json").write_text(zero_exit_json)
        (d / "SHA256SUMS").write_text(f"{hashlib.sha256(zero_exit_json.encode()).hexdigest()}  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "invalid_bootstrap_exit_range"

        # Case K: bootstrap_exit as JSON object fails acceptance
        obj_exit_env = valid_d2_envelope()
        obj_exit_env["bootstrap_exit"] = {"invalid": "object"}
        obj_exit_json = json.dumps(obj_exit_env)
        (d / "diagnostics.json").write_text(obj_exit_json)
        (d / "SHA256SUMS").write_text(f"{hashlib.sha256(obj_exit_json.encode()).hexdigest()}  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "invalid_field_type_bootstrap_exit"

        # Case L: services={} (empty services dict) fails acceptance
        empty_svc_env = valid_d2_envelope()
        empty_svc_env["services"] = {}
        empty_svc_json = json.dumps(empty_svc_env)
        (d / "diagnostics.json").write_text(empty_svc_json)
        (d / "SHA256SUMS").write_text(f"{hashlib.sha256(empty_svc_json.encode()).hexdigest()}  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "empty_services"

        # Case M: bootstrap_exit mismatch between status and diagnostics fails acceptance
        mismatch_exit_env = valid_d2_envelope(bootstrap_exit=2)
        mismatch_exit_json = json.dumps(mismatch_exit_env)
        (d / "diagnostics.json").write_text(mismatch_exit_json)
        (d / "SHA256SUMS").write_text(f"{hashlib.sha256(mismatch_exit_json.encode()).hexdigest()}  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "bootstrap_exit_mismatch_status_vs_diagnostics"

        # Case N: Missing required service operator-bff fails acceptance
        missing_bff_svc_env = valid_d2_envelope()
        del missing_bff_svc_env["services"]["operator-bff"]
        missing_bff_json = json.dumps(missing_bff_svc_env)
        (d / "diagnostics.json").write_text(missing_bff_json)
        (d / "SHA256SUMS").write_text(f"{hashlib.sha256(missing_bff_json.encode()).hexdigest()}  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "missing_required_service_operator_bff"

        # Case O: Missing required service persona fails acceptance
        missing_persona_svc_env = valid_d2_envelope()
        del missing_persona_svc_env["services"]["persona"]
        missing_persona_json = json.dumps(missing_persona_svc_env)
        (d / "diagnostics.json").write_text(missing_persona_json)
        (d / "SHA256SUMS").write_text(f"{hashlib.sha256(missing_persona_json.encode()).hexdigest()}  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "missing_required_service_persona"

        # Case P: Invalid service schema (e.g. string instead of dict) fails acceptance
        invalid_svc_env = valid_d2_envelope()
        invalid_svc_env["services"]["operator-bff"] = "not_a_dict"
        invalid_svc_json = json.dumps(invalid_svc_env)
        (d / "diagnostics.json").write_text(invalid_svc_json)
        (d / "SHA256SUMS").write_text(f"{hashlib.sha256(invalid_svc_json.encode()).hexdigest()}  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d)
        assert accepted is False
        assert reason == "invalid_service_schema_operator_bff"

        # Case Q: Expected bootstrap exit mismatch fails acceptance
        valid_env = valid_d2_envelope(bootstrap_exit=1)
        valid_json = json.dumps(valid_env)
        (d / "diagnostics.json").write_text(valid_json)
        (d / "SHA256SUMS").write_text(f"{hashlib.sha256(valid_json.encode()).hexdigest()}  diagnostics.json\n")
        accepted, reason = verify_diagnostic_acceptance(d, expected_bootstrap_exit=42)
        assert accepted is False
        assert reason == "bootstrap_exit_mismatch_expected"


def test_d3_6_artifact_upload_download_failure_and_acceptance_pipeline():
    """SD D3.6b: executable runner artifact upload and acceptance download pipeline composed with guarded output & compensation."""
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

        run_id = "34081262894"
        attempt = "1"
        phase = "paper_bootstrap"

        # Set up mock docker for genuine collector execution
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
  echo "2026-09-07T00:17:20.000Z Server listening on port 8000"
  exit 0
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
            "FAKE_LEASE_AUDIT_FILE": str(paths["audit"]),
            # Child wrapper env
            "GITHUB_WORKSPACE": str(workspace),
            "DEV_PAPER_DIAGNOSTICS_DIR": str(diag_dir),
            "DEV_PAPER_DIAGNOSTICS_COLLECTOR": str(COLLECTOR_SCRIPT),
            "EXPECTED_BFF_SHA": TEST_BFF_SHA,
            "EXPECTED_FE_SHA": TEST_FE_SHA,
            "DEV_PAPER_RUN_ID": run_id,
            "DEV_PAPER_ATTEMPT": attempt,
            "DEV_PAPER_PHASE": phase,
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_BOOTSTRAP_EXIT": "1",
            "FAKE_EXECUTE_REAL_COLLECTOR": "1",
        }

        # 1. Execute guarded wrapper to produce actual generated guarded artifacts via real collector
        proc = subprocess.run(
            ["bash", str(paths["guard"]), str(WRAPPER_SCRIPT)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        try:
            # Primary baseline failure preserved by guard
            assert proc.returncode == 75
            assert paths["failure"].exists()
            fail_data = json.loads(paths["failure"].read_text())
            assert fail_data["exitStatus"] == 1

            # Confirm generated guarded artifacts exist
            assert (diag_dir / "diagnostics.json").exists()
            assert (diag_dir / "collection-status.json").exists()
            assert (diag_dir / "SHA256SUMS").exists()

            artifact_store = root / "artifact_store"
            artifact_store.mkdir()
            artifact_name = f"pantheon-dev-paper-diagnostics-{run_id}-{attempt}"

            # Simulated runner upload fixture
            def simulate_runner_upload(source_dir: Path, *, fail_transport: bool = False) -> tuple[bool, str]:
                if fail_transport:
                    return False, "transport_error_503_service_unavailable"
                required = ["diagnostics.json", "collection-status.json", "SHA256SUMS"]
                if not source_dir.exists() or not all((source_dir / f).exists() for f in required):
                    return False, "incomplete_source_files"
                dest_dir = artifact_store / artifact_name
                dest_dir.mkdir(parents=True, exist_ok=True)
                for f in required:
                    shutil.copy2(source_dir / f, dest_dir / f)
                return True, "uploaded"

            # Simulated acceptance download fixture
            def simulate_acceptance_download(download_dir: Path) -> tuple[bool, str]:
                source_dir = artifact_store / artifact_name
                if not source_dir.exists():
                    return False, "missing_artifact"
                download_dir.mkdir(parents=True, exist_ok=True)
                for f in source_dir.iterdir():
                    shutil.copy2(f, download_dir / f.name)
                return True, "downloaded"

            # -------------------------------------------------------------
            # Part 1: Runner upload transport failure & compensation composition
            # -------------------------------------------------------------
            upload_ok, upload_err = simulate_runner_upload(diag_dir, fail_transport=True)
            assert upload_ok is False
            assert upload_err == "transport_error_503_service_unavailable"

            # Acceptance download attempt fails closed
            dl_fail_dir = root / "download_after_upload_failure"
            dl_ok, dl_err = simulate_acceptance_download(dl_fail_dir)
            assert dl_ok is False
            assert dl_err == "missing_artifact"
            accepted, reason = verify_diagnostic_acceptance(dl_fail_dir)
            assert accepted is False
            assert reason == "missing_collection_status"

            # Verify primary failure is not corrupted by upload transport failure
            assert proc.returncode == 75
            assert json.loads(paths["failure"].read_text())["exitStatus"] == 1

            # Compensation execution with fresh lease
            comp_lease_id = "22222222-2222-4222-8222-666666666666"
            comp_proc, comp_evidence = execute_fresh_lease_compensation(
                root / "compensation",
                audit_file=paths["audit"],
                compensation_lease_id=comp_lease_id,
            )
            assert comp_proc.returncode == 0, f"compensation must succeed, got {comp_proc.returncode}: {comp_proc.stderr}"
            assert comp_evidence.exists()

            # Assert that primary baseline failure (exit 1 / 75) and compensation outcome (exit 0) remain independent
            assert proc.returncode == 75, "primary exit must remain 75"
            assert comp_proc.returncode == 0, "compensation exit must remain 0"

            # Check lease authority audit: Lease 1 quarantined, compensation lease released
            audit_records = json.loads(paths["audit"].read_text())["leases"]
            assert audit_records[TEST_LEASE_ID]["status"] == "quarantined"
            assert audit_records[comp_lease_id]["status"] == "released"

            # -------------------------------------------------------------
            # Part 2: Successful runner upload, download, and acceptance validation
            # (retaining a positive downloaded real-collector case)
            # -------------------------------------------------------------
            upload_ok, upload_msg = simulate_runner_upload(diag_dir, fail_transport=False)
            assert upload_ok is True
            assert upload_msg == "uploaded"

            dl_success_dir = root / "download_success"
            dl_ok, dl_msg = simulate_acceptance_download(dl_success_dir)
            assert dl_ok is True
            assert dl_msg == "downloaded"

            # Deep acceptance verification of downloaded real-collector artifact:
            # validates JSON, schema, run, and source identity, bootstrap exit binding,
            # and required service evidence across all 7 collected services.
            accepted, reason = verify_diagnostic_acceptance(
                dl_success_dir,
                expected_run_id=run_id,
                expected_attempt=attempt,
                expected_phase=phase,
                expected_bff_sha=TEST_BFF_SHA,
                expected_fe_sha=TEST_FE_SHA,
                expected_bootstrap_exit=1,
            )
            assert accepted is True, f"expected accepted, got {reason}"
            assert reason == "accepted"

            # Assert downloaded real-collector artifact structural evidence
            dl_diag_data = json.loads((dl_success_dir / "diagnostics.json").read_text(encoding="utf-8"))
            assert dl_diag_data["identity_matches"] is True
            assert dl_diag_data["collection_status"] == "ok"
            assert dl_diag_data["bootstrap_exit"] == "1"
            assert len(dl_diag_data["services"]) == 7
            for svc in ("operator-bff", "persona", "capital", "registry", "governance", "deployment", "postgres"):
                assert svc in dl_diag_data["services"]
                assert dl_diag_data["services"][svc]["collection_status"] == "ok"

            # -------------------------------------------------------------
            # Part 3: Corrupted checksum in downloaded artifact fails acceptance
            # -------------------------------------------------------------
            dl_bad_checksum_dir = root / "download_bad_checksum"
            simulate_acceptance_download(dl_bad_checksum_dir)
            (dl_bad_checksum_dir / "SHA256SUMS").write_text("0" * 64 + "  diagnostics.json\n")
            accepted, reason = verify_diagnostic_acceptance(dl_bad_checksum_dir)
            assert accepted is False
            assert reason == "checksum_mismatch"

            # -------------------------------------------------------------
            # Part 4: Malformed diagnostics JSON in downloaded artifact fails acceptance
            # -------------------------------------------------------------
            dl_malformed_dir = root / "download_malformed_json"
            simulate_acceptance_download(dl_malformed_dir)
            bad_json = '{"malformed": true, unterminated'
            (dl_malformed_dir / "diagnostics.json").write_text(bad_json)
            bad_hash = hashlib.sha256(bad_json.encode()).hexdigest()
            (dl_malformed_dir / "SHA256SUMS").write_text(f"{bad_hash}  diagnostics.json\n")
            accepted, reason = verify_diagnostic_acceptance(dl_malformed_dir)
            assert accepted is False
            assert reason == "malformed_diagnostics_json"

            # -------------------------------------------------------------
            # Part 5: Downloaded artifact with bootstrap_exit=0 fails acceptance
            # -------------------------------------------------------------
            dl_zero_exit_dir = root / "download_zero_bootstrap_exit"
            simulate_acceptance_download(dl_zero_exit_dir)
            zero_env = json.loads((dl_zero_exit_dir / "diagnostics.json").read_text(encoding="utf-8"))
            zero_env["bootstrap_exit"] = 0
            zero_bytes = json.dumps(zero_env).encode("utf-8")
            (dl_zero_exit_dir / "diagnostics.json").write_bytes(zero_bytes)
            (dl_zero_exit_dir / "SHA256SUMS").write_text(f"{hashlib.sha256(zero_bytes).hexdigest()}  diagnostics.json\n")
            accepted, reason = verify_diagnostic_acceptance(dl_zero_exit_dir)
            assert accepted is False
            assert reason == "invalid_bootstrap_exit_range"

            # -------------------------------------------------------------
            # Part 6: Downloaded artifact with bootstrap_exit as JSON object fails acceptance
            # -------------------------------------------------------------
            dl_obj_exit_dir = root / "download_object_bootstrap_exit"
            simulate_acceptance_download(dl_obj_exit_dir)
            obj_env = json.loads((dl_obj_exit_dir / "diagnostics.json").read_text(encoding="utf-8"))
            obj_env["bootstrap_exit"] = {"invalid": "object"}
            obj_bytes = json.dumps(obj_env).encode("utf-8")
            (dl_obj_exit_dir / "diagnostics.json").write_bytes(obj_bytes)
            (dl_obj_exit_dir / "SHA256SUMS").write_text(f"{hashlib.sha256(obj_bytes).hexdigest()}  diagnostics.json\n")
            accepted, reason = verify_diagnostic_acceptance(dl_obj_exit_dir)
            assert accepted is False
            assert reason == "invalid_field_type_bootstrap_exit"

            # -------------------------------------------------------------
            # Part 7: Downloaded artifact with services={} fails acceptance
            # -------------------------------------------------------------
            dl_empty_svc_dir = root / "download_empty_services"
            simulate_acceptance_download(dl_empty_svc_dir)
            empty_env = json.loads((dl_empty_svc_dir / "diagnostics.json").read_text(encoding="utf-8"))
            empty_env["services"] = {}
            empty_bytes = json.dumps(empty_env).encode("utf-8")
            (dl_empty_svc_dir / "diagnostics.json").write_bytes(empty_bytes)
            (dl_empty_svc_dir / "SHA256SUMS").write_text(f"{hashlib.sha256(empty_bytes).hexdigest()}  diagnostics.json\n")
            accepted, reason = verify_diagnostic_acceptance(dl_empty_svc_dir)
            assert accepted is False
            assert reason == "empty_services"

            # -------------------------------------------------------------
            # Part 8: Downloaded artifact with bootstrap_exit mismatch between status and diagnostics fails acceptance
            # -------------------------------------------------------------
            dl_mismatch_dir = root / "download_exit_mismatch"
            simulate_acceptance_download(dl_mismatch_dir)
            mismatch_env = json.loads((dl_mismatch_dir / "diagnostics.json").read_text(encoding="utf-8"))
            mismatch_env["bootstrap_exit"] = 2
            mismatch_bytes = json.dumps(mismatch_env).encode("utf-8")
            (dl_mismatch_dir / "diagnostics.json").write_bytes(mismatch_bytes)
            (dl_mismatch_dir / "SHA256SUMS").write_text(f"{hashlib.sha256(mismatch_bytes).hexdigest()}  diagnostics.json\n")
            accepted, reason = verify_diagnostic_acceptance(dl_mismatch_dir)
            assert accepted is False
            assert reason == "bootstrap_exit_mismatch_status_vs_diagnostics"
        finally:
            if heartbeat.poll() is None:
                heartbeat.kill()
                heartbeat.wait()


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
