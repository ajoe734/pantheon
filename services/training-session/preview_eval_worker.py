"""Async preview/eval worker for trainer teaching sessions.

The training-session API owns the durable preview job queue.  This worker is a
small supervised poller that claims queued jobs by invoking the service's
``/api/training/preview-jobs/{job_id}/run`` endpoint.  Duplicate ticks are
idempotent because completed jobs are replayed by the API without creating a
second preview result event.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json_response(request: urllib.request.Request, timeout_seconds: float) -> Any:
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def fetch_queued_jobs(
    *,
    api_url: str,
    limit: int,
    timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"status": "queued", "limit": limit})
    request = urllib.request.Request(
        api_url.rstrip("/") + f"/api/training/preview-jobs?{query}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    payload = _read_json_response(request, timeout_seconds)
    return payload if isinstance(payload, list) else []


def run_job(
    *,
    api_url: str,
    job_id: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    payload = json.dumps({"run_at": _utc_now()}).encode("utf-8")
    request = urllib.request.Request(
        api_url.rstrip("/") + f"/api/training/preview-jobs/{job_id}/run",
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    response = _read_json_response(request, timeout_seconds)
    return response if isinstance(response, dict) else {}


def run_tick(
    *,
    api_url: str,
    limit: int,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    jobs = fetch_queued_jobs(api_url=api_url, limit=limit, timeout_seconds=timeout_seconds)
    completed = 0
    replayed = 0
    failed = 0
    errors: list[str] = []
    job_ids: list[str] = []

    for job in jobs:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            failed += 1
            errors.append("queued preview job missing job_id")
            continue
        job_ids.append(job_id)
        try:
            result = run_job(api_url=api_url, job_id=job_id, timeout_seconds=timeout_seconds)
        except urllib.error.HTTPError as exc:
            failed += 1
            detail = exc.read().decode("utf-8", errors="replace")
            errors.append(f"job_id={job_id} http_error={exc.code} {detail}")
            continue
        except urllib.error.URLError as exc:
            failed += 1
            errors.append(f"job_id={job_id} url_error={exc.reason}")
            continue

        if result.get("replayed"):
            replayed += 1
        if result.get("status") == "completed":
            completed += 1
        else:
            failed += 1
            errors.append(f"job_id={job_id} unexpected_status={result.get('status')!r}")

    return {
        "jobs_found": len(jobs),
        "job_ids": job_ids,
        "completed": completed,
        "replayed": replayed,
        "failed": failed,
        "errors": errors,
    }


def _write_alive(path: str) -> None:
    if not path:
        return
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_utc_now(), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    api_url = os.getenv("TRAINING_SESSION_API_URL", "http://training-session-svc:8099")
    interval_seconds = _env_int("TRAINING_SESSION_PREVIEW_WORKER_INTERVAL_SECONDS", 30, minimum=1)
    max_ticks = _env_int("TRAINING_SESSION_PREVIEW_WORKER_MAX_TICKS", 0, minimum=0)
    batch_limit = _env_int("TRAINING_SESSION_PREVIEW_WORKER_BATCH_LIMIT", 10, minimum=1)
    timeout_seconds = float(os.getenv("TRAINING_SESSION_PREVIEW_WORKER_TIMEOUT_SECONDS", "30"))
    alive_path = os.getenv("TRAINING_SESSION_PREVIEW_WORKER_ALIVE_PATH", "")

    tick = 0
    while True:
        tick += 1
        try:
            result = run_tick(api_url=api_url, limit=batch_limit, timeout_seconds=timeout_seconds)
            print(json.dumps({"tick": tick, "result": result}, sort_keys=True), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps({"tick": tick, "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True),
                flush=True,
            )
        _write_alive(alive_path)
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke worker.
    raise SystemExit(main())
