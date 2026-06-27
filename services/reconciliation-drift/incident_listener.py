"""Incident-triggered reconciliation listener.

Polls the incident service for open incidents and posts each incident to the
reconciliation-drift incident trigger endpoint.  Duplicate incident reads are
safe because the service endpoint uses deterministic evaluation ids.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def fetch_open_incidents(
    *,
    incidents_url: str,
    timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    if not incidents_url:
        return []
    query = urllib.parse.urlencode({"open_only": "true"})
    request = urllib.request.Request(
        incidents_url.rstrip("/") + f"/api/incidents?{query}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    payload = json.loads(body) if body else []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        items = payload.get("incidents") or payload.get("items") or []
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    return []


def post_incident_trigger(
    *,
    reconciliation_url: str,
    incident: dict[str, Any],
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    payload = json.dumps({"incident": incident}).encode("utf-8")
    request = urllib.request.Request(
        reconciliation_url.rstrip("/") + "/api/reconciliation-drift/incident-triggers/consume",
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def run_tick(
    *,
    incidents_url: str,
    reconciliation_url: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    try:
        incidents = fetch_open_incidents(
            incidents_url=incidents_url,
            timeout_seconds=timeout_seconds,
        )
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "phase": "fetch_incidents", "detail": str(exc)}

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for incident in incidents:
        incident_id = str(incident.get("incident_id") or incident.get("id") or "")
        try:
            result = post_incident_trigger(
                reconciliation_url=reconciliation_url,
                incident=incident,
                timeout_seconds=timeout_seconds,
            )
            results.append({"incident_id": incident_id, "result": result})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            errors.append({"incident_id": incident_id, "code": exc.code, "detail": detail})
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            errors.append({"incident_id": incident_id, "detail": str(exc)})

    return {
        "status": "ok" if not errors else "partial_error",
        "fetched_incident_count": len(incidents),
        "triggered_incident_count": len(results),
        "error_count": len(errors),
        "results": results,
        "errors": errors,
    }


def main() -> int:
    incidents_url = os.getenv("PANTHEON_INCIDENTS_API_URL", "http://incidents:8090")
    reconciliation_url = os.getenv(
        "RECONCILIATION_DRIFT_URL", "http://reconciliation-drift-svc:8102"
    )
    interval_seconds = _env_int(
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_INTERVAL_SECONDS", 15, minimum=1
    )
    max_ticks = _env_int(
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_MAX_TICKS", 0, minimum=0
    )
    tick = 0
    while True:
        tick += 1
        result = run_tick(
            incidents_url=incidents_url,
            reconciliation_url=reconciliation_url,
        )
        print(json.dumps({"tick": tick, "result": result}, sort_keys=True), flush=True)
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
