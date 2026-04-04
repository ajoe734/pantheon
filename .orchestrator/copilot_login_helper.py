#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import os
import pty
import re
import select
import signal
import subprocess
import sys
import termios
import time
from pathlib import Path


ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[@-_]")
DEVICE_CODE_PATTERN = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run `copilot login` in a PTY and auto-confirm plaintext token storage on WSL."
    )
    parser.add_argument(
        "--config-dir",
        default=str(Path.home() / ".copilot"),
        help="Copilot config directory. Defaults to ~/.copilot",
    )
    parser.add_argument(
        "--host",
        default="https://github.com",
        help="GitHub host to use for device login.",
    )
    return parser.parse_args()


def strip_ansi(text: str) -> str:
    return ANSI_PATTERN.sub("", text)


def main() -> int:
    args = parse_args()

    cmd = ["copilot", "login", "--config-dir", args.config_dir, "--host", args.host]
    master_fd, slave_fd = pty.openpty()
    try:
        # Give the child a sane terminal size so prompts render consistently.
        winsize = termios.tcgetwinsize(sys.stdout.fileno()) if sys.stdout.isatty() else (24, 120)
        termios.tcsetwinsize(slave_fd, winsize)
    except OSError:
        pass

    process = subprocess.Popen(
        cmd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        text=False,
        close_fds=True,
    )
    os.close(slave_fd)

    device_code: str | None = None
    sent_plaintext_yes = False
    buffer = ""

    try:
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.25)
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                sys.stdout.write(text)
                sys.stdout.flush()

                clean = strip_ansi(text)
                buffer = (buffer + clean)[-16000:]

                if device_code is None:
                    match = DEVICE_CODE_PATTERN.search(buffer)
                    if match:
                        device_code = match.group(1)
                        print(
                            f"\nDEVICE_CODE={device_code}\n"
                            f"Open https://github.com/login/device and enter that code.",
                            flush=True,
                        )

                if (
                    not sent_plaintext_yes
                    and (
                        "Store token in plaintext config file?" in buffer
                        or "System keychain unavailable." in buffer
                    )
                ):
                    for reply in (b"y\n", b"y\r", b"y\n"):
                        try:
                            os.write(master_fd, reply)
                        except OSError as exc:
                            if exc.errno != errno.EIO:
                                raise
                            break
                        time.sleep(0.1)
                    sent_plaintext_yes = True

            if process.poll() is not None:
                # Drain any remaining output after the child exits.
                while True:
                    ready, _, _ = select.select([master_fd], [], [], 0)
                    if not ready:
                        break
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    sys.stdout.write(chunk.decode("utf-8", errors="replace"))
                    sys.stdout.flush()
                break
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=5)
    finally:
        os.close(master_fd)

    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
