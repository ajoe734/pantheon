"""HTTP worker for evolution daily-sweep schedule ticks."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

from services.evolution.worker_health import (
    healthcheck as check_worker_health,
    write_health,
)


_WORKER_NAME = "evolution-daily-sweep-scheduler"


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_tick(
    *,
    api_url: str,
    max_incidents: int | None = None,
    timeout_seconds: float = 30.0,
    tenant_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"sweep_id": "scheduled-daily"}
    if max_incidents is not None:
        body["max_incidents"] = max_incidents
    payload = json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    request = urllib.request.Request(
        api_url.rstrip("/") + "/api/evolution/daily-sweep",
        data=payload,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        response_body = response.read().decode("utf-8")
    return json.loads(response_body) if response_body else {}


def healthcheck() -> int:
    return check_worker_health(
        health_file=os.getenv("EVOLUTION_SCHEDULER_HEALTH_FILE", ""),
        interval_seconds=_env_int(
            "EVOLUTION_SCHEDULER_INTERVAL_SECONDS",
            86400,
            minimum=1,
        ),
        worker_name=_WORKER_NAME,
    )


def main() -> int:
    api_url = os.getenv("EVOLUTION_API_URL", "http://127.0.0.1:8093")
    interval_seconds = _env_int("EVOLUTION_SCHEDULER_INTERVAL_SECONDS", 86400, minimum=1)
    max_ticks = _env_int("EVOLUTION_SCHEDULER_MAX_TICKS", 0, minimum=0)
    health_file = os.getenv("EVOLUTION_SCHEDULER_HEALTH_FILE", "")
    max_incidents_raw = os.getenv("EVOLUTION_SWEEP_MAX_INCIDENTS", "").strip()
    max_incidents = int(max_incidents_raw) if max_incidents_raw else None
    auth_mode = os.getenv("EVOLUTION_AUTH_MODE", "disabled").strip().lower()
    auth_token = os.getenv("EVOLUTION_AUTH_TOKEN", "").strip() or None
    tenant_id = (
        os.getenv("EVOLUTION_SCHEDULER_TENANT_ID")
        or os.getenv("EVOLUTION_DEFAULT_TENANT_ID")
        or os.getenv("PANTHEON_TENANT_ID")
        or "pantheon-default"
    ).strip()
    if auth_mode not in {"disabled", "token"}:
        raise RuntimeError("EVOLUTION_AUTH_MODE must be disabled or token")
    if auth_mode == "token" and auth_token is None:
        raise RuntimeError("EVOLUTION_AUTH_TOKEN is required when EVOLUTION_AUTH_MODE=token")
    if not tenant_id:
        raise RuntimeError("EVOLUTION_SCHEDULER_TENANT_ID is required")

    health: dict[str, Any] = {
        "worker_name": _WORKER_NAME,
        "status": "starting",
        "ticks": 0,
        "last_tick_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_reason": None,
        "tenant_id": tenant_id,
        "auth_mode": auth_mode,
    }
    write_health(health_file, health)

    tick = 0
    while True:
        tick += 1
        try:
            result = run_tick(
                api_url=api_url,
                max_incidents=max_incidents,
                tenant_id=tenant_id,
                auth_token=auth_token,
            )
        except Exception as exc:
            health["ticks"] = tick
            health["status"] = "degraded"
            health["last_tick_at"] = _utc_now()
            health["last_failure_at"] = health["last_tick_at"]
            health["last_failure_reason"] = str(exc)
            write_health(health_file, health)
            print(
                json.dumps(
                    {"tick": tick, "health": health, "error": str(exc)},
                    sort_keys=True,
                ),
                flush=True,
            )
            raise

        health["ticks"] = tick
        health["status"] = "ok"
        health["last_tick_at"] = _utc_now()
        health["last_success_at"] = health["last_tick_at"]
        health["last_failure_reason"] = None
        write_health(health_file, health)
        print(
            json.dumps(
                {"tick": tick, "health": health, "result": result},
                sort_keys=True,
            ),
            flush=True,
        )
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke worker.
    if sys.argv[1:] == ["healthcheck"]:
        raise SystemExit(healthcheck())
    if sys.argv[1:]:
        print(
            "usage: python -m services.evolution.scheduler_worker [healthcheck]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(main())
