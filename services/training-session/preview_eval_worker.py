"""Async preview/eval worker for trainer teaching sessions.

The training-session API owns the durable preview job queue.  This worker is a
small supervised poller that asks for claimable jobs and invokes the service's
``/api/training/preview-jobs/{job_id}/run`` endpoint.  The API owns leases,
reclaim, retry eligibility, and the trusted evaluation clock.  Duplicate ticks
are idempotent because completed jobs are replayed without creating a second
preview result event.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_ALIVE_PATH = "/data/training-session/preview-worker-alive"
TERMINAL_SESSION_STATUSES = {"completed", "committed", "discarded", "abandoned", "expired"}
LOOP_ID = "persona_teaching"


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


def _authority_headers() -> dict[str, str]:
    token = str(os.getenv("TRAINING_SESSION_WORKER_TOKEN") or "").strip()
    tenant_id = str(os.getenv("TRAINING_SESSION_TENANT_ID") or "").strip()
    actor_service = str(
        os.getenv("TRAINING_SESSION_WORKER_SERVICE_ID")
        or "training-session-preview-worker"
    ).strip()
    missing = [
        name
        for name, value in (
            ("TRAINING_SESSION_WORKER_TOKEN", token),
            ("TRAINING_SESSION_TENANT_ID", tenant_id),
            ("TRAINING_SESSION_WORKER_SERVICE_ID", actor_service),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "preview worker inbound authority is incomplete: "
            + ", ".join(missing)
        )
    return {
        "Authorization": token if token.startswith("Bearer ") else f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Pantheon-Service": actor_service,
    }


def fetch_claimable_jobs(
    *,
    api_url: str,
    limit: int,
    timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"status": "claimable", "limit": limit})
    request = urllib.request.Request(
        api_url.rstrip("/") + f"/api/training/preview-jobs?{query}",
        headers={"Accept": "application/json", **_authority_headers()},
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
    # The service owns the trusted evaluation clock.  Sending a worker clock
    # here would let client-controlled time influence dataset freshness.
    payload = b"{}"
    request = urllib.request.Request(
        api_url.rstrip("/") + f"/api/training/preview-jobs/{job_id}/run",
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            **_authority_headers(),
        },
        method="POST",
    )
    response = _read_json_response(request, timeout_seconds)
    return response if isinstance(response, dict) else {}


def complete_session(
    *,
    api_url: str,
    session_id: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    request = urllib.request.Request(
        api_url.rstrip("/") + f"/api/training/sessions/{session_id}/complete",
        data=b"{}",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            **_authority_headers(),
        },
        method="POST",
    )
    response = _read_json_response(request, timeout_seconds)
    return response if isinstance(response, dict) else {}


def fetch_session(
    *,
    api_url: str,
    session_id: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    request = urllib.request.Request(
        api_url.rstrip("/") + f"/api/training/sessions/{session_id}",
        headers={"Accept": "application/json", **_authority_headers()},
        method="GET",
    )
    response = _read_json_response(request, timeout_seconds)
    return response if isinstance(response, dict) else {}


def complete_and_read_terminal_session(
    *,
    api_url: str,
    job: dict[str, Any],
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Complete a successful evaluation and prove its persisted session readback."""

    session_id = str(job.get("session_id") or "").strip()
    if not session_id:
        raise RuntimeError("completed preview job missing session_id")

    complete_session(
        api_url=api_url,
        session_id=session_id,
        timeout_seconds=timeout_seconds,
    )
    session = fetch_session(
        api_url=api_url,
        session_id=session_id,
        timeout_seconds=timeout_seconds,
    )
    readback_id = str(session.get("session_id") or session.get("id") or "").strip()
    status = str(session.get("status") or "").strip().lower()
    ended_at = str(session.get("ended_at") or session.get("completed_at") or "").strip()
    if readback_id != session_id:
        raise RuntimeError(
            f"terminal session readback identity mismatch: expected={session_id!r} actual={readback_id!r}"
        )
    if status not in TERMINAL_SESSION_STATUSES or not ended_at:
        raise RuntimeError(
            f"session_id={session_id} did not reach persisted terminal state: "
            f"status={status!r} ended_at={ended_at!r}"
        )

    return {
        "session_id": session_id,
        "status": status,
        "ended_at": ended_at,
        "job_id": job.get("job_id"),
        "evaluation_proof_ref": job.get("evaluation_proof_ref"),
        "governance_gate_state": job.get("governance_gate_state"),
    }


def build_loop_writer() -> Any | None:
    """Best-effort GAP-F05 observation writer; the worker still runs without it.

    ``services/loop-control`` is the shared owner-observation store for all
    twelve canonical loops -- this must reuse it, not add a second Teaching
    controller service or state store.
    """

    dsn = str(os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        return None
    try:
        module = importlib.import_module("services.loop-control")
    except ModuleNotFoundError:
        return None
    return module.LoopControllerWriter(
        dsn,
        tenant_id=str(os.getenv("TRAINING_SESSION_TENANT_ID") or os.getenv("PANTHEON_TENANT_ID") or "default"),
        environment=str(os.getenv("PANTHEON_ENV") or "dev"),
        controller_id=str(
            os.getenv("PANTHEON_CONTROLLER_ID")
            or f"training-session-preview-eval-worker-{socket.gethostname()}-{os.getpid()}"
        ),
        controller_name=str(
            os.getenv("PANTHEON_CONTROLLER_NAME") or "training-session-preview-eval-worker"
        ),
        deployment_sha=str(os.getenv("GIT_SHA") or os.getenv("PANTHEON_DEPLOYMENT_SHA") or "unknown"),
    )


def _extract_consultation(result: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Read the optional consultation receipt off a completed preview job."""

    preview = result.get("preview") if isinstance(result.get("preview"), dict) else {}
    evaluation_result = (
        preview.get("evaluation_result") if isinstance(preview.get("evaluation_result"), dict) else {}
    )
    consult_request_id = evaluation_result.get("consult_request_id")
    consultation_receipt = evaluation_result.get("consultation_receipt")
    return (
        str(consult_request_id) if consult_request_id else None,
        consultation_receipt if isinstance(consultation_receipt, dict) else None,
    )


def _write_loop_heartbeat(loop_writer: Any, *, api_url: str) -> None:
    try:
        asyncio.run(
            loop_writer.record_heartbeat(
                LOOP_ID,
                desired_state_query="claimable preview/eval jobs",
                actual_state_query=(
                    api_url.rstrip("/") + "/api/training/preview-jobs?status=claimable"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: failed to write loop-control heartbeat: {exc}", file=sys.stderr)


def _write_loop_result(loop_writer: Any, result: dict[str, Any]) -> None:
    evidence_refs: list[str] = list(result.get("terminal_session_ids") or [])
    for consult_request_id in result.get("consult_request_ids") or []:
        ref = f"consult-request:{consult_request_id}"
        if ref not in evidence_refs:
            evidence_refs.append(ref)
    payload = {
        "jobs_found": result.get("jobs_found"),
        "job_ids": result.get("job_ids"),
        "terminal_sessions": result.get("terminal_sessions"),
    }
    try:
        if result.get("failed"):
            asyncio.run(
                loop_writer.record_failure(
                    LOOP_ID,
                    "; ".join(result.get("errors") or []) or "preview/eval tick reported failures",
                    evidence_refs=evidence_refs,
                    payload=payload,
                )
            )
        else:
            asyncio.run(
                loop_writer.record_success(
                    LOOP_ID,
                    summary=(
                        f"completed={result.get('completed')} "
                        f"terminal_sessions={len(result.get('terminal_session_ids') or [])}"
                    ),
                    evidence_refs=evidence_refs,
                    payload=payload,
                )
            )
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: failed to write loop-control result: {exc}", file=sys.stderr)


def run_tick(
    *,
    api_url: str,
    limit: int,
    timeout_seconds: float = 30.0,
    heartbeat: Callable[[], None] | None = None,
    loop_writer: Any | None = None,
) -> dict[str, Any]:
    if heartbeat:
        heartbeat()
    if loop_writer is not None:
        _write_loop_heartbeat(loop_writer, api_url=api_url)
    completed = 0
    replayed = 0
    reclaimed = 0
    retryable = 0
    failed = 0
    errors: list[str] = []
    job_ids: list[str] = []
    terminal_sessions: list[dict[str, Any]] = []
    consult_request_ids: list[str] = []

    try:
        jobs = fetch_claimable_jobs(api_url=api_url, limit=limit, timeout_seconds=timeout_seconds)
    except urllib.error.HTTPError as exc:
        failed += 1
        detail = exc.read().decode("utf-8", errors="replace")
        errors.append(f"fetch_claimable_jobs http_error={exc.code} {detail}")
        result = {
            "jobs_found": 0,
            "job_ids": [],
            "completed": 0,
            "replayed": 0,
            "reclaimed": 0,
            "retryable": 0,
            "failed": failed,
            "errors": errors,
            "terminal_session_ids": [],
            "terminal_sessions": [],
            "consult_request_ids": [],
        }
        if loop_writer is not None:
            _write_loop_result(loop_writer, result)
        return result
    except urllib.error.URLError as exc:
        failed += 1
        errors.append(f"fetch_claimable_jobs url_error={exc.reason}")
        result = {
            "jobs_found": 0,
            "job_ids": [],
            "completed": 0,
            "replayed": 0,
            "reclaimed": 0,
            "retryable": 0,
            "failed": failed,
            "errors": errors,
            "terminal_session_ids": [],
            "terminal_sessions": [],
            "consult_request_ids": [],
        }
        if loop_writer is not None:
            _write_loop_result(loop_writer, result)
        return result

    for job in jobs:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            failed += 1
            errors.append("claimable preview job missing job_id")
            if heartbeat:
                heartbeat()
            continue
        job_ids.append(job_id)
        try:
            result = run_job(api_url=api_url, job_id=job_id, timeout_seconds=timeout_seconds)
        except urllib.error.HTTPError as exc:
            failed += 1
            detail = exc.read().decode("utf-8", errors="replace")
            errors.append(f"job_id={job_id} http_error={exc.code} {detail}")
            if heartbeat:
                heartbeat()
            continue
        except urllib.error.URLError as exc:
            failed += 1
            errors.append(f"job_id={job_id} url_error={exc.reason}")
            if heartbeat:
                heartbeat()
            continue

        if heartbeat:
            heartbeat()

        if result.get("replayed"):
            replayed += 1
        if result.get("reclaimed"):
            reclaimed += 1
        if result.get("retryable"):
            retryable += 1
        if result.get("status") == "completed":
            completed += 1
            consult_request_id, _consultation_receipt = _extract_consultation(result)
            if consult_request_id and consult_request_id not in consult_request_ids:
                consult_request_ids.append(consult_request_id)
            if result.get("terminalize_session") is True:
                try:
                    terminal_sessions.append(
                        complete_and_read_terminal_session(
                            api_url=api_url,
                            job=result,
                            timeout_seconds=timeout_seconds,
                        )
                    )
                except urllib.error.HTTPError as exc:
                    failed += 1
                    detail = exc.read().decode("utf-8", errors="replace")
                    errors.append(
                        f"job_id={job_id} terminal_session_http_error={exc.code} {detail}"
                    )
                except urllib.error.URLError as exc:
                    failed += 1
                    errors.append(f"job_id={job_id} terminal_session_url_error={exc.reason}")
                except RuntimeError as exc:
                    failed += 1
                    errors.append(f"job_id={job_id} terminal_session_error={exc}")
        else:
            failed += 1
            errors.append(f"job_id={job_id} unexpected_status={result.get('status')!r}")

    result = {
        "jobs_found": len(jobs),
        "job_ids": job_ids,
        "completed": completed,
        "replayed": replayed,
        "reclaimed": reclaimed,
        "retryable": retryable,
        "failed": failed,
        "errors": errors,
        "terminal_session_ids": [item["session_id"] for item in terminal_sessions],
        "terminal_sessions": terminal_sessions,
        "consult_request_ids": consult_request_ids,
    }
    if loop_writer is not None:
        _write_loop_result(loop_writer, result)
    return result


def _write_alive(path: str, result: dict[str, Any] | None = None) -> None:
    """Persist a marker only for a functionally successful worker tick."""

    if not path:
        return
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "completed_at": _utc_now(),
                    "result": dict(result or {}),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def main() -> int:
    api_url = os.getenv("TRAINING_SESSION_API_URL", "http://training-session-svc:8099")
    interval_seconds = _env_int("TRAINING_SESSION_PREVIEW_WORKER_INTERVAL_SECONDS", 30, minimum=1)
    max_ticks = _env_int("TRAINING_SESSION_PREVIEW_WORKER_MAX_TICKS", 0, minimum=0)
    batch_limit = _env_int("TRAINING_SESSION_PREVIEW_WORKER_BATCH_LIMIT", 10, minimum=1)
    timeout_seconds = float(os.getenv("TRAINING_SESSION_PREVIEW_WORKER_TIMEOUT_SECONDS", "30"))
    alive_path = os.getenv("TRAINING_SESSION_PREVIEW_WORKER_ALIVE_PATH", DEFAULT_ALIVE_PATH)
    loop_writer = build_loop_writer()

    tick = 0
    while True:
        tick += 1
        try:
            result = run_tick(
                api_url=api_url,
                limit=batch_limit,
                timeout_seconds=timeout_seconds,
                loop_writer=loop_writer,
            )
            print(json.dumps({"tick": tick, "result": result}, sort_keys=True), flush=True)
            if result.get("failed") == 0:
                _write_alive(alive_path, result)
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps({"tick": tick, "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True),
                flush=True,
            )
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke worker.
    raise SystemExit(main())
