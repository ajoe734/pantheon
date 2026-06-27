#!/usr/bin/env python3
"""Bounded source-ingest/search smoke for CI.

This keeps the test narrow to the source/search P0 contract: configured static
records, guarded external_feed, DLQ replay, frontier scheduling, audit replay,
incremental search refresh, and no unrestricted crawler posture.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any


SOURCE_INGEST_URL = os.getenv("SOURCE_INGEST_URL", "http://127.0.0.1:8097").rstrip("/")
SEARCH_URL = os.getenv("SEARCH_URL", "http://127.0.0.1:8098").rstrip("/")
SOURCE_FEED_HOST = os.getenv("SOURCE_INGEST_EXTERNAL_FEED_HOST", "source-search-bounded-smoke")


def _request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: int = 10,
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


def _wait_for_health(name: str, url: str, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    last_error = "unknown"
    while time.time() < deadline:
        try:
            status, payload = _request_json("GET", url, timeout=5)
            if status == 200 and payload.get("status") == "ok":
                print(f"ok  {name} ready")
                return
            last_error = f"status={status} payload={payload}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"{name} did not become ready: {last_error}")


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


def _assert_no_unrestricted_crawler() -> None:
    status, registry = _request_json("GET", f"{SOURCE_INGEST_URL}/api/source-ingest/registry")
    if status != 200:
        raise RuntimeError(f"source registry unavailable: {status} {registry}")
    entries = list(registry.get("connectors") or [])
    entries.extend(registry.get("provider_examples") or [])
    for entry in entries:
        fetch = entry.get("fetch_policy") or {}
        mode = fetch.get("mode")
        if mode not in {None, "static_records", "external_feed"}:
            raise RuntimeError(f"unexpected source fetch mode enabled: {entry}")
        if mode == "external_feed" and int(fetch.get("allowed_url_prefix_count") or 0) < 1:
            raise RuntimeError(f"external_feed lacks allowed_url_prefix guard: {entry}")
    print("ok  source registry has no unrestricted crawler mode")


def main() -> int:
    suffix = str(int(time.time()))
    token = f"bounded-smoke-{suffix}"
    future_timestamp = (datetime.now(timezone.utc) + timedelta(seconds=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    _wait_for_health("source-ingest", f"{SOURCE_INGEST_URL}/readyz")
    _wait_for_health("search-svc", f"{SEARCH_URL}/readyz")
    _assert_no_unrestricted_crawler()

    status, configured_static = _request_json(
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/connectors",
        body={
            "connector": _connector("conn-bounded-static", "Pantheon bounded static"),
            "fetch": {
                "mode": "static_records",
                "next_watermark": future_timestamp,
                "records": [_record(f"src-bounded-static-{suffix}", token)],
            },
        },
    )
    if status != 201 or configured_static.get("fetch", {}).get("mode") != "static_records":
        raise RuntimeError(f"static_records connector configuration failed: {status} {configured_static}")
    status, static_run = _request_json(
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/jobs",
        body={
            "connector_id": "conn-bounded-static",
            "trace_id": f"trace-static-{suffix}",
            "trigger_type": "bounded_smoke_static",
        },
    )
    if status != 201 or static_run.get("run", {}).get("status") != "completed":
        raise RuntimeError(f"static_records ingest failed: {status} {static_run}")
    print("ok  static_records connector ingested source evidence")

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
        status, configured_feed = _request_json(
            "POST",
            f"{SOURCE_INGEST_URL}/api/source-ingest/connectors",
            body={
                "connector": _connector("conn-bounded-feed", "Pantheon bounded external feed"),
                "fetch": {
                    "mode": "external_feed",
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
        status, feed_run = _request_json(
            "POST",
            f"{SOURCE_INGEST_URL}/api/source-ingest/jobs",
            body={
                "connector_id": "conn-bounded-feed",
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
    print("ok  guarded external_feed fetched through allowed_url_prefixes")

    status, completion_truth = _request_json("GET", f"{SEARCH_URL}/api/search/index/source-completions/{feed_run_id}")
    truth = completion_truth.get("truth") or {}
    if (
        status != 200
        or truth.get("index_refreshed") is not True
        or truth.get("materialized_matches_completion") is not True
    ):
        raise RuntimeError(f"search did not replay source-completion truth: {status} {completion_truth}")
    print("ok  search replayed source-completion refresh/materialization truth")

    status, pipeline_runs = _request_json("GET", f"{SEARCH_URL}/api/search/index/pipeline-runs?limit=20")
    if status != 200 or not any(run.get("trigger_ref") == feed_run_id for run in pipeline_runs.get("runs", [])):
        raise RuntimeError(f"search did not record ingest-completion incremental refresh: {status} {pipeline_runs}")
    print("ok  search recorded ingest-completion incremental refresh")

    status, replay_config = _request_json(
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/connectors",
        body={
            "connector": _connector("conn-bounded-replay", "Pantheon bounded replay"),
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
    status, failed_run = _request_json(
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/jobs",
        body={
            "connector_id": "conn-bounded-replay",
            "trace_id": f"trace-replay-{suffix}",
            "trigger_type": "bounded_smoke_failure",
        },
    )
    dlq_entry_id = (failed_run.get("dlq_entries") or [{}])[0].get("entry_id")
    if status != 201 or failed_run.get("run", {}).get("status") != "failed" or not dlq_entry_id:
        raise RuntimeError(f"configured failure did not enter DLQ: {status} {failed_run}")
    status, dlq_replay = _request_json(
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
    print("ok  DLQ replay restored configured failure")

    status, sched_config = _request_json(
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/connectors",
        body={
            "connector": _connector("conn-bounded-scheduled", "Pantheon bounded scheduled"),
            "fetch": {
                "mode": "static_records",
                "next_watermark": future_timestamp,
                "records": [_record(f"src-bounded-scheduled-{suffix}", token)],
            },
        },
    )
    if status != 201:
        raise RuntimeError(f"scheduled connector configuration failed: {status} {sched_config}")
    status, schedule = _request_json(
        "PUT",
        f"{SOURCE_INGEST_URL}/api/source-ingest/connectors/conn-bounded-scheduled/schedule",
        body={"interval_seconds": 1, "enabled": True},
    )
    if status != 200 or schedule.get("schedule", {}).get("enabled") is not True:
        raise RuntimeError(f"schedule enable failed: {status} {schedule}")
    status, scheduled_run = _request_json("POST", f"{SOURCE_INGEST_URL}/api/source-ingest/run-scheduled")
    if status != 200 or scheduled_run.get("summary", {}).get("total_ran") != 1:
        raise RuntimeError(f"frontier scheduled run failed: {status} {scheduled_run}")
    frontier_id = ((scheduled_run.get("ran") or [{}])[0].get("frontier") or {}).get("frontier_id")
    if not frontier_id:
        raise RuntimeError(f"scheduled run did not return frontier evidence: {scheduled_run}")
    print("ok  frontier scheduler claimed and completed bounded connector")

    for source_id in (
        f"src-bounded-static-{suffix}",
        f"src-bounded-feed-{suffix}",
        f"src-bounded-replay-{suffix}",
        f"src-bounded-scheduled-{suffix}",
    ):
        status, payload = _request_json("GET", f"{SOURCE_INGEST_URL}/api/source-ingest/source-records/{source_id}")
        if status != 200 or payload.get("source_record", {}).get("source_id") != source_id:
            raise RuntimeError(f"source record replay failed for {source_id}: {status} {payload}")
    status, audit = _request_json("GET", f"{SOURCE_INGEST_URL}/api/source-ingest/audit")
    actions = audit.get("actions") or []
    action_types = {str(action.get("action_type")) for action in actions}
    expected_audit_actions = {
        "source_ingestion.scheduled_run.dead_lettered",
        "foundation.dlq.replay.applied",
    }
    if status != 200 or not expected_audit_actions.issubset(action_types):
        raise RuntimeError(f"source audit replay missing expected actions: {status} {audit}")
    print("ok  source records and audit actions replay")

    status, refresh = _request_json(
        "POST",
        f"{SEARCH_URL}/api/search/index/refresh",
        body={"triggered_by": "bounded_source_search_smoke", "trigger_ref": feed_run_id},
    )
    snapshot = refresh.get("pipeline_snapshot") or {}
    if status != 200 or snapshot.get("schema_version") != "index_pipeline_snapshot.v1":
        raise RuntimeError(f"search incremental refresh failed: {status} {refresh}")
    status, query = _request_json(
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
    print("ok  governed search query used durable evidence and access filters")

    print("source/search bounded smoke passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"source/search bounded smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
