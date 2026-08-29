#!/usr/bin/env python3
"""Bounded source-ingest/search smoke for CI.

This keeps the test narrow to the source/search P0 contract: configured static
records, guarded external_feed, DLQ replay, frontier scheduling, audit replay,
incremental search refresh, and no unrestricted crawler posture.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any


SOURCE_INGEST_URL = os.getenv("SOURCE_INGEST_URL", "http://127.0.0.1:8097").rstrip("/")
SEARCH_URL = os.getenv("SEARCH_URL", "http://127.0.0.1:8098").rstrip("/")
SOURCE_FEED_HOST = os.getenv("SOURCE_INGEST_EXTERNAL_FEED_HOST", "source-search-bounded-smoke")
DEFAULT_SMOKE_TIMEOUT_SECONDS = 180.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 15.0


def _positive_seconds_from_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive number")
    return value


class _SmokeTimeout(RuntimeError):
    """Measured smoke deadline failure with the last durable checkpoint."""


class _SmokeBudget:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        request_timeout_seconds: float,
        clock: Any = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0 or request_timeout_seconds <= 0:
            raise ValueError("smoke and request timeouts must be positive")
        self.timeout_seconds = float(timeout_seconds)
        self.request_timeout_seconds = float(request_timeout_seconds)
        self._clock = clock
        self.started_at = self._clock()
        self.deadline = self.started_at + self.timeout_seconds
        self.phase = "startup"
        self.connector_id = "none"
        self.last_successful_checkpoint = "none"

    def _now(self) -> float:
        return self._clock()

    def elapsed_seconds(self) -> float:
        return max(0.0, self._now() - self.started_at)

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - self._now())

    def begin_phase(self, phase: str, *, connector_id: str = "none") -> None:
        self.phase = phase
        self.connector_id = connector_id
        if self.remaining_seconds() <= 0:
            raise self.timeout_error("overall smoke budget exhausted before phase started")
        print(
            "run "
            f"phase={self.phase} connector={self.connector_id} "
            f"last_successful_checkpoint={self.last_successful_checkpoint} "
            f"elapsed_seconds={self.elapsed_seconds():.3f} "
            f"remaining_seconds={self.remaining_seconds():.3f}",
            flush=True,
        )

    def request_timeout(self, requested_seconds: float | None = None) -> float:
        remaining = self.remaining_seconds()
        if remaining <= 0:
            raise self.timeout_error("overall smoke budget exhausted before request")
        requested = self.request_timeout_seconds if requested_seconds is None else float(requested_seconds)
        return max(0.001, min(requested, remaining))

    def checkpoint(self, checkpoint: str, message: str) -> None:
        self.last_successful_checkpoint = checkpoint
        print(
            "ok  "
            f"{message} checkpoint={checkpoint} "
            f"elapsed_seconds={self.elapsed_seconds():.3f} "
            f"remaining_seconds={self.remaining_seconds():.3f}",
            flush=True,
        )

    def timeout_error(self, detail: str) -> _SmokeTimeout:
        return _SmokeTimeout(
            "measured timeout "
            f"phase={self.phase} connector={self.connector_id} "
            f"last_successful_checkpoint={self.last_successful_checkpoint} "
            f"elapsed_seconds={self.elapsed_seconds():.3f} "
            f"budget_seconds={self.timeout_seconds:.3f} "
            f"remaining_seconds={self.remaining_seconds():.3f} "
            f"detail={detail}"
        )


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    return isinstance(exc, urllib.error.URLError) and isinstance(
        exc.reason,
        (TimeoutError, socket.timeout),
    )


def _request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 10,
) -> tuple[int, dict[str, Any]]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
            return response.getcode(), json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            payload_body = json.loads(text) if text else {}
        except json.JSONDecodeError:
            payload_body = {"raw": text}
        return exc.code, {"url": url, **payload_body}


def _request_for_phase(
    budget: _SmokeBudget,
    phase: str,
    connector_id: str,
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
) -> tuple[int, dict[str, Any]]:
    budget.begin_phase(phase, connector_id=connector_id)
    timeout = budget.request_timeout(timeout_seconds)
    try:
        return _request_json(method, url, body=body, timeout=timeout)
    except Exception as exc:
        if not _is_timeout_error(exc):
            raise
        raise budget.timeout_error(
            f"request_timeout_seconds={timeout:.3f} url={url} error={exc}"
        ) from exc


def _wait_for_health(
    name: str,
    url: str,
    *,
    budget: _SmokeBudget,
    timeout_seconds: float = 60,
) -> None:
    budget.begin_phase(f"readiness:{name}", connector_id=name)
    deadline = min(budget.deadline, budget._now() + timeout_seconds)
    last_error = "unknown"
    while budget._now() < deadline:
        try:
            request_timeout = min(5.0, deadline - budget._now())
            if request_timeout <= 0:
                break
            status, payload = _request_json("GET", url, timeout=request_timeout)
            if status == 200 and payload.get("status") == "ok":
                budget.checkpoint(f"{name}_ready", f"{name} ready")
                return
            last_error = f"status={status} payload={payload}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        sleep_seconds = min(1.0, max(0.0, deadline - budget._now()))
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    raise budget.timeout_error(
        f"health_timeout_seconds={timeout_seconds:.3f} url={url} last_error={last_error}"
    )


def _serve_feed(payload: dict[str, Any]) -> tuple[ThreadingHTTPServer, str]:
    body = json.dumps(payload).encode("utf-8")
    robots = b"User-agent: pantheon-source-ingest\nAllow: /\n"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/robots.txt":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(robots)))
                self.end_headers()
                self.wfile.write(robots)
                return
            if self.path != "/feed.json":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{SOURCE_FEED_HOST}:{server.server_port}/feed.json"


def _connector(connector_id: str, provider: str) -> dict[str, Any]:
    return {
        "connector_id": connector_id,
        "source_type": "internal_note",
        "provider": provider,
        "license_scope": "internal",
    }


def _record(source_id: str, token: str, *, access_scope: list[str] | None = None) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"Bounded source smoke {source_id}",
        "content_ref": f"memory://source-search-bounded/{source_id}",
        "metadata": {
            "body": f"Bounded source search evidence {source_id} for momentum volatility {token}",
            "access_scope": access_scope or ["operator", "research"],
            "keywords": ["bounded", "source", "search", token],
        },
    }


def _run_scoped_connector_ids(run_id: str) -> dict[str, str]:
    return {
        kind: f"conn-bounded-{kind}-{run_id}"
        for kind in ("static", "feed", "replay", "scheduled")
    }


def _assert_no_unrestricted_crawler(budget: _SmokeBudget) -> None:
    status, registry = _request_for_phase(
        budget,
        "registry_policy_check",
        "registry",
        "GET",
        f"{SOURCE_INGEST_URL}/api/source-ingest/registry",
    )
    if status != 200:
        raise RuntimeError(f"source registry unavailable: {status} {registry}")
    entries = list(registry.get("connectors") or [])
    entries.extend(registry.get("provider_examples") or [])
    for entry in entries:
        fetch = entry.get("fetch_policy") or {}
        mode = fetch.get("mode")
        if mode not in {None, "static_records", "external_feed", "provider_owned_adapter"}:
            raise RuntimeError(f"unexpected source fetch mode enabled: {entry}")
        if mode == "external_feed" and int(fetch.get("allowed_url_prefix_count") or 0) < 1:
            raise RuntimeError(f"external_feed lacks allowed_url_prefix guard: {entry}")
        if mode == "provider_owned_adapter" and not str(fetch.get("adapter") or "").strip():
            raise RuntimeError(f"provider_owned_adapter lacks explicit adapter guard: {entry}")
    budget.checkpoint(
        "registry_policy_checked",
        "source registry has no unrestricted crawler mode",
    )


def main() -> int:
    budget = _SmokeBudget(
        timeout_seconds=_positive_seconds_from_env(
            "SOURCE_SEARCH_SMOKE_TIMEOUT_SECONDS",
            DEFAULT_SMOKE_TIMEOUT_SECONDS,
        ),
        request_timeout_seconds=_positive_seconds_from_env(
            "SOURCE_SEARCH_SMOKE_REQUEST_TIMEOUT_SECONDS",
            DEFAULT_REQUEST_TIMEOUT_SECONDS,
        ),
    )
    suffix = uuid.uuid4().hex
    connector_ids = _run_scoped_connector_ids(suffix)
    token = f"bounded-smoke-{suffix}"
    future_timestamp = (datetime.now(timezone.utc) + timedelta(seconds=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    print(
        "source/search bounded smoke started "
        f"budget_seconds={budget.timeout_seconds:.3f} "
        f"request_timeout_seconds={budget.request_timeout_seconds:.3f}",
        flush=True,
    )

    _wait_for_health("source-ingest", f"{SOURCE_INGEST_URL}/readyz", budget=budget)
    _wait_for_health("search-svc", f"{SEARCH_URL}/readyz", budget=budget)
    _assert_no_unrestricted_crawler(budget)

    status, configured_static = _request_for_phase(
        budget,
        "static_connector_configure",
        connector_ids["static"],
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/connectors",
        body={
            "connector": _connector(connector_ids["static"], "Pantheon bounded static"),
            "fetch": {
                "mode": "static_records",
                "next_watermark": future_timestamp,
                "records": [_record(f"src-bounded-static-{suffix}", token)],
            },
        },
    )
    if status != 201 or configured_static.get("fetch", {}).get("mode") != "static_records":
        raise RuntimeError(f"static_records connector configuration failed: {status} {configured_static}")
    budget.checkpoint("static_connector_configured", "static_records connector configured")
    status, static_run = _request_for_phase(
        budget,
        "static_ingest",
        connector_ids["static"],
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/jobs",
        body={
            "connector_id": connector_ids["static"],
            "trace_id": f"trace-static-{suffix}",
            "trigger_type": "bounded_smoke_static",
        },
    )
    if status != 201 or static_run.get("run", {}).get("status") != "completed":
        raise RuntimeError(f"static_records ingest failed: {status} {static_run}")
    budget.checkpoint(
        "static_ingest_completed",
        "static_records connector ingested source evidence",
    )

    feed_server, feed_url = _serve_feed(
        {
            "next_watermark": future_timestamp,
            "records": [
                _record(f"src-bounded-feed-{suffix}", token),
                _record(f"src-bounded-private-{suffix}", token, access_scope=["risk-committee"]),
            ],
        }
    )
    try:
        status, configured_feed = _request_for_phase(
            budget,
            "feed_connector_configure",
            connector_ids["feed"],
            "POST",
            f"{SOURCE_INGEST_URL}/api/source-ingest/connectors",
            body={
                "connector": _connector(connector_ids["feed"], "Pantheon bounded external feed"),
                "fetch": {
                    "mode": "external_feed",
                    "network_scope": "internal_service",
                    "url": feed_url,
                    "allowed_url_prefixes": [feed_url.rsplit("/", 1)[0] + "/"],
                    "timeout_seconds": 5,
                    "max_bytes": 8192,
                    "max_records": 5,
                    "default_access_scope": ["operator", "research"],
                },
            },
        )
        if status != 201 or configured_feed.get("fetch", {}).get("mode") != "external_feed":
            raise RuntimeError(f"external_feed connector configuration failed: {status} {configured_feed}")
        budget.checkpoint("feed_connector_configured", "guarded external_feed connector configured")
        status, feed_run = _request_for_phase(
            budget,
            "feed_ingest",
            connector_ids["feed"],
            "POST",
            f"{SOURCE_INGEST_URL}/api/source-ingest/jobs",
            body={
                "connector_id": connector_ids["feed"],
                "trace_id": f"trace-feed-{suffix}",
                "trigger_type": "bounded_smoke_feed",
            },
        )
        if status != 201 or feed_run.get("run", {}).get("status") != "completed":
            raise RuntimeError(f"external_feed ingest failed: {status} {feed_run}")
    finally:
        feed_server.shutdown()
        feed_server.server_close()
    feed_run_id = feed_run["run"]["ingest_run_id"]
    refresh_summary = feed_run.get("source_search_refresh") or {}
    refresh_service = refresh_summary.get("search_service") or {}
    if (
        refresh_summary.get("status") != "refreshed"
        or refresh_service.get("materialized_matches_completion") is not True
        or not refresh_service.get("pipeline_run_id")
    ):
        raise RuntimeError(f"source-ingest did not observe search refresh/materialization truth: {feed_run}")
    budget.checkpoint(
        "feed_ingest_completed",
        "guarded external_feed fetched through allowed_url_prefixes",
    )

    status, completion_truth = _request_for_phase(
        budget,
        "search_completion_truth",
        connector_ids["feed"],
        "GET",
        f"{SEARCH_URL}/api/search/index/source-completions/{feed_run_id}",
    )
    truth = completion_truth.get("truth") or {}
    if (
        status != 200
        or truth.get("index_refreshed") is not True
        or truth.get("materialized_matches_completion") is not True
    ):
        raise RuntimeError(f"search did not replay source-completion truth: {status} {completion_truth}")
    budget.checkpoint(
        "search_completion_truth_replayed",
        "search replayed source-completion refresh/materialization truth",
    )

    status, pipeline_runs = _request_for_phase(
        budget,
        "search_pipeline_replay",
        connector_ids["feed"],
        "GET",
        f"{SEARCH_URL}/api/search/index/pipeline-runs?limit=20",
    )
    if status != 200 or not any(run.get("trigger_ref") == feed_run_id for run in pipeline_runs.get("runs", [])):
        raise RuntimeError(f"search did not record ingest-completion incremental refresh: {status} {pipeline_runs}")
    budget.checkpoint(
        "search_pipeline_replayed",
        "search recorded ingest-completion incremental refresh",
    )

    status, replay_config = _request_for_phase(
        budget,
        "replay_connector_configure",
        connector_ids["replay"],
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/connectors",
        body={
            "connector": _connector(connector_ids["replay"], "Pantheon bounded replay"),
            "fetch": {
                "mode": "static_records",
                "next_watermark": future_timestamp,
                "fail_until_attempt": 2,
                "failure_reason": "bounded smoke configured failure",
                "records": [_record(f"src-bounded-replay-{suffix}", token)],
            },
        },
    )
    if status != 201:
        raise RuntimeError(f"DLQ replay connector configuration failed: {status} {replay_config}")
    budget.checkpoint("replay_connector_configured", "DLQ replay connector configured")
    status, failed_run = _request_for_phase(
        budget,
        "replay_failure_ingest",
        connector_ids["replay"],
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/jobs",
        body={
            "connector_id": connector_ids["replay"],
            "trace_id": f"trace-replay-{suffix}",
            "trigger_type": "bounded_smoke_failure",
        },
    )
    dlq_entry_id = (failed_run.get("dlq_entries") or [{}])[0].get("entry_id")
    if status != 201 or failed_run.get("run", {}).get("status") != "failed" or not dlq_entry_id:
        raise RuntimeError(f"configured failure did not enter DLQ: {status} {failed_run}")
    budget.checkpoint("replay_failure_dead_lettered", "configured failure entered DLQ")
    status, dlq_replay = _request_for_phase(
        budget,
        "dlq_replay",
        connector_ids["replay"],
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/dlq/replay",
        body={
            "tag": "retry_exhausted",
            "entry_ids": [dlq_entry_id],
            "reason": "bounded source/search CI replay",
        },
    )
    if status != 200 or dlq_replay.get("summary", {}).get("applied") != 1:
        raise RuntimeError(f"DLQ replay failed: {status} {dlq_replay}")
    budget.checkpoint("dlq_replay_completed", "DLQ replay restored configured failure")

    status, sched_config = _request_for_phase(
        budget,
        "scheduled_connector_configure",
        connector_ids["scheduled"],
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/connectors",
        body={
            "connector": _connector(connector_ids["scheduled"], "Pantheon bounded scheduled"),
            "fetch": {
                "mode": "static_records",
                "next_watermark": future_timestamp,
                "records": [_record(f"src-bounded-scheduled-{suffix}", token)],
            },
        },
    )
    if status != 201:
        raise RuntimeError(f"scheduled connector configuration failed: {status} {sched_config}")
    budget.checkpoint("scheduled_connector_configured", "bounded scheduled connector configured")
    status, schedule = _request_for_phase(
        budget,
        "scheduled_connector_enable",
        connector_ids["scheduled"],
        "PUT",
        f"{SOURCE_INGEST_URL}/api/source-ingest/connectors/{connector_ids['scheduled']}/schedule",
        body={"interval_seconds": 1, "enabled": True},
    )
    if status != 200 or schedule.get("schedule", {}).get("enabled") is not True:
        raise RuntimeError(f"schedule enable failed: {status} {schedule}")
    budget.checkpoint("scheduled_connector_enabled", "bounded scheduled connector enabled")
    status, scheduled_run = _request_for_phase(
        budget,
        "scheduled_run",
        connector_ids["scheduled"],
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/run-scheduled",
        body={"exclusive_connector_ids": [connector_ids["scheduled"]]},
    )
    if status != 200 or scheduled_run.get("summary", {}).get("total_ran") != 1:
        raise RuntimeError(f"frontier scheduled run failed: {status} {scheduled_run}")
    frontier_id = ((scheduled_run.get("ran") or [{}])[0].get("frontier") or {}).get("frontier_id")
    if not frontier_id:
        raise RuntimeError(f"scheduled run did not return frontier evidence: {scheduled_run}")
    budget.checkpoint(
        "scheduled_run_completed",
        "frontier scheduler claimed and completed bounded connector",
    )

    for source_id in (
        f"src-bounded-static-{suffix}",
        f"src-bounded-feed-{suffix}",
        f"src-bounded-replay-{suffix}",
        f"src-bounded-scheduled-{suffix}",
    ):
        status, payload = _request_for_phase(
            budget,
            "source_record_replay",
            source_id,
            "GET",
            f"{SOURCE_INGEST_URL}/api/source-ingest/source-records/{source_id}",
        )
        if status != 200 or payload.get("source_record", {}).get("source_id") != source_id:
            raise RuntimeError(f"source record replay failed for {source_id}: {status} {payload}")
    budget.checkpoint("source_records_replayed", "source records replayed")
    status, audit = _request_for_phase(
        budget,
        "source_audit_replay",
        connector_ids["replay"],
        "GET",
        f"{SOURCE_INGEST_URL}/api/source-ingest/audit",
    )
    actions = audit.get("actions") or []
    action_types = {str(action.get("action_type")) for action in actions}
    expected_audit_actions = {
        "source_ingestion.scheduled_run.dead_lettered",
        "foundation.dlq.replay.applied",
    }
    if status != 200 or not expected_audit_actions.issubset(action_types):
        raise RuntimeError(f"source audit replay missing expected actions: {status} {audit}")
    budget.checkpoint("source_audit_replayed", "source records and audit actions replay")

    status, refresh = _request_for_phase(
        budget,
        "search_incremental_refresh",
        connector_ids["feed"],
        "POST",
        f"{SEARCH_URL}/api/search/index/refresh",
        body={"triggered_by": "bounded_source_search_smoke", "trigger_ref": feed_run_id},
    )
    snapshot = refresh.get("pipeline_snapshot") or {}
    if status != 200 or snapshot.get("schema_version") != "index_pipeline_snapshot.v1":
        raise RuntimeError(f"search incremental refresh failed: {status} {refresh}")
    budget.checkpoint("search_incremental_refresh_completed", "search incremental refresh completed")
    status, query = _request_for_phase(
        budget,
        "governed_search_query",
        connector_ids["feed"],
        "POST",
        f"{SEARCH_URL}/api/search/query",
        body={
            "request_id": f"bounded-search-{suffix}",
            "trace_id": f"trace-search-{suffix}",
            "query": f"momentum volatility {token}",
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "source_types": ["internal_note"],
            "access_context": {
                "persona_id": "operator-workbench",
                "workspace_id": "research-workbench",
                "environment": "paper",
                "access_scopes": ["operator", "research"],
                "license_scopes": ["internal"],
            },
        },
    )
    result_source_ids = {
        str(match.get("source_id"))
        for result in query.get("results", [])
        for match in result.get("matched_items", [])
    }
    if (
        status != 200
        or f"src-bounded-private-{suffix}" in result_source_ids
        or f"src-bounded-feed-{suffix}" not in result_source_ids
    ):
        raise RuntimeError(f"governed search query did not enforce bounded filters: {status} {query}")
    budget.checkpoint(
        "governed_search_query_completed",
        "governed search query used durable evidence and access filters",
    )

    print(
        "source/search bounded smoke passed "
        f"elapsed_seconds={budget.elapsed_seconds():.3f} "
        f"budget_seconds={budget.timeout_seconds:.3f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"source/search bounded smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
