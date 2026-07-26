"""Container-local supervisor for consultation API and durable executor."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Sequence


def _truthy(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Child:
    name: str
    command: Sequence[str]
    process: subprocess.Popen[bytes] | None = None
    restart_count: int = 0

    def start(self, *, env: dict[str, str]) -> None:
        self.process = subprocess.Popen(  # noqa: S603 - fixed internal commands
            list(self.command),
            env=env,
        )

    def terminate(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()

    def kill(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.kill()


def _shutdown(children: Sequence[Child], *, timeout_seconds: float = 10.0) -> None:
    for child in children:
        child.terminate()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if all(
            child.process is None or child.process.poll() is not None
            for child in children
        ):
            return
        time.sleep(0.1)
    for child in children:
        child.kill()


def main() -> int:
    port = str(os.getenv("PORT") or "8096")
    env = dict(os.environ)
    env.setdefault("CONSULTATION_API_URL", f"http://127.0.0.1:{port}")
    api = Child(
        name="api",
        command=(
            sys.executable,
            "-m",
            "uvicorn",
            "services.consultation.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
        ),
    )
    children = [api]
    if _truthy(
        os.getenv("CONSULTATION_WORKFLOW_EXECUTOR_ENABLED"),
        default=True,
    ):
        children.append(
            Child(
                name="workflow-executor",
                command=(
                    sys.executable,
                    "-m",
                    "services.consultation.workflow_executor",
                ),
            )
        )

    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    for child in children:
        child.start(env=env)

    try:
        while not stopping:
            api_code = api.process.poll() if api.process is not None else 1
            if api_code is not None:
                return int(api_code or 1)
            for child in children[1:]:
                code = child.process.poll() if child.process is not None else 1
                if code is None:
                    continue
                child.restart_count += 1
                if child.restart_count > 5:
                    return int(code or 1)
                time.sleep(min(2**child.restart_count, 10))
                child.start(env=env)
            time.sleep(0.5)
        return 0
    finally:
        _shutdown(children)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
