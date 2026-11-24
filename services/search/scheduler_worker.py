"""HTTP worker for scheduled search index refresh ticks."""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _post_json(api_url: str, path: str, body: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        api_url.rstrip("/") + path,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        text = response.read().decode("utf-8")
    return json.loads(text) if text else {}


def is_timeout_error(exc: BaseException) -> bool:
    """True when the client gave up waiting; the server-side run may still be going."""

    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        return isinstance(exc.reason, (TimeoutError, socket.timeout))
    return False


def retry_delay_seconds(consecutive_failures: int, interval_seconds: int, base_seconds: int = 15) -> int:
    """Delay after a failed tick: base, doubling per consecutive failure, capped at the interval."""

    if consecutive_failures <= 0:
        return interval_seconds
    return min(interval_seconds, base_seconds * (2 ** min(consecutive_failures - 1, 16)))


def _describe_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            detail = ""
        return f"HTTP {exc.code} {exc.reason}: {detail}"
    return str(exc) or repr(exc)


def run_tick(
    *,
    api_url: str,
    force_full: bool = False,
    materialize: bool = False,
    trigger_ref: str | None = None,
    timeout_seconds: float = 120.0,
    materialize_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Refresh the durable search index and optionally materialize a recovery snapshot."""

    ref = trigger_ref or f"scheduled:{int(time.time())}"
    refresh = _post_json(
        api_url,
        "/api/search/index/refresh",
        {
            "triggered_by": "scheduled_refresh",
            "trigger_ref": ref,
            "force_full": force_full,
        },
        timeout_seconds=timeout_seconds,
    )
    result: dict[str, Any] = {"refresh": refresh, "materialize": None}
    if materialize:
        result["materialize"] = _post_json(
            api_url,
            "/api/search/index/materialize",
            {},
            timeout_seconds=timeout_seconds if materialize_timeout_seconds is None else materialize_timeout_seconds,
        )
    return result


def assess_progress(refresh: Any, last_backlog: int | None) -> tuple[bool, int | None, str]:
    """Classify a refresh result: (progressing, backlog_remaining, reason).

    Progress = backlog drained, or embedded items / smaller backlog than last tick. A refresh
    answered "in_progress" counts as progress only when its snapshot's backlog_remaining is below the
    previous tick's; otherwise it is a stalled tick, tolerated up to
    SEARCH_INDEX_SCHEDULER_MAX_STALLED_TICKS consecutive ticks.
    """

    if not isinstance(refresh, dict):
        return True, last_backlog, "no_refresh_detail"
    snap = refresh.get("pipeline_snapshot") or {}
    if refresh.get("status") == "in_progress":
        # Another run owns the pipeline; the snapshot is the latest recorded run. A backlog that
        # fell since the previous tick is a drain in progress, not a stall.
        raw = snap.get("backlog_remaining") if isinstance(snap, dict) else None
        if raw is not None:
            backlog = int(raw)
            if last_backlog is not None and backlog < last_backlog:
                return True, backlog, f"backlog_draining_during_in_progress remaining={backlog}"
            return False, backlog, f"refresh_already_in_progress remaining={backlog}"
        return False, last_backlog, "refresh_already_in_progress"
    backlog = int(snap.get("backlog_remaining") or 0)
    if backlog == 0:
        return True, 0, "backlog_drained"
    if int(snap.get("incremental_count") or 0) > 0:
        return True, backlog, f"backlog_draining remaining={backlog}"
    return False, backlog, f"no_progress remaining={backlog}"


def main() -> int:
    api_url = os.getenv("SEARCH_API_URL", "http://127.0.0.1:8098")
    interval_seconds = _env_int("SEARCH_INDEX_SCHEDULER_INTERVAL_SECONDS", 300, minimum=1)
    max_ticks = _env_int("SEARCH_INDEX_SCHEDULER_MAX_TICKS", 0, minimum=0)
    force_full = _env_bool("SEARCH_INDEX_SCHEDULER_FORCE_FULL", False)
    materialize = _env_bool("SEARCH_INDEX_SCHEDULER_MATERIALIZE", True)
    max_stalled_ticks = _env_int("SEARCH_INDEX_SCHEDULER_MAX_STALLED_TICKS", 6, minimum=1)
    # A cold search-svc loads its embedding model inside the first refresh (26.4 s measured), so the
    # default covers that plus a bounded refresh; materialize only writes a snapshot.
    tick_timeout = _env_int("SEARCH_INDEX_SCHEDULER_TICK_TIMEOUT_SECONDS", 120, minimum=1)
    materialize_timeout = _env_int("SEARCH_INDEX_SCHEDULER_MATERIALIZE_TIMEOUT_SECONDS", tick_timeout, minimum=1)
    retry_base = _env_int("SEARCH_INDEX_SCHEDULER_RETRY_BASE_SECONDS", 15, minimum=1)
    # A previous container's alive file must not decide this container's health.
    alive_path = os.getenv("SEARCH_INDEX_SCHEDULER_ALIVE_PATH")
    if alive_path:
        try:
            Path(alive_path).unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
    tick = 0
    failures = 0
    stalled = 0
    last_backlog: int | None = None
    while True:
        tick += 1
        try:
            result = run_tick(
                api_url=api_url,
                force_full=force_full,
                materialize=materialize,
                timeout_seconds=tick_timeout,
                materialize_timeout_seconds=materialize_timeout,
            )
        except Exception as exc:  # noqa: BLE001 - recoverable tick failure must stay observable, not fatal
            # Failed tick: report reason, do NOT touch the alive file (health stays honest), keep looping.
            failures += 1
            timed_out = is_timeout_error(exc)
            print(
                json.dumps(
                    {
                        "tick": tick,
                        # A timed-out refresh keeps running server-side; the retry is answered
                        # "in_progress" by the server instead of starting a parallel run.
                        "status": "refresh_in_progress_timeout" if timed_out else "failed",
                        "retry_in_seconds": retry_delay_seconds(failures, interval_seconds, retry_base),
                        "error_type": type(exc).__name__,
                        "error": _describe_error(exc),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        else:
            failures = 0
            progressing, last_backlog, reason = assess_progress(result.get("refresh"), last_backlog)
            stalled = 0 if progressing else stalled + 1
            healthy = stalled < max_stalled_ticks
            if alive_path and healthy:
                try:
                    alive_p = Path(alive_path)
                    alive_p.parent.mkdir(parents=True, exist_ok=True)
                    alive_p.touch()
                except Exception:
                    pass
            print(
                json.dumps(
                    {
                        "tick": tick,
                        "result": result,
                        "progress": reason,
                        "stalled_ticks": stalled,
                        "max_stalled_ticks": max_stalled_ticks,
                        "health": "healthy" if healthy else "unhealthy",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(retry_delay_seconds(failures, interval_seconds, retry_base))


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke worker.
    raise SystemExit(main())
