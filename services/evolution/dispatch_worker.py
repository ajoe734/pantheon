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
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


_WORKER_NAME = "evolution-dispatch-worker"
_ACTOR_ROLE = "evolution_controller"
_AUTO_DISPATCH_RESEARCH_ACTIONS = frozenset(
    {
        "observe",
        "revalidate",
        "retrain",
        "require_more_data",
        "flag_for_review",
    }
)
_EXECUTION_PLANES = frozenset({"research", "governance", "deployment", "runtime"})


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _decode_json_response(body: str, *, context: str) -> Any:
    if not body.strip():
        raise RuntimeError(f"{context} returned an empty 2xx response")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{context} returned malformed JSON") from exc


def _http_get(url: str, timeout_seconds: float) -> Any:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json"}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return _decode_json_response(body, context=f"GET {url}")


def _http_post(url: str, payload: dict[str, Any], timeout_seconds: float) -> Any:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return _decode_json_response(body, context=f"POST {url}")


def _require_object(payload: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"{context} returned a non-object payload")
    if not payload:
        raise RuntimeError(f"{context} returned an empty object payload")
    return payload


def _require_nonempty_string(
    payload: dict[str, Any], field: str, *, context: str
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{context} has invalid {field!r}")
    return value


def _validate_approved_decisions(payload: Any) -> list[dict[str, Any]]:
    context = "approved-decision list"
    if not isinstance(payload, list):
        raise RuntimeError(f"{context} returned a non-list payload")

    decisions: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        item_context = f"{context} item {index}"
        decision = _require_object(item, context=item_context)
        _require_nonempty_string(decision, "decision_id", context=item_context)
        _require_nonempty_string(decision, "action_type", context=item_context)
        _require_nonempty_string(decision, "decision_state", context=item_context)
        metadata = decision.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise RuntimeError(f"{item_context} has invalid 'metadata'")
        decisions.append(decision)
    return decisions


def _validate_boundary_payload(
    payload: Any, *, decision_id: str
) -> dict[str, Any]:
    context = f"boundary for decision_id={decision_id}"
    boundary = _require_object(payload, context=context)
    _require_nonempty_string(boundary, "boundary_key", context=context)
    execution_plane = _require_nonempty_string(
        boundary, "execution_plane", context=context
    )
    if execution_plane not in _EXECUTION_PLANES:
        raise RuntimeError(
            f"{context} has unsupported execution_plane={execution_plane!r}"
        )
    _require_nonempty_string(boundary, "threshold_policy_source", context=context)

    for field in ("reviewed_owner_roles", "approved_owner_roles", "followthrough"):
        value = boundary.get(field)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise RuntimeError(f"{context} has invalid {field!r}")
    for field in ("default_cooldown_days", "default_observation_days"):
        value = boundary.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError(f"{context} has invalid {field!r}")
    return boundary


def _validate_execute_payload(
    payload: Any,
    *,
    decision: dict[str, Any],
    expected_execution_plane: str,
) -> dict[str, Any]:
    decision_id = decision["decision_id"]
    context = f"execute response for decision_id={decision_id}"
    result = _require_object(payload, context=context)
    if _require_nonempty_string(result, "decision_id", context=context) != decision_id:
        raise RuntimeError(f"{context} has a mismatched decision_id")
    if (
        _require_nonempty_string(result, "action_type", context=context)
        != decision["action_type"]
    ):
        raise RuntimeError(f"{context} has a mismatched action_type")
    if _require_nonempty_string(result, "decision_state", context=context) != "executed":
        raise RuntimeError(f"{context} did not confirm decision_state='executed'")

    execution_result = _require_object(
        result.get("execution_result"), context=f"{context} execution_result"
    )
    if (
        _require_nonempty_string(execution_result, "status", context=context)
        != "submitted"
    ):
        raise RuntimeError(f"{context} did not confirm execution status='submitted'")
    if (
        _require_nonempty_string(execution_result, "plane", context=context)
        != expected_execution_plane
    ):
        raise RuntimeError(f"{context} has a mismatched execution plane")
    _require_nonempty_string(execution_result, "execution_ref_id", context=context)
    _require_nonempty_string(execution_result, "executed_at", context=context)
    _require_nonempty_string(result, "cooldown_ends_at", context=context)
    _require_nonempty_string(result, "observation_window_ends_at", context=context)
    return result


def fetch_approved_decisions(
    *,
    api_url: str,
    timeout_seconds: float = 10.0,
) -> list[dict[str, Any]]:
    """Return all decisions currently in 'approved' state."""
    url = api_url.rstrip("/") + "/api/evolution/proposals?decision_state=approved"
    try:
        return _validate_approved_decisions(_http_get(url, timeout_seconds))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Failed to fetch approved decisions: HTTP {exc.code} {exc.reason}"
        ) from exc


def fetch_boundary(
    *,
    api_url: str,
    decision_id: str,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Return the no-runtime ActionBoundary for a research decision."""
    url = (
        api_url.rstrip("/")
        + f"/api/evolution/proposals/{decision_id}/boundary"
    )
    return _validate_boundary_payload(
        _http_get(url, timeout_seconds), decision_id=decision_id
    )


def dispatch_decision(
    *,
    api_url: str,
    decision: dict[str, Any],
    actor_id: str,
    expected_execution_plane: str,
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
    }
    return _validate_execute_payload(
        _http_post(url, payload, timeout_seconds),
        decision=decision,
        expected_execution_plane=expected_execution_plane,
    )


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
    Fetch approved decisions and dispatch supported research actions.

    Returns a summary with decisions_found, dispatched, skipped, and errors.
    The evolution service is the gate enforcer — this worker only fans out
    research decisions to the correct path; it never mutates production state
    directly. Governance, deployment, and runtime action families remain
    approved for their authoritative owner because this worker cannot infer an
    active binding or an approved operational follow-through mode.
    """
    decisions = fetch_approved_decisions(api_url=api_url, timeout_seconds=timeout_seconds)

    dispatched = 0
    skipped_already_executed = 0
    skipped_unsupported = 0
    errors: list[str] = []
    dispatch_items: list[dict[str, Any]] = []
    skip_items: list[dict[str, Any]] = []

    for decision in decisions:
        decision_id = decision.get("decision_id", "")
        action_type = decision.get("action_type", "")
        decision_state = decision.get("decision_state", "")

        if decision_state != "approved":
            skipped_already_executed += 1
            continue

        if action_type not in _AUTO_DISPATCH_RESEARCH_ACTIONS:
            skipped_unsupported += 1
            metadata = decision.get("metadata") or {}
            skip_items.append(
                {
                    "decision_id": decision_id,
                    "action_type": action_type,
                    "target_stage": decision.get("target_stage"),
                    "reported_runtime_binding_id": (
                        metadata.get("runtime_binding_id")
                        or metadata.get("binding_id")
                    ),
                    "reason": (
                        "automatic dispatch supports research-plane actions only; "
                        "governance/deployment/runtime actions require an explicit "
                        "authoritative owner and approved follow-through mode"
                    ),
                }
            )
            continue

        try:
            boundary = fetch_boundary(
                api_url=api_url,
                decision_id=decision_id,
                timeout_seconds=timeout_seconds,
            )
            execution_plane = boundary.get("execution_plane", "unknown")
            expected_boundary_key = f"research_{action_type}"
            if (
                execution_plane != "research"
                or boundary.get("boundary_key") != expected_boundary_key
                or boundary.get("followthrough")
            ):
                raise RuntimeError(
                    "research auto-dispatch boundary mismatch: "
                    f"expected key={expected_boundary_key!r}, plane='research', "
                    f"followthrough=[]; received key={boundary.get('boundary_key')!r}, "
                    f"plane={execution_plane!r}, "
                    f"followthrough={boundary.get('followthrough')!r}"
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"decision_id={decision_id} boundary_fetch_error={exc}")
            # Fail closed: the boundary read is part of the execution gate.  If
            # it cannot be verified, do not attempt the mutating /execute call.
            continue

        try:
            result = dispatch_decision(
                api_url=api_url,
                decision=decision,
                actor_id=actor_id,
                expected_execution_plane=execution_plane,
                timeout_seconds=timeout_seconds,
            )
            dispatched += 1
            dispatch_items.append(
                {
                    "decision_id": decision_id,
                    "action_type": action_type,
                    "boundary_key": boundary["boundary_key"],
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
        "skipped_unsupported": skipped_unsupported,
        "errors": errors,
        "dispatch_items": dispatch_items,
        "skip_items": skip_items,
    }


def _write_health(path: str, state: dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError:
        pass


def healthcheck() -> int:
    """Return success only after a recent, error-free dispatch poll."""
    health_file = os.getenv("EVOLUTION_DISPATCH_HEALTH_FILE", "")
    interval_seconds = _env_int(
        "EVOLUTION_DISPATCH_INTERVAL_SECONDS", 30, minimum=1
    )
    if not health_file:
        print("EVOLUTION_DISPATCH_HEALTH_FILE is not configured", file=sys.stderr)
        return 1

    try:
        with open(health_file, encoding="utf-8") as fh:
            state = json.load(fh)
        age_seconds = max(0.0, time.time() - os.path.getmtime(health_file))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"evolution dispatch health unavailable: {exc}", file=sys.stderr)
        return 1

    if not isinstance(state, dict):
        print("evolution dispatch health payload is not an object", file=sys.stderr)
        return 1

    max_age_seconds = max(60, interval_seconds * 3)
    if state.get("status") != "ok" or state.get("ticks", 0) < 1:
        print(
            "evolution dispatch health is not ready: "
            f"status={state.get('status')} ticks={state.get('ticks')}",
            file=sys.stderr,
        )
        return 1
    if age_seconds > max_age_seconds:
        print(
            "evolution dispatch health is stale: "
            f"age_seconds={age_seconds:.1f} max_age_seconds={max_age_seconds}",
            file=sys.stderr,
        )
        return 1
    return 0


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
    if health_file:
        # A container restart can retain the prior writable-layer health file.
        # Reset it before the first network call so stale "ok" state cannot
        # make a newly booted worker healthy before it completes a poll.
        _write_health(health_file, health)

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
            health["total_skipped"] += (
                result["skipped_already_executed"]
                + result["skipped_unsupported"]
            )
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
                "skipped_unsupported": 0,
                "errors": [str(exc)],
                "dispatch_items": [],
                "skip_items": [],
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
    if sys.argv[1:] == ["healthcheck"]:
        raise SystemExit(healthcheck())
    if sys.argv[1:]:
        print(
            "usage: python -m services.evolution.dispatch_worker [healthcheck]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(main())
