"""Scheduled reconciliation worker with bounded, observable retries.

Each invocation uses one stable ``tick_id`` for every retry.  The service
endpoint is idempotent for that identifier, so an ambiguous HTTP failure can
be retried without manufacturing a second reconciliation pass.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from services.background_worker_health import (
    healthcheck as check_worker_health,
    write_health,
)

SleepFn = Callable[[float], None]
_WORKER_NAME = "reconciliation-drift-scheduler"


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _window_id(
    *,
    tenant_id: str,
    window_seconds: int,
    now: datetime | None = None,
) -> str:
    if not tenant_id.strip():
        raise ValueError("tenant_id must not be empty")
    if window_seconds < 1:
        raise ValueError("window_seconds must be >= 1")
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start_epoch = int(observed.timestamp())
    start_epoch -= start_epoch % window_seconds
    start = datetime.fromtimestamp(start_epoch, tz=timezone.utc)
    start_text = start.isoformat().replace("+00:00", "Z")
    return f"scheduled:{tenant_id}:{start_text}:PT{window_seconds}S"


def _new_tick_id(
    *,
    tenant_id: str = "default",
    window_seconds: int = 300,
    now: datetime | None = None,
) -> str:
    return _window_id(
        tenant_id=tenant_id,
        window_seconds=window_seconds,
        now=now,
    )


def _error_record(exc: Exception, *, attempt: int, phase: str) -> dict[str, Any]:
    detail = str(exc)
    record: dict[str, Any] = {
        "attempt": attempt,
        "phase": phase,
        "error_type": type(exc).__name__,
        "detail": detail,
        "at": _utc_now(),
    }
    if isinstance(exc, urllib.error.HTTPError):
        record["code"] = exc.code
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - defensive response handling
            body = ""
        if body:
            record["detail"] = body
    elif isinstance(exc, urllib.error.URLError):
        record["detail"] = str(exc.reason)
    return record


def _decorate_result(
    payload: dict[str, Any],
    *,
    tick_id: str,
    controller_status: str,
    attempts: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    max_attempts: int,
    retry_backoff_seconds: float,
) -> dict[str, Any]:
    result = dict(payload)
    result.update(
        {
            "tick_id": tick_id,
            "controller_status": controller_status,
            "attempt_count": len(attempts),
            "error_count": len(errors),
            "attempts": attempts,
            "errors": errors,
            "last_success_at": next(
                (item["at"] for item in reversed(attempts) if item["status"] == "ok"),
                None,
            ),
            "last_failure_at": errors[-1]["at"] if errors else None,
            "retry_policy": {
                "max_attempts": max_attempts,
                "retry_backoff_seconds": retry_backoff_seconds,
            },
        }
    )
    return result


def run_tick(
    *,
    api_url: str,
    tick_id: str | None = None,
    window_id: str | None = None,
    tenant_id: str = "default",
    worker_id: str = "reconciliation-scheduler",
    window_seconds: int = 300,
    sla_seconds: float = 60.0,
    timeout_seconds: float = 90.0,
    max_attempts: int = 1,
    retry_backoff_seconds: float = 0.0,
    sleep_fn: SleepFn = time.sleep,
) -> dict[str, Any]:
    """Run one idempotent scheduler tick with bounded transport retries."""
    if not api_url.strip():
        raise ValueError("api_url must not be empty")
    if not tenant_id.strip() or not worker_id.strip():
        raise ValueError("tenant_id and worker_id must not be empty")
    if window_seconds < 1:
        raise ValueError("window_seconds must be >= 1")
    if not math.isfinite(sla_seconds) or sla_seconds <= 0:
        raise ValueError("sla_seconds must be a finite number > 0")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a finite number > 0")
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if not math.isfinite(retry_backoff_seconds) or retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be a finite number >= 0")

    effective_window_id = window_id or tick_id or _new_tick_id(
        tenant_id=tenant_id,
        window_seconds=window_seconds,
    )
    effective_tick_id = tick_id or effective_window_id
    payload = json.dumps(
        {
            "tenant_id": tenant_id,
            "tick_id": effective_tick_id,
            "window_id": effective_window_id,
            "worker_id": worker_id,
            "sla_seconds": sla_seconds,
        }
    ).encode("utf-8")
    attempts: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    last_payload: dict[str, Any] = {
        "status": "error",
        "window_id": effective_window_id,
        "tenant_id": tenant_id,
        "worker_id": worker_id,
        "sla_seconds": sla_seconds,
    }

    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            api_url.rstrip("/") + "/api/reconciliation-drift/scheduled-reconcile",
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Tenant-Id": tenant_id,
                **(
                    {
                        "Authorization": (
                            "Bearer "
                            + os.getenv("RECONCILIATION_DRIFT_AUTH_TOKEN", "")
                        )
                    }
                    if os.getenv("RECONCILIATION_DRIFT_AUTH_TOKEN", "")
                    else {}
                ),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                response_body = response.read().decode("utf-8")
            decoded = json.loads(response_body) if response_body else {}
            if not isinstance(decoded, dict):
                raise ValueError("scheduled reconcile response must be a JSON object")
            decoded.setdefault("window_id", effective_window_id)
            decoded.setdefault("tenant_id", tenant_id)
            decoded.setdefault("worker_id", worker_id)
            decoded.setdefault("sla_seconds", sla_seconds)
            last_payload = decoded
            downstream_status = str(decoded.get("status") or "unobserved").lower()
            observed_at = _utc_now()
            if decoded.get("within_sla") is False:
                downstream_status = "failure"
                decoded.setdefault("detail", "scheduled reconciliation SLA breached")
            if downstream_status in {
                "deferred",
                "error",
                "failed",
                "failure",
                "unavailable",
            }:
                error = {
                    "attempt": attempt,
                    "phase": "scheduled_reconcile",
                    "error_type": "DownstreamStatusError",
                    "detail": str(decoded.get("detail") or downstream_status),
                    "at": observed_at,
                }
                errors.append(error)
                attempts.append({"attempt": attempt, "status": "error", "at": observed_at})
            else:
                attempt_status = "ok" if downstream_status == "ok" else "degraded"
                attempts.append({"attempt": attempt, "status": attempt_status, "at": observed_at})
                return _decorate_result(
                    decoded,
                    tick_id=effective_tick_id,
                    controller_status="healthy" if attempt_status == "ok" else "degraded",
                    attempts=attempts,
                    errors=errors,
                    max_attempts=max_attempts,
                    retry_backoff_seconds=retry_backoff_seconds,
                )
        except Exception as exc:  # network/HTTP/decoding failures are worker state
            error = _error_record(exc, attempt=attempt, phase="scheduled_reconcile")
            errors.append(error)
            attempts.append({"attempt": attempt, "status": "error", "at": error["at"]})

        if attempt < max_attempts and retry_backoff_seconds:
            sleep_fn(retry_backoff_seconds)

    if errors:
        last_payload.setdefault("detail", errors[-1]["detail"])
        if "code" in errors[-1]:
            last_payload.setdefault("code", errors[-1]["code"])
    return _decorate_result(
        last_payload,
        tick_id=effective_tick_id,
        controller_status="unhealthy",
        attempts=attempts,
        errors=errors,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )


def _configuration_error(exc: Exception) -> dict[str, Any]:
    return {
        "status": "invalid_config",
        "controller_status": "invalid_config",
        "attempt_count": 0,
        "error_count": 1,
        "errors": [{"error_type": type(exc).__name__, "detail": str(exc), "at": _utc_now()}],
    }


def healthcheck() -> int:
    try:
        interval_seconds = _env_float(
            "RECONCILIATION_DRIFT_SCHEDULER_INTERVAL_SECONDS",
            300.0,
            minimum=0.001,
        )
        max_age_seconds = _env_float(
            "RECONCILIATION_DRIFT_SCHEDULER_HEALTH_MAX_AGE_SECONDS",
            900.0,
            minimum=0.001,
        )
    except (TypeError, ValueError):
        return 1
    return check_worker_health(
        health_file=os.getenv(
            "RECONCILIATION_DRIFT_SCHEDULER_HEALTH_FILE",
            "",
        ),
        interval_seconds=interval_seconds,
        max_age_seconds=max_age_seconds,
        worker_name=_WORKER_NAME,
    )


def _health_failure_reason(result: dict[str, Any]) -> str | None:
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("detail") or first)
        return str(first)
    return None


def main() -> int:
    health_file = os.getenv("RECONCILIATION_DRIFT_SCHEDULER_HEALTH_FILE", "")
    health: dict[str, Any] = {
        "worker_name": _WORKER_NAME,
        "status": "starting",
        "ticks": 0,
        "last_tick_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_reason": None,
        "controller_status": "starting",
        "pid": os.getpid(),
    }
    write_health(health_file, health)

    try:
        api_url = os.getenv(
            "RECONCILIATION_DRIFT_URL", "http://reconciliation-drift-svc:8102"
        ).strip()
        if not api_url:
            raise ValueError("RECONCILIATION_DRIFT_URL must not be empty")
        interval_seconds = _env_int(
            "RECONCILIATION_DRIFT_SCHEDULER_INTERVAL_SECONDS", 300, minimum=1
        )
        max_ticks = _env_int(
            "RECONCILIATION_DRIFT_SCHEDULER_MAX_TICKS", 0, minimum=0
        )
        max_attempts = _env_int(
            "RECONCILIATION_DRIFT_SCHEDULER_MAX_ATTEMPTS", 3, minimum=1
        )
        retry_backoff_seconds = _env_float(
            "RECONCILIATION_DRIFT_SCHEDULER_RETRY_BACKOFF_SECONDS", 1.0, minimum=0.0
        )
        timeout_seconds = _env_float(
            "RECONCILIATION_DRIFT_SCHEDULER_TIMEOUT_SECONDS", 90.0, minimum=0.001
        )
        sla_seconds = _env_float(
            "RECONCILIATION_DRIFT_SCHEDULED_SLA_SECONDS", 60.0, minimum=0.001
        )
        window_seconds = _env_int(
            "RECONCILIATION_DRIFT_SCHEDULER_WINDOW_SECONDS",
            interval_seconds,
            minimum=1,
        )
        tenant_id = (
            os.getenv("PANTHEON_TENANT_ID")
            or os.getenv("RECONCILIATION_DRIFT_DEFAULT_TENANT_ID")
            or "default"
        ).strip()
        worker_id = os.getenv(
            "RECONCILIATION_DRIFT_SCHEDULER_WORKER_ID",
            f"{os.getenv('HOSTNAME', 'scheduler')}:{os.getpid()}",
        ).strip()
        if not tenant_id or not worker_id:
            raise ValueError("scheduler tenant_id and worker_id must not be empty")
        if timeout_seconds < sla_seconds:
            raise ValueError(
                "RECONCILIATION_DRIFT_SCHEDULER_TIMEOUT_SECONDS must be >= "
                "RECONCILIATION_DRIFT_SCHEDULED_SLA_SECONDS"
            )
    except (TypeError, ValueError) as exc:
        print(json.dumps(_configuration_error(exc), sort_keys=True), flush=True)
        return 2

    tick = 0
    while True:
        tick += 1
        try:
            result = run_tick(
                api_url=api_url,
                tenant_id=tenant_id,
                worker_id=worker_id,
                window_seconds=window_seconds,
                sla_seconds=sla_seconds,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
        except Exception as exc:  # keep the controller alive on operational faults
            error = _error_record(exc, attempt=1, phase="worker_tick")
            result = {
                "status": "error",
                "controller_status": "unhealthy",
                "attempt_count": 1,
                "error_count": 1,
                "attempts": [{"attempt": 1, "status": "error", "at": error["at"]}],
                "errors": [error],
                "last_success_at": None,
                "last_failure_at": error["at"],
            }
        controller_status = str(result.get("controller_status") or "unhealthy")
        result.setdefault("controller_status", controller_status)
        tick_at = _utc_now()
        health.update(
            {
                "status": "ok",
                "ticks": tick,
                "last_tick_at": tick_at,
                "controller_status": controller_status,
            }
        )
        if controller_status == "healthy":
            health["last_success_at"] = tick_at
            health["last_failure_reason"] = None
        else:
            health["last_failure_at"] = tick_at
            health["last_failure_reason"] = _health_failure_reason(result)
        write_health(health_file, health)
        print(
            json.dumps(
                {"tick": tick, "controller_status": controller_status, "result": result},
                sort_keys=True,
            ),
            flush=True,
        )
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover
    if sys.argv[1:] == ["healthcheck"]:
        raise SystemExit(healthcheck())
    if sys.argv[1:]:
        print(
            "usage: python services/reconciliation-drift/scheduler_worker.py "
            "[healthcheck]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(main())
