#!/usr/bin/env python3
"""Fail-closed MLflow server entrypoint.

The research image is safe for local tracking by default. A non-loopback bind
is refused unless an operator explicitly selects MLflow basic authentication
and mounts a non-default authentication configuration.
"""

from __future__ import annotations

import configparser
import ipaddress
import os
import sys
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


FALSE_VALUES = frozenset({"0", "false", "no", "off"})
LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain"})
UNSAFE_ADMIN_PASSWORDS = frozenset({"admin", "password"})


class MlflowSecurityBoundaryError(RuntimeError):
    """Raised when an MLflow server configuration would fail open."""


def _is_loopback(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _require_false(env: Mapping[str, str], key: str, *, default: str = "false") -> None:
    value = env.get(key, default).strip().lower()
    if value not in FALSE_VALUES:
        raise MlflowSecurityBoundaryError(f"{key} must remain false")


def _require_no_wildcard(env: Mapping[str, str], key: str, *, default: str) -> None:
    values = [item.strip() for item in env.get(key, default).split(",") if item.strip()]
    for value in values:
        if "://" in value:
            host = urlsplit(value).hostname
        elif value.startswith("["):
            host = value.partition("]")[0].lstrip("[")
        else:
            host = value.partition(":")[0]
        if not host or "*" in host:
            raise MlflowSecurityBoundaryError(
                f"{key} must be an explicit non-wildcard host allowlist"
            )
    if not values:
        raise MlflowSecurityBoundaryError(
            f"{key} must be an explicit non-wildcard host allowlist"
        )


def _validate_auth_config(path_value: str) -> Path:
    if not path_value.strip():
        raise MlflowSecurityBoundaryError(
            "MLFLOW_AUTH_CONFIG_PATH is required for a non-loopback MLflow bind"
        )
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise MlflowSecurityBoundaryError(
            f"MLFLOW_AUTH_CONFIG_PATH does not name a readable file: {path}"
        )
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except (configparser.Error, OSError) as exc:
        raise MlflowSecurityBoundaryError(
            f"MLFLOW_AUTH_CONFIG_PATH is not a valid readable INI file: {path}"
        ) from exc
    username = parser.get("mlflow", "admin_username", fallback="").strip()
    password = parser.get("mlflow", "admin_password", fallback="").strip()
    if not username or not password:
        raise MlflowSecurityBoundaryError(
            "MLFLOW_AUTH_CONFIG_PATH must define explicit mlflow admin credentials"
        )
    if password.casefold() in UNSAFE_ADMIN_PASSWORDS:
        raise MlflowSecurityBoundaryError(
            "MLFLOW_AUTH_CONFIG_PATH contains a known default admin password"
        )
    return path


def build_server_command(env: Mapping[str, str] | None = None) -> list[str]:
    runtime = os.environ if env is None else env
    host = runtime.get("MLFLOW_HOST", "127.0.0.1").strip()
    if not host:
        raise MlflowSecurityBoundaryError("MLFLOW_HOST must not be empty")

    try:
        port = int(runtime.get("MLFLOW_PORT", "5000"))
    except ValueError as exc:
        raise MlflowSecurityBoundaryError("MLFLOW_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise MlflowSecurityBoundaryError("MLFLOW_PORT must be between 1 and 65535")

    _require_false(runtime, "MLFLOW_SERVER_DISABLE_SECURITY_MIDDLEWARE")
    _require_false(runtime, "MLFLOW_SERVER_ENABLE_JOB_EXECUTION")
    _require_no_wildcard(
        runtime,
        "MLFLOW_SERVER_ALLOWED_HOSTS",
        default="localhost:*,127.0.0.1:*",
    )
    _require_no_wildcard(
        runtime,
        "MLFLOW_SERVER_CORS_ALLOWED_ORIGINS",
        default="http://localhost:*,http://127.0.0.1:*",
    )

    app_name = runtime.get("MLFLOW_APP_NAME", "").strip()
    if app_name and app_name != "basic-auth":
        raise MlflowSecurityBoundaryError(
            "MLFLOW_APP_NAME must be empty or basic-auth"
        )
    if not _is_loopback(host):
        if app_name != "basic-auth":
            raise MlflowSecurityBoundaryError(
                "non-loopback MLflow binds require MLFLOW_APP_NAME=basic-auth"
            )
        _validate_auth_config(runtime.get("MLFLOW_AUTH_CONFIG_PATH", ""))
    elif app_name == "basic-auth":
        _validate_auth_config(runtime.get("MLFLOW_AUTH_CONFIG_PATH", ""))

    command = ["mlflow", "server", "--host", host, "--port", str(port)]
    backend_store_uri = runtime.get("MLFLOW_BACKEND_STORE_URI", "").strip()
    if backend_store_uri:
        command.extend(["--backend-store-uri", backend_store_uri])
    artifact_root = runtime.get("MLFLOW_DEFAULT_ARTIFACT_ROOT", "").strip()
    if artifact_root:
        command.extend(["--default-artifact-root", artifact_root])
    if app_name:
        command.extend(["--app-name", app_name])
    return command


def main() -> int:
    try:
        command = build_server_command()
    except (MlflowSecurityBoundaryError, OSError) as exc:
        print(f"MLflow activation refused: {exc}", file=sys.stderr)
        return 78
    os.execvp(command[0], command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
