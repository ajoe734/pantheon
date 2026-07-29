"""Fail-closed security policy for local Ray/RLlib activation."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Mapping


MIN_TOKEN_LENGTH = 32
LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain"})
PROTECTED_INIT_KEYS = frozenset({"include_dashboard", "dashboard_host", "_node_ip_address"})


class RaySecurityBoundaryError(EnvironmentError):
    """Raised when a real Ray backend would start without containment."""


def _is_loopback(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _read_token(runtime: Mapping[str, str]) -> str:
    inline = runtime.get("RAY_AUTH_TOKEN", "").strip()
    if inline:
        return inline
    path_value = runtime.get("RAY_AUTH_TOKEN_PATH", "").strip()
    if not path_value:
        raise RaySecurityBoundaryError(
            "Ray activation requires RAY_AUTH_TOKEN or RAY_AUTH_TOKEN_PATH"
        )
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise RaySecurityBoundaryError(f"Ray auth token file is missing: {path}")
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RaySecurityBoundaryError(f"Ray auth token file is unreadable: {path}") from exc


def require_secure_ray_runtime(env: Mapping[str, str] | None = None) -> None:
    runtime = os.environ if env is None else env
    if runtime.get("RAY_AUTH_MODE", "").strip().lower() != "token":
        raise RaySecurityBoundaryError("Ray activation requires RAY_AUTH_MODE=token")

    token = _read_token(runtime)
    if len(token) < MIN_TOKEN_LENGTH:
        raise RaySecurityBoundaryError(
            f"Ray authentication token must contain at least {MIN_TOKEN_LENGTH} characters"
        )

    address = runtime.get("RAY_ADDRESS", "").strip().lower()
    if address not in {"", "local"}:
        raise RaySecurityBoundaryError(
            "Pantheon RLlib activation may not attach to a remote Ray cluster"
        )
    dashboard_host = runtime.get("RAY_DASHBOARD_HOST", "127.0.0.1")
    if not _is_loopback(dashboard_host):
        raise RaySecurityBoundaryError("RAY_DASHBOARD_HOST must remain loopback-only")


def secure_local_ray_init_kwargs(**overrides: object) -> dict[str, object]:
    require_secure_ray_runtime()
    leaked = PROTECTED_INIT_KEYS.intersection(overrides)
    if leaked:
        raise RaySecurityBoundaryError(
            f"Ray security-owned init options may not be overridden: {sorted(leaked)}"
        )
    return {
        "include_dashboard": False,
        "dashboard_host": "127.0.0.1",
        "_node_ip_address": "127.0.0.1",
        **overrides,
    }
