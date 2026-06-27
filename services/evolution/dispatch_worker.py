"""
Evolution dispatch worker — LOOP-AUTO-EVO-004.

Polls for approved EvolutionDecisions and dispatches each through the correct
gated execution path (research / governance / runtime) via the evolution
service API. No direct production mutation is allowed; all dispatches go
through the boundary-enforced /execute endpoint.

Acceptance (LOOP-AUTO-EVO-004):
- Approved action dispatches only through allowed gated path.
- Production-affecting mutation requires correct approval gate.
- Dispatch result is visible in EvolutionDecision follow-through.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


_WORKER_NAME = "evolution-dispatch-worker"
_ACTOR_ROLE = "evolution_controller"


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _http_get(url: str, timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json"}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def _http_post(url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def fetch_approved_decisions(
    *,
    api_url: str,
    timeout_seconds: float = 10.0,
) -> list[dict[str, Any]]:
    """Return all decisions currently in 'approved' state."""
    url = api_url.rstrip("/") + "/api/evolution/proposals?decision_state=approved"
    try:
        return _http_get(url, timeout_seconds)  # type: ignore[return-value]
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Failed to fetch approved decisions: HTTP {exc.code} {exc.reason}"
        ) from exc


def fetch_boundary(
    *,
    api_url: str,
    decision_id: str,
    has_active_runtime: bool = False,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Return the ActionBoundary for a decision (read-only, for audit logging)."""
    flag = "true" if has_active_runtime else "false"
    url = (
        api_url.rstrip("/")
        + f"/api/evolution/proposals/{decision_id}/boundary"
        + f"?has_active_runtime={flag}"
    )
    return _http_get(url, timeout_seconds)


def dispatch_decision(
    *,
    api_url: str,
    decision: dict[str, Any],
    actor_id: str,
    has_active_runtime: bool = False,
    freeze_mode: str = "governance_only",
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """
    Submit an approved EvolutionDecision to the /execute endpoint.

    The execute endpoint enforces the ActionBoundary (gates) — this worker
    never bypasses it. The response is the updated DecisionResponse with
    cooldown/observation metadata and execution_result set.
    """
    decision_id = decision["decision_id"]
    url = api_url.rstrip("/") + f"/api/evolution/proposals/{decision_id}/execute"
    payload: dict[str, Any] = {
        "actor_role": _ACTOR_ROLE,
        "actor_id": actor_id,
        "has_active_runtime": has_active_runtime,
        "freeze_mode": freeze_mode,
    }
    return _http_post(url, payload, timeout_seconds)


def _http_error_retryable(exc: urllib.error.HTTPError) -> bool:
    if exc.code in {408, 409, 425, 429}:
        return True
    return 500 <= exc.code <= 599


def run_poll(
    *,
    api_url: str,
    actor_id: str,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """
    Fetch all approved decisions and dispatch each through the gated execute path.

    Returns a summary with decisions_found, dispatched, skipped, and errors.
    The evolution service is the gate enforcer — this worker only fans out
    approved decisions to the correct path; it never mutates production state
    directly.
    """
    decisions = fetch_approved_decisions(api_url=api_url, timeout_seconds=timeout_seconds)

    dispatched = 0
    skipped_already_executed = 0
    errors: list[str] = []
    dispatch_items: list[dict[str, Any]] = []

    for decision in decisions:
        decision_id = decision.get("decision_id", "")
        action_type = decision.get("action_type", "")
        decision_state = decision.get("decision_state", "")

        if decision_state != "approved":
            skipped_already_executed += 1
            continue

        # Determine safe dispatch context from the decision.
        # has_active_runtime defaults False: the safe path that avoids
        # triggering unexpected runtime follow-through. Callers that know
        # an active runtime exists should set this explicitly.
        has_active_runtime = bool(
            (decision.get("metadata") or {}).get("has_active_runtime", False)
        )

        try:
            boundary = fetch_boundary(
                api_url=api_url,
                decision_id=decision_id,
                has_active_runtime=has_active_runtime,
                timeout_seconds=timeout_seconds,
            )
            execution_plane = boundary.get("execution_plane", "unknown")
        except Exception as exc:  # noqa: BLE001
            boundary = {}
            execution_plane = "unknown"
            errors.append(f"decision_id={decision_id} boundary_fetch_error={exc}")

        try:
            result = dispatch_decision(
                api_url=api_url,
                decision=decision,
                actor_id=actor_id,
                has_active_runtime=has_active_runtime,
                freeze_mode="governance_only",
                timeout_seconds=timeout_seconds,
            )
            dispatched += 1
            dispatch_items.append(
                {
                    "decision_id": decision_id,
                    "action_type": action_type,
                    "execution_plane": execution_plane,
                    "resulting_state": result.get("decision_state"),
                    "cooldown_ends_at": result.get("cooldown_ends_at"),
                    "observation_window_ends_at": result.get("observation_window_ends_at"),
                    "execution_result": result.get("execution_result"),
                }
            )
        except urllib.error.HTTPError as exc:
            reason = f"decision_id={decision_id} http_error={exc.code} {exc.reason}"
            errors.append(reason)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"decision_id={decision_id} error={exc}")

    return {
        "decisions_found": len(decisions),
        "dispatched": dispatched,
        "skipped_already_executed": skipped_already_executed,
        "errors": errors,
        "dispatch_items": dispatch_items,
    }


def _write_health(path: str, state: dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError:
        pass


def main() -> int:
    api_url = os.getenv("EVOLUTION_API_URL", "http://127.0.0.1:8093")
    actor_id = os.getenv("EVOLUTION_DISPATCH_ACTOR_ID", _WORKER_NAME)
    interval_seconds = _env_int("EVOLUTION_DISPATCH_INTERVAL_SECONDS", 30, minimum=1)
    max_ticks = _env_int("EVOLUTION_DISPATCH_MAX_TICKS", 0, minimum=0)
    timeout_seconds = float(os.getenv("EVOLUTION_DISPATCH_TIMEOUT_SECONDS", "10"))
    health_file = os.getenv("EVOLUTION_DISPATCH_HEALTH_FILE", "")

    health: dict[str, Any] = {
        "worker_name": actor_id,
        "status": "starting",
        "total_dispatched": 0,
        "total_skipped": 0,
        "total_errors": 0,
        "ticks": 0,
        "last_success": None,
        "last_failure": None,
        "last_failure_reason": None,
    }

    tick = 0
    result: dict[str, Any] = {}
    while True:
        tick += 1
        try:
            result = run_poll(
                api_url=api_url,
                actor_id=actor_id,
                timeout_seconds=timeout_seconds,
            )
            health["ticks"] = tick
            health["total_dispatched"] += result["dispatched"]
            health["total_skipped"] += result["skipped_already_executed"]
            if result["errors"]:
                health["total_errors"] += len(result["errors"])
                health["status"] = "degraded"
                health["last_failure"] = _utc_now()
                health["last_failure_reason"] = "; ".join(result["errors"])
            else:
                health["status"] = "ok"
                health["last_success"] = _utc_now()
            if health_file:
                _write_health(health_file, health)
        except Exception as exc:  # noqa: BLE001
            health["ticks"] = tick
            health["total_errors"] += 1
            health["status"] = "degraded"
            health["last_failure"] = _utc_now()
            health["last_failure_reason"] = str(exc)
            result = {
                "decisions_found": 0,
                "dispatched": 0,
                "skipped_already_executed": 0,
                "errors": [str(exc)],
                "dispatch_items": [],
            }
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
