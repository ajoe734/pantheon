"""Incident-triggered reconciliation listener with durable replay truth."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from services.background_worker_health import (
    healthcheck as check_worker_health,
    write_health,
)

SleepFn = Callable[[float], None]
DEFAULT_STATE_PATH = "/data/reconciliation-drift/incident-listener-state.json"
_WORKER_NAME = "reconciliation-drift-incident-listener"


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


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _incident_identity(incident: dict[str, Any]) -> str:
    for name in ("incident_id", "id"):
        if incident.get(name):
            return str(incident[name])
    for name in ("source_event_id", "event_id", "telemetry_event_id"):
        if incident.get(name):
            return f"source:{incident[name]}"
    event_ids = incident.get("telemetry_event_ids")
    if isinstance(event_ids, list) and event_ids:
        return f"source:{event_ids[0]}"
    canonical = json.dumps(incident, sort_keys=True, separators=(",", ":"), default=str)
    return f"payload-sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _error_record(
    exc: Exception,
    *,
    attempt: int,
    phase: str,
    identity: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "attempt": attempt,
        "phase": phase,
        "error_type": type(exc).__name__,
        "detail": str(exc),
        "at": _utc_now(),
    }
    if identity:
        record["identity"] = identity
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


class _DownstreamStatusError(RuntimeError):
    pass


class IncidentListenerState:
    """Atomic JSON state for failed incident deliveries and controller history."""

    def __init__(self, state_path: str | Path | None = None) -> None:
        self.path = Path(state_path) if state_path else None
        self.backlog: dict[str, dict[str, Any]] = {}
        self.last_success_at: str | None = None
        self.last_failure_at: str | None = None
        self.last_failure_error: str | None = None
        self.load_error: str | None = None
        self.reload()

    def reload(self) -> None:
        if self.path is None or not self.path.exists():
            self.load_error = None
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("backlog", {}), dict):
                raise ValueError("listener state must contain an object backlog")
            backlog: dict[str, dict[str, Any]] = {}
            for identity, entry in payload.get("backlog", {}).items():
                if not isinstance(entry, dict) or not isinstance(entry.get("incident"), dict):
                    raise ValueError(f"invalid backlog entry for {identity}")
                backlog[str(identity)] = dict(entry)
            self.backlog = backlog
            self.last_success_at = payload.get("last_success_at")
            self.last_failure_at = payload.get("last_failure_at")
            self.last_failure_error = payload.get("last_failure_error")
            self.load_error = None
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.load_error = f"{type(exc).__name__}: {exc}"

    def _document(self) -> dict[str, Any]:
        return {
            "version": 1,
            "backlog": self.backlog,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_failure_error": self.last_failure_error,
            "updated_at": _utc_now(),
        }

    def save(self) -> None:
        if self.path is None:
            return
        if self.load_error:
            raise RuntimeError(
                f"refusing to overwrite unreadable listener state: {self.load_error}"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            fd, temp_name = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=f".{self.path.name}.", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._document(), handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            temp_name = None
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass

    def record_delivery_failure(
        self,
        *,
        identity: str,
        incident: dict[str, Any],
        error: str,
        attempt_count: int,
        failed_at: str,
    ) -> None:
        old = self.backlog.get(identity)
        entry = {
            "identity": identity,
            "incident": incident,
            "first_failed_at": (old or {}).get("first_failed_at") or failed_at,
            "last_failed_at": failed_at,
            "last_error": error,
            "attempt_count": int((old or {}).get("attempt_count") or 0) + attempt_count,
        }
        self.backlog[identity] = entry
        try:
            self.save()
        except Exception:
            if old is None:
                self.backlog.pop(identity, None)
            else:
                self.backlog[identity] = old
            raise

    def remove_delivery(self, identity: str) -> None:
        old = self.backlog.pop(identity, None)
        if old is None:
            return
        try:
            self.save()
        except Exception:
            self.backlog[identity] = old
            raise

    def record_tick(self, *, successful: bool, at: str, error: str | None = None) -> None:
        previous = (self.last_success_at, self.last_failure_at, self.last_failure_error)
        if successful:
            self.last_success_at = at
        else:
            self.last_failure_at = at
            self.last_failure_error = error
        try:
            self.save()
        except Exception:
            self.last_success_at, self.last_failure_at, self.last_failure_error = previous
            raise

    def backlog_metrics(self, *, now: str) -> tuple[int, float | None]:
        oldest = min(
            (
                parsed
                for entry in self.backlog.values()
                if (parsed := _parse_timestamp(entry.get("first_failed_at"))) is not None
            ),
            default=None,
        )
        current = _parse_timestamp(now) or datetime.now(timezone.utc)
        age = max(0.0, (current - oldest).total_seconds()) if oldest else None
        return len(self.backlog), age


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
    delivered_incident = dict(incident)
    envelope = delivered_incident.get("correlation_envelope")
    tenant_id = str(
        delivered_incident.get("tenant_id")
        or (
            envelope.get("tenant_id")
            if isinstance(envelope, dict)
            else ""
        )
        or os.getenv("PANTHEON_TENANT_ID")
        or ""
    ).strip()
    if tenant_id:
        delivered_incident["tenant_id"] = tenant_id
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    auth_token = os.getenv("RECONCILIATION_DRIFT_AUTH_TOKEN", "")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    payload = json.dumps(
        {
            "tenant_id": tenant_id or None,
            "incident": delivered_incident,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        reconciliation_url.rstrip("/") + "/api/reconciliation-drift/incident-triggers/consume",
        data=payload,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    decoded = json.loads(body) if body else {}
    if not isinstance(decoded, dict):
        raise ValueError("incident trigger response must be a JSON object")
    return decoded


def _retry_operation(
    operation: Callable[[], Any],
    *,
    phase: str,
    identity: str | None,
    max_attempts: int,
    retry_backoff_seconds: float,
    sleep_fn: SleepFn,
) -> tuple[Any | None, list[dict[str, Any]], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        try:
            result = operation()
            if isinstance(result, dict):
                downstream_status = str(result.get("status") or "").lower()
                if downstream_status in {"error", "failed", "failure", "unavailable"}:
                    raise _DownstreamStatusError(
                        str(result.get("detail") or result.get("status"))
                    )
                if phase in {"replay_incident", "trigger_incident"} and not (
                    downstream_status == "ok"
                    or any(key in result for key in ("created", "skipped", "evaluation_id"))
                ):
                    raise _DownstreamStatusError(
                        "incident trigger response omitted delivery acknowledgement"
                    )
            attempts.append(
                {
                    "attempt": attempt,
                    "phase": phase,
                    "identity": identity,
                    "status": "ok",
                    "at": _utc_now(),
                }
            )
            return result, attempts, errors
        except Exception as exc:
            error = _error_record(exc, attempt=attempt, phase=phase, identity=identity)
            errors.append(error)
            attempts.append(
                {
                    "attempt": attempt,
                    "phase": phase,
                    "identity": identity,
                    "status": "error",
                    "at": error["at"],
                }
            )
            if attempt < max_attempts and retry_backoff_seconds:
                sleep_fn(retry_backoff_seconds)
    return None, attempts, errors


def run_tick(
    *,
    incidents_url: str,
    reconciliation_url: str,
    timeout_seconds: float = 30.0,
    max_attempts: int = 1,
    retry_backoff_seconds: float = 0.0,
    state: IncidentListenerState | None = None,
    state_path: str | Path | None = None,
    sleep_fn: SleepFn = time.sleep,
) -> dict[str, Any]:
    """Replay durable failures, fetch current incidents, and report controller truth."""
    if not incidents_url.strip() or not reconciliation_url.strip():
        raise ValueError("incidents_url and reconciliation_url must not be empty")
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if not math.isfinite(retry_backoff_seconds) or retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be a finite number >= 0")
    if state is not None and state_path is not None:
        raise ValueError("pass state or state_path, not both")

    listener_state = state or IncidentListenerState(state_path)
    if listener_state.load_error:
        listener_state.reload()
    if listener_state.load_error:
        observed_at = _utc_now()
        backlog_count, oldest_age = listener_state.backlog_metrics(now=observed_at)
        error = {
            "attempt": 0,
            "phase": "load_state",
            "error_type": "StateLoadError",
            "detail": listener_state.load_error,
            "at": observed_at,
        }
        return {
            "status": "error",
            "controller_status": "unhealthy",
            "attempt_count": 0,
            "error_count": 1,
            "attempts": [],
            "errors": [error],
            "fetched_incident_count": 0,
            "triggered_incident_count": 0,
            "replay_attempted_count": 0,
            "replayed_incident_count": 0,
            "backlog_count": backlog_count,
            "oldest_backlog_age_seconds": oldest_age,
            "last_success_at": listener_state.last_success_at,
            "last_failure_at": observed_at,
            "state_path": str(listener_state.path) if listener_state.path else None,
            "results": [],
        }

    attempts: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    processed_identities: set[str] = set()
    replay_attempted = 0
    replayed = 0
    delivery_failures = 0
    persistence_failed = False

    # Replay persisted work first.  Current fetch deduplication below prevents a
    # still-open incident from being delivered twice in the same tick.
    initial_backlog = sorted(
        listener_state.backlog.items(),
        key=lambda item: str(item[1].get("first_failed_at") or ""),
    )
    for identity, entry in initial_backlog:
        incident = dict(entry["incident"])
        processed_identities.add(identity)
        replay_attempted += 1
        result, op_attempts, op_errors = _retry_operation(
            lambda incident=incident: post_incident_trigger(
                reconciliation_url=reconciliation_url,
                incident=incident,
                timeout_seconds=timeout_seconds,
            ),
            phase="replay_incident",
            identity=identity,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
            sleep_fn=sleep_fn,
        )
        attempts.extend(op_attempts)
        errors.extend(op_errors)
        if result is not None:
            try:
                listener_state.remove_delivery(identity)
                replayed += 1
                results.append(
                    {
                        "incident_id": identity,
                        "identity": identity,
                        "replayed": True,
                        "result": result,
                    }
                )
            except Exception as exc:
                persistence_failed = True
                errors.append(
                    _error_record(exc, attempt=0, phase="persist_state", identity=identity)
                )
        else:
            delivery_failures += 1
            last_error = (
                op_errors[-1]
                if op_errors
                else {"detail": "delivery failed", "at": _utc_now()}
            )
            try:
                listener_state.record_delivery_failure(
                    identity=identity,
                    incident=incident,
                    error=str(last_error["detail"]),
                    attempt_count=len(op_attempts),
                    failed_at=str(last_error["at"]),
                )
            except Exception as exc:
                persistence_failed = True
                errors.append(
                    _error_record(exc, attempt=0, phase="persist_state", identity=identity)
                )

    incidents_result, fetch_attempts, fetch_errors = _retry_operation(
        lambda: fetch_open_incidents(
            incidents_url=incidents_url,
            timeout_seconds=timeout_seconds,
        ),
        phase="fetch_incidents",
        identity=None,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        sleep_fn=sleep_fn,
    )
    attempts.extend(fetch_attempts)
    errors.extend(fetch_errors)
    fetch_failed = incidents_result is None
    incidents = incidents_result if isinstance(incidents_result, list) else []

    deduplicated = 0
    for incident in incidents:
        identity = _incident_identity(incident)
        if identity in processed_identities:
            deduplicated += 1
            continue
        processed_identities.add(identity)
        result, op_attempts, op_errors = _retry_operation(
            lambda incident=incident: post_incident_trigger(
                reconciliation_url=reconciliation_url,
                incident=incident,
                timeout_seconds=timeout_seconds,
            ),
            phase="trigger_incident",
            identity=identity,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
            sleep_fn=sleep_fn,
        )
        attempts.extend(op_attempts)
        errors.extend(op_errors)
        if result is not None:
            incident_id = str(incident.get("incident_id") or incident.get("id") or "")
            results.append(
                {
                    "incident_id": incident_id,
                    "identity": identity,
                    "replayed": False,
                    "result": result,
                }
            )
        else:
            delivery_failures += 1
            last_error = (
                op_errors[-1]
                if op_errors
                else {"detail": "delivery failed", "at": _utc_now()}
            )
            try:
                listener_state.record_delivery_failure(
                    identity=identity,
                    incident=incident,
                    error=str(last_error["detail"]),
                    attempt_count=len(op_attempts),
                    failed_at=str(last_error["at"]),
                )
            except Exception as exc:
                persistence_failed = True
                errors.append(
                    _error_record(exc, attempt=0, phase="persist_state", identity=identity)
                )

    observed_at = _utc_now()
    backlog_count, oldest_age = listener_state.backlog_metrics(now=observed_at)
    terminal_failure = fetch_failed or persistence_failed
    controller_status = (
        "unhealthy"
        if terminal_failure
        else "degraded"
        if delivery_failures or backlog_count
        else "healthy"
    )
    try:
        listener_state.record_tick(
            successful=controller_status == "healthy",
            at=observed_at,
            error=(errors[-1]["detail"] if errors else None),
        )
    except Exception as exc:
        persistence_failed = True
        controller_status = "unhealthy"
        errors.append(_error_record(exc, attempt=0, phase="persist_state"))

    return {
        "status": (
            "ok"
            if controller_status == "healthy"
            else "partial_error"
            if controller_status == "degraded"
            else "error"
        ),
        "controller_status": controller_status,
        "attempt_count": len(attempts),
        "error_count": len(errors),
        "attempts": attempts,
        "errors": errors,
        "fetched_incident_count": len(incidents),
        "triggered_incident_count": len(results),
        "replay_attempted_count": replay_attempted,
        "replayed_incident_count": replayed,
        "deduplicated_incident_count": deduplicated,
        "backlog_count": backlog_count,
        "oldest_backlog_age_seconds": oldest_age,
        "last_success_at": listener_state.last_success_at,
        "last_failure_at": listener_state.last_failure_at,
        "state_path": str(listener_state.path) if listener_state.path else None,
        "retry_policy": {
            "max_attempts": max_attempts,
            "retry_backoff_seconds": retry_backoff_seconds,
        },
        "results": results,
    }


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
            "RECONCILIATION_DRIFT_INCIDENT_LISTENER_INTERVAL_SECONDS",
            15.0,
            minimum=0.001,
        )
        max_age_seconds = _env_float(
            "RECONCILIATION_DRIFT_INCIDENT_LISTENER_HEALTH_MAX_AGE_SECONDS",
            300.0,
            minimum=0.001,
        )
    except (TypeError, ValueError):
        return 1
    return check_worker_health(
        health_file=os.getenv(
            "RECONCILIATION_DRIFT_INCIDENT_LISTENER_HEALTH_FILE",
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
    health_file = os.getenv(
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_HEALTH_FILE",
        "",
    )
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
        incidents_url = os.getenv("PANTHEON_INCIDENTS_API_URL", "http://incidents:8090").strip()
        reconciliation_url = os.getenv(
            "RECONCILIATION_DRIFT_URL", "http://reconciliation-drift-svc:8102"
        ).strip()
        state_path = os.getenv(
            "RECONCILIATION_DRIFT_INCIDENT_LISTENER_STATE_PATH", DEFAULT_STATE_PATH
        ).strip()
        if not incidents_url or not reconciliation_url or not state_path:
            raise ValueError("incident listener URLs and state path must not be empty")
        interval_seconds = _env_int(
            "RECONCILIATION_DRIFT_INCIDENT_LISTENER_INTERVAL_SECONDS", 15, minimum=1
        )
        max_ticks = _env_int(
            "RECONCILIATION_DRIFT_INCIDENT_LISTENER_MAX_TICKS", 0, minimum=0
        )
        max_attempts = _env_int(
            "RECONCILIATION_DRIFT_INCIDENT_LISTENER_MAX_ATTEMPTS", 3, minimum=1
        )
        retry_backoff_seconds = _env_float(
            "RECONCILIATION_DRIFT_INCIDENT_LISTENER_RETRY_BACKOFF_SECONDS", 1.0, minimum=0.0
        )
    except (TypeError, ValueError) as exc:
        print(json.dumps(_configuration_error(exc), sort_keys=True), flush=True)
        return 2

    state = IncidentListenerState(state_path)
    tick = 0
    while True:
        tick += 1
        try:
            result = run_tick(
                incidents_url=incidents_url,
                reconciliation_url=reconciliation_url,
                max_attempts=max_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
                state=state,
            )
        except Exception as exc:  # operational faults must not stop the controller
            error = _error_record(exc, attempt=1, phase="worker_tick")
            try:
                state.record_tick(successful=False, at=error["at"], error=error["detail"])
            except Exception:
                pass
            backlog_count, oldest_age = state.backlog_metrics(now=error["at"])
            result = {
                "status": "error",
                "controller_status": "unhealthy",
                "attempt_count": 1,
                "error_count": 1,
                "attempts": [{"attempt": 1, "status": "error", "at": error["at"]}],
                "errors": [error],
                "backlog_count": backlog_count,
                "oldest_backlog_age_seconds": oldest_age,
                "last_success_at": state.last_success_at,
                "last_failure_at": state.last_failure_at,
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
            "usage: python services/reconciliation-drift/incident_listener.py "
            "[healthcheck]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(main())
