"""Scheduled worker for human imitation and shadow evaluation ticks.

Each cycle does two things against the policy-learning service:

  1. ``shadow-eval-tick`` — discover tenant-scoped Agora DatasetVersions and
     propose ShadowImitationCandidate records for them.
  2. ``worker/process``  — claim a batch of that backlog under an expiring
     lease and run the shadow evaluation for the claimed candidates.

Duplicate ticks are safe: the tick id is derived from the schedule window, so a
restarted or doubled scheduler re-proposes the same window instead of creating
a second copy of the work, and backlog claims are leased so two workers never
process the same candidate concurrently.

Production training remains fail-closed; candidates require a separate
experiment approval and deployment gate before any training is activated.
There is no seed fallback: when the dataset authority is unavailable the tick
returns a truthful degradation record and this worker reports it as a failure.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_LEASE_SECONDS = 60
DEFAULT_BATCH_SIZE = 25

SERVICE_TOKEN_ENV = "POLICY_LEARNING_SERVICE_TOKEN"
API_TOKEN_ENV = "POLICY_LEARNING_API_TOKEN"
TENANT_ENV = "POLICY_LEARNING_AGORA_TENANT_ID"


class SchedulerConfigurationError(RuntimeError):
    """The sidecar is not configured to be a trusted, tenant-bound caller."""


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _env_text(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


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


def main() -> int:
    api_url = os.getenv("POLICY_LEARNING_API_URL", "http://policy-learning-svc:8100")
    interval_seconds = _env_int("SHADOW_EVAL_SCHEDULER_INTERVAL_SECONDS", 3600, minimum=1)
    max_ticks = _env_int("SHADOW_EVAL_SCHEDULER_MAX_TICKS", 0, minimum=0)
    eval_type = os.getenv("SHADOW_EVAL_TYPE", "shadow").strip() or "shadow"
    max_datasets_raw = os.getenv("SHADOW_EVAL_MAX_DATASETS", "").strip()
    max_datasets = int(max_datasets_raw) if max_datasets_raw else None
    try:
        _, tenant_id = require_caller_configuration()
    except SchedulerConfigurationError as exc:
        print(
            json.dumps({"status": "error", "reason": "scheduler_unconfigured", "detail": str(exc)}),
            flush=True,
        )
        return 2
    user_id = _env_text("POLICY_LEARNING_AGORA_USER_ID")
    batch_size = _env_int("SHADOW_EVAL_WORKER_BATCH_SIZE", DEFAULT_BATCH_SIZE, minimum=1)
    lease_seconds = _env_int("SHADOW_EVAL_WORKER_LEASE_SECONDS", DEFAULT_LEASE_SECONDS, minimum=1)
    worker = worker_id()

    recovery = run_recovery(api_url=api_url, worker=worker, tenant_id=tenant_id)
    print(json.dumps({"startup_recovery": recovery, "worker_id": worker}, sort_keys=True), flush=True)

    tick = 0
    last_success: dict[str, Any] | None = None
    last_failure: dict[str, Any] | None = None

    while True:
        tick += 1
        try:
            result = run_tick(
                api_url=api_url,
                tick_id=window_tick_id(
                    eval_type=eval_type,
                    interval_seconds=interval_seconds,
                    now=time.time(),
                ),
                eval_type=eval_type,
                max_datasets=max_datasets,
                tenant_id=tenant_id,
                user_id=user_id,
            )
            if result.get("status") == "error":
                last_failure = result
                cycle: dict[str, Any] = {"status": "skipped", "reason": "tick_failed"}
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
            cycle = {"status": "skipped", "reason": "tick_exception"}

        metrics = {
            "tick": tick,
            "worker_id": worker,
            "result": result,
            "cycle": cycle,
            "last_success_candidate_count": (last_success or {}).get("candidate_count"),
            "last_failure_detail": (last_failure or {}).get("detail"),
            "last_failure_reason": (last_failure or {}).get("reason"),
        }
        print(json.dumps(metrics, sort_keys=True, default=str), flush=True)

        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
