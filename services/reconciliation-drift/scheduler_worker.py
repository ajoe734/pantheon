"""Scheduled reconciliation worker with bounded, observable retries.

Each invocation uses one stable ``tick_id`` for every retry.  The service
endpoint is idempotent for that identifier, so an ambiguous HTTP failure can
be retried without manufacturing a second reconciliation pass.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Callable


SleepFn = Callable[[float], None]


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


def _new_tick_id() -> str:
    return f"scheduler-{_utc_now()}-{uuid.uuid4().hex[:8]}"


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
    timeout_seconds: float = 30.0,
    max_attempts: int = 1,
    retry_backoff_seconds: float = 0.0,
    sleep_fn: SleepFn = time.sleep,
) -> dict[str, Any]:
    """Run one idempotent scheduler tick with bounded transport retries."""
    if not api_url.strip():
        raise ValueError("api_url must not be empty")
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if not math.isfinite(retry_backoff_seconds) or retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be a finite number >= 0")

    effective_tick_id = tick_id or _new_tick_id()
    payload = json.dumps({"tick_id": effective_tick_id}).encode("utf-8")
    attempts: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    last_payload: dict[str, Any] = {"status": "error"}

    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            api_url.rstrip("/") + "/api/reconciliation-drift/scheduled-reconcile",
            data=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                response_body = response.read().decode("utf-8")
            decoded = json.loads(response_body) if response_body else {}
            if not isinstance(decoded, dict):
                raise ValueError("scheduled reconcile response must be a JSON object")
            last_payload = decoded
            downstream_status = str(decoded.get("status") or "unobserved").lower()
            observed_at = _utc_now()
            if downstream_status in {"error", "failed", "failure", "unavailable"}:
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


def main() -> int:
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
    except (TypeError, ValueError) as exc:
        print(json.dumps(_configuration_error(exc), sort_keys=True), flush=True)
        return 2

    tick = 0
    while True:
        tick += 1
        try:
            result = run_tick(
                api_url=api_url,
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
    raise SystemExit(main())
