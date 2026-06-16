from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


def _strip_output(value: str) -> str:
    return value.strip()


@dataclass(frozen=True)
class OpenClawGatewayConfig:
    repository_url: str = "https://github.com/openclaw/openclaw"
    release_tag: str = "v2026.6.6"
    commit_sha: str = "8c802aa683510c7f7503597b54c3021733245e59"
    image_ref: str = "ghcr.io/openclaw/openclaw:2026.6.6"
    image_digest: str = "sha256:4826ca6157377e93463786d5c16852e34eede9f4bd4be55e3773cdc509762857"
    container_name: str = "pantheon-openclaw-gateway"
    host: str = "127.0.0.1"
    host_port: int = 18789
    container_port: int = 18789
    gateway_token: str = "pantheon-local-token"
    state_dir: str | None = None
    auth_mode: str = "token"
    allow_unconfigured: bool = True
    health_timeout_seconds: float = 30.0
    poll_interval_seconds: float = 1.0
    startup_pull: bool = False

    @property
    def http_base_url(self) -> str:
        return f"http://{self.host}:{self.host_port}"

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.host_port}"

    @property
    def resolved_state_dir(self) -> Path:
        if self.state_dir:
            return Path(self.state_dir).expanduser().resolve()
        return Path("/tmp") / self.container_name

    @property
    def container_ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.container_port}"


class OpenClawGatewayTransportError(RuntimeError):
    """Structured transport/system error for the Pantheon-side adapter."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        error_layer: str,
        retryable: bool,
        raw_payload: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.error_layer = error_layer
        self.retryable = retryable
        self.raw_payload = raw_payload or {}
        self.owner_plane = "pantheon.adapter"

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": str(self),
            "retryable": self.retryable,
            "owner_plane": self.owner_plane,
            "error_layer": self.error_layer,
            "raw_payload": self.raw_payload,
        }


class OpenClawDockerGatewayRuntime:
    """Control and query the pinned upstream gateway runtime through Docker."""

    def __init__(self, config: OpenClawGatewayConfig | None = None):
        self.config = config or OpenClawGatewayConfig()

    def start(self) -> dict[str, Any]:
        if self.config.startup_pull:
            self._run_command(["docker", "pull", self.config.image_ref])

        self.config.resolved_state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.config.resolved_state_dir, 0o777)
        if self._container_exists():
            self._run_command(
                ["docker", "rm", "-f", self.config.container_name],
                allow_failure=True,
            )

        command = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            self.config.container_name,
            "-p",
            f"{self.config.host_port}:{self.config.container_port}",
            "-e",
            f"OPENCLAW_GATEWAY_TOKEN={self.config.gateway_token}",
            "-v",
            f"{self.config.resolved_state_dir}:/home/node/.openclaw",
            self.config.image_ref,
            "node",
            "dist/index.js",
            "gateway",
        ]
        if self.config.allow_unconfigured:
            command.append("--allow-unconfigured")
        command.extend(
            [
                "--port",
                str(self.config.container_port),
                "--auth",
                self.config.auth_mode,
                "--token",
                self.config.gateway_token,
            ]
        )
        result = self._run_command(command)
        self.wait_until_healthy()
        return {
            "container_id": _strip_output(result.stdout),
            "container_name": self.config.container_name,
            "state_dir": str(self.config.resolved_state_dir),
            "http_base_url": self.config.http_base_url,
            "ws_url": self.config.ws_url,
        }

    def stop(self) -> None:
        self._run_command(
            ["docker", "stop", self.config.container_name],
            allow_failure=True,
        )

    def wait_until_healthy(self, timeout_seconds: float | None = None) -> dict[str, Any]:
        deadline = time.time() + (timeout_seconds or self.config.health_timeout_seconds)
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"{self.config.http_base_url}/healthz",
                    timeout=5,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if payload.get("ok") is True:
                    return payload
            except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
            time.sleep(self.config.poll_interval_seconds)

        raise OpenClawGatewayTransportError(
            "OpenClaw gateway did not become healthy before the timeout elapsed.",
            error_code="UPSTREAM_UNAVAILABLE",
            error_layer="transport",
            retryable=True,
            raw_payload={
                "http_base_url": self.config.http_base_url,
                "last_error": str(last_error) if last_error else None,
            },
        )

    def disable_heartbeat(self) -> dict[str, Any]:
        return self.exec_json(
            [
                "system",
                "heartbeat",
                "disable",
                "--url",
                self.config.container_ws_url,
                "--token",
                self.config.gateway_token,
                "--json",
            ]
        )

    def gateway_health(self) -> dict[str, Any]:
        return self.exec_json(
            [
                "gateway",
                "health",
                "--url",
                self.config.container_ws_url,
                "--token",
                self.config.gateway_token,
                "--json",
            ]
        )

    def gateway_status(self) -> dict[str, Any]:
        return self.exec_json(
            [
                "gateway",
                "status",
                "--url",
                self.config.container_ws_url,
                "--token",
                self.config.gateway_token,
                "--require-rpc",
                "--json",
            ]
        )

    def gateway_call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        command = [
            "gateway",
            "call",
            method,
            "--url",
            self.config.container_ws_url,
            "--token",
            self.config.gateway_token,
            "--json",
        ]
        if params is not None:
            command.extend(["--params", json.dumps(params, separators=(",", ":"), sort_keys=True)])
        return self.exec_json(command)

    def agent_turn(
        self,
        *,
        message: str,
        session_id: str,
        agent_id: str = "main",
        timeout_seconds: int = 30,
        model: str | None = None,
    ) -> dict[str, Any]:
        command = [
            "agent",
            "--agent",
            agent_id,
            "--session-id",
            session_id,
            "--message",
            message,
            "--json",
            "--timeout",
            str(timeout_seconds),
        ]
        if model:
            command.extend(["--model", model])

        result = self._run_command(self._build_exec_command(command))
        stdout = _strip_output(result.stdout)
        if not stdout:
            return {}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            # Some successful command paths still return plain text. Keep the raw
            # payload so callers can surface a truthful degraded message instead
            # of fabricating a structured success.
            return {"text": stdout}

    def sessions_overview(self) -> dict[str, Any]:
        return self.exec_json(["sessions", "--json"])

    def probe(self) -> dict[str, Any]:
        http_health = self.wait_until_healthy()
        return {
            "http_health": http_health,
            "gateway_health": self.gateway_health(),
            "gateway_status": self.gateway_status(),
        }

    def exec_json(self, args: Sequence[str]) -> dict[str, Any]:
        command = self._build_exec_command(args)
        result = self._run_command(command)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise OpenClawGatewayTransportError(
                "OpenClaw gateway command returned a non-JSON payload.",
                error_code="SERIALIZATION_FAILURE",
                error_layer="transport",
                retryable=True,
                raw_payload={
                    "command": command,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            ) from exc

    def _build_exec_command(self, args: Sequence[str]) -> list[str]:
        return [
            "docker",
            "exec",
            self.config.container_name,
            "env",
            f"OPENCLAW_GATEWAY_TOKEN={self.config.gateway_token}",
            "OPENCLAW_HIDE_BANNER=1",
            "OPENCLAW_SUPPRESS_NOTES=1",
            "node",
            "dist/index.js",
            *args,
        ]

    def _container_exists(self) -> bool:
        result = self._run_command(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name=^{self.config.container_name}$",
                "--format",
                "{{.ID}}",
            ],
            allow_failure=True,
        )
        return bool(_strip_output(result.stdout))

    def _run_command(
        self,
        command: Sequence[str],
        *,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(command),
                check=not allow_failure,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise OpenClawGatewayTransportError(
                f"Required executable was not found while running: {' '.join(command)}",
                error_code="RUNTIME_UNAVAILABLE",
                error_layer="transport",
                retryable=False,
                raw_payload={"command": list(command)},
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = _strip_output(exc.stderr)
            stdout = _strip_output(exc.stdout)
            combined_output = "\n".join(part for part in (stdout, stderr) if part)
            error_code = "UPSTREAM_UNAVAILABLE"
            error_layer = "transport"
            retryable = True
            if "ECONNREFUSED" in combined_output or "Connection refused" in combined_output:
                error_code = "CONNECTION_REFUSED"
            elif "No API key found for provider" in combined_output:
                error_code = "AUTH_UNAVAILABLE"
                error_layer = "known"
                retryable = False
            elif "timed out" in combined_output.lower():
                error_code = "TIMEOUT"
                error_layer = "known"
            raise OpenClawGatewayTransportError(
                f"OpenClaw gateway command failed: {' '.join(command)}",
                error_code=error_code,
                error_layer=error_layer,
                retryable=retryable,
                raw_payload={
                    "command": list(command),
                    "stdout": exc.stdout,
                    "stderr": exc.stderr,
                    "returncode": exc.returncode,
                },
            ) from exc
