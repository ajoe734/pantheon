"""Agora Dataset Handoff Drainer worker for policy-learning.

Durable drainer worker that claims unacknowledged dataset handoffs from Agora BFF,
posts them to policy-learning's /api/policy-learning/agora-handoff endpoint,
and acknowledges each handoff upon successful ingestion.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.worker_health import healthcheck as check_worker_health, write_health

DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_BATCH_SIZE = 25
DEFAULT_TIMEOUT_SECONDS = 30.0

AGORA_BFF_URL_ENV = "AGORA_BFF_URL"
POLICY_LEARNING_API_URL_ENV = "POLICY_LEARNING_API_URL"
SERVICE_TOKEN_ENV = "POLICY_LEARNING_SERVICE_TOKEN"
API_TOKEN_ENV = "POLICY_LEARNING_API_TOKEN"
TENANT_ENV = "POLICY_LEARNING_AGORA_TENANT_ID"

_WORKER_NAME = "policy-learning-agora-handoff-drainer"
_logger = logging.getLogger("policy-learning.agora-handoff-drainer")


class DrainerConfigurationError(RuntimeError):
    """The drainer worker is missing required environment configuration."""


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
    """Stable worker identity."""
    configured = _env_text("AGORA_HANDOFF_DRAINER_WORKER_ID")
    if configured:
        return configured
    return f"agora-drainer-{socket.gethostname()}-{os.getpid()}"


def caller_token() -> str:
    """Authentication token for requests."""
    return _env_text(SERVICE_TOKEN_ENV) or _env_text(API_TOKEN_ENV)


def require_drainer_configuration() -> tuple[str, str, str, str]:
    """Validate and return (agora_url, policy_learning_url, token, tenant_id)."""
    agora_url = _env_text(AGORA_BFF_URL_ENV, "http://control-plane-bff-svc:8000")
    policy_learning_url = _env_text(POLICY_LEARNING_API_URL_ENV, "http://policy-learning-svc:8100")
    token = caller_token()
    tenant_id = _env_text(TENANT_ENV)

    missing = []
    if not token:
        missing.append(f"{SERVICE_TOKEN_ENV} (or {API_TOKEN_ENV})")
    if not tenant_id:
        missing.append(TENANT_ENV)

    if missing:
        raise DrainerConfigurationError(
            f"Agora handoff drainer misconfigured; set {', '.join(missing)}"
        )
    return agora_url, policy_learning_url, token, tenant_id


def request_headers(tenant_id: str, token: str) -> dict[str, str]:
    """Headers for API requests."""
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    return headers


def _http_request(
    url: str,
    method: str = "GET",
    body: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=payload,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            resp_data = response.read().decode("utf-8")
            return json.loads(resp_data) if resp_data else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
        except Exception:
            parsed = detail
        return {"status": "error", "code": exc.code, "detail": parsed}
    except urllib.error.URLError as exc:
        return {"status": "error", "detail": str(exc.reason)}


def fetch_pending_handoffs(
    agora_url: str,
    tenant_id: str,
    token: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Fetch generated dataset handoffs from Agora BFF."""
    url = f"{agora_url.rstrip('/')}/bff/agora/dataset-worker/handoffs"
    headers = request_headers(tenant_id, token)
    res = _http_request(url, method="GET", headers=headers, timeout_seconds=timeout_seconds)
    if res.get("status") == "error":
        _logger.warning("Failed to fetch Agora handoffs: %s", res)
        return []
    items = res.get("items") or []
    return [item for item in items if not item.get("acknowledged")]


def post_handoff_to_policy_learning(
    policy_learning_url: str,
    handoff: dict[str, Any],
    tenant_id: str,
    token: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Post handoff payload to policy-learning's /api/policy-learning/agora-handoff."""
    url = f"{policy_learning_url.rstrip('/')}/api/policy-learning/agora-handoff"
    headers = request_headers(tenant_id, token)

    handoff_id = str(handoff.get("handoff_id") or "")
    dataset_version = handoff.get("dataset_version") or handoff.get("record")
    dataset_ref = handoff.get("dataset_ref")

    body: dict[str, Any] = {
        "handoff_id": handoff_id,
        "eval_type": handoff.get("eval_type", "shadow"),
        "actor_id": worker_id(),
        "tenant_id": tenant_id,
        "process_immediately": True,
    }
    if dataset_version:
        body["dataset_version"] = dataset_version
    elif dataset_ref:
        body["dataset_ref"] = dataset_ref
    elif handoff.get("dataset_version_id"):
        body["dataset_ref"] = {
            "dataset_version_id": handoff["dataset_version_id"],
            "tenant_id": tenant_id,
        }

    return _http_request(url, method="POST", body=body, headers=headers, timeout_seconds=timeout_seconds)


def acknowledge_agora_handoff(
    agora_url: str,
    handoff_id: str,
    dataset_version_id: str,
    candidate_id: str,
    tenant_id: str,
    token: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Acknowledge handoff on Agora BFF."""
    url = f"{agora_url.rstrip('/')}/bff/agora/dataset-worker/handoffs/{handoff_id}/ack"
    headers = request_headers(tenant_id, token)
    idempotency_key = f"ack-{handoff_id}"
    headers["Idempotency-Key"] = idempotency_key

    body = {
        "acknowledgement_id": f"ack-pl-{candidate_id}",
        "dataset_version_id": dataset_version_id,
        "downstream_ref": {
            "service": "policy-learning",
            "candidate_id": candidate_id,
            "idempotency_key": idempotency_key,
        },
    }
    return _http_request(url, method="POST", body=body, headers=headers, timeout_seconds=timeout_seconds)


def process_drainer_cycle(
    agora_url: str,
    policy_learning_url: str,
    tenant_id: str,
    token: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """One drainer cycle: fetch pending -> ingest to policy-learning -> ack on Agora."""
    pending = fetch_pending_handoffs(agora_url, tenant_id, token, timeout_seconds=timeout_seconds)
    if not pending:
        return {"status": "ok", "processed_count": 0, "acked_count": 0}

    batch = pending[:batch_size]
    processed_count = 0
    acked_count = 0
    errors: list[dict[str, Any]] = []

    for handoff in batch:
        handoff_id = str(handoff.get("handoff_id") or "")
        ds_ver_id = str(handoff.get("dataset_version_id") or "")

        ingest_res = post_handoff_to_policy_learning(
            policy_learning_url, handoff, tenant_id, token, timeout_seconds=timeout_seconds
        )
        if ingest_res.get("status") == "error":
            errors.append({"handoff_id": handoff_id, "step": "ingest", "error": ingest_res})
            continue

        processed_count += 1
        candidate_id = str(ingest_res.get("candidate_id") or "")

        ack_res = acknowledge_agora_handoff(
            agora_url,
            handoff_id=handoff_id,
            dataset_version_id=ds_ver_id or str(ingest_res.get("dataset_version_id") or ""),
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            token=token,
            timeout_seconds=timeout_seconds,
        )
        if ack_res.get("status") == "error":
            errors.append({"handoff_id": handoff_id, "step": "ack", "error": ack_res})
        else:
            acked_count += 1

    return {
        "status": "ok" if not errors else "degraded",
        "processed_count": processed_count,
        "acked_count": acked_count,
        "errors": errors,
    }


def healthcheck() -> int:
    """Worker healthcheck for sidecar / container check."""
    return check_worker_health(
        health_file=os.getenv("AGORA_HANDOFF_DRAINER_HEALTH_FILE", ""),
        interval_seconds=_env_int("AGORA_HANDOFF_DRAINER_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS, minimum=1),
        worker_name=_WORKER_NAME,
        expected={"agora_drainer": "active"},
    )


def main() -> int:
    poll_interval = _env_int("AGORA_HANDOFF_DRAINER_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS, minimum=1)
    max_ticks = _env_int("AGORA_HANDOFF_DRAINER_MAX_TICKS", 0, minimum=0)
    batch_size = _env_int("AGORA_HANDOFF_DRAINER_BATCH_SIZE", DEFAULT_BATCH_SIZE, minimum=1)
    health_file = _env_text("AGORA_HANDOFF_DRAINER_HEALTH_FILE")

    try:
        agora_url, policy_learning_url, token, tenant_id = require_drainer_configuration()
    except DrainerConfigurationError as exc:
        print(json.dumps({"status": "error", "reason": "drainer_unconfigured", "detail": str(exc)}), flush=True)
        return 2

    health: dict[str, Any] = {
        "worker_name": _WORKER_NAME,
        "status": "starting",
        "ticks": 0,
        "last_tick_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "tenant_id": tenant_id,
        "agora_drainer": "active",
    }
    write_health(health_file, health)

    tick = 0
    while True:
        tick += 1
        health["ticks"] = tick
        health["last_tick_at"] = _utc_now()
        try:
            res = process_drainer_cycle(
                agora_url=agora_url,
                policy_learning_url=policy_learning_url,
                tenant_id=tenant_id,
                token=token,
                batch_size=batch_size,
            )
            if res.get("status") == "error" or res.get("errors"):
                health["status"] = "degraded"
                health["last_failure_at"] = health["last_tick_at"]
            else:
                health["status"] = "ok"
                health["last_success_at"] = health["last_tick_at"]
            print(json.dumps({"tick": tick, "result": res, "health": health}, sort_keys=True), flush=True)
        except Exception as exc:
            health["status"] = "degraded"
            health["last_failure_at"] = health["last_tick_at"]
            print(json.dumps({"tick": tick, "error": str(exc), "health": health}, sort_keys=True), flush=True)

        write_health(health_file, health)

        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(poll_interval)


if __name__ == "__main__":
    if sys.argv[1:] == ["healthcheck"]:
        raise SystemExit(healthcheck())
    if sys.argv[1:]:
        print("usage: python services/policy-learning/agora_handoff_drainer.py [healthcheck]", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main())
