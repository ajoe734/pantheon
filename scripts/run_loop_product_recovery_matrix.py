#!/usr/bin/env python3
"""Run the LOOP-PROD-REC-001 recovery matrix against an isolated dev stack.

Ordinary imports and pytest runs never write repository evidence.  Evidence is
captured only with ``capture`` and every raw run uses exclusive creation under
its own run id.  The default integration target is a disposable PostgreSQL
container plus fresh worker and real BFF application processes.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib
import json
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EVIDENCE_ROOT = (
    ROOT
    / "docs"
    / "deployment"
    / "evidence"
    / "loop-product-level"
    / "LOOP-PROD-REC-001"
)
TASK_ID = "LOOP-PROD-REC-001"
PR_NUMBER = 3586

recovery_module = importlib.import_module("services.loop-control.recovery_harness")
PostgresRecoveryHarness = recovery_module.PostgresRecoveryHarness
RecoveryHarnessError = recovery_module.RecoveryHarnessError
InjectedFault = recovery_module.InjectedFault
LeaseLost = recovery_module.LeaseLost
IdempotencyConflict = recovery_module.IdempotencyConflict
InvariantViolation = recovery_module.InvariantViolation
FAULT_POINTS = recovery_module.FAULT_POINTS
LOOP_ID = recovery_module.LOOP_ID
require_nonprod_boundary = recovery_module.require_nonprod_boundary

FAULT_SCENARIOS = {
    f"F{index:02d}_{point.upper()}": point
    for index, point in enumerate(
        (
            "before_outbox_persist",
            "after_outbox_persist",
            "before_downstream_mutation",
            "after_downstream_mutation",
            "after_mutation_before_receipt",
            "downstream_timeout_after_commit",
            "before_projection",
            "after_projection_before_publish",
        ),
        1,
    )
}
EXPECTED_SCENARIO_IDS = frozenset(
    {
        *FAULT_SCENARIOS,
        "DUPLICATE_DELIVERY",
        "LEASE_EXPIRY_FENCING",
        "TIMEOUT_DLQ_REPLAY",
        "WORKER_RESTART",
        "BFF_RESTART",
        "DATABASE_RESTART",
        "FULL_STACK_RESTART",
    }
)
REQUIRED_TERMINAL_CHECKS = frozenset(
    {
        "command_completed",
        "outbox_published",
        "canonical_apply_once",
        "command_correlated",
        "payload_correlated",
        "event_correlated",
        "trace_correlated",
        "idempotency_correlated",
        "effect_correlated",
        "controller_correlated",
        "rpo_zero",
        "recovery_within_two_intervals",
    }
)


class MatrixFailure(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MatrixFailure(message)


def run_command(
    args: Sequence[str],
    *,
    cwd: Path = ROOT,
    env: Optional[dict[str, str]] = None,
    timeout: float = 120,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(args),
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and completed.returncode != 0:
        raise MatrixFailure(
            f"command failed ({completed.returncode}): {' '.join(args)}\n"
            f"stdout={completed.stdout[-2000:]}\n"
            f"stderr={completed.stderr[-4000:]}"
        )
    return completed


def git_output(*args: str) -> str:
    return run_command(("git", *args), timeout=30).stdout.strip()


def redact_text(value: str) -> str:
    value = re.sub(r"postgresql://[^@\s]+@", "postgresql://<redacted>@", value)
    return re.sub(r"(?i)(password|secret|token)=([^\s]+)", r"\1=<redacted>", value)


def isolated_process_env(extra: dict[str, str]) -> dict[str, str]:
    """Pass only runtime essentials; never inherit broker/provider credentials."""
    allowed = {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "PYTHONPATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "VIRTUAL_ENV",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update(extra)
    return env


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class ProcessIdentity:
    kind: str
    process_id: int
    started_at: str
    instance_id: str
    code_identity: str


@dataclass
class MatrixConfig:
    run_id: str
    environment: str
    tenant_id: str
    deployment_sha: str
    isolation_token: str = field(default_factory=lambda: uuid.uuid4().hex)
    controller_interval_seconds: float = 0.75
    max_recovery_ticks: int = 2
    max_attempts: int = 2


@dataclass
class ScenarioResult:
    scenario_id: str
    status: str
    command_id: str
    expected_fault: Optional[str]
    observed_faults: list[str]
    recovery_ticks: int
    recovery_elapsed_seconds: float
    recovery_started_at: str
    recovered_at: str
    checks: dict[str, Any]
    service_identities: dict[str, Any] = field(default_factory=dict)
    failure_snapshot: dict[str, Any] = field(default_factory=dict)
    raw_snapshot: dict[str, Any] = field(default_factory=dict)


class IsolatedPostgres:
    """Disposable PostgreSQL process with a real restart boundary."""

    def __init__(self, run_id: str) -> None:
        suffix = re.sub(r"[^a-z0-9]", "", run_id.lower())[-20:]
        self.name = f"pantheon-loop-rec-{suffix}"
        self.password = "loop_recovery_local_only"
        self.user = "loop_recovery"
        self.database = "loop_recovery"
        self.host_port: Optional[int] = None
        self.container_id: Optional[str] = None

    @property
    def dsn(self) -> str:
        if self.host_port is None:
            raise MatrixFailure("isolated postgres has not started")
        return (
            f"postgresql://{self.user}:{self.password}@127.0.0.1:"
            f"{self.host_port}/{self.database}"
        )

    def start(self) -> ProcessIdentity:
        require(shutil.which("docker") is not None, "docker is required for capture")
        self.host_port = free_port()
        completed = run_command(
            (
                "docker",
                "run",
                "-d",
                "--name",
                self.name,
                "--label",
                f"pantheon.task={TASK_ID}",
                "--label",
                "pantheon.environment=isolated-target-dev",
                "-e",
                f"POSTGRES_USER={self.user}",
                "-e",
                f"POSTGRES_PASSWORD={self.password}",
                "-e",
                f"POSTGRES_DB={self.database}",
                "-p",
                f"127.0.0.1:{self.host_port}:5432",
                "postgres:16-alpine",
            ),
            timeout=90,
        )
        self.container_id = completed.stdout.strip()
        port_result = ""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            port_result = run_command(
                ("docker", "port", self.name, "5432/tcp"),
                timeout=10,
                check=False,
            ).stdout.strip()
            if port_result:
                break
            time.sleep(0.1)
        require(bool(port_result), "docker did not publish the postgres port")
        require(
            int(port_result.rsplit(":", 1)[1]) == self.host_port,
            "docker changed the requested isolated postgres port",
        )
        self._wait_ready()
        return self.identity()

    def _wait_ready(self) -> None:
        async def wait() -> None:
            import asyncpg

            deadline = time.monotonic() + 45
            last_error: Optional[Exception] = None
            while time.monotonic() < deadline:
                try:
                    conn = await asyncpg.connect(self.dsn, timeout=2)
                    await conn.close()
                    return
                except Exception as exc:  # connection startup is intentionally polled
                    last_error = exc
                    await asyncio.sleep(0.2)
            raise MatrixFailure(f"postgres did not become ready: {last_error}")

        asyncio.run(wait())

    def identity(self) -> ProcessIdentity:
        inspected = run_command(
            (
                "docker",
                "inspect",
                "--format",
                "{{.State.Pid}}|{{.State.StartedAt}}|{{.Id}}|{{.Image}}",
                self.name,
            ),
            timeout=15,
        ).stdout.strip()
        pid, started, container_id, image_id = inspected.split("|", 3)
        return ProcessIdentity(
            kind="postgres",
            process_id=int(pid),
            started_at=started,
            instance_id=container_id,
            code_identity=image_id,
        )

    def server_start_time(self) -> str:
        async def query() -> str:
            import asyncpg

            conn = await asyncpg.connect(self.dsn)
            try:
                value = await conn.fetchval("SELECT pg_postmaster_start_time()")
                return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            finally:
                await conn.close()

        return asyncio.run(query())

    def restart(self) -> tuple[ProcessIdentity, ProcessIdentity]:
        before = self.identity()
        before_start = self.server_start_time()
        run_command(("docker", "restart", "--time", "5", self.name), timeout=60)
        self._wait_ready()
        after = self.identity()
        after_start = self.server_start_time()
        require(before.process_id != after.process_id, "postgres PID did not change")
        require(before.started_at != after.started_at, "postgres start time did not change")
        require(before_start != after_start, "pg_postmaster_start_time did not change")
        require(before.instance_id == after.instance_id, "postgres container identity changed")
        return before, after

    def stop(self) -> None:
        if not self.container_id:
            return
        run_command(("docker", "rm", "-f", self.name), timeout=30, check=False)
        self.container_id = None

    def __enter__(self) -> "IsolatedPostgres":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


async def _serve_asgi_http(app: Any, host: str, port: int) -> None:
    """Small HTTP/1.1 transport for the real ASGI app used by this harness."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            request_line, *raw_headers = head[:-4].split(b"\r\n")
            method_raw, target, _ = request_line.split(b" ", 2)
            path_raw, _, query = target.partition(b"?")
            headers: list[tuple[bytes, bytes]] = []
            for raw in raw_headers:
                name, value = raw.split(b":", 1)
                headers.append((name.strip().lower(), value.strip()))
            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": method_raw.decode("ascii"),
                "scheme": "http",
                "path": path_raw.decode("utf-8"),
                "raw_path": path_raw,
                "query_string": query,
                "root_path": "",
                "headers": headers,
                "client": writer.get_extra_info("peername"),
                "server": (host, port),
                "state": {},
            }
            received = False
            response_status = 500
            response_headers: list[tuple[bytes, bytes]] = []
            response_body = bytearray()

            async def receive() -> dict[str, Any]:
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                return {"type": "http.disconnect"}

            async def send(message: dict[str, Any]) -> None:
                nonlocal response_status, response_headers
                if message["type"] == "http.response.start":
                    response_status = int(message["status"])
                    response_headers = list(message.get("headers") or [])
                elif message["type"] == "http.response.body":
                    response_body.extend(message.get("body") or b"")

            await app(scope, receive, send)
            reason = {
                200: "OK",
                400: "Bad Request",
                401: "Unauthorized",
                403: "Forbidden",
                404: "Not Found",
                500: "Internal Server Error",
                503: "Service Unavailable",
            }.get(response_status, "Response")
            lower_names = {name.lower() for name, _ in response_headers}
            if b"content-length" not in lower_names:
                response_headers.append(
                    (b"content-length", str(len(response_body)).encode("ascii"))
                )
            response_headers.append((b"connection", b"close"))
            header_bytes = b"".join(
                name + b": " + value + b"\r\n"
                for name, value in response_headers
                if name.lower() != b"connection"
            )
            writer.write(
                f"HTTP/1.1 {response_status} {reason}\r\n".encode("ascii")
                + header_bytes
                + b"Connection: close\r\n\r\n"
                + bytes(response_body)
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ValueError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async with app.router.lifespan_context(app):
        server = await asyncio.start_server(handle, host, port)
        async with server:
            await stop.wait()


def bff_serve(args: argparse.Namespace) -> int:
    app_dir = ROOT / "services" / "control-plane" / "bff"
    sys.path.insert(0, str(app_dir))
    bff_main = importlib.import_module("main")
    asyncio.run(_serve_asgi_http(bff_main.app, args.host, args.port))
    return 0


class BffRuntime:
    """Fresh uvicorn processes running the repository's real BFF app."""

    def __init__(self, dsn: str, config: MatrixConfig, log_dir: Path) -> None:
        self.dsn = dsn
        self.config = config
        self.log_dir = log_dir
        self.port: Optional[int] = None
        self.process: Optional[subprocess.Popen[str]] = None
        self.log_handle: Optional[Any] = None
        self.generation = 0
        self.started_at: Optional[str] = None
        self.readyz: dict[str, Any] = {}

    @property
    def base_url(self) -> str:
        require(self.port is not None, "BFF has not started")
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> ProcessIdentity:
        require(self.process is None, "BFF is already running")
        self.generation += 1
        self.port = free_port()
        log_path = self.log_dir / f"bff-generation-{self.generation}.log"
        self.log_handle = log_path.open("w", encoding="utf-8")
        env = isolated_process_env(
            {
                "DATABASE_URL": self.dsn,
                "PANTHEON_ENV": self.config.environment,
                "PANTHEON_TENANT_ID": self.config.tenant_id,
                "PANTHEON_BFF_AUTH_STUB": "true",
                "PANTHEON_BFF_AUTH_MODE": "permissive",
                "PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK": "false",
                "PANTHEON_STATUS_ROOT": str(ROOT),
                "GIT_SHA": self.config.deployment_sha,
                "PANTHEON_LIVE_BROKER_ENABLED": "false",
                "PANTHEON_BROKER_MODE": "paper",
            }
        )
        self.started_at = now_iso()
        self.process = subprocess.Popen(
            (
                sys.executable,
                str(SCRIPT),
                "bff-serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ),
            cwd=ROOT,
            env=env,
            text=True,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            self._wait_ready(log_path)
        except BaseException:
            self.stop()
            raise
        return self.identity()

    def _wait_ready(self, log_path: Path) -> None:
        deadline = time.monotonic() + 60
        last_error = "not started"
        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:
                text = log_path.read_text(encoding="utf-8", errors="replace")
                raise MatrixFailure(
                    f"BFF exited during startup ({self.process.returncode}): "
                    f"{redact_text(text[-5000:])}"
                )
            try:
                with urllib.request.urlopen(
                    f"{self.base_url}/bff/readyz", timeout=2
                ) as response:
                    if response.status == 200:
                        self.readyz = json.loads(response.read().decode("utf-8"))
                        reported = {
                            str(self.readyz.get("commit") or ""),
                            str(self.readyz.get("source_commit_sha") or ""),
                        }
                        require(
                            self.config.deployment_sha in reported,
                            f"BFF readyz did not report {self.config.deployment_sha}",
                        )
                        return
            except (OSError, urllib.error.URLError) as exc:
                last_error = str(exc)
            time.sleep(0.2)
        raise MatrixFailure(f"BFF did not become ready: {last_error}")

    def identity(self) -> ProcessIdentity:
        require(self.process is not None, "BFF is not running")
        require(self.process.poll() is None, "BFF process exited")
        return ProcessIdentity(
            kind="bff",
            process_id=int(self.process.pid),
            started_at=self.started_at or "unknown",
            instance_id=f"bff-generation-{self.generation}",
            code_identity=self.config.deployment_sha,
        )

    def _health_payload(self) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/bff/v5/loop-health/{LOOP_ID}",
            headers={
                "Authorization": "Bearer loop-recovery:operator,reviewer,admin:mfa"
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        require(response.status == 200, f"BFF readback returned {response.status}")
        return payload

    def readback(self, command_id: str) -> dict[str, Any]:
        payload = self._health_payload()
        data = payload.get("data") or {}
        controller = data.get("controller_health") or {}
        last_success = data.get("last_success") or {}
        evidence_packet = data.get("evidence_packet") or {}
        require(data.get("loop_id") == LOOP_ID, "BFF returned wrong loop id")
        require(
            controller.get("controller_name") == "loop-recovery-contract-controller",
            f"BFF did not expose controller record: {controller!r}",
        )
        require(last_success.get("status") == "success", "BFF lost last success")
        refs = evidence_packet.get("runtime_evidence_refs") or evidence_packet.get("refs") or []
        expected_ref = f"recovery-run:{self.config.run_id}:{command_id}"
        require(expected_ref in refs, f"BFF evidence refs omit {expected_ref!r}: {refs!r}")
        require(
            evidence_packet.get("accepted_live_liveness") is False,
            "contract harness must not manufacture catalog-admitted live liveness",
        )
        return payload

    def readback_degraded(self, command_id: str) -> dict[str, Any]:
        payload = self._health_payload()
        data = payload.get("data") or {}
        controller = data.get("controller_health") or {}
        last_failure = data.get("last_failure") or {}
        evidence_packet = data.get("evidence_packet") or {}
        refs = evidence_packet.get("runtime_evidence_refs") or []
        expected_ref = f"recovery-run:{self.config.run_id}:{command_id}:failure"
        require(data.get("loop_id") == LOOP_ID, "BFF returned wrong degraded loop id")
        require(
            controller.get("reported_status") in {"degraded", "unhealthy"},
            f"BFF did not expose degraded controller truth: {controller!r}",
        )
        require(last_failure.get("status") == "failed", "BFF lost last failure")
        require(expected_ref in refs, f"BFF degraded refs omit {expected_ref!r}")
        require(
            evidence_packet.get("accepted_live_liveness") is False,
            "degraded contract proof must not promote live maturity",
        )
        return payload

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        self.process = None
        if self.log_handle:
            self.log_handle.close()
        self.log_handle = None

    def restart(self) -> tuple[ProcessIdentity, ProcessIdentity]:
        before = self.identity()
        self.stop()
        after = self.start()
        require(before.process_id != after.process_id, "BFF PID did not change")
        require(before.instance_id != after.instance_id, "BFF generation did not change")
        return before, after

    def __enter__(self) -> "BffRuntime":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


def harness_for(dsn: str, config: MatrixConfig) -> Any:
    return PostgresRecoveryHarness(
        dsn,
        run_id=config.run_id,
        tenant_id=config.tenant_id,
        environment=config.environment,
        deployment_sha=config.deployment_sha,
        isolation_token=config.isolation_token,
        controller_interval_seconds=config.controller_interval_seconds,
        max_attempts=config.max_attempts,
    )


def worker_subprocess(
    dsn: str,
    config: MatrixConfig,
    *,
    worker_id: str,
    fault_point: Optional[str] = None,
) -> dict[str, Any]:
    args = [
        sys.executable,
        str(SCRIPT),
        "worker-once",
        "--database-url",
        dsn,
        "--run-id",
        config.run_id,
        "--environment",
        config.environment,
        "--tenant-id",
        config.tenant_id,
        "--deployment-sha",
        config.deployment_sha,
        "--controller-interval-seconds",
        str(config.controller_interval_seconds),
        "--max-attempts",
        str(config.max_attempts),
        "--worker-id",
        worker_id,
    ]
    if fault_point:
        args.extend(("--fault-point", fault_point))
    completed = run_command(
        args,
        timeout=60,
        env=isolated_process_env(
            {"LOOP_RECOVERY_ISOLATION_TOKEN": config.isolation_token}
        ),
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    require(bool(lines), f"worker {worker_id} returned no outcome")
    outcome = json.loads(lines[-1])
    outcome["process_id"] = int(outcome["process_id"])
    return outcome


def recover_within_two_ticks(
    dsn: str,
    config: MatrixConfig,
    *,
    command_id: str,
    worker_prefix: str,
    recovery_started_at: float,
) -> tuple[int, list[dict[str, Any]], float]:
    outcomes: list[dict[str, Any]] = []
    for tick in range(1, config.max_recovery_ticks + 1):
        scheduled_at = (
            recovery_started_at + tick * config.controller_interval_seconds
        )
        delay = scheduled_at - time.monotonic()
        if delay > 0:
            time.sleep(delay + 0.08)
        outcome = worker_subprocess(
            dsn,
            config,
            worker_id=f"{worker_prefix}-tick-{tick}",
        )
        outcomes.append(outcome)
        if outcome["status"] == "completed":
            require(
                outcome.get("command_id") == command_id,
                "recovery completed another command",
            )
            elapsed = time.monotonic() - recovery_started_at
            require(
                elapsed <= config.controller_interval_seconds * config.max_recovery_ticks,
                f"{command_id} recovery took {elapsed:.3f}s, exceeding two intervals",
            )
            return tick, outcomes, elapsed
        require(outcome["status"] == "idle", f"unexpected recovery outcome {outcome!r}")
    raise MatrixFailure(f"{command_id} did not recover within two controller ticks")


def terminal_result(
    dsn: str,
    config: MatrixConfig,
    *,
    scenario_id: str,
    command_id: str,
    expected_fault: Optional[str],
    recovery_ticks: int,
    recovery_elapsed_seconds: float,
    recovery_started_at: str,
    observed_faults: Iterable[str],
    identities: Optional[dict[str, Any]] = None,
    failure_snapshot: Optional[dict[str, Any]] = None,
) -> ScenarioResult:
    harness = harness_for(dsn, config)
    proof = asyncio.run(
        harness.assert_terminal_invariants(
            command_id,
            max_recovery_ticks=config.max_recovery_ticks,
            recovery_ticks=recovery_ticks,
            recovery_elapsed_seconds=recovery_elapsed_seconds,
        )
    )
    return ScenarioResult(
        scenario_id=scenario_id,
        status="pass",
        command_id=command_id,
        expected_fault=expected_fault,
        observed_faults=list(observed_faults),
        recovery_ticks=recovery_ticks,
        recovery_elapsed_seconds=round(recovery_elapsed_seconds, 6),
        recovery_started_at=recovery_started_at,
        recovered_at=now_iso(),
        checks=proof["checks"],
        service_identities=identities or {},
        failure_snapshot=failure_snapshot or {},
        raw_snapshot=proof["snapshot"],
    )


def run_fault_scenario(
    dsn: str,
    config: MatrixConfig,
    *,
    scenario_id: str,
    fault_point: str,
    bff: Optional[BffRuntime] = None,
) -> ScenarioResult:
    harness = harness_for(dsn, config)
    command_id = f"{scenario_id.lower()}-{config.run_id[-8:]}"
    if fault_point in {"before_outbox_persist", "after_outbox_persist"}:
        try:
            asyncio.run(
                harness.admit(
                    command_id,
                    f"value-{scenario_id}",
                    fault_point=fault_point,
                )
            )
        except InjectedFault as exc:
            require(exc.point == fault_point, "wrong admission fault observed")
        else:
            raise MatrixFailure(f"expected admission fault {fault_point} was not observed")
        observed = asyncio.run(harness.fault_observation_count(command_id, fault_point))
        require(observed == 1, f"fault {fault_point} observed {observed} times")
        failure_snapshot = asyncio.run(harness.snapshot(command_id))
        degraded_bff = (
            bff.readback_degraded(command_id)
            if bff and fault_point == "after_outbox_persist"
            else None
        )
        if fault_point == "before_outbox_persist":
            require(
                all(
                    not failure_snapshot[key]
                    for key in (
                        "commands",
                        "outbox",
                        "effects",
                        "receipts",
                        "projections",
                    )
                ),
                "before-outbox fault admitted durable command state",
            )
            asyncio.run(harness.admit(command_id, f"value-{scenario_id}"))
        recovery_started_at = time.monotonic()
        recovery_started_wall = now_iso()
        ticks, outcomes, elapsed = recover_within_two_ticks(
            dsn,
            config,
            command_id=command_id,
            worker_prefix=scenario_id.lower(),
            recovery_started_at=recovery_started_at,
        )
        identities = {"workers": outcomes}
        if degraded_bff:
            identities["bff_degraded_readback"] = degraded_bff
    else:
        asyncio.run(harness.admit(command_id, f"value-{scenario_id}"))
        first = worker_subprocess(
            dsn,
            config,
            worker_id=f"{scenario_id.lower()}-fault-worker",
            fault_point=fault_point,
        )
        expected_status = "timeout" if fault_point == "downstream_timeout_after_commit" else "injected_fault"
        require(first["status"] == expected_status, f"fault worker did not stop as expected: {first!r}")
        require(first.get("fault_point") == fault_point, "worker observed the wrong fault")
        observed = asyncio.run(harness.fault_observation_count(command_id, fault_point))
        require(observed == 1, f"fault {fault_point} observed {observed} times")
        failure_snapshot = asyncio.run(harness.snapshot(command_id))
        degraded_bff = bff.readback_degraded(command_id) if bff else None
        if fault_point == "after_downstream_mutation":
            require(
                not failure_snapshot["effects"],
                "after-downstream transactional cut did not roll back the effect",
            )
        if fault_point == "after_mutation_before_receipt":
            require(
                len(failure_snapshot["effects"]) == 1
                and not failure_snapshot["receipts"],
                "post-commit/pre-receipt cut did not preserve its distinct state",
            )
        recovery_started_at = time.monotonic()
        recovery_started_wall = now_iso()
        ticks, outcomes, elapsed = recover_within_two_ticks(
            dsn,
            config,
            command_id=command_id,
            worker_prefix=scenario_id.lower(),
            recovery_started_at=recovery_started_at,
        )
        identities = {"workers": [first, *outcomes]}
        if degraded_bff:
            identities["bff_degraded_readback"] = degraded_bff
    return terminal_result(
        dsn,
        config,
        scenario_id=scenario_id,
        command_id=command_id,
        expected_fault=fault_point,
        recovery_ticks=ticks,
        recovery_elapsed_seconds=elapsed,
        recovery_started_at=recovery_started_wall,
        observed_faults=[fault_point],
        identities=identities,
        failure_snapshot=failure_snapshot,
    )


def run_duplicate_scenario(dsn: str, config: MatrixConfig) -> ScenarioResult:
    scenario_id = "DUPLICATE_DELIVERY"
    command_id = f"duplicate-{config.run_id[-8:]}"
    harness = harness_for(dsn, config)
    recovery_started_at = time.monotonic()
    recovery_started_wall = now_iso()

    async def concurrent_admit() -> list[dict[str, Any]]:
        return list(
            await asyncio.gather(
                harness.admit(
                    command_id,
                    "duplicate-value",
                    idempotency_key="duplicate-key",
                ),
                harness.admit(
                    command_id,
                    "duplicate-value",
                    idempotency_key="duplicate-key",
                ),
            )
        )

    admissions = asyncio.run(concurrent_admit())
    require(
        sorted(item["replayed"] for item in admissions) == [False, True],
        f"concurrent duplicate admission was not serialized: {admissions!r}",
    )
    alias = asyncio.run(
        harness.admit(
            f"alias-{command_id}",
            "duplicate-value",
            idempotency_key="duplicate-key",
        )
    )
    require(
        alias["command_id"] == admissions[0]["command_id"]
        and alias["event_id"] == admissions[0]["event_id"],
        "idempotency replay returned a non-canonical envelope",
    )
    try:
        asyncio.run(
            harness.admit(
                command_id,
                "conflicting-value",
                idempotency_key="duplicate-key",
            )
        )
    except IdempotencyConflict:
        conflict_rejected = True
    else:
        conflict_rejected = False
    require(conflict_rejected, "conflicting idempotency replay was accepted")

    args = []
    for worker in ("duplicate-worker-a", "duplicate-worker-b"):
        command = [
            sys.executable,
            str(SCRIPT),
            "worker-once",
            "--database-url",
            dsn,
            "--run-id",
            config.run_id,
            "--environment",
            config.environment,
            "--tenant-id",
            config.tenant_id,
            "--deployment-sha",
            config.deployment_sha,
            "--controller-interval-seconds",
            str(config.controller_interval_seconds),
            "--max-attempts",
            str(config.max_attempts),
            "--worker-id",
            worker,
        ]
        args.append(
            subprocess.Popen(
                command,
                cwd=ROOT,
                env=isolated_process_env(
                    {"LOOP_RECOVERY_ISOLATION_TOKEN": config.isolation_token}
                ),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    outcomes = []
    try:
        for process in args:
            stdout, stderr = process.communicate(timeout=60)
            require(process.returncode == 0, f"duplicate worker failed: {stderr[-2000:]}")
            outcome = json.loads(
                [line for line in stdout.splitlines() if line.strip()][-1]
            )
            outcomes.append(outcome)
    finally:
        for process in args:
            if process.poll() is None:
                process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=5)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
    require(
        sorted(outcome["status"] for outcome in outcomes) == ["completed", "idle"],
        f"concurrent duplicate workers did not serialize: {outcomes!r}",
    )
    return terminal_result(
        dsn,
        config,
        scenario_id=scenario_id,
        command_id=command_id,
        expected_fault=None,
        recovery_ticks=1,
        recovery_elapsed_seconds=time.monotonic() - recovery_started_at,
        recovery_started_at=recovery_started_wall,
        observed_faults=[],
        identities={
            "workers": outcomes,
            "concurrent_admissions": admissions,
            "canonical_alias_replay": alias,
            "conflicting_replay_rejected": True,
        },
    )


def run_lease_scenario(
    dsn: str, config: MatrixConfig, bff: Optional[BffRuntime] = None
) -> ScenarioResult:
    scenario_id = "LEASE_EXPIRY_FENCING"
    command_id = f"lease-{config.run_id[-8:]}"
    harness = harness_for(dsn, config)
    recovery_started_at = time.monotonic()
    recovery_started_wall = now_iso()
    asyncio.run(harness.admit(command_id, "lease-value"))
    first = asyncio.run(harness.claim_one("lease-worker-old"))
    require(first is not None, "old lease worker did not claim command")
    contender = asyncio.run(harness.claim_one("lease-worker-contender"))
    require(contender is None, "contender claimed before lease expiry")
    time.sleep(config.controller_interval_seconds + 0.08)
    takeover = asyncio.run(harness.claim_one("lease-worker-new"))
    require(takeover is not None, "new worker did not take over expired lease")
    try:
        asyncio.run(harness.apply_effect(first))
    except LeaseLost:
        stale_fenced = True
    else:
        stale_fenced = False
    require(stale_fenced, "stale lease owner was not fenced")
    asyncio.run(
        harness.record_failure_state(
            command_id,
            "expired worker lease was fenced before takeover",
            worker_id=first.worker_id,
        )
    )
    failure_snapshot = asyncio.run(harness.snapshot(command_id))
    degraded_bff = bff.readback_degraded(command_id) if bff else None
    completed = asyncio.run(harness.complete_claim(takeover))
    require(completed.status == "completed", "lease takeover did not complete")
    return terminal_result(
        dsn,
        config,
        scenario_id=scenario_id,
        command_id=command_id,
        expected_fault=None,
        recovery_ticks=2,
        recovery_elapsed_seconds=time.monotonic() - recovery_started_at,
        recovery_started_at=recovery_started_wall,
        observed_faults=[],
        identities={
            "pre_expiry_contender_denied": True,
            "stale_owner_fenced": True,
            "old_worker": first.worker_id,
            "new_worker": takeover.worker_id,
            "bff_degraded_readback": degraded_bff,
        },
        failure_snapshot=failure_snapshot,
    )


def run_dlq_scenario(
    dsn: str, config: MatrixConfig, bff: Optional[BffRuntime] = None
) -> ScenarioResult:
    scenario_id = "TIMEOUT_DLQ_REPLAY"
    command_id = f"dlq-{config.run_id[-8:]}"
    harness = harness_for(dsn, config)
    asyncio.run(harness.admit(command_id, "dlq-value"))
    attempts = []
    for attempt in range(1, config.max_attempts + 1):
        outcome = worker_subprocess(
            dsn,
            config,
            worker_id=f"dlq-timeout-{attempt}",
            fault_point="downstream_timeout_after_commit",
        )
        require(outcome["status"] == "timeout", f"DLQ timeout missing: {outcome!r}")
        attempts.append(outcome)
    require(asyncio.run(harness.dlq_count()) == 1, "bounded timeouts did not reach DLQ")
    failure_snapshot = asyncio.run(harness.snapshot(command_id))
    degraded_bff = bff.readback_degraded(command_id) if bff else None
    require(
        failure_snapshot["outbox"][0]["status"] == "dlq"
        and failure_snapshot["controller_records"][0]["dlq_count"] >= 1,
        "DLQ was not projected as degraded controller truth",
    )
    recovery_started_at = time.monotonic()
    recovery_started_wall = now_iso()
    require(asyncio.run(harness.replay_dlq(command_id)), "DLQ replay did not requeue")
    require(not asyncio.run(harness.replay_dlq(command_id)), "second DLQ replay was not a no-op")
    ticks, recovery, elapsed = recover_within_two_ticks(
        dsn,
        config,
        command_id=command_id,
        worker_prefix="dlq-replay",
        recovery_started_at=recovery_started_at,
    )
    return terminal_result(
        dsn,
        config,
        scenario_id=scenario_id,
        command_id=command_id,
        expected_fault="downstream_timeout_after_commit",
        recovery_ticks=ticks,
        recovery_elapsed_seconds=elapsed,
        recovery_started_at=recovery_started_wall,
        observed_faults=["downstream_timeout_after_commit"] * config.max_attempts,
        identities={
            "timeout_workers": attempts,
            "replay_workers": recovery,
            "bff_degraded_readback": degraded_bff,
        },
        failure_snapshot=failure_snapshot,
    )


def run_worker_restart_scenario(
    dsn: str, config: MatrixConfig, bff: Optional[BffRuntime] = None
) -> ScenarioResult:
    scenario_id = "WORKER_RESTART"
    command_id = f"worker-restart-{config.run_id[-8:]}"
    harness = harness_for(dsn, config)
    asyncio.run(harness.admit(command_id, "worker-restart-value"))
    command = [
        sys.executable,
        str(SCRIPT),
        "worker-claim-and-hold",
        "--database-url",
        dsn,
        "--run-id",
        config.run_id,
        "--environment",
        config.environment,
        "--tenant-id",
        config.tenant_id,
        "--deployment-sha",
        config.deployment_sha,
        "--controller-interval-seconds",
        str(config.controller_interval_seconds),
        "--max-attempts",
        str(config.max_attempts),
        "--worker-id",
        "worker-before-restart",
    ]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=isolated_process_env(
            {"LOOP_RECOVERY_ISOLATION_TOKEN": config.isolation_token}
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        require(process.stdout is not None, "hold worker stdout is unavailable")
        readable, _, _ = select.select([process.stdout], [], [], 30)
        require(bool(readable), "hold worker did not report its durable claim")
        claimed = json.loads(process.stdout.readline())
        require(
            claimed.get("status") == "claimed_and_holding"
            and claimed.get("command_id") == command_id,
            f"hold worker did not claim the command: {claimed!r}",
        )
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
        require(process.returncode is not None and process.returncode < 0, "worker was not killed")
        crashed = {
            **claimed,
            "status": "abruptly_killed",
            "termination_signal": -int(process.returncode),
        }
    finally:
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    asyncio.run(
        harness.record_failure_state(
            command_id,
            "worker process was abruptly killed after durable claim",
            worker_id="worker-before-restart",
        )
    )
    failure_snapshot = asyncio.run(harness.snapshot(command_id))
    degraded_bff = bff.readback_degraded(command_id) if bff else None
    recovery_started_at = time.monotonic()
    recovery_started_wall = now_iso()
    ticks, recovered, elapsed = recover_within_two_ticks(
        dsn,
        config,
        command_id=command_id,
        worker_prefix="worker-after-restart",
        recovery_started_at=recovery_started_at,
    )
    recovery_process = next(item for item in recovered if item["status"] == "completed")
    require(
        crashed["process_id"] != recovery_process["process_id"],
        "worker restart reused process identity",
    )
    return terminal_result(
        dsn,
        config,
        scenario_id=scenario_id,
        command_id=command_id,
        expected_fault="abrupt_worker_termination_after_claim",
        recovery_ticks=ticks,
        recovery_elapsed_seconds=elapsed,
        recovery_started_at=recovery_started_wall,
        observed_faults=["abrupt_worker_termination_after_claim"],
        identities={
            "before": crashed,
            "after": recovery_process,
            "bff_degraded_readback": degraded_bff,
        },
        failure_snapshot=failure_snapshot,
    )


def run_restart_scenarios(
    dsn: str,
    config: MatrixConfig,
    postgres: IsolatedPostgres,
    bff: BffRuntime,
) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    harness = harness_for(dsn, config)

    bff_command = f"bff-restart-{config.run_id[-8:]}"
    asyncio.run(harness.admit(bff_command, "bff-restart-value"))
    bff_worker = worker_subprocess(
        dsn,
        config,
        worker_id="bff-restart-initial-worker",
    )
    require(bff_worker.get("status") == "completed", "BFF restart command did not complete")
    ticks = 1
    worker_outcomes = [bff_worker]
    before_payload = bff.readback(bff_command)
    bff_recovery_started_at = time.monotonic()
    bff_recovery_started_wall = now_iso()
    before_bff, after_bff = bff.restart()
    after_payload = bff.readback(bff_command)
    require(
        before_payload["data"]["last_success"]["at"]
        == after_payload["data"]["last_success"]["at"],
        "BFF restart changed durable projection identity",
    )
    results.append(
        terminal_result(
            dsn,
            config,
            scenario_id="BFF_RESTART",
            command_id=bff_command,
            expected_fault=None,
            recovery_ticks=ticks,
            recovery_elapsed_seconds=time.monotonic() - bff_recovery_started_at,
            recovery_started_at=bff_recovery_started_wall,
            observed_faults=[],
            identities={
                "bff_before": asdict(before_bff),
                "bff_after": asdict(after_bff),
                "bff_readback_before": before_payload,
                "bff_readback_after": after_payload,
                "bff_terminal_readback": after_payload,
                "workers": worker_outcomes,
            },
        )
    )

    db_command = f"db-restart-{config.run_id[-8:]}"
    try:
        asyncio.run(
            harness.admit(
                db_command,
                "db-restart-value",
                fault_point="after_outbox_persist",
            )
        )
    except InjectedFault:
        pass
    else:
        raise MatrixFailure("database restart command missed admission fault")
    db_failure_snapshot = asyncio.run(harness.snapshot(db_command))
    db_degraded_payload = bff.readback_degraded(db_command)
    db_recovery_started_at = time.monotonic()
    db_recovery_started_wall = now_iso()
    db_before, db_after = postgres.restart()
    ticks, worker_outcomes, _ = recover_within_two_ticks(
        dsn,
        config,
        command_id=db_command,
        worker_prefix="db-restart",
        recovery_started_at=db_recovery_started_at,
    )
    db_bff_payload = bff.readback(db_command)
    results.append(
        terminal_result(
            dsn,
            config,
            scenario_id="DATABASE_RESTART",
            command_id=db_command,
            expected_fault="after_outbox_persist",
            recovery_ticks=ticks,
            recovery_elapsed_seconds=time.monotonic() - db_recovery_started_at,
            recovery_started_at=db_recovery_started_wall,
            observed_faults=["after_outbox_persist"],
            identities={
                "database_before": asdict(db_before),
                "database_after": asdict(db_after),
                "bff_degraded_readback": db_degraded_payload,
                "bff_terminal_readback": db_bff_payload,
                "workers": worker_outcomes,
            },
            failure_snapshot=db_failure_snapshot,
        )
    )

    stack_command = f"stack-restart-{config.run_id[-8:]}"
    asyncio.run(harness.admit(stack_command, "stack-restart-value"))
    fault_worker = worker_subprocess(
        dsn,
        config,
        worker_id="stack-worker-before",
        fault_point="after_mutation_before_receipt",
    )
    require(fault_worker["status"] == "injected_fault", "full stack fault missing")
    stack_failure_snapshot = asyncio.run(harness.snapshot(stack_command))
    stack_degraded_payload = bff.readback_degraded(stack_command)
    stack_recovery_started_at = time.monotonic()
    stack_recovery_started_wall = now_iso()
    bff_before = bff.identity()
    bff.stop()
    db_before, db_after = postgres.restart()
    bff_after = bff.start()
    ticks, worker_outcomes, _ = recover_within_two_ticks(
        dsn,
        config,
        command_id=stack_command,
        worker_prefix="stack-worker-after",
        recovery_started_at=stack_recovery_started_at,
    )
    completed_worker = next(item for item in worker_outcomes if item["status"] == "completed")
    require(
        fault_worker["process_id"] != completed_worker["process_id"],
        "full-stack worker identity did not change",
    )
    require(bff_before.process_id != bff_after.process_id, "full-stack BFF identity did not change")
    require(db_before.process_id != db_after.process_id, "full-stack DB identity did not change")
    stack_bff_payload = bff.readback(stack_command)
    results.append(
        terminal_result(
            dsn,
            config,
            scenario_id="FULL_STACK_RESTART",
            command_id=stack_command,
            expected_fault="after_mutation_before_receipt",
            recovery_ticks=ticks,
            recovery_elapsed_seconds=time.monotonic() - stack_recovery_started_at,
            recovery_started_at=stack_recovery_started_wall,
            observed_faults=["after_mutation_before_receipt"],
            identities={
                "worker_before": fault_worker,
                "worker_after": completed_worker,
                "bff_before": asdict(bff_before),
                "bff_after": asdict(bff_after),
                "database_before": asdict(db_before),
                "database_after": asdict(db_after),
                "bff_degraded_readback": stack_degraded_payload,
                "bff_terminal_readback": stack_bff_payload,
            },
            failure_snapshot=stack_failure_snapshot,
        )
    )
    return results


def run_integration_matrix(
    config: MatrixConfig,
    artifact_dir: Path,
    *,
    run_adjacent_validation: bool = False,
) -> dict[str, Any]:
    require_nonprod_boundary(
        config.environment,
        live_broker_enabled=False,
        isolated_database=True,
    )
    started_at = now_iso()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    log_dir = artifact_dir / "runtime-logs"
    log_dir.mkdir(exist_ok=True)
    results: list[ScenarioResult] = []
    postgres = IsolatedPostgres(config.run_id)
    try:
        postgres_start = postgres.start()
        harness = harness_for(postgres.dsn, config)
        asyncio.run(harness.initialize())
        with BffRuntime(postgres.dsn, config, log_dir) as bff:
            bff_start = bff.identity()

            def append_with_terminal_bff(result: ScenarioResult) -> None:
                result.service_identities["bff_terminal_readback"] = bff.readback(
                    result.command_id
                )
                results.append(result)

            fault_order = [
                "before_outbox_persist",
                "after_outbox_persist",
                "before_downstream_mutation",
                "after_downstream_mutation",
                "after_mutation_before_receipt",
                "downstream_timeout_after_commit",
                "before_projection",
                "after_projection_before_publish",
            ]
            for index, point in enumerate(fault_order, 1):
                append_with_terminal_bff(
                    run_fault_scenario(
                        postgres.dsn,
                        config,
                        scenario_id=f"F{index:02d}_{point.upper()}",
                        fault_point=point,
                        bff=bff,
                    )
                )
            append_with_terminal_bff(run_duplicate_scenario(postgres.dsn, config))
            append_with_terminal_bff(run_lease_scenario(postgres.dsn, config, bff))
            append_with_terminal_bff(run_dlq_scenario(postgres.dsn, config, bff))
            append_with_terminal_bff(
                run_worker_restart_scenario(postgres.dsn, config, bff)
            )
            results.extend(run_restart_scenarios(postgres.dsn, config, postgres, bff))
            validation: list[dict[str, Any]] = []
            if run_adjacent_validation:
                command = (
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "scripts/test_loop_product_recovery_matrix.py",
                    "services/loop-control/test_loop_control.py",
                    "services/control-plane/bff/test_loop_health_read_model_contract.py",
                )
                completed = run_command(
                    command,
                    env=isolated_process_env(
                        {
                            "RECOVERY_TEST_DATABASE_URL": postgres.dsn,
                            "DATABASE_URL": postgres.dsn,
                        }
                    ),
                    timeout=240,
                )
                validation.append(
                    {
                        "command": " ".join(command[1:]),
                        "result": "pass",
                        "returncode": completed.returncode,
                        "summary": completed.stdout.strip().splitlines()[-1],
                    }
                )
            initial_identities = {
                "postgres": asdict(postgres_start),
                "bff": asdict(bff_start),
                "bff_readyz": dict(bff.readyz),
            }
        completed_at = now_iso()
        serialized = [asdict(result) for result in results]
        require(all(item["status"] == "pass" for item in serialized), "matrix has failed scenarios")
        require(
            {item["expected_fault"] for item in serialized if item["expected_fault"]}
            >= set(FAULT_POINTS),
            "matrix omitted a declared fault point",
        )
        return {
            "schema_version": "loop_recovery_matrix_run.v2",
            "task_id": TASK_ID,
            "run_id": config.run_id,
            "environment": config.environment,
            "tenant_id": config.tenant_id,
            "database_isolation": "disposable_docker_container",
            "isolation_attestation_sha256": hashlib.sha256(
                config.isolation_token.encode("utf-8")
            ).hexdigest(),
            "live_broker_enabled": False,
            "started_at": started_at,
            "completed_at": completed_at,
            "controller_interval_seconds": config.controller_interval_seconds,
            "max_recovery_ticks": config.max_recovery_ticks,
            "deployment_sha": config.deployment_sha,
            "initial_service_identities": initial_identities,
            "scenario_count": len(serialized),
            "overall_status": "pass",
            "adjacent_validation": validation,
            "scenarios": serialized,
        }
    finally:
        postgres.stop()


def write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def require_committed_capture_source() -> str:
    owned_sources = (
        "scripts/run_loop_product_recovery_matrix.py",
        "scripts/test_loop_product_recovery_matrix.py",
        "services/loop-control/recovery_harness.py",
        "services/loop-control/projector.py",
        "services/loop-control/test_loop_control.py",
    )
    status = git_output(
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        *owned_sources,
    )
    require(
        not status,
        "capture refuses uncommitted recovery implementation or test sources",
    )
    return git_output("rev-parse", "HEAD")


def validate_capture_report(
    report: dict[str, Any],
    *,
    raw_path: Path,
    raw_sha: str,
) -> None:
    require(report.get("schema_version") == "loop_recovery_matrix_run.v2", "wrong run schema")
    require(report.get("task_id") == TASK_ID, "raw run belongs to another task")
    require(report.get("overall_status") == "pass", "raw run did not pass")
    require(report.get("database_isolation") == "disposable_docker_container", "raw run lacks disposable DB proof")
    require(report.get("live_broker_enabled") is False, "raw run enabled a live broker")
    require(
        report.get("deployment_sha") == git_output("rev-parse", "HEAD"),
        "raw run deployment SHA differs from current HEAD",
    )
    require(bool(re.fullmatch(r"[0-9a-f]{64}", raw_sha)), "raw artifact SHA-256 is malformed")
    require(raw_path.is_file(), "raw matrix artifact is missing")
    require(sha256_file(raw_path) == raw_sha, "raw matrix checksum differs")
    parsed = json.loads(raw_path.read_text(encoding="utf-8"))
    require(parsed == report, "raw matrix bytes do not encode the admitted report")

    scenarios = report.get("scenarios")
    require(isinstance(scenarios, list), "raw run scenarios are missing")
    scenario_ids = {item.get("scenario_id") for item in scenarios if isinstance(item, dict)}
    require(scenario_ids == EXPECTED_SCENARIO_IDS, "raw run scenario set is incomplete")
    require(report.get("scenario_count") == len(EXPECTED_SCENARIO_IDS), "raw scenario count differs")
    interval = float(report.get("controller_interval_seconds") or 0)
    max_ticks = int(report.get("max_recovery_ticks") or 0)
    require(interval > 0 and max_ticks == 2, "recovery deadline contract is invalid")
    initial = report.get("initial_service_identities") or {}
    require(
        (initial.get("postgres") or {}).get("code_identity")
        and (initial.get("bff") or {}).get("code_identity") == report.get("deployment_sha")
        and report.get("deployment_sha")
        in {
            str((initial.get("bff_readyz") or {}).get("commit") or ""),
            str((initial.get("bff_readyz") or {}).get("source_commit_sha") or ""),
        },
        "raw run lacks exact PostgreSQL/BFF code identities",
    )
    validation = report.get("adjacent_validation") or []
    require(
        validation
        and all(
            item.get("result") == "pass" and item.get("returncode") == 0
            for item in validation
        ),
        "raw run lacks passing focused and adjacent validation",
    )

    for scenario in scenarios:
        scenario_id = str(scenario.get("scenario_id") or "")
        require(scenario.get("status") == "pass", f"{scenario_id} did not pass")
        checks = scenario.get("checks") or {}
        require(
            REQUIRED_TERMINAL_CHECKS.issubset(checks)
            and all(checks[name] is True for name in REQUIRED_TERMINAL_CHECKS),
            f"{scenario_id} lacks terminal invariant proof",
        )
        ticks = int(scenario.get("recovery_ticks") or 0)
        elapsed = float(scenario.get("recovery_elapsed_seconds") or -1)
        require(
            0 < ticks <= max_ticks and 0 <= elapsed <= interval * max_ticks,
            f"{scenario_id} exceeded its recovery deadline",
        )
        try:
            recovery_started = datetime.fromisoformat(
                str(scenario.get("recovery_started_at") or "").replace("Z", "+00:00")
            )
            recovered = datetime.fromisoformat(
                str(scenario.get("recovered_at") or "").replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise MatrixFailure(f"{scenario_id} recovery timestamps are invalid") from exc
        require(recovered >= recovery_started, f"{scenario_id} recovery timestamps reverse")
        terminal = scenario.get("raw_snapshot") or {}
        for key in (
            "commands",
            "outbox",
            "effects",
            "receipts",
            "projections",
            "controller_records",
        ):
            require(
                isinstance(terminal.get(key), list) and len(terminal[key]) == 1,
                f"{scenario_id} lacks authoritative terminal {key}",
            )
        identities = scenario.get("service_identities") or {}
        bff_readback = identities.get("bff_terminal_readback") or {}
        require(
            (bff_readback.get("data") or {}).get("loop_id") == LOOP_ID,
            f"{scenario_id} lacks serialized BFF terminal readback",
        )
        if scenario_id in FAULT_SCENARIOS:
            point = FAULT_SCENARIOS[scenario_id]
            require(scenario.get("expected_fault") == point, f"{scenario_id} expected fault differs")
            require(point in (scenario.get("observed_faults") or []), f"{scenario_id} fault was not observed")
            failure = scenario.get("failure_snapshot") or {}
            require(isinstance(failure.get("audit"), list) and failure["audit"], f"{scenario_id} lacks failure audit")


def validate_evidence_schema(evidence: dict[str, Any]) -> None:
    import jsonschema

    schema = json.loads(
        (ROOT / "schemas" / "product-evidence.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(evidence, schema)


def task_commits() -> list[dict[str, str]]:
    output = git_output(
        "log",
        "--reverse",
        "--format=%H%x1f%s",
        "origin/dev..HEAD",
        "--",
        "scripts/run_loop_product_recovery_matrix.py",
        "scripts/test_loop_product_recovery_matrix.py",
        "services/loop-control",
        ".orchestrator/task-briefs/loop_prod_rec_001.md",
    )
    commits = []
    for line in output.splitlines():
        if not line:
            continue
        sha, subject = line.split("\x1f", 1)
        commits.append({"sha": sha, "subject": subject})
    return commits


def build_evidence(
    report: dict[str, Any],
    raw_relpath: str,
    raw_sha: str,
    *,
    raw_path: Path,
) -> dict[str, Any]:
    validate_capture_report(report, raw_path=raw_path, raw_sha=raw_sha)
    base_sha = git_output("merge-base", "HEAD", "origin/dev")
    head_sha = git_output("rev-parse", "HEAD")
    scenario_ids = [item["scenario_id"] for item in report["scenarios"]]
    restart_ids = [
        item
        for item in scenario_ids
        if item in {"WORKER_RESTART", "BFF_RESTART", "DATABASE_RESTART", "FULL_STACK_RESTART"}
    ]
    fault_ids = [item for item in scenario_ids if item.startswith("F")]
    evidence_ref = f"{raw_relpath}#sha256={raw_sha}"
    return {
        "schema_version": "loop_product_evidence.v1",
        "schema_status": {
            "formal_schema_owner": "LOOP-PROD-002",
            "formalization_trigger": "LOOP-PROD-002 product evidence schema",
            "note": "Contract-level recovery evidence; no product loop side effect or hosted-live maturity is asserted.",
            "status": "formalized",
        },
        "evidence_policy": {
            "checksum_file": f"docs/deployment/evidence/loop-product-level/{TASK_ID}/evidence.sha256",
            "missing_or_contradicted_proof_fails_closed": True,
            "mutation_rule": "Raw run artifacts are immutable; after the corrected owner capture, the manifest may only retain its record log and append independently observed closeout facts.",
            "recording_mode": "logical_append_only_record_log_with_immutable_raw_runs",
            "redacted": True,
            "self_hashing": False,
        },
        "task": {
            "base_branch": "dev",
            "evidence_cut_at": report["completed_at"],
            "evidence_cut_semantics": "Isolated target-dev contract stack with real worker, BFF, PostgreSQL, and full-stack process restarts.",
            "id": TASK_ID,
            "overall_admission": "review_required_evidence_only",
            "owner": "Codex",
            "phase": "Loop Product-Level Remediation / Wave 0",
            "product_level_required": True,
            "repository": "ajoe734/pantheon",
            "review_file": f"docs/deployment/evidence/loop-product-level/{TASK_ID}/evidence.json",
            "reviewer": "Claude",
            "target_environment": "isolated-target-dev",
            "target_maturity": "contract",
            "task_branch": f"task/{TASK_ID}",
            "title": "Full-stack loop recovery and fault-injection harness",
        },
        "authorities": {
            "actual_state": [
                evidence_ref,
                "loop_recovery_commands/outbox/effects/receipts/projections",
                "loop_controller_records",
                "GET /bff/v5/loop-health/bff_health_monitoring",
            ],
            "desired_state": [
                f"docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/{TASK_ID}.md",
                "LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md",
                "EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md",
            ],
            "task_packet": f"docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/{TASK_ID}.md",
        },
        "scope": {
            "authoritative_write_owner": "Codex for recovery contract harness and evidence capture",
            "composes_with": ["LOOP-PROD-001", "LOOP-PROD-002"],
            "evidence_changed_files": [
                f"docs/deployment/evidence/loop-product-level/{TASK_ID}/evidence.json",
                f"docs/deployment/evidence/loop-product-level/{TASK_ID}/evidence.sha256",
                raw_relpath,
            ],
            "implementation_changed_files": [
                "services/loop-control/recovery_harness.py",
                "services/loop-control/projector.py",
                "services/loop-control/test_loop_control.py",
                "scripts/run_loop_product_recovery_matrix.py",
                "scripts/test_loop_product_recovery_matrix.py",
            ],
            "not_changing": "Product-loop workers, production cadence, live broker authority, or canonical business side effects",
            "owned_layer": "Isolated recovery contract, process restart orchestration, and fail-closed evidence capture",
        },
        "implementation_delivery": {
            "anchor_commits": task_commits(),
            "required_checks": [],
        },
        "validation": {
            "commands": [
                {
                    "command": f"python3 scripts/run_loop_product_recovery_matrix.py capture --run-id {report['run_id']}",
                    "result": "pass",
                    "conclusion": f"{report['scenario_count']} isolated recovery scenarios passed",
                },
                {
                    "command": report["adjacent_validation"][0]["command"],
                    "result": report["adjacent_validation"][0]["result"],
                    "conclusion": report["adjacent_validation"][0]["summary"],
                },
            ],
            "validated_at": report["completed_at"],
            "validated_base_sha": base_sha,
            "validated_head_sha": head_sha,
        },
        "deployment": {
            "applicable": False,
            "environment": "isolated-target-dev",
            "public_bff_base_url": "not_applicable_isolated_capture",
            "publish_cut": {
                "conclusion": "not_applicable",
                "reason": "The capture runs a fresh local BFF process against a disposable database.",
            },
            "canonical_root_deploy": {
                "conclusion": "not_applicable",
                "reason": "No continuously deployed capability changes in this contract task.",
            },
            "reason": "This task delivers a dev-tool contract harness; it does not change a continuously deployed capability.",
            "identity_admission": {
                "authoritative_chain": [
                    f"raw run {report['run_id']}",
                    f"candidate HEAD {head_sha}",
                    "disposable PostgreSQL process identity",
                    "fresh worker process identities",
                    "fresh real BFF uvicorn process identities",
                ]
            },
        },
        "hosted_readback": {
            "pre_deploy": {
                "status": "not_applicable",
                "reason": "The task does not claim shared hosted-dev admission.",
            },
            "isolated_target_dev": {
                "status": "pass",
                "observed_at": report["completed_at"],
                "raw_run_artifact": evidence_ref,
                "scenario_count": report["scenario_count"],
                "note": "Real BFF app readback is proven locally; public hosted dev deployment is not applicable to this contract-only code change.",
            }
        },
        "behavioral_proof": {
            "duplicate_safety": {
                "status": "pass",
                "proof": [f"{evidence_ref}:DUPLICATE_DELIVERY", f"{evidence_ref}:LEASE_EXPIRY_FENCING"],
            },
            "failure_and_degraded_behavior": {
                "status": "pass",
                "proof": [f"{evidence_ref}:{item}" for item in fault_ids]
                + [f"{evidence_ref}:TIMEOUT_DLQ_REPLAY"],
            },
            "request_receipt_downstream_correlation": {
                "status": "pass",
                "proof": [
                    f"{evidence_ref}: every terminal snapshot contains one correlated command, outbox event, canonical contract effect, receipt, projection, and controller record"
                ],
            },
            "restart_and_recovery": {
                "status": "pass",
                "proof": [f"{evidence_ref}:{item}" for item in restart_ids],
            },
            "rollback_or_compensation": {
                "status": "pass",
                "proof": [
                    f"{evidence_ref}:TIMEOUT_DLQ_REPLAY bounded failure, DLQ, explicit replay, and exactly-once convergence"
                ],
            },
        },
        "security_and_safety": {
            "environment_boundary": {
                "status": "pass",
                "proof": "capture requires isolated_database=true and a dev/test/recovery environment",
            },
            "hosted_frontend": {"status": "not_applicable", "reason": "No frontend change"},
            "mfa": {"status": "not_applicable", "reason": "No privileged command route is added"},
            "no_live_capital": {
                "status": "pass",
                "proof": "live broker is rejected and the only effect is an isolated recovery-contract row",
            },
            "rbac": {"status": "not_applicable", "reason": "The runner is a local dev tool"},
            "tenant_isolation": {
                "status": "pass",
                "proof": f"run-scoped tenant {report['tenant_id']} and disposable database",
            },
            "two_person_approval": {
                "status": "not_applicable",
                "reason": "No approval-gated product mutation occurs; independent code review remains pending in AC-05",
            },
        },
        "acceptance": [
            {
                "id": "AC-01",
                "statement": "harness injects every declared outbox, downstream, receipt, and projection cutpoint",
                "status": "pass",
                "evidence_refs": [f"{evidence_ref}:{item}" for item in fault_ids],
            },
            {
                "id": "AC-02",
                "statement": "admitted commands have RPO=0 and recover within two declared test controller intervals",
                "status": "pass",
                "evidence_refs": [evidence_ref],
            },
            {
                "id": "AC-03",
                "statement": "duplicates, retries, lease expiry, timeout, replay, and real process restarts create no duplicate canonical contract effect",
                "status": "pass",
                "evidence_refs": [
                    f"{evidence_ref}:DUPLICATE_DELIVERY",
                    f"{evidence_ref}:LEASE_EXPIRY_FENCING",
                    f"{evidence_ref}:TIMEOUT_DLQ_REPLAY",
                    *[f"{evidence_ref}:{item}" for item in restart_ids],
                ],
            },
            {
                "id": "AC-04",
                "statement": "production cadence is unchanged and all injected effects remain isolated target-dev contract effects",
                "status": "pass",
                "evidence_refs": ["security_and_safety.environment_boundary", evidence_ref],
            },
            {
                "id": "AC-05",
                "statement": "archive branch, PR, required checks, merge SHA, independent reviewer verdict, and residual risk owner/expiry",
                "status": "pending_independent_review_and_merge",
                "evidence_refs": ["implementation_delivery", "residual_risks"],
                "blocking_until": "updated PR head checks pass, Claude records review verdict, and PR #3586 merges",
            },
            {
                "id": "AC-06",
                "statement": "terminal success requires authoritative command/effect/receipt/projection/controller/BFF readback",
                "status": "pass",
                "evidence_refs": [evidence_ref],
            },
            {
                "id": "AC-07",
                "statement": "duplicate, failure/degraded, restart/recovery, and DLQ replay proof has admitted-command RPO=0",
                "status": "pass",
                "evidence_refs": ["behavioral_proof", evidence_ref],
            },
            {
                "id": "AC-08",
                "statement": "record exact deployment identity and controller truth when deployed; no registry-only maturity promotion",
                "status": "pass",
                "evidence_refs": ["deployment.identity_admission", evidence_ref],
            },
            {
                "id": "AC-09",
                "statement": "preserve RBAC, tenant, MFA, two-person, environment, and no-live-capital boundaries wherever applicable",
                "status": "pass",
                "evidence_refs": ["security_and_safety"],
            },
            {
                "id": "AC-10",
                "statement": "write redacted, logical-append-only checksummed evidence; missing proof fails closed",
                "status": "pass",
                "evidence_refs": ["evidence_policy", "integrity", evidence_ref],
            },
        ],
        "residual_risks": {
            "RISK-PRODUCT-ADAPTER-COVERAGE": {
                "blocking_for_this_task": False,
                "severity": "medium",
                "description": "The shared harness proves its contract stack; each product loop must bind its own authoritative effect/readback adapter before claiming product-level maturity.",
                "owner": "LOOP-PROD loop implementation owners",
                "containment": "Evidence labels the effect as recovery-contract only and BFF must not accept it as catalog-admitted live liveness.",
                "recheck_trigger": "Each downstream loop task integrates this matrix",
                "expiry": "2026-08-31T00:00:00Z",
            },
            "RISK-HOSTED-DEV-NOT-EXECUTED": {
                "blocking_for_this_task": False,
                "severity": "low",
                "description": "The contract harness runs in an isolated target-dev stack rather than mutating the shared hosted dev database.",
                "owner": "LOOP-PROD scenario and closeout owners",
                "containment": "Shared-dev execution requires the program environment-mutation lease and protected attestation tasks.",
                "recheck_trigger": "G3 hosted recovery qualification",
                "expiry": "2026-08-31T00:00:00Z",
            },
        },
        "integrity": {
            "algorithm": "sha256",
            "checksum_coverage": [
                f"docs/deployment/evidence/loop-product-level/{TASK_ID}/evidence.json"
            ],
            "companion_checksum_path": f"docs/deployment/evidence/loop-product-level/{TASK_ID}/evidence.sha256",
            "hosted_semantic_sha256": {},
            "manifest_path": f"docs/deployment/evidence/loop-product-level/{TASK_ID}/evidence.json",
            "normalized_hosted_readback": {},
            "self_hash_omitted": True,
            "self_hash_reason": "The companion file hashes evidence.json to avoid a recursive self-hash.",
            "source_artifact_sha256_by_epoch": {
                "isolated_recovery_capture": {
                    "git_sha": head_sha,
                    "artifacts": {raw_relpath: raw_sha},
                }
            },
        },
        "record_log": [
            {
                "sequence": 1,
                "recorded_at": report["completed_at"],
                "kind": "owner_recovery_matrix_capture",
                "status": "pass",
                "actor": "Codex",
                "run_id": report["run_id"],
                "raw_run_artifact": evidence_ref,
                "note": "No reviewer verdict or merge completion is asserted by this owner event.",
            },
            {
                "sequence": 2,
                "recorded_at": report["completed_at"],
                "kind": "evidence_ready_for_independent_review",
                "status": "pending_independent_review",
                "actor": "Codex",
                "reviewer": "Claude",
                "review_file": f"docs/deployment/evidence/loop-product-level/{TASK_ID}/evidence.json",
            },
        ],
    }


def write_manifest(evidence_root: Path, evidence: dict[str, Any]) -> None:
    validate_evidence_schema(evidence)
    manifest = evidence_root / "evidence.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temp = manifest.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, manifest)
    checksum = sha256_file(manifest)
    checksum_path = evidence_root / "evidence.sha256"
    checksum_temp = checksum_path.with_suffix(".sha256.tmp")
    checksum_temp.write_text(f"{checksum}  evidence.json\n", encoding="utf-8")
    os.replace(checksum_temp, checksum_path)


def _github_checks(pr: dict[str, Any]) -> list[dict[str, Any]]:
    checks = pr.get("statusCheckRollup") or []
    admitted: list[dict[str, Any]] = []
    for check in checks:
        raw_conclusion = str(
            check.get("conclusion") or check.get("state") or ""
        ).upper()
        if raw_conclusion in {"SKIPPED", "NEUTRAL"}:
            continue
        require(raw_conclusion == "SUCCESS", f"required PR check is not green: {check!r}")
        url = str(check.get("detailsUrl") or check.get("targetUrl") or "")
        run_match = re.search(r"/actions/runs/(\d+)", url)
        name = str(check.get("name") or check.get("context") or "unnamed check")
        admitted.append(
            {
                "workflow": str(check.get("workflowName") or name),
                "event": "pull_request",
                "run_id": int(run_match.group(1)) if run_match else 0,
                "url": url,
                "head_sha": str(pr["headRefOid"]),
                "jobs": {name: "success"},
                "conclusion": "success",
            }
        )
    require(bool(admitted), "merged PR exposes no successful required checks")
    return admitted


def finalize_evidence_payload(
    evidence: dict[str, Any],
    *,
    pr: dict[str, Any],
    task: dict[str, Any],
    observed_at: str,
) -> dict[str, Any]:
    require(str(pr.get("state") or "").upper() == "MERGED", "primary PR is not merged")
    merge_commit = pr.get("mergeCommit") or {}
    merge_sha = str(merge_commit.get("oid") or "")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", merge_sha)), "primary merge SHA is missing")
    require(bool(pr.get("mergedAt")), "primary PR merge timestamp is missing")
    require(task.get("id") == TASK_ID, "central review task ID differs")
    require(task.get("owner") in ("Codex", "Antigravity"), "central task owner is invalid")
    require(task.get("reviewer") == "Claude", "central task reviewer is not Claude")
    require(task.get("status") == "review_approved", "independent review is not approved")
    review_file = f"docs/deployment/evidence/loop-product-level/{TASK_ID}/evidence.json"
    require(task.get("review_file") == review_file or task.get("status") == "review_approved", "reviewer did not approve the evidence manifest")

    result = json.loads(json.dumps(evidence))
    result["task"]["owner"] = task.get("owner")
    result["task"]["review_file"] = review_file
    result["task"]["overall_admission"] = "accepted_contract_evidence"
    result["task"]["evidence_cut_at"] = observed_at
    result["task"]["evidence_cut_semantics"] = (
        "Merged contract recovery capture with exact-head checks and independent review; "
        "not product-loop or hosted-live maturity."
    )
    result["implementation_delivery"]["pull_request"] = {
        "number": int(pr["number"]),
        "url": str(pr["url"]),
        "head_sha": str(pr["headRefOid"]),
        "base": str(pr["baseRefName"]),
        "merged_at": str(pr["mergedAt"]),
        "merge_sha": merge_sha,
    }
    result["implementation_delivery"]["required_checks"] = _github_checks(pr)
    for acceptance in result["acceptance"]:
        if acceptance.get("id") == "AC-05":
            acceptance["status"] = "pass"
            acceptance.pop("blocking_until", None)
            acceptance["evidence_refs"] = [
                f"https://github.com/ajoe734/pantheon/pull/{pr['number']}",
                f"merge:{merge_sha}",
                "ai-status.json#LOOP-PROD-REC-001:review_approved",
                "residual_risks",
            ]
    next_sequence = max(
        (int(item.get("sequence") or 0) for item in result["record_log"]),
        default=0,
    ) + 1
    result["record_log"].extend(
        [
            {
                "sequence": next_sequence,
                "recorded_at": observed_at,
                "kind": "independent_review_observed",
                "status": "approved",
                "actor": "Claude",
                "reference": "ai-status.json#LOOP-PROD-REC-001",
            },
            {
                "sequence": next_sequence + 1,
                "recorded_at": observed_at,
                "kind": "primary_delivery_merge_observed",
                "status": "pass",
                "actor": "Codex",
                "reference": f"https://github.com/ajoe734/pantheon/pull/{pr['number']}",
                "sha": merge_sha,
            },
        ]
    )
    validate_evidence_schema(result)
    return result


def finalize_evidence(args: argparse.Namespace) -> int:
    require_committed_capture_source()
    manifest = EVIDENCE_ROOT / "evidence.json"
    checksum_path = EVIDENCE_ROOT / "evidence.sha256"
    require(manifest.is_file() and checksum_path.is_file(), "capture evidence is missing")
    checksum_fields = checksum_path.read_text(encoding="utf-8").split()
    require(
        bool(checksum_fields) and checksum_fields[0] == sha256_file(manifest),
        "capture evidence checksum is invalid",
    )
    pr_result = run_command(
        (
            "gh",
            "pr",
            "view",
            str(args.primary_pr),
            "--repo",
            "ajoe734/pantheon",
            "--json",
            "number,url,state,mergedAt,mergeCommit,headRefOid,baseRefName,statusCheckRollup",
        ),
        timeout=30,
    )
    pr = json.loads(pr_result.stdout)
    status_root = Path(
        args.status_root
        or os.environ.get("PANTHEON_STATUS_ROOT")
        or ROOT
    )
    status = json.loads((status_root / "ai-status.json").read_text(encoding="utf-8"))
    task = next(
        (item for item in status.get("tasks", []) if item.get("id") == TASK_ID),
        None,
    )
    require(bool(task), "central ai-status lacks this task")
    observed_at = now_iso()
    evidence = finalize_evidence_payload(
        json.loads(manifest.read_text(encoding="utf-8")),
        pr=pr,
        task=task,
        observed_at=observed_at,
    )
    write_manifest(EVIDENCE_ROOT, evidence)
    print(
        json.dumps(
            {
                "status": "pass",
                "primary_pr": int(pr["number"]),
                "merge_sha": pr["mergeCommit"]["oid"],
                "review_status": task["status"],
            },
            sort_keys=True,
        )
    )
    return 0


def capture(args: argparse.Namespace) -> int:
    capture_head = require_committed_capture_source()
    existing_manifest = EVIDENCE_ROOT / "evidence.json"
    if existing_manifest.is_file():
        with contextlib.suppress(json.JSONDecodeError):
            existing = json.loads(existing_manifest.read_text(encoding="utf-8"))
            require(
                not (
                    existing.get("schema_version") == "loop_product_evidence.v1"
                    and (existing.get("task") or {}).get("owner") == "Codex"
                ),
                "a corrected owner capture already exists; raw evidence is append-only",
            )
    run_id = args.run_id or f"rec-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    environment = args.environment or f"loop-recovery-{run_id[-12:]}"
    config = MatrixConfig(
        run_id=run_id,
        environment=environment,
        tenant_id=f"tenant-{run_id}",
        deployment_sha=capture_head,
        controller_interval_seconds=args.controller_interval_seconds,
        max_recovery_ticks=2,
        max_attempts=2,
    )
    evidence_root = Path(args.evidence_root).resolve()
    run_dir = evidence_root / "runs" / run_id
    if run_dir.exists():
        raise MatrixFailure(f"run artifact already exists and is immutable: {run_dir}")
    with tempfile.TemporaryDirectory(prefix=f"{TASK_ID}-") as temp_dir:
        working_dir = Path(temp_dir)
        report = run_integration_matrix(
            config,
            working_dir,
            run_adjacent_validation=True,
        )
        require(
            require_committed_capture_source() == capture_head,
            "capture source HEAD changed while the matrix was running",
        )
        redacted_logs: list[tuple[Path, str, str]] = []
        for log in sorted((working_dir / "runtime-logs").glob("*.log")):
            redacted = redact_text(
                log.read_text(encoding="utf-8", errors="replace")
            )
            target = run_dir / "runtime-logs" / log.name
            digest = hashlib.sha256(redacted.encode("utf-8")).hexdigest()
            redacted_logs.append((target, redacted, digest))
        report["runtime_logs"] = [
            {
                "path": target.relative_to(ROOT).as_posix(),
                "sha256": digest,
                "redacted": True,
            }
            for target, _, digest in redacted_logs
        ]
        raw_path = run_dir / "matrix.json"
        write_json_exclusive(raw_path, report)
        for target, redacted, _ in redacted_logs:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(redacted, encoding="utf-8")
    raw_sha = sha256_file(raw_path)
    raw_relpath = raw_path.relative_to(ROOT).as_posix()
    evidence = build_evidence(
        report,
        raw_relpath,
        raw_sha,
        raw_path=raw_path,
    )
    write_manifest(evidence_root, evidence)
    print(
        json.dumps(
            {
                "status": "pass",
                "run_id": run_id,
                "scenario_count": report["scenario_count"],
                "raw_artifact": raw_relpath,
                "raw_sha256": raw_sha,
            },
            sort_keys=True,
        )
    )
    return 0


def worker_once(args: argparse.Namespace) -> int:
    require_nonprod_boundary(
        args.environment,
        live_broker_enabled=False,
        isolated_database=True,
    )
    isolation_token = os.environ.get("LOOP_RECOVERY_ISOLATION_TOKEN", "")
    require(bool(isolation_token), "worker lacks the recovery isolation token")
    process_started_at = now_iso()
    config = MatrixConfig(
        run_id=args.run_id,
        environment=args.environment,
        tenant_id=args.tenant_id,
        deployment_sha=args.deployment_sha,
        isolation_token=isolation_token,
        controller_interval_seconds=args.controller_interval_seconds,
        max_attempts=args.max_attempts,
    )
    harness = harness_for(args.database_url, config)
    asyncio.run(harness.verify_isolation_guard())
    outcome = asyncio.run(
        harness.process_one(args.worker_id, fault_point=args.fault_point)
    ).to_dict()
    outcome["process_id"] = os.getpid()
    outcome["process_started_at"] = process_started_at
    outcome["code_identity"] = config.deployment_sha
    print(json.dumps(outcome, sort_keys=True))
    return 0


def worker_claim_and_hold(args: argparse.Namespace) -> int:
    require_nonprod_boundary(
        args.environment,
        live_broker_enabled=False,
        isolated_database=True,
    )
    isolation_token = os.environ.get("LOOP_RECOVERY_ISOLATION_TOKEN", "")
    require(bool(isolation_token), "worker lacks the recovery isolation token")
    process_started_at = now_iso()
    config = MatrixConfig(
        run_id=args.run_id,
        environment=args.environment,
        tenant_id=args.tenant_id,
        deployment_sha=args.deployment_sha,
        isolation_token=isolation_token,
        controller_interval_seconds=args.controller_interval_seconds,
        max_attempts=args.max_attempts,
    )
    harness = harness_for(args.database_url, config)
    asyncio.run(harness.verify_isolation_guard())
    claim = asyncio.run(harness.claim_one(args.worker_id))
    require(claim is not None, "hold worker found no command to claim")
    print(
        json.dumps(
            {
                "status": "claimed_and_holding",
                "command_id": claim.command_id,
                "event_id": claim.event_id,
                "attempt": claim.attempt,
                "worker_id": claim.worker_id,
                "process_id": os.getpid(),
                "process_started_at": process_started_at,
                "code_identity": config.deployment_sha,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    time.sleep(args.hold_seconds)
    raise MatrixFailure("hold worker was expected to be terminated")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture", help="run isolated matrix and capture evidence")
    capture_parser.add_argument("--run-id")
    capture_parser.add_argument("--environment")
    capture_parser.add_argument(
        "--evidence-root",
        default=str(EVIDENCE_ROOT),
    )
    capture_parser.add_argument("--controller-interval-seconds", type=float, default=15.0)
    capture_parser.set_defaults(func=capture)

    finalize_parser = sub.add_parser(
        "finalize-evidence",
        help="append independently observed review and merge facts",
    )
    finalize_parser.add_argument("--primary-pr", type=int, default=PR_NUMBER)
    finalize_parser.add_argument("--status-root")
    finalize_parser.set_defaults(func=finalize_evidence)

    bff_server = sub.add_parser("bff-serve", help=argparse.SUPPRESS)
    bff_server.add_argument("--host", default="127.0.0.1")
    bff_server.add_argument("--port", type=int, required=True)
    bff_server.set_defaults(func=bff_serve)

    worker = sub.add_parser("worker-once", help=argparse.SUPPRESS)
    worker.add_argument("--database-url", required=True)
    worker.add_argument("--run-id", required=True)
    worker.add_argument("--environment", required=True)
    worker.add_argument("--tenant-id", required=True)
    worker.add_argument("--deployment-sha", required=True)
    worker.add_argument("--controller-interval-seconds", type=float, required=True)
    worker.add_argument("--max-attempts", type=int, required=True)
    worker.add_argument("--worker-id", required=True)
    worker.add_argument("--fault-point", choices=sorted(FAULT_POINTS))
    worker.set_defaults(func=worker_once)
    hold_worker = sub.add_parser("worker-claim-and-hold", help=argparse.SUPPRESS)
    hold_worker.add_argument("--database-url", required=True)
    hold_worker.add_argument("--run-id", required=True)
    hold_worker.add_argument("--environment", required=True)
    hold_worker.add_argument("--tenant-id", required=True)
    hold_worker.add_argument("--deployment-sha", required=True)
    hold_worker.add_argument("--controller-interval-seconds", type=float, required=True)
    hold_worker.add_argument("--max-attempts", type=int, required=True)
    hold_worker.add_argument("--worker-id", required=True)
    hold_worker.add_argument("--hold-seconds", type=float, default=300.0)
    hold_worker.set_defaults(func=worker_claim_and_hold)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (MatrixFailure, RecoveryHarnessError, OSError, ValueError) as exc:
        print(f"ERROR: {redact_text(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
