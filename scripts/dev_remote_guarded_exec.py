#!/usr/bin/env python3
"""Keep SSH and a one-second cancellation sender inside the existing guard pgid.

This is not lease acquisition or validation. Run only under the pinned
run_with_dev_environment_lease.sh authority. No heartbeat catch-up, reconnect,
secret argv, environment dump, or arbitrary remote-command CLI is provided.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import select
import shlex
import signal
import stat
import subprocess
import sys
import time
import uuid
from typing import Callable

# Direct workflow execution uses PYTHONSAFEPATH=1; import only this
# accepted script's siblings, not the caller's working directory.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import dev_release_artifacts as artifacts
else:
    import dev_release_artifacts as artifacts


FAILURE = 75
MAX_SCRIPT_BYTES = 512 * 1024
MAX_SOURCE_BYTES = 128 * 1024
RECEIPT_DIGEST = re.compile(r"(?!0{64}$)[0-9a-f]{64}")
# Read exactly the first line without a buffered reader stealing later frames.
# The only shell argument is this fixed bootstrap, never script or credentials.
BOOTSTRAP = """import base64,os,sys
try:
 line=bytearray()
 while True:
  b=os.read(0,1)
  if not b or len(line)>180000: raise ValueError()
  if b==b'\\n': break
  line.extend(b)
 source=base64.b64decode(line,validate=True)
 if not source or len(source)>131072: raise ValueError()
except Exception:
 print('[artifact-bootstrap] invalid transport',file=sys.stderr)
 sys.exit(75)
exec(compile(source,'<artifact-watchdog>','exec'),{'__name__':'__main__'})
"""
REMOTE_COMMAND = "python3 -I -S -c " + shlex.quote(BOOTSTRAP)


class TransportError(artifacts.ArtifactError):
    pass


def _read_regular(path: Path, *, maximum: int, private: bool = False) -> bytes:
    if not path.is_absolute() or path.resolve() != path:
        raise TransportError("non-canonical path")
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise TransportError("symlink path")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > maximum
                or info.st_mode & 0o022 or info.st_uid != os.geteuid()
                or (private and info.st_mode & 0o077)):
            raise TransportError("unsafe input file")
        raw = stream.read(maximum + 1)
    if not raw or len(raw) > maximum:
        raise TransportError("invalid input size")
    return raw


def _frame(nonce: str, sequence: int, receipt: str | None = None) -> bytes:
    value = {"type": "heartbeat", "nonce": nonce, "sequence": sequence}
    if receipt is not None:
        value["candidate_receipt_sha256"] = receipt
    return (json.dumps(value,
                       separators=(",", ":")) + "\n").encode()


class OutputObserver:
    """Bounded raw stdout observer; each complete line is a bytes argument.

    The callback must durably save and validate a receipt before returning its
    digest. Returning None is not an acknowledgement. No receiver/context policy
    is defined here. Backpressure pauses reading, never the heartbeat schedule.
    """

    def __init__(self, fd: int, observer: Callable[[bytes], str | None]):
        self.fd, self.observer = fd, observer
        self.buffer = bytearray()
        self.forward = bytearray()
        self.eof = False
        self.receipt = None
        self.pending_receipt = None
        os.set_blocking(fd, False)

    def _line(self, line: bytes) -> None:
        try:
            receipt = self.observer(line.rstrip(b"\n"))
        except Exception:
            raise TransportError("stdout observer rejected input") from None
        if receipt is not None:
            if (not isinstance(receipt, str) or not RECEIPT_DIGEST.fullmatch(receipt)
                    or self.receipt is not None):
                raise TransportError("invalid or duplicate receipt acknowledgement")
            self.receipt = self.pending_receipt = receipt
        self.forward.extend(line)

    def pump(self) -> None:
        if self.forward:
            _, writable, _ = select.select([], [1], [], 0)
            if writable:
                # <= PIPE_BUF after writable-select avoids blocking on the
                # ordinary CLI stdout pipe, even if the consumer is slow.
                written = os.write(1, self.forward[:4096])
                del self.forward[:written]
        if len(self.forward) >= 65536:
            return
        if not self.eof and b"\n" not in self.buffer:
            try:
                data = os.read(self.fd, 16384)
                if data:
                    self.buffer.extend(data)
                else:
                    self.eof = True
            except BlockingIOError:
                pass
        count = 0
        while b"\n" in self.buffer and count < 64 and len(self.forward) < 65536:
            index = self.buffer.index(b"\n")
            if index > 65536:
                raise TransportError("stdout line exceeds bound")
            self._line(bytes(self.buffer[:index + 1]))
            del self.buffer[:index + 1]
            count += 1
        if len(self.buffer) > 65536:
            raise TransportError("stdout line exceeds bound")
        if self.eof and self.buffer and b"\n" not in self.buffer:
            # An unterminated tail may be forwarded, but is not a receipt frame.
            self.forward.extend(self.buffer)
            self.buffer.clear()

    @property
    def done(self) -> bool:
        return self.eof and not self.buffer and not self.forward


def execute(ssh_helper: Path, script_file: Path, deadline_seconds: int, *,
            heartbeat_seconds: float = 1.0, poll_seconds: float = 0.05,
            observer: Callable[[bytes], str | None] | None = None) -> int:
    """Unit-level entry point. Production CLI has no timing override.

    Optional observer(bytes_line) -> digest/None owns receipt validation and
    durability. Its first digest is acknowledged once in the next heartbeat.
    A receipt-requiring wrapper MUST additionally check observer.received on
    success; a generic observer returning None does not assert a deployment.
    """
    interrupted = 0

    def stop(signum, frame):
        nonlocal interrupted
        interrupted = signum

    original = {sig: signal.signal(sig, stop)
                for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)}
    child = None
    try:
        if os.environ.get("TARGET_ENV") != "dev":
            raise TransportError("dev guard required")
        lease_id = os.environ.get("PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID", "")
        if not lease_id or str(uuid.UUID(lease_id)) != lease_id.lower() or uuid.UUID(lease_id).int == 0:
            raise TransportError("guard lease identity required")
        if type(deadline_seconds) is not int or not 1 <= deadline_seconds <= 7200:
            raise TransportError("invalid deadline")
        if not 0 < poll_seconds < heartbeat_seconds <= 1:
            raise TransportError("invalid sender timings")
        if ssh_helper.name != "dev_vm_ssh.sh" or not os.access(ssh_helper, os.X_OK):
            raise TransportError("trusted SSH helper required")
        _read_regular(ssh_helper, maximum=128 * 1024)
        script = _read_regular(script_file, maximum=MAX_SCRIPT_BYTES, private=True)
        source = _read_regular(Path(__file__).resolve().with_name("dev_remote_watchdog.py"),
                               maximum=MAX_SOURCE_BYTES)
        nonce = uuid.uuid4().hex
        envelope = (json.dumps({"script": base64.b64encode(script).decode(), "nonce": nonce,
                                "deadline_seconds": deadline_seconds}, separators=(",", ":")) + "\n").encode()
        initial = base64.b64encode(source) + b"\n" + envelope + _frame(nonce, 1)
        deadline = time.monotonic() + deadline_seconds
        # Deliberately NO setsid/start_new_session: outer guard owns this pgid.
        child = subprocess.Popen([str(ssh_helper), "exec", REMOTE_COMMAND], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE if observer is not None else None, bufsize=0)
        output = OutputObserver(child.stdout.fileno(), observer) if observer is not None else None
        fd = child.stdin.fileno()
        os.set_blocking(fd, False)
        offset = 0
        while offset < len(initial):
            if interrupted or time.monotonic() >= deadline:
                raise TransportError("startup interrupted")
            status = child.poll()
            if status is not None:
                if status != 0:
                    raise subprocess.CalledProcessError(status, [])
                raise TransportError("remote exited before receiving startup context")
            _, writable, _ = select.select([], [fd], [], poll_seconds)
            if writable:
                try:
                    offset += os.write(fd, initial[offset:offset + 4096])
                except BlockingIOError:
                    pass
        sequence = 1
        last_sent = time.monotonic()
        while True:
            if interrupted or time.monotonic() >= deadline:
                raise TransportError("execution interrupted")
            if output is not None:
                output.pump()
            result = child.poll()
            if result is not None:
                if output is None or output.done:
                    if output is not None and output.pending_receipt is not None:
                        raise TransportError("receipt arrived after remote exit")
                    if result < 0:
                        raise subprocess.CalledProcessError(result, [])
                    return result if 0 <= result <= 255 else FAILURE
                time.sleep(poll_seconds)
                continue
            now = time.monotonic()
            if now - last_sent >= heartbeat_seconds:
                sequence += 1
                receipt = output.pending_receipt if output is not None else None
                frame = _frame(nonce, sequence, receipt)
                # <= PIPE_BUF: no backlog or deferred replay of partial frames.
                if os.write(fd, frame) != len(frame):
                    raise TransportError("partial heartbeat")
                if output is not None:
                    output.pending_receipt = None
                last_sent = now  # no catch-up loop after a guard freeze
            time.sleep(poll_seconds)
    except Exception as error:
        record = artifacts.failure_record(error, "transport")
        if interrupted in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            record["exit_code"] = 128 + interrupted
        print(json.dumps(record, sort_keys=True), file=sys.stderr)
        return record["exit_code"]
    finally:
        if child is not None:
            if child.stdin is not None:
                child.stdin.close()  # EOF is permanent cancellation, never resume.
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.terminate()  # helper execs SSH; never kill our shared pgid
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for sig, handler in original.items():
            signal.signal(sig, handler)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-helper", type=Path, required=True)
    parser.add_argument("--script-file", type=Path, required=True)
    parser.add_argument("--deadline-seconds", type=int, required=True)
    parser.add_argument("--candidate-receipt-context", type=Path)
    parser.add_argument("--candidate-receipt-output", type=Path)
    args = parser.parse_args(argv)
    if (args.candidate_receipt_context is None) != (args.candidate_receipt_output is None):
        print("[artifact-transport] candidate receipt arguments must be paired", file=sys.stderr)
        return FAILURE
    observer = None
    if args.candidate_receipt_context is not None:
        try:
            # Import only in candidate mode. Baseline capture/restore does not
            # gain a dependency on, or accept, candidate receipt assertions.
            try:
                from .dev_candidate_receipt import CandidateReceiptObserver
            except ImportError:
                if not __package__:
                    sys.path.insert(0, str(Path(__file__).resolve().parent))
                from dev_candidate_receipt import CandidateReceiptObserver
            observer = CandidateReceiptObserver(context_path=args.candidate_receipt_context,
                                                output_path=args.candidate_receipt_output)
            if observer.context["guard_lease_id"] != os.environ.get(
                "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID"
            ):
                raise TransportError("candidate receipt guard context differs")
        except (ImportError, OSError, ValueError, RuntimeError, TypeError, KeyError, TransportError):
            print("[artifact-transport] candidate receipt context rejected", file=sys.stderr)
            return FAILURE
    result = execute(args.ssh_helper, args.script_file, args.deadline_seconds, observer=observer)
    if result == 0 and observer is not None and observer.received is not True:
        print("[artifact-transport] required candidate receipt was not received", file=sys.stderr)
        return FAILURE
    return result


if __name__ == "__main__":
    raise SystemExit(main())
