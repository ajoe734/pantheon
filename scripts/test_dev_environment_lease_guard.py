from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_SCRIPT = REPO_ROOT / "scripts" / "run_with_dev_environment_lease.sh"
LEASE_CLI = REPO_ROOT / "scripts" / "dev_environment_lease.py"
TOKEN_ENV = "PANTHEON_ENVIRONMENT_LEASE_TOKEN"
TEST_TOKEN = "guard-adjacent-cli-test-token"
TEST_LEASE_ID = "11111111-1111-4111-8111-111111111111"


FAKE_ADJACENT_CLI = r'''#!/usr/bin/env python3
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
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_args: sys.exit(0))
    while True:
        time.sleep(1)

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
    assert os.environ.get(TOKEN_ENV) == os.environ["FAKE_EXPECTED_TOKEN"]
    count_file = Path(os.environ["FAKE_VERIFY_COUNT_FILE"])
    count = int(count_file.read_text(encoding="utf-8") or "0") + 1
    count_file.write_text(f"{count}\n", encoding="utf-8")
    fail_at = int(os.environ.get("FAKE_VERIFY_FAIL_AT", "0"))
    if fail_at and count >= fail_at:
        print("simulated GitHub API outage", file=sys.stderr)
        raise SystemExit(78)
    print('{"status":"verified"}')
    raise SystemExit(0)

raise SystemExit(f"unsupported fake CLI command: {command}")
'''


NESTED_TARGET = r'''
grandchild_file="$1"
pid_file="$2"
(
  sleep 30 &
  grandchild=$!
  printf '%s\n' "${grandchild}" >"${grandchild_file}"
  wait "${grandchild}"
) &
nested=$!
sleep 30 &
background=$!
while [[ ! -s "${grandchild_file}" ]]; do sleep 0.02; done
grandchild="$(tr -d '[:space:]' <"${grandchild_file}")"
printf '%s %s %s %s\n' "$$" "${nested}" "${background}" "${grandchild}" >"${pid_file}"
wait
'''


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


def assert_processes_terminated(pids: list[int]) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if all(process_state(pid) in ("", "Z", "X", "x") for pid in pids):
            return
        time.sleep(0.05)
    states = {pid: process_state(pid) for pid in pids}
    assert False, f"guarded process group still has live members: {states}"


def prepare_fixture(root: Path) -> dict[str, Path]:
    guard_dir = root / "guard"
    guard_dir.mkdir()
    guard = guard_dir / GUARD_SCRIPT.name
    shutil.copy2(GUARD_SCRIPT, guard)
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
        "target_pids": root / "target-pids.txt",
        "grandchild": root / "grandchild.pid",
    }
    paths["state"].write_text(
        json.dumps({"leaseId": TEST_LEASE_ID, "expectedBackendSha": "a" * 40})
        + "\n",
        encoding="utf-8",
    )
    paths["verify_count"].write_text("0\n", encoding="utf-8")
    return paths


def start_fake_heartbeat(paths: dict[str, Path]) -> subprocess.Popen[str]:
    environment = dict(os.environ)
    environment.pop(TOKEN_ENV, None)
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
        env=environment,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    paths["heartbeat_pid"].write_text(f"{heartbeat.pid}\n", encoding="utf-8")
    wait_for_file(paths["heartbeat_identity"])
    return heartbeat


def guard_environment(paths: dict[str, Path], *, fail_at: int = 0) -> dict[str, str]:
    return {
        **os.environ,
        "TARGET_ENV": "dev",
        TOKEN_ENV: TEST_TOKEN,
        "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(paths["state"]),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE": str(
            paths["heartbeat_pid"]
        ),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_IDENTITY_FILE": str(
            paths["heartbeat_identity"]
        ),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE": str(paths["failure"]),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_VERIFY_INTERVAL_SECONDS": "1",
        "PANTHEON_DEV_ENVIRONMENT_LEASE_MAX_HEARTBEAT_AGE_SECONDS": "120",
        "FAKE_EXPECTED_TOKEN": TEST_TOKEN,
        "FAKE_VERIFY_COUNT_FILE": str(paths["verify_count"]),
        "FAKE_VERIFY_FAIL_AT": str(fail_at),
    }


def start_nested_guard(
    paths: dict[str, Path],
    *,
    fail_at: int = 0,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    environment = guard_environment(paths, fail_at=fail_at)
    environment.update(extra_env or {})
    guard = subprocess.Popen(
        [
            "bash",
            str(paths["guard"]),
            "bash",
            "-c",
            NESTED_TARGET,
            "lease-target",
            str(paths["grandchild"]),
            str(paths["target_pids"]),
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    wait_for_file(paths["target_pids"])
    return guard


def read_target_pids(path: Path) -> list[int]:
    return [int(value) for value in path.read_text(encoding="utf-8").split()]


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=3)


def test_real_cli_heartbeat_identity_binds_pid_start_cmdline_cli_and_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state_file = root / "state.json"
        identity_file = root / "heartbeat-identity.json"
        state_file.write_text("{}\n", encoding="utf-8")
        environment = dict(os.environ)
        environment.pop(TOKEN_ENV, None)
        heartbeat = subprocess.Popen(
            [
                sys.executable,
                str(LEASE_CLI),
                "heartbeat-loop",
                "--state-file",
                str(state_file),
                "--ttl-seconds",
                "30",
                "--interval-seconds",
                "10",
                "--identity-json-out",
                str(identity_file),
                "--token-stdin",
            ],
            env=environment,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            assert heartbeat.stdin is not None
            heartbeat.stdin.write("identity-test-token\n")
            heartbeat.stdin.close()
            heartbeat.stdin = None
            wait_for_file(identity_file)
            verified = subprocess.run(
                [
                    sys.executable,
                    str(LEASE_CLI),
                    "verify-heartbeat-identity",
                    "--identity-file",
                    str(identity_file),
                    "--pid",
                    str(heartbeat.pid),
                    "--expected-cli",
                    str(LEASE_CLI),
                    "--state-file",
                    str(state_file),
                ],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            assert verified.returncode == 0, verified.stderr

            identity = json.loads(identity_file.read_text(encoding="utf-8"))
            identity["startTicks"] += 1
            identity_file.write_text(json.dumps(identity) + "\n", encoding="utf-8")
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(LEASE_CLI),
                    "verify-heartbeat-identity",
                    "--identity-file",
                    str(identity_file),
                    "--pid",
                    str(heartbeat.pid),
                    "--expected-cli",
                    str(LEASE_CLI),
                    "--state-file",
                    str(state_file),
                ],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            assert rejected.returncode == 75
            assert "startTicks mismatch" in rejected.stderr
        finally:
            if heartbeat.poll() is None:
                heartbeat.terminate()
            heartbeat.communicate(timeout=3)


def test_periodic_remote_verify_outage_kills_entire_isolated_process_group() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        paths = prepare_fixture(Path(tmp))
        malicious_log = Path(tmp) / "malicious-cli.log"
        malicious_cli = Path(tmp) / "malicious-cli.py"
        malicious_cli.write_text(
            f"from pathlib import Path\nPath({str(malicious_log)!r}).write_text('used')\n",
            encoding="utf-8",
        )
        heartbeat = start_fake_heartbeat(paths)
        guard = start_nested_guard(
            paths,
            fail_at=2,
            extra_env={"PANTHEON_DEV_ENVIRONMENT_LEASE_CLI": str(malicious_cli)},
        )
        try:
            target_pids = read_target_pids(paths["target_pids"])
            parent_pid = target_pids[0]
            assert os.getpgid(parent_pid) == parent_pid
            assert os.getsid(parent_pid) == parent_pid
            assert all(os.getpgid(pid) == parent_pid for pid in target_pids)
            _stdout, stderr = guard.communicate(timeout=10)
            heartbeat.wait(timeout=3)
        finally:
            stop_process(guard)
            stop_process(heartbeat)

        assert guard.returncode == 75, stderr
        assert "remote lease verification failed" in stderr
        assert int(paths["verify_count"].read_text(encoding="utf-8")) >= 2
        assert not malicious_log.exists()
        assert_processes_terminated(target_pids)


def test_stopped_heartbeat_kills_nested_and_background_processes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        paths = prepare_fixture(Path(tmp))
        heartbeat = start_fake_heartbeat(paths)
        guard = start_nested_guard(paths)
        try:
            target_pids = read_target_pids(paths["target_pids"])
            os.kill(heartbeat.pid, signal.SIGSTOP)
            _stdout, stderr = guard.communicate(timeout=10)
            heartbeat.wait(timeout=3)
        finally:
            if heartbeat.poll() is None:
                os.kill(heartbeat.pid, signal.SIGCONT)
            stop_process(guard)
            stop_process(heartbeat)

        assert guard.returncode == 75, stderr
        assert any(
            message in stderr
            for message in (
                "lease heartbeat identity/health was lost",
                "lease heartbeat was lost during remote verification",
            )
        ), stderr
        assert_processes_terminated(target_pids)


def test_guard_rejects_unsafe_heartbeat_states_and_cli_override() -> None:
    script = GUARD_SCRIPT.read_text(encoding="utf-8")
    assert 'LEASE_CLI="${SCRIPT_DIR}/dev_environment_lease.py"' in script
    assert "PANTHEON_DEV_ENVIRONMENT_LEASE_CLI:-" not in script
    assert '""|T*|t*|Z*|X*|x*|D*' in script
    assert "verify-heartbeat-identity" in script
    assert "REMOTE_VERIFY_INTERVAL_SECONDS" in script
    assert 'kill -TERM -- "-${pgid}"' in script
    assert 'kill -KILL -- "-${pgid}"' in script
