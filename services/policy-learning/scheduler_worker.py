"""Scheduled worker for human imitation and shadow evaluation ticks.

Each cycle does two things against the policy-learning service:

  1. **durable Agora handoff intake** — claim unacknowledged dataset handoffs
     from the Agora BFF, hand each one to policy-learning's
     ``/api/policy-learning/agora-handoff`` endpoint, and acknowledge it once
     ingestion is durable. This is the sole production intake path: the
     direct Agora database scanner (``shadow-eval-tick`` discovery through
     ``AgoraDatasetAuthority``) is no longer invoked from this scheduled
     loop. ``run_tick``/``window_tick_id`` remain for direct/manual use of
     the scanner-backed discovery endpoint; production no longer calls them.
  2. ``worker/process``  — claim a batch of the resulting backlog under an
     expiring lease and run the shadow evaluation for the claimed candidates.

A handoff already acknowledged on the Agora BFF is not returned by a later
poll, so a restarted or doubled scheduler claims and acknowledges each
handoff exactly once; backlog claims are separately leased so two workers
never process the same candidate concurrently.

Production training remains fail-closed; candidates require a separate
experiment approval and deployment gate before any training is activated.
There is no seed fallback: when the durable handoff intake cannot reach its
dependencies it reports zero processed and this worker's health degrades
only if the backlog claim cycle itself fails.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.worker_health import healthcheck as check_worker_health
from services.worker_health import write_health

# ``agora_handoff_drainer`` is a sibling flat module inside this hyphenated
# service directory (not importable as a dotted ``services.policy_learning``
# package). Resolve this file's own directory so the import works regardless
# of how this module is loaded -- as ``__main__``, via a dotted import, or
# via ``importlib.util.spec_from_file_location`` in tests, none of which
# reliably put this directory on ``sys.path`` for us.
_SERVICE_DIR = str(Path(__file__).resolve().parent)
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

import agora_handoff_drainer


DEFAULT_LEASE_SECONDS = 60
DEFAULT_BATCH_SIZE = 25

SERVICE_TOKEN_ENV = "POLICY_LEARNING_SERVICE_TOKEN"
API_TOKEN_ENV = "POLICY_LEARNING_API_TOKEN"
TENANT_ENV = "POLICY_LEARNING_AGORA_TENANT_ID"
AGORA_BFF_URL_ENV = agora_handoff_drainer.AGORA_BFF_URL_ENV
INTAKE_BATCH_SIZE_ENV = "SHADOW_EVAL_INTAKE_BATCH_SIZE"
_WORKER_NAME = "policy-learning-shadow-eval-scheduler"


class SchedulerConfigurationError(RuntimeError):
    """The sidecar is not configured to be a trusted, tenant-bound caller."""


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _env_text(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def worker_id() -> str:
    """Stable-per-process worker identity used as the lease owner."""

    configured = _env_text("POLICY_LEARNING_WORKER_ID")
    if configured:
        return configured
    return f"shadow-eval-{socket.gethostname()}-{os.getpid()}"


def window_tick_id(*, eval_type: str, interval_seconds: int, now: float) -> str:
    """Derive a tick id from the schedule window.

    Two schedulers firing inside the same window, or one scheduler restarting
    mid-window, produce the same tick id — so the service's duplicate-tick
    suppression absorbs the repeat instead of doubling the backlog.
    """

    span = max(1, int(interval_seconds))
    window = int(now) // span
    return f"shadow-tick-{eval_type}-{span}s-{window}"


def caller_token() -> str:
    """The credential this sidecar presents to the policy-learning API."""

    return _env_text(SERVICE_TOKEN_ENV) or _env_text(API_TOKEN_ENV)


def require_caller_configuration() -> tuple[str, str]:
    """Return ``(token, tenant_id)``, refusing to run without both.

    A sidecar with no credential gets 401 on every tick and one with no tenant
    scope gets 400 — both silently, forever, in a scheduling loop nobody
    watches.  Compose wires these from the same variables the API authorizes,
    so a missing value means the deployment is misconfigured and the worker
    fails closed at start-up instead of emitting unauthenticated ticks.
    """

    token = caller_token()
    tenant_id = _env_text(TENANT_ENV)
    missing = [
        name
        for name, value in ((f"{SERVICE_TOKEN_ENV} (or {API_TOKEN_ENV})", token), (TENANT_ENV, tenant_id))
        if not value
    ]
    if missing:
        raise SchedulerConfigurationError(
            "The shadow-eval scheduler is not an authorized tenant-bound caller; "
            f"set {', '.join(missing)}"
        )
    return token, tenant_id


def request_headers(tenant_id: str) -> dict[str, str]:
    """Headers that make this sidecar a trusted, tenant-bound caller.

    The imitation-loop routes require an authenticated caller and take the
    tenant from ``X-Tenant-Id``, not from the request body, so a scheduler
    without a configured token is refused rather than silently scheduling into
    an unauthenticated scope.
    """

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    token = caller_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    return headers


def _post(
    url: str,
    body: dict[str, Any],
    *,
    timeout_seconds: float,
    tenant_id: str = "",
) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers=request_headers(tenant_id),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            response_body = response.read().decode("utf-8")
        return json.loads(response_body) if response_body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        parsed: Any = detail
        try:
            parsed = json.loads(detail).get("detail", detail)
        except ValueError:
            pass
        result: dict[str, Any] = {"status": "error", "code": exc.code, "detail": parsed}
        # 503 from the tick is the dataset-authority degradation contract; keep
        # the machine reason so the operator sees why nothing was scheduled.
        if isinstance(parsed, dict) and parsed.get("reason"):
            result["reason"] = parsed["reason"]
            result["seed_fallback_used"] = parsed.get("seed_fallback_used", False)
        return result
    except urllib.error.URLError as exc:
        return {"status": "error", "detail": str(exc.reason)}


def run_tick(
    *,
    api_url: str,
    tick_id: str | None = None,
    eval_type: str = "shadow",
    dataset_refs: list[dict[str, Any]] | None = None,
    max_datasets: int | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """POST /api/policy-learning/shadow-eval-tick and return the response."""
    body: dict[str, Any] = {"eval_type": eval_type}
    if tick_id:
        body["tick_id"] = tick_id
    if dataset_refs is not None:
        body["dataset_refs"] = dataset_refs
    if max_datasets is not None:
        body["max_datasets"] = max_datasets
    if tenant_id:
        body["tenant_id"] = tenant_id
    if user_id:
        body["user_id"] = user_id
    return _post(
        api_url.rstrip("/") + "/api/policy-learning/shadow-eval-tick",
        body,
        timeout_seconds=timeout_seconds,
        tenant_id=tenant_id or "",
    )


def run_claim_cycle(
    *,
    api_url: str,
    worker: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    tenant_id: str | None = None,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Claim and process a leased batch of the candidate backlog."""

    body: dict[str, Any] = {
        "worker_id": worker,
        "batch_size": batch_size,
        "lease_seconds": lease_seconds,
    }
    if tenant_id:
        body["tenant_id"] = tenant_id
    return _post(
        api_url.rstrip("/") + "/api/policy-learning/worker/process",
        body,
        timeout_seconds=timeout_seconds,
        tenant_id=tenant_id or "",
    )


def run_recovery(
    *,
    api_url: str,
    worker: str,
    tenant_id: str | None = None,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Return leases orphaned by a crashed worker to the backlog.

    Run once at start-up: a worker that died mid-lease left its candidates in
    ``claimed``, and they only become claimable again once the lease expires or
    is explicitly released.
    """

    body: dict[str, Any] = {"worker_id": worker}
    if tenant_id:
        body["tenant_id"] = tenant_id
    return _post(
        api_url.rstrip("/") + "/api/policy-learning/worker/restart",
        body,
        timeout_seconds=timeout_seconds,
        tenant_id=tenant_id or "",
    )


def run_intake_cycle(
    *,
    agora_url: str,
    policy_learning_url: str,
    tenant_id: str,
    token: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Claim and acknowledge the durable Agora dataset handoff, once.

    This is the sole production admission path: it drains whatever the Agora
    BFF currently reports as unacknowledged, posts each one to
    policy-learning's ``/api/policy-learning/agora-handoff`` endpoint, and
    acknowledges it on the BFF only after that ingestion durably succeeds. A
    handoff the BFF already has recorded as acknowledged is not returned by a
    later poll, so calling this once per scheduled tick claims and
    acknowledges each handoff exactly once rather than repeatedly.

    A short ``timeout_seconds`` default keeps one unreachable-BFF tick from
    stalling the scheduler loop; an unreachable BFF degrades to zero
    processed for this tick rather than raising, matching
    ``agora_handoff_drainer.process_drainer_cycle``'s own contract.
    """

    return agora_handoff_drainer.process_drainer_cycle(
        agora_url=agora_url,
        policy_learning_url=policy_learning_url,
        tenant_id=tenant_id,
        token=token,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
    )


def healthcheck() -> int:
    """Validate a recent successful shadow-evaluation tick."""

    return check_worker_health(
        health_file=os.getenv("SHADOW_EVAL_SCHEDULER_HEALTH_FILE", ""),
        interval_seconds=_env_int(
            "SHADOW_EVAL_SCHEDULER_INTERVAL_SECONDS",
            3600,
            minimum=1,
        ),
        worker_name=_WORKER_NAME,
        expected={"production_training": "fail_closed"},
    )


def main() -> int:
    api_url = os.getenv("POLICY_LEARNING_API_URL", "http://policy-learning-svc:8100")
    agora_url = _env_text(AGORA_BFF_URL_ENV, agora_handoff_drainer.DEFAULT_AGORA_BFF_URL)
    interval_seconds = _env_int("SHADOW_EVAL_SCHEDULER_INTERVAL_SECONDS", 3600, minimum=1)
    max_ticks = _env_int("SHADOW_EVAL_SCHEDULER_MAX_TICKS", 0, minimum=0)
    try:
        token, tenant_id = require_caller_configuration()
    except SchedulerConfigurationError as exc:
        print(
            json.dumps({"status": "error", "reason": "scheduler_unconfigured", "detail": str(exc)}),
            flush=True,
        )
        return 2
    batch_size = _env_int("SHADOW_EVAL_WORKER_BATCH_SIZE", DEFAULT_BATCH_SIZE, minimum=1)
    lease_seconds = _env_int("SHADOW_EVAL_WORKER_LEASE_SECONDS", DEFAULT_LEASE_SECONDS, minimum=1)
    intake_batch_size = _env_int(INTAKE_BATCH_SIZE_ENV, agora_handoff_drainer.DEFAULT_BATCH_SIZE, minimum=1)
    worker = worker_id()
    health_file = _env_text("SHADOW_EVAL_SCHEDULER_HEALTH_FILE")
    health: dict[str, Any] = {
        "worker_name": _WORKER_NAME,
        "status": "starting",
        "ticks": 0,
        "last_tick_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_reason": None,
        "tenant_id": tenant_id,
        "production_training": "fail_closed",
        "agora_intake": "durable_handoff",
    }
    write_health(health_file, health)

    recovery = run_recovery(api_url=api_url, worker=worker, tenant_id=tenant_id)
    print(json.dumps({"startup_recovery": recovery, "worker_id": worker}, sort_keys=True), flush=True)

    tick = 0
    last_success: dict[str, Any] | None = None
    last_failure: dict[str, Any] | None = None

    while True:
        tick += 1
        try:
            result = run_intake_cycle(
                agora_url=agora_url,
                policy_learning_url=api_url,
                tenant_id=tenant_id,
                token=token,
                batch_size=intake_batch_size,
            )
            if result.get("status") == "error":
                last_failure = result
                cycle: dict[str, Any] = {"status": "skipped", "reason": "intake_failed"}
            else:
                last_success = result
                cycle = run_claim_cycle(
                    api_url=api_url,
                    worker=worker,
                    batch_size=batch_size,
                    lease_seconds=lease_seconds,
                    tenant_id=tenant_id,
                )
        except Exception as exc:
            last_failure = {"status": "error", "detail": str(exc)}
            result = last_failure
            cycle = {"status": "skipped", "reason": "intake_exception"}

        tick_failed = result.get("status") == "error"
        cycle_failed = cycle.get("status") in {"error", "skipped", "degraded"}
        health["ticks"] = tick
        health["last_tick_at"] = _utc_now()
        if tick_failed or cycle_failed:
            health["status"] = "degraded"
            health["last_failure_at"] = health["last_tick_at"]
            health["last_failure_reason"] = (
                result.get("reason")
                or result.get("detail")
                or cycle.get("reason")
                or cycle.get("detail")
                or "shadow evaluation cycle failed"
            )
        else:
            health["status"] = "ok"
            health["last_success_at"] = health["last_tick_at"]
            health["last_failure_reason"] = None
        write_health(health_file, health)

        metrics = {
            "tick": tick,
            "worker_id": worker,
            "result": result,
            "cycle": cycle,
            "last_success_processed_count": (last_success or {}).get("processed_count"),
            "last_success_acked_count": (last_success or {}).get("acked_count"),
            "last_failure_detail": (last_failure or {}).get("detail"),
            "last_failure_reason": (last_failure or {}).get("reason"),
            "health": health,
        }
        print(json.dumps(metrics, sort_keys=True, default=str), flush=True)

        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover
    if sys.argv[1:] == ["healthcheck"]:
        raise SystemExit(healthcheck())
    if sys.argv[1:]:
        print(
            "usage: python services/policy-learning/scheduler_worker.py "
            "[healthcheck]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(main())
