#!/usr/bin/env python3
"""Cancellation transport, not a lease authority or a rollback proof.

The pinned runner lease guard owns admission. Its process-group freeze stops
heartbeats; this remote watchdog pauses the child after three seconds and
terminates it after 120 seconds. Already submitted daemon requests cannot be
recalled. The artifact driver must also enforce its ten-second pipe-pulse TTL.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import tempfile
import time


FAILURE = 75
MAX_SCRIPT_BYTES = 512 * 1024
MAX_ENVELOPE_BYTES = 710 * 1024
MAX_FRAME_BYTES = 384
NONCE = re.compile(r"[0-9a-f]{32}")
RECEIPT_DIGEST = re.compile(r"(?!0{64}$)[0-9a-f]{64}")


class ProtocolError(Exception):
    """An intentionally non-sensitive protocol failure."""


@dataclass(frozen=True)
class Timings:
    # Shorter timings are injectable only into the isolated unit-level API.
    pause: float = 3.0
    silence_kill: float = 120.0
    poll: float = 0.05
    terminate_grace: float = 1.0


class Lines:
    """Bounded raw-fd reader; never mix buffered readline with select."""

    def __init__(self, fd: int):
        self.fd = fd
        self.buffer = bytearray()
        self.eof = False
        os.set_blocking(fd, False)

    def read(self, limit: int, timeout: float = 0.0) -> bytes | None:
        if b"\n" not in self.buffer and not self.eof:
            ready, _, _ = select.select([self.fd], [], [], max(0.0, timeout))
            if ready:
                chunk = os.read(self.fd, min(8192, limit + 1))
                if not chunk:
                    self.eof = True
                else:
                    self.buffer.extend(chunk)
        newline = self.buffer.find(b"\n")
        if newline >= 0:
            if newline > limit:
                raise ProtocolError("oversized frame")
            result = bytes(self.buffer[:newline])
            del self.buffer[:newline + 1]
            return result
        if len(self.buffer) > limit:
            raise ProtocolError("oversized frame")
        if self.eof:
            raise ProtocolError("channel closed")
        return None


def _object(raw: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProtocolError("duplicate field")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise ProtocolError("invalid JSON") from None
    if not isinstance(value, dict):
        raise ProtocolError("invalid object")
    return value


def _envelope(raw: bytes) -> tuple[bytes, str, int]:
    value = _object(raw)
    if set(value) != {"script", "nonce", "deadline_seconds"}:
        raise ProtocolError("invalid envelope fields")
    nonce, deadline = value["nonce"], value["deadline_seconds"]
    if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
        raise ProtocolError("invalid nonce")
    if type(deadline) is not int or not 1 <= deadline <= 7200:
        raise ProtocolError("invalid deadline")
    if not isinstance(value["script"], str):
        raise ProtocolError("invalid script")
    try:
        script = base64.b64decode(value["script"], validate=True)
    except (ValueError, UnicodeError):
        raise ProtocolError("invalid script encoding") from None
    if not script or len(script) > MAX_SCRIPT_BYTES or b"\0" in script:
        raise ProtocolError("invalid script size or encoding")
    return script, nonce, deadline


def _heartbeat(raw: bytes, nonce: str, previous: int) -> tuple[int, str | None]:
    value = _object(raw)
    if value.get("type") == "cancel":
        raise ProtocolError("cancelled")
    required = {"type", "nonce", "sequence"}
    if set(value) not in (required, required | {"candidate_receipt_sha256"}):
        raise ProtocolError("invalid heartbeat fields")
    sequence = value["sequence"]
    if (value["type"] != "heartbeat" or value["nonce"] != nonce
            or type(sequence) is not int or not previous < sequence <= 2**53 - 1):
        raise ProtocolError("invalid heartbeat identity or sequence")
    receipt = value.get("candidate_receipt_sha256")
    if "candidate_receipt_sha256" in value and (
        not isinstance(receipt, str) or not RECEIPT_DIGEST.fullmatch(receipt)
    ):
        raise ProtocolError("invalid receipt acknowledgement")
    return sequence, receipt


def _signal_group(group: int, sig: int) -> None:
    try:
        os.killpg(group, sig)
    except ProcessLookupError:
        pass


def _group_has_live_members(group: int) -> bool:
    # Linux dev VM: killpg(..., 0) alone counts unreaped zombie descendants.
    # Inspect only process state and pgid, never command lines or environments.
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if int(fields[2]) == group and fields[0] not in {"Z", "X", "x"}:
            return True
    return False


def _terminate(child: subprocess.Popen, grace: float) -> bool:
    # The child alone starts a new session; never signal the watchdog group.
    for sig in (signal.SIGSTOP, signal.SIGTERM, signal.SIGCONT):
        _signal_group(child.pid, sig)
    until = time.monotonic() + grace
    while time.monotonic() < until:
        child.poll()
        if not _group_has_live_members(child.pid):
            return True
        time.sleep(min(0.02, max(0.0, until - time.monotonic())))
    _signal_group(child.pid, signal.SIGKILL)
    try:
        child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    until = time.monotonic() + 1
    while time.monotonic() < until:
        if not _group_has_live_members(child.pid):
            return True
        time.sleep(0.02)
    return False


def _pulse(fd: int) -> None:
    try:
        if os.write(fd, b".") != 1:
            raise ProtocolError("incomplete guard pulse")
    except (BlockingIOError, BrokenPipeError):
        raise ProtocolError("guard pulse channel unavailable") from None


def _acknowledge(fd: int, digest: str) -> None:
    try:
        data = (digest + "\n").encode("ascii")
        if os.write(fd, data) != len(data):
            raise ProtocolError("incomplete receipt acknowledgement")
    except (BlockingIOError, BrokenPipeError):
        raise ProtocolError("receipt acknowledgement channel unavailable") from None


def run_watchdog(input_fd: int = 0, *, timings: Timings = Timings()) -> int:
    """Run one private script; callers cannot configure production timings."""
    stopped_by_signal = False

    def stop(signum, frame):
        nonlocal stopped_by_signal
        stopped_by_signal = True

    original = {sig: signal.signal(sig, stop)
                for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)}
    child = None
    read_fd = write_fd = None
    ack_read_fd = ack_write_fd = None
    temporary = None
    try:
        if not (0 < timings.poll < timings.pause < timings.silence_kill
                and 0 < timings.terminate_grace <= 5):
            raise ProtocolError("invalid watchdog timings")
        lines = Lines(input_fd)
        startup_deadline = time.monotonic() + timings.pause
        envelope = None
        while envelope is None:
            if stopped_by_signal or time.monotonic() >= startup_deadline:
                raise ProtocolError("startup interrupted")
            envelope = lines.read(MAX_ENVELOPE_BYTES, timings.poll)
        script, nonce, seconds = _envelope(envelope)
        deadline = time.monotonic() + seconds
        sequence = 0
        last_heartbeat = None
        # No assumed initial grant: require a real frame before child launch.
        while last_heartbeat is None:
            if stopped_by_signal or time.monotonic() >= min(startup_deadline, deadline):
                raise ProtocolError("initial heartbeat missing")
            frame = lines.read(MAX_FRAME_BYTES, timings.poll)
            if frame is not None:
                sequence, receipt = _heartbeat(frame, nonce, sequence)
                if receipt is not None:
                    raise ProtocolError("receipt acknowledgement before launch")
                last_heartbeat = time.monotonic()
        # Drain already queued frames, including EOF, before the first action.
        queued = 0
        while True:
            frame = lines.read(MAX_FRAME_BYTES)
            if frame is None:
                break
            sequence, receipt = _heartbeat(frame, nonce, sequence)
            if receipt is not None:
                raise ProtocolError("receipt acknowledgement before launch")
            last_heartbeat = time.monotonic()
            queued += 1
            if queued > 32:
                raise ProtocolError("heartbeat flood")
        temporary = tempfile.TemporaryDirectory(prefix="pantheon-artifact-remote-")
        directory = Path(temporary.name)
        os.chmod(directory, 0o700)
        script_path = directory / "run.sh"
        descriptor = os.open(script_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(script)
        read_fd, write_fd = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        ack_read_fd, ack_write_fd = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        # os.pipe2 can allocate 0/1/2 when a launcher closed its stdio.
        if min(read_fd, write_fd, ack_read_fd, ack_write_fd) < 3:
            raise ProtocolError("guard descriptors overlap stdio")
        _pulse(write_fd)
        child_env = dict(os.environ)
        # --noprofile/--norc alone do not disable noninteractive BASH_ENV.
        for key in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "CDPATH",
                    "BASH_XTRACEFD", "PROMPT_COMMAND", "NODE_OPTIONS", "NODE_PATH",
                    "PERL5OPT", "PERL5LIB", "RUBYOPT", "RUBYLIB"):
            child_env.pop(key, None)
        for key in tuple(child_env):
            if key.startswith(("BASH_FUNC_", "PYTHON", "LD_")):
                child_env.pop(key)
        child_env["PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD"] = str(read_fd)
        child_env["PANTHEON_DEV_ARTIFACT_RECEIPT_ACK_FD"] = str(ack_read_fd)
        if stopped_by_signal or time.monotonic() >= deadline:
            raise ProtocolError("launch interrupted")
        child = subprocess.Popen(
            ["bash", "--noprofile", "--norc", str(script_path)],
            stdin=subprocess.DEVNULL, pass_fds=(read_fd, ack_read_fd),
            start_new_session=True, env=child_env,
        )
        os.close(read_fd)
        read_fd = None
        os.close(ack_read_fd)
        ack_read_fd = None
        paused = False
        acknowledged = None
        while True:
            if stopped_by_signal:
                raise ProtocolError("watchdog interrupted")
            now = time.monotonic()
            if now >= deadline or now - last_heartbeat >= timings.silence_kill:
                raise ProtocolError("watchdog deadline")
            if now - last_heartbeat >= timings.pause and not paused:
                _signal_group(child.pid, signal.SIGSTOP)
                paused = True
            frame = lines.read(MAX_FRAME_BYTES, timings.poll)
            # Validate the entire available batch before resuming any action.
            fresh = False
            pending_ack = None
            count = 0
            while frame is not None:
                sequence, receipt = _heartbeat(frame, nonce, sequence)
                if receipt is not None:
                    if acknowledged is not None or pending_ack is not None:
                        raise ProtocolError("duplicate receipt acknowledgement")
                    pending_ack = receipt
                fresh = True
                count += 1
                if count > 32:
                    raise ProtocolError("heartbeat flood")
                frame = lines.read(MAX_FRAME_BYTES)
            result = child.poll()
            if result is not None:
                if (stopped_by_signal or paused or time.monotonic() >= deadline
                        or time.monotonic() - last_heartbeat >= timings.pause):
                    raise ProtocolError("child exited without fresh channel")
                # A completed child may have closed its read end. Do not turn
                # that expected exit into a failed pulse write; input identity
                # and EOF were checked above, and finally cleans descendants.
                return result if result >= 0 else FAILURE
            if fresh:
                now = time.monotonic()
                if (stopped_by_signal or now >= deadline
                        or now - last_heartbeat >= timings.silence_kill):
                    raise ProtocolError("resume interrupted")
                _pulse(write_fd)
                if pending_ack is not None:
                    _acknowledge(ack_write_fd, pending_ack)
                    acknowledged = pending_ack
                last_heartbeat = time.monotonic()
                if paused:
                    _signal_group(child.pid, signal.SIGCONT)
                    paused = False
    except (ProtocolError, OSError, ValueError, subprocess.SubprocessError):
        # Do not include raw frames, script contents, environment or exceptions.
        print("[artifact-watchdog] guarded execution failed", file=sys.stderr)
        return FAILURE
    finally:
        # Close cancellation authority before killing any surviving descendants.
        for fd in (write_fd, ack_write_fd, read_fd, ack_read_fd):
            if fd is not None:
                os.close(fd)
        cleanup_ok = True
        if child is not None:
            try:
                cleanup_ok = _terminate(child, timings.terminate_grace)
            except (OSError, ValueError, IndexError):
                cleanup_ok = False
        if temporary is not None:
            temporary.cleanup()
        for sig, handler in original.items():
            signal.signal(sig, handler)
        if not cleanup_ok:
            print("[artifact-watchdog] child group cleanup unverified", file=sys.stderr)
            return FAILURE


if __name__ == "__main__":
    raise SystemExit(run_watchdog())
