"""Isolated transport tests: no network, SSH connection, or Docker daemon."""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
import uuid

import pytest

from scripts import dev_remote_guarded_exec as sender
from scripts import dev_remote_watchdog as watchdog


ROOT = Path(__file__).resolve().parents[1]
NONCE = "a" * 32


def wait_until(check, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("isolated subprocess condition timed out")


def frame(sequence, nonce=NONCE):
    return sender._frame(nonce, sequence)


def envelope(script, seconds=5):
    return (json.dumps({"script": base64.b64encode(script.encode()).decode(),
                        "nonce": NONCE, "deadline_seconds": seconds}) + "\n").encode()


def process_state(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return None


@pytest.fixture
def children():
    processes = []
    yield processes
    for process in processes:
        if process.stdin and not process.stdin.closed:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def launch_watchdog(children, *, pause=0.15, kill=0.8, environment=None):
    code = (
        "from scripts.dev_remote_watchdog import run_watchdog,Timings; "
        f"raise SystemExit(run_watchdog(timings=Timings(pause={pause!r},"
        f"silence_kill={kill!r},poll=0.005,terminate_grace=0.05)))"
    )
    process = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True,
                               env=environment)
    children.append(process)
    return process


def write(process, data):
    process.stdin.write(data)
    process.stdin.flush()


def finish(process, timeout=4):
    result = process.wait(timeout=timeout)
    output = process.stdout.read().decode()
    errors = process.stderr.read().decode()
    return result, output, errors


@pytest.fixture
def fake_ssh(tmp_path):
    helper = tmp_path / "dev_vm_ssh.sh"
    # This launcher simulates an SSH remote host with a separate session. The
    # local helper itself remains in the sender's externally guarded pgid.
    helper.write_text(
        f"#!{sys.executable}\n"
        "import json,os,shlex,subprocess,sys\n"
        "assert sys.argv[1]=='exec'\n"
        "p=subprocess.Popen(shlex.split(sys.argv[2]),start_new_session=True)\n"
        "if os.environ.get('TEST_SSH_IDENTITY'):\n"
        " open(os.environ['TEST_SSH_IDENTITY'],'w').write(json.dumps([os.getpid(),os.getpgrp(),p.pid]))\n"
        "raise SystemExit(p.wait())\n"
    )
    helper.chmod(0o700)
    return helper


def launch_sender(children, tmp_path, fake_ssh, script, *, seconds=5, extra_env=None,
                  observer_code=None, candidate_args=(), cli_setup=None):
    script_path = tmp_path / "private-run.sh"
    script_path.write_text(script)
    script_path.chmod(0o600)
    environment = {**os.environ, "TARGET_ENV": "dev",
                   "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": str(uuid.uuid4()),
                   **(extra_env or {})}
    command = [sys.executable, str(ROOT / "scripts/dev_remote_guarded_exec.py"),
               "--ssh-helper", str(fake_ssh), "--script-file", str(script_path),
               "--deadline-seconds", str(seconds), *map(str, candidate_args)]
    if cli_setup is not None:
        code = ("from scripts import dev_remote_guarded_exec as transport\n"
                + cli_setup + "\n"
                + f"raise SystemExit(transport.main({command[2:]!r}))")
        command = [sys.executable, "-c", code]
    if observer_code is not None:
        code = ("from pathlib import Path\nfrom scripts.dev_remote_guarded_exec import execute\n"
                + observer_code + "\n"
                + f"raise SystemExit(execute(Path({str(fake_ssh)!r}),Path({str(script_path)!r}),"
                + f"{seconds},observer=observer))")
        command = [sys.executable, "-c", code]
    process = subprocess.Popen(
        command,
        cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,  # simulates the existing guard, not the runner
    )
    children.append(process)
    return process


@pytest.mark.parametrize("status", [0, 3, 17])
def test_sender_bootstrap_success_and_exact_exit_status(children, tmp_path, fake_ssh, status):
    process = launch_sender(children, tmp_path, fake_ssh, f"printf 'safe-result\\n'; exit {status}\n")
    code, output, errors = finish(process)
    assert code == status, (output, errors)
    assert output == "safe-result\n"
    assert errors == ""


def test_sender_and_ssh_stay_in_existing_guard_pgid(children, tmp_path, fake_ssh):
    identity = tmp_path / "identity.json"
    process = launch_sender(children, tmp_path, fake_ssh, "sleep 0.4\n",
                            extra_env={"TEST_SSH_IDENTITY": str(identity)})
    wait_until(identity.exists)
    helper_pid, helper_group, remote_pid = json.loads(identity.read_text())
    assert helper_pid != process.pid
    assert helper_group == os.getpgid(process.pid) == process.pid
    assert os.getpgid(remote_pid) != helper_group
    assert finish(process)[0] == 0


@pytest.mark.parametrize("sig,expected", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_sender_signal_mapping(children, tmp_path, fake_ssh, sig, expected):
    started = tmp_path / "started"
    process = launch_sender(children, tmp_path, fake_ssh, f"touch {shlex.quote(str(started))}; sleep 20\n")
    wait_until(started.exists)
    process.send_signal(sig)
    assert finish(process)[0] == expected


def test_sender_deadline_fails_and_diagnostics_never_echo_script(children, tmp_path, fake_ssh):
    secret = "PRIVATE_SCRIPT_CANARY_do_not_print_921"
    process = launch_sender(children, tmp_path, fake_ssh, f"export PRIVATE_VALUE={secret}\nsleep 20\n", seconds=1)
    code, output, errors = finish(process)
    assert code == 75
    assert secret not in output + errors
    assert "PRIVATE_VALUE" not in output + errors


@pytest.mark.parametrize("environment", [{"TARGET_ENV": "prod"},
                                          {"PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": "bad"}])
def test_sender_requires_dev_guard_identity(children, tmp_path, fake_ssh, environment):
    effect = tmp_path / "must-not-exist"
    process = launch_sender(children, tmp_path, fake_ssh, f"touch {effect}\n", extra_env=environment)
    assert finish(process)[0] == 75
    assert not effect.exists()


def test_private_script_rejects_world_readable_symlink_and_fifo(tmp_path):
    target = tmp_path / "target"
    target.write_text("private")
    target.chmod(0o644)
    with pytest.raises(sender.TransportError):
        sender._read_regular(target, maximum=100, private=True)
    target.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(sender.TransportError):
        sender._read_regular(link, maximum=100, private=True)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(sender.TransportError):
        sender._read_regular(fifo, maximum=100, private=True)


def test_no_child_launch_without_real_first_heartbeat(children, tmp_path):
    effect = tmp_path / "must-not-exist"
    process = launch_watchdog(children)
    write(process, envelope(f"touch {effect}\n"))
    assert finish(process)[0] == 75
    assert not effect.exists()


@pytest.mark.parametrize("bad", [b"{bad PRIVATE_INPUT}\n", frame(1), frame(0), frame(2, "b" * 32),
                                 b'{"type":"heartbeat","nonce":"x","sequence":2,"sequence":3}\n',
                                 b"x" * 300 + b"\n", b'{"type":"cancel"}\n'])
def test_invalid_initial_batch_fails_before_any_child_action(children, tmp_path, bad):
    effect = tmp_path / "must-not-exist"
    process = launch_watchdog(children)
    write(process, envelope(f"touch {effect}\n") + frame(1) + bad)
    code, output, errors = finish(process)
    assert code == 75
    assert not effect.exists()
    assert "PRIVATE_INPUT" not in output + errors


def test_eof_with_queued_initial_pulses_still_prevents_launch(children, tmp_path):
    effect = tmp_path / "must-not-exist"
    process = launch_watchdog(children)
    write(process, envelope(f"touch {effect}\n") + frame(1) + frame(2))
    process.stdin.close()
    assert finish(process)[0] == 75
    assert not effect.exists()


def test_pause_and_fresh_heartbeat_resume(children, tmp_path):
    pid_file = tmp_path / "pid"
    process = launch_watchdog(children, kill=1.5)
    write(process, envelope(f"echo $$ > {pid_file}\nsleep 0.5\n") + frame(1))
    wait_until(pid_file.exists)
    pid = int(pid_file.read_text())
    wait_until(lambda: process_state(pid) == "T")
    write(process, frame(2))
    wait_until(lambda: process_state(pid) != "T")
    for number in range(3, 20):
        if process.poll() is not None:
            break
        time.sleep(0.04)
        try:
            write(process, frame(number))
        except BrokenPipeError:
            break
    assert finish(process)[0] == 0


def test_frozen_runner_pauses_remote_then_resumes_without_heartbeat_backlog(children, tmp_path, fake_ssh):
    pid_file = tmp_path / "remote-child"
    process = launch_sender(children, tmp_path, fake_ssh,
                            f"echo $$ > {pid_file}\nsleep 4.2\n", seconds=12)
    wait_until(pid_file.exists)
    pid = int(pid_file.read_text())
    os.killpg(process.pid, signal.SIGSTOP)
    try:
        wait_until(lambda: process_state(pid) == "T", timeout=5)
    finally:
        os.killpg(process.pid, signal.SIGCONT)
    assert finish(process, timeout=8)[0] == 0


@pytest.mark.parametrize("cause", ["eof", "silence", "replay", "signal", "deadline"])
def test_cancellation_terminates_descendants(children, tmp_path, cause):
    pid_file = tmp_path / "descendant"
    process = launch_watchdog(children, pause=0.1, kill=0.25 if cause == "silence" else 3)
    script = f"sleep 20 &\necho $! > {pid_file}\nwait\n"
    write(process, envelope(script, seconds=1 if cause == "deadline" else 5) + frame(1))
    wait_until(pid_file.exists)
    pid = int(pid_file.read_text())
    if cause == "eof":
        process.stdin.close()
    elif cause == "replay":
        write(process, frame(1))
    elif cause == "signal":
        process.send_signal(signal.SIGTERM)
    elif cause == "deadline":
        for number in range(2, 35):
            if process.poll() is not None:
                break
            time.sleep(0.04)
            try:
                write(process, frame(number))
            except BrokenPipeError:
                break
    assert finish(process)[0] == 75
    wait_until(lambda: process_state(pid) in (None, "Z"))


def test_paused_child_not_resumed_by_valid_frame_followed_by_replay(children, tmp_path):
    counter = tmp_path / "counter"
    pid_file = tmp_path / "pid"
    script = f"echo $$ > {pid_file}\nwhile true; do echo tick >> {counter}; sleep 0.01; done\n"
    process = launch_watchdog(children, kill=1.5)
    write(process, envelope(script) + frame(1))
    wait_until(pid_file.exists)
    wait_until(lambda: process_state(int(pid_file.read_text())) == "T")
    before = counter.read_text()
    write(process, frame(2) + frame(2))
    assert finish(process)[0] == 75
    assert counter.read_text() == before


def test_child_stdin_is_devnull_and_only_read_end_inherited(children):
    script = f"""{shlex.quote(sys.executable)} - <<'PY'
import os,stat
fd=int(os.environ['PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD'])
assert fd >= 3
assert os.read(0,1) == b''
assert os.read(fd,1) == b'.'
matching=[]
for name in os.listdir('/proc/self/fd'):
 try:
  if os.fstat(int(name)).st_ino == os.fstat(fd).st_ino: matching.append(int(name))
 except OSError: pass
assert matching == [fd], matching
try: os.write(fd,b'x')
except OSError: pass
else: raise AssertionError('writable guard descriptor inherited')
print('read-only-pipe')
PY
"""
    process = launch_watchdog(children, pause=0.5, kill=2)
    write(process, envelope(script) + frame(1))
    assert finish(process)[:2] == (0, "read-only-pipe\n")


def test_watchdog_death_closes_pipe_and_fake_driver_drains_queued_pulses_before_mutation(children, tmp_path):
    ready, result = tmp_path / "ready", tmp_path / "result"
    script = f"""{shlex.quote(sys.executable)} - <<'PY'
import os,time
fd=int(os.environ['PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD'])
open({str(ready)!r},'w').write('ready')
time.sleep(0.2)
# Model the actual driver contract: do not accept one queued byte as fresh
# authority without draining all buffered bytes and checking for EOF.
while True:
 try:
  data=os.read(fd,4096)
  if not data:
   open({str(result)!r},'w').write('EOF-before-mutation')
   raise SystemExit(75)
 except BlockingIOError: break
open({str(result)!r},'w').write('UNSAFE-mutation')
PY
"""
    process = launch_watchdog(children, pause=0.5, kill=2)
    write(process, envelope(script) + frame(1))
    wait_until(ready.exists)
    write(process, frame(2))
    time.sleep(0.03)
    process.kill()
    process.wait(timeout=2)
    wait_until(result.exists)
    assert result.read_text() == "EOF-before-mutation"


def test_full_guard_pipe_fails_closed():
    read_fd, write_fd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        while True:
            try:
                os.write(write_fd, b"x" * 4096)
            except BlockingIOError:
                break
        with pytest.raises(watchdog.ProtocolError):
            watchdog._pulse(write_fd)
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_parent_success_still_cleans_live_background_descendant(children, tmp_path):
    pid_file = tmp_path / "background"
    process = launch_watchdog(children, pause=0.5, kill=2)
    script = f"sleep 20 &\necho $! > {pid_file}\nexit 0\n"
    write(process, envelope(script) + frame(1))
    assert finish(process)[0] == 0
    assert pid_file.exists()
    assert process_state(int(pid_file.read_text())) in (None, "Z")


def test_kill_escalation_reaps_term_ignoring_descendant(children, tmp_path):
    pid_file = tmp_path / "ignores-term"
    # Quote the embedded source as one actual shell argument.
    code = ("import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            f"open({str(pid_file)!r},'w').write(str(os.getpid())); time.sleep(20)")
    script = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)} &\nwait\n"
    process = launch_watchdog(children, pause=0.5, kill=2)
    write(process, envelope(script) + frame(1))
    wait_until(pid_file.exists)
    process.stdin.close()
    assert finish(process)[0] == 75
    assert process_state(int(pid_file.read_text())) in (None, "Z")


def test_raw_reader_handles_multiple_frames_in_one_read_and_eof():
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b'first\nsecond\n')
        os.close(write_fd)
        write_fd = None
        lines = watchdog.Lines(read_fd)
        assert lines.read(20) == b'first'
        assert lines.read(20) == b'second'
        with pytest.raises(watchdog.ProtocolError):
            lines.read(20)
    finally:
        if write_fd is not None:
            os.close(write_fd)
        os.close(read_fd)


def test_production_cli_has_no_test_timing_override():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/dev_remote_guarded_exec.py"), "--help"],
                            capture_output=True, text=True, check=True)
    assert "--heartbeat" not in result.stdout
    assert "--pause" not in result.stdout
    assert "--kill" not in result.stdout


def test_child_does_not_source_ambient_shell_startup_file(children, tmp_path):
    effect = tmp_path / "must-not-exist"
    startup = tmp_path / "untrusted-bash-env"
    startup.write_text(f"touch {effect}\n")
    process = launch_watchdog(children, pause=0.5, kill=2,
                              environment={**os.environ, "BASH_ENV": str(startup)})
    write(process, envelope("exit 0\n") + frame(1))
    assert finish(process)[0] == 0
    assert not effect.exists()


def test_child_does_not_import_ambient_bash_functions(children):
    process = launch_watchdog(children, pause=0.5, kill=2, environment={
        **os.environ, "BASH_FUNC_printf%%": '() { builtin printf "ambient-function\\n"; }',
    })
    write(process, envelope('printf "reviewed-script\\n"\n') + frame(1))
    assert finish(process)[:2] == (0, "reviewed-script\n")


def ack_script(digest, *, after_ack="", timeout=3):
    return f"""printf 'SEAL {digest}\\n'
{shlex.quote(sys.executable)} - <<'PY'
import os,select,time
fd=int(os.environ['PANTHEON_DEV_ARTIFACT_RECEIPT_ACK_FD'])
assert fd>=3
try: os.write(fd,b'x')
except OSError: pass
else: raise AssertionError('ack writer inherited')
until=time.monotonic()+{timeout}
data=b''
while not data.endswith(b'\\n'):
 if time.monotonic()>=until: raise SystemExit(75)
 if not select.select([fd],[],[],0.02)[0]: continue
 chunk=os.read(fd,100)
 if not chunk: raise SystemExit(75)
 data+=chunk
assert data=={(digest + chr(10)).encode()!r},data
{after_ack}
print('after-durable-ack')
PY
"""


def test_observer_persists_before_one_shot_receipt_ack(children, tmp_path, fake_ssh):
    digest = "b" * 64
    receipt = tmp_path / "durable-receipt"
    observer = f"""import os
def observer(line):
 if line != b'SEAL {digest}': return None
 fd=os.open({str(receipt)!r},os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 os.write(fd,b'durable')
 os.fsync(fd)
 os.close(fd)
 return '{digest}'
"""
    process = launch_sender(children, tmp_path, fake_ssh,
                            ack_script(digest, after_ack=f"assert open({str(receipt)!r}).read()=='durable'"),
                            observer_code=observer)
    code, output, errors = finish(process, timeout=6)
    assert (code, errors) == (0, "")
    assert output == f"SEAL {digest}\nafter-durable-ack\n"
    assert receipt.stat().st_mode & 0o777 == 0o600


def test_no_observer_never_acknowledges_stdout_claim(children, tmp_path, fake_ssh):
    process = launch_sender(children, tmp_path, fake_ssh, ack_script("b" * 64, timeout=0.3))
    code, output, errors = finish(process)
    assert code == 75
    assert "after-durable-ack" not in output


@pytest.mark.parametrize("receipt", ["bad", "0" * 64, "B" * 64])
def test_invalid_observer_ack_rejected(children, tmp_path, fake_ssh, receipt):
    process = launch_sender(children, tmp_path, fake_ssh, ack_script("b" * 64),
                            observer_code=f"def observer(line): return {receipt!r}")
    code, output, errors = finish(process)
    assert code == 75
    assert "after-durable-ack" not in output


def test_observer_exception_is_redacted_and_cancels(children, tmp_path, fake_ssh):
    process = launch_sender(children, tmp_path, fake_ssh, ack_script("b" * 64), observer_code=
                            "def observer(line): raise ValueError('PRIVATE_OBSERVER_CANARY')")
    code, output, errors = finish(process)
    assert code == 75
    assert "PRIVATE_OBSERVER_CANARY" not in output + errors


def test_repeated_receipt_callback_is_rejected_before_ack(children, tmp_path, fake_ssh):
    process = launch_sender(children, tmp_path, fake_ssh, "printf 'seal\\nseal\\n'; sleep 2\n",
                            observer_code="def observer(line): return 'b'*64")
    assert finish(process)[0] == 75


@pytest.mark.parametrize("second", ["b" * 64, "c" * 64])
def test_duplicate_or_conflicting_ack_fails_before_resume(children, tmp_path, second):
    pid_file = tmp_path / "pid"
    process = launch_watchdog(children, kill=2)
    write(process, envelope(f"echo $$ > {pid_file}\nsleep 20\n") + frame(1))
    wait_until(pid_file.exists)
    wait_until(lambda: process_state(int(pid_file.read_text())) == "T")
    write(process, sender._frame(NONCE, 2, "b" * 64) + sender._frame(NONCE, 3, second))
    assert finish(process)[0] == 75


def test_malformed_ack_frame_is_rejected(children, tmp_path):
    pid_file = tmp_path / "pid"
    process = launch_watchdog(children, pause=0.5, kill=2)
    write(process, envelope(f"echo $$ > {pid_file}\nsleep 20\n") + frame(1))
    wait_until(pid_file.exists)
    write(process, sender._frame(NONCE, 2, "bad"))
    assert finish(process)[0] == 75


def test_stdout_flood_is_bounded_and_does_not_starve_heartbeats(children, tmp_path, fake_ssh):
    # >1 second of bounded forwarding: the remote watchdog must remain fresh.
    script = f"{shlex.quote(sys.executable)} -c 'print((\"x\"*1000+\"\\n\")*120,end=\"\")'\nsleep 1.1\n"
    process = launch_sender(children, tmp_path, fake_ssh, script, seconds=8,
                            observer_code="def observer(line): return None")
    # Drain stdout while the process runs, as a real CI log consumer would.
    output, errors = process.communicate(timeout=10)
    assert process.returncode == 0, errors.decode()
    assert len(output) == 120120


def test_unterminated_oversized_stdout_fails_closed(children, tmp_path, fake_ssh):
    script = f"{shlex.quote(sys.executable)} -c 'print(\"x\"*70000,end=\"\")'\nsleep 1\n"
    process = launch_sender(children, tmp_path, fake_ssh, script,
                            observer_code="def observer(line): return None")
    output, errors = process.communicate(timeout=6)
    assert process.returncode == 75


def test_stdout_observer_drains_normal_tail_after_child_exit(children, tmp_path, fake_ssh):
    process = launch_sender(children, tmp_path, fake_ssh, "printf 'tail-without-newline'; exit 3\n",
                            observer_code="def observer(line): raise AssertionError('not a complete frame')")
    assert finish(process)[:2] == (3, "tail-without-newline")


def test_child_clears_other_interpreter_startup_hooks(children):
    hooks = {key: "synthetic-invalid-startup" for key in
             ("CDPATH", "PYTHONPATH", "PYTHONSTARTUP", "NODE_OPTIONS", "PERL5OPT", "RUBYOPT")}
    # Set hooks only after Python started, so the test interpreter itself does
    # not consume them before reaching the actual watchdog launch under test.
    code = ("import os\nfrom scripts.dev_remote_watchdog import run_watchdog,Timings\n"
            + f"os.environ.update({hooks!r})\n"
            + "os.environ['LD_PRELOAD']='synthetic-invalid-startup'\n"
            + "raise SystemExit(run_watchdog(timings=Timings(pause=0.5,silence_kill=2,poll=0.005,terminate_grace=0.05)))")
    process = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    children.append(process)
    script = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        f"import os; assert not any(k in os.environ for k in {list(hooks) + ['LD_PRELOAD']!r}); print('clean-startup')"
    ) + "\n"
    write(process, envelope(script) + frame(1))
    assert finish(process) == (0, "clean-startup\n", "")


@pytest.fixture
def candidate_documents(tmp_path, monkeypatch):
    # The integrated checkout contains Root's receiver and baseline module.
    # During isolated parallel development only, the test command may supply
    # the unchanged sibling module directory; no production path fallback.
    dependency_root = os.environ.get("PANTHEON_TRANSPORT_RECEIPT_TEST_MODULE_ROOT")
    if dependency_root:
        monkeypatch.syspath_prepend(dependency_root)
        monkeypatch.setenv("PYTHONPATH", dependency_root + os.pathsep + str(ROOT))
    try:
        receiver = importlib.import_module("scripts.dev_candidate_receipt")
    except ModuleNotFoundError:
        receiver = importlib.import_module("dev_candidate_receipt")

    def encoded(value):
        return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()

    identity = {"candidate_id": "b" * 64, "run_id": "12345", "attempt": "2",
                "controller_sha": "c" * 40, "candidate_backend_sha": "a" * 40,
                "candidate_frontend_sha": "d" * 40, "previous_backend_sha": "e" * 40,
                "previous_frontend_sha": "f" * 40}
    guard = "11111111-1111-4111-8111-111111111111"
    context = {"schema_version": "pantheon.dev-candidate-receipt-context.v1", "identity": identity,
               "baseline_manifest_sha256": "1" * 64, "guard_lease_id": guard}
    services = {service: {"image_id": "sha256:" + str(index) * 64, "oci_revision": "a" * 40,
                          "git_sha": None if service == "loop-run-projector-scheduler" else "a" * 40,
                          "compose_image": "pantheon-" + service}
                for index, service in enumerate(("operator-bff", "agora-interaction-worker",
                                                 "loop-run-projector-scheduler"), start=3)}
    override = {"services": {service: {"image": row["image_id"], "pull_policy": "never"}
                             for service, row in services.items()}}
    override_hash = hashlib.sha256(encoded(override)).hexdigest()
    record = {"schema_version": "pantheon.dev-candidate-image-admission.v1", "environment": "dev",
              "project_id": "pantheon-dev-20260902", "vm": "pantheon-dev-deploy", "identity": identity,
              "seal_lease_id": guard, "sealed_at": "2026-09-09T03:00:00Z",
              "baseline_manifest_sha256": "1" * 64, "candidate_compose_sha256": "2" * 64,
              "image_override_sha256": override_hash, "services": services}
    folder = receiver.ARTIFACT_ROOT / ("baseline-12345-2-" + "b" * 64)
    result = {"candidate_image_manifest_path": str(folder / "candidate-images.json"),
              "candidate_image_manifest_sha256": hashlib.sha256(encoded(record)).hexdigest(),
              "candidate_image_manifest": record,
              "candidate_image_override_path": str(folder / "candidate-images.override.json"),
              "candidate_image_override_sha256": override_hash}
    directory = tmp_path / "private-candidate"
    directory.mkdir(mode=0o700)
    context_path = directory / "context.json"
    context_path.write_bytes(encoded(context))
    context_path.chmod(0o600)
    output = directory / "candidate-result.json"
    return {"context": context, "context_path": context_path, "output": output,
            "guard": guard, "result": result, "line": receiver.PREFIX + encoded(result),
            "args": ("--candidate-receipt-context", context_path, "--candidate-receipt-output", output)}


def typed_ack_script(documents, after_ack=""):
    digest = documents["result"]["candidate_image_manifest_sha256"]
    script = ack_script(digest, after_ack=after_ack)
    return "printf '%s\\n' " + shlex.quote(documents["line"].decode().rstrip("\n")) + "\n" + script.split("\n", 1)[1]


def test_candidate_cli_requires_receipt_even_when_remote_script_exits_zero(children, tmp_path, fake_ssh, candidate_documents):
    doc = candidate_documents
    process = launch_sender(children, tmp_path, fake_ssh, "exit 0\n", candidate_args=doc["args"],
                            extra_env={"PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": doc["guard"]})
    code, output, errors = finish(process)
    assert code == 75
    assert "required candidate receipt was not received" in errors
    assert not doc["output"].exists()


def test_candidate_cli_real_receiver_persists_and_acknowledges_before_action(children, tmp_path, fake_ssh, candidate_documents):
    doc = candidate_documents
    action = tmp_path / "after-ack-action"
    after_ack = f"assert os.path.exists({str(doc['output'])!r}); open({str(action)!r},'w').write('after-durable-ack')"
    process = launch_sender(children, tmp_path, fake_ssh, typed_ack_script(doc, after_ack),
                            candidate_args=doc["args"],
                            extra_env={"PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": doc["guard"]})
    code, output, errors = finish(process, timeout=6)
    assert (code, errors) == (0, "")
    assert json.loads(doc["output"].read_bytes()) == doc["result"]
    assert action.read_text() == "after-durable-ack"


@pytest.mark.parametrize("bad_field", ["schema", "identity", "guard", "foreign-guard"])
def test_invalid_candidate_context_fails_before_ssh(children, tmp_path, fake_ssh, candidate_documents, bad_field):
    doc = candidate_documents
    context = doc["context"]
    if bad_field == "schema":
        context["schema_version"] = "invalid"
    elif bad_field == "identity":
        context["identity"]["candidate_backend_sha"] = "not-a-commit"
    else:
        context["guard_lease_id"] = ("not-a-uuid" if bad_field == "guard"
                                     else "22222222-2222-4222-8222-222222222222")
    doc["context_path"].write_text(json.dumps(context))
    identity = tmp_path / "must-not-start-ssh"
    process = launch_sender(children, tmp_path, fake_ssh, "exit 0\n", candidate_args=doc["args"], extra_env={
        "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": doc["guard"], "TEST_SSH_IDENTITY": str(identity)})
    assert finish(process)[0] == 75
    assert not identity.exists()
    assert not doc["output"].exists()


@pytest.mark.parametrize("which", ["--candidate-receipt-context", "--candidate-receipt-output"])
def test_candidate_cli_arguments_are_paired_before_ssh(children, tmp_path, fake_ssh, which):
    identity = tmp_path / "must-not-start-ssh"
    process = launch_sender(children, tmp_path, fake_ssh, "exit 0\n", candidate_args=(which, tmp_path / "missing"),
                            extra_env={"TEST_SSH_IDENTITY": str(identity)})
    assert finish(process)[0] == 75
    assert not identity.exists()


@pytest.mark.parametrize("failure", ["create", "write", "file-fsync", "directory-fsync"])
def test_candidate_cli_persistence_failure_never_acknowledges_or_succeeds(children, tmp_path, fake_ssh, candidate_documents, failure):
    doc = candidate_documents
    action = tmp_path / "must-not-act"
    if failure == "create":
        setup = f"""import os
original_open=os.open
def controlled_open(path,flags,*args,**kwargs):
 if str(path)=={str(doc['output'])!r} and flags & os.O_CREAT:
  raise PermissionError('PRIVATE_WRITE_FAILURE_CANARY')
 return original_open(path,flags,*args,**kwargs)
os.open=controlled_open
"""
    elif failure == "write":
        # Substitute a real receiver output stream only for this exact O_EXCL
        # target. It fails stream.write before either fsync or received=True.
        setup = f"""import os
original_open,original_fdopen=os.open,os.fdopen
output_fd=None
def controlled_open(path,flags,*args,**kwargs):
 global output_fd
 fd=original_open(path,flags,*args,**kwargs)
 if str(path)=={str(doc['output'])!r} and flags & os.O_CREAT: output_fd=fd
 return fd
class FailingStream:
 def __init__(self,stream): self.stream=stream
 def __enter__(self): return self
 def __exit__(self,*args): self.stream.close()
 def write(self,*args): raise OSError('PRIVATE_WRITE_FAILURE_CANARY')
def controlled_fdopen(fd,*args,**kwargs):
 stream=original_fdopen(fd,*args,**kwargs)
 return FailingStream(stream) if fd==output_fd else stream
os.open,os.fdopen=controlled_open,controlled_fdopen
"""
    else:
        nth = 1 if failure == "file-fsync" else 2
        setup = f"""import os
original_fsync=os.fsync
count=0
def controlled_fsync(fd):
 global count
 count+=1
 if count=={nth}: raise OSError('PRIVATE_WRITE_FAILURE_CANARY')
 return original_fsync(fd)
os.fsync=controlled_fsync
"""
    process = launch_sender(children, tmp_path, fake_ssh,
                            typed_ack_script(doc, f"open({str(action)!r},'w').write('UNSAFE')"),
                            candidate_args=doc["args"], cli_setup=setup,
                            extra_env={"PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": doc["guard"]})
    code, output, errors = finish(process, timeout=6)
    assert code == 75
    assert not action.exists()
    assert "after-durable-ack" not in output
    assert "PRIVATE_WRITE_FAILURE_CANARY" not in output + errors
