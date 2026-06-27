"""
Durable deployment saga outbox consumer worker.

Polls the deployment service outbox for pending events and consumes each
one via the service API. Designed to run as a supervised long-lived process
(docker-compose restart: unless-stopped) so that approved DeploymentPlan
transitions do not require manual endpoint stepping.

Acceptance (LOOP-AUTO-DEP-001):
- Deployment outbox events are consumed durably.
- Duplicate outbox events are idempotent (status: duplicate is not an error).
- Consumer exposes health: last success, last failure.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


_CONSUMER_NAME = "deployment-outbox-consumer"


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def fetch_pending_outbox(*, api_url: str, timeout_seconds: float = 10.0) -> list[dict[str, Any]]:
    """Return all pending outbox records from the deployment service."""
    url = api_url.rstrip("/") + "/api/deployment/outbox?status=pending"
    request = urllib.request.Request(
        url, headers={"Accept": "application/json"}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else []


def consume_event(
    *,
    api_url: str,
    event_id: str,
    consumer_name: str,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """
    Consume one outbox event. Returns the inbox receipt.

    The deployment service returns HTTP 200 with status="duplicate" when the
    event has already been consumed by this consumer — this is not an error.
    """
    url = api_url.rstrip("/") + f"/api/deployment/outbox/{event_id}/consume"
    payload = json.dumps({"consumer_name": consumer_name}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def run_poll(
    *,
    api_url: str,
    consumer_name: str,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """
    Fetch pending outbox events and consume each one.

    Returns a summary: events_found, consumed, duplicates, errors.
    """
    events = fetch_pending_outbox(api_url=api_url, timeout_seconds=timeout_seconds)
    consumed = 0
    duplicates = 0
    errors: list[str] = []

    for record in events:
        event = record.get("event", {})
        event_id = event.get("event_id", "")
        if not event_id:
            errors.append(f"missing event_id in record: {record}")
            continue
        try:
            receipt = consume_event(
                api_url=api_url,
                event_id=event_id,
                consumer_name=consumer_name,
                timeout_seconds=timeout_seconds,
            )
            if receipt.get("status") == "duplicate":
                duplicates += 1
            else:
                consumed += 1
        except urllib.error.HTTPError as exc:
            errors.append(f"event_id={event_id} http_error={exc.code} {exc.reason}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"event_id={event_id} error={exc}")

    return {
        "events_found": len(events),
        "consumed": consumed,
        "duplicates": duplicates,
        "errors": errors,
    }


def _write_health(path: str, state: dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError:
        pass


def main() -> int:
    api_url = os.getenv("DEPLOYMENT_API_URL", "http://127.0.0.1:8095")
    consumer_name = os.getenv("DEPLOYMENT_OUTBOX_CONSUMER_NAME", _CONSUMER_NAME)
    interval_seconds = _env_int("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", 10, minimum=1)
    max_ticks = _env_int("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", 0, minimum=0)
    timeout_seconds = float(os.getenv("DEPLOYMENT_OUTBOX_CONSUMER_TIMEOUT_SECONDS", "10"))
    health_file = os.getenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", "")

    health: dict[str, Any] = {
        "consumer_name": consumer_name,
        "status": "starting",
        "total_consumed": 0,
        "total_duplicates": 0,
        "total_errors": 0,
        "ticks": 0,
        "last_success": None,
        "last_failure": None,
        "last_failure_reason": None,
    }

    tick = 0
    while True:
        tick += 1
        try:
            result = run_poll(
                api_url=api_url,
                consumer_name=consumer_name,
                timeout_seconds=timeout_seconds,
            )
            health["ticks"] = tick
            health["total_consumed"] += result["consumed"]
            health["total_duplicates"] += result["duplicates"]
            if result["errors"]:
                health["total_errors"] += len(result["errors"])
                health["status"] = "degraded"
                health["last_failure"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                health["last_failure_reason"] = "; ".join(result["errors"])
            else:
                if health["status"] != "degraded" or result["consumed"] > 0:
                    health["status"] = "ok"
                health["last_success"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if health_file:
                _write_health(health_file, health)
        except Exception as exc:  # noqa: BLE001
            health["ticks"] = tick
            health["total_errors"] += 1
            health["status"] = "degraded"
            health["last_failure"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            health["last_failure_reason"] = str(exc)
            result = {"events_found": 0, "consumed": 0, "duplicates": 0, "errors": [str(exc)]}
            if health_file:
                _write_health(health_file, health)

        print(
            json.dumps({"tick": tick, "health": health, "result": result}, sort_keys=True),
            flush=True,
        )

        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke.
    raise SystemExit(main())
