#!/usr/bin/env python3
"""Credentialed/test source-search connector smoke.

This script proves the governed non-ordering source/search path:
external feed connector -> SourceRecord/EvidenceBundle -> durable search query.
It never calls Lean, broker, order, paper, canary, live, or capital endpoints.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_ID = "P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001"
SOURCE_INGEST_URL = os.getenv("SOURCE_INGEST_URL", "http://127.0.0.1:8097").rstrip("/")
SEARCH_URL = os.getenv("SEARCH_URL", "http://127.0.0.1:8098").rstrip("/")
BFF_URL = os.getenv("BFF_URL", os.getenv("PANTHEON_BFF_URL", "")).rstrip("/")
OPENCLAW_ADAPTER_URL = os.getenv("OPENCLAW_ADAPTER_URL", os.getenv("PANTHEON_OPENCLAW_ADAPTER_URL", "")).rstrip("/")
FEED_URL = os.getenv("SOURCE_SEARCH_LIVE_FEED_URL", "").strip()
ALLOWLIST_PREFIXES = [
    item.strip()
    for item in os.getenv("SOURCE_SEARCH_LIVE_ALLOWED_URL_PREFIXES", "").split(",")
    if item.strip()
]
SECRET_REF_ID = os.getenv("SOURCE_SEARCH_LIVE_SECRET_REF_ID", "").strip() or None
SOURCE_TYPE = os.getenv("SOURCE_SEARCH_LIVE_SOURCE_TYPE", "news").strip() or "news"
LICENSE_SCOPE = os.getenv("SOURCE_SEARCH_LIVE_LICENSE_SCOPE", "vendor").strip() or "vendor"
ENTITLEMENT_TAG = os.getenv("SOURCE_SEARCH_LIVE_ENTITLEMENT_TAG", "source-search-live-test").strip()
ACCESS_SCOPE = [item.strip() for item in os.getenv("SOURCE_SEARCH_LIVE_ACCESS_SCOPE", "research").split(",") if item.strip()]
OUTPUT_PATH = Path(
    os.getenv(
        "SOURCE_SEARCH_LIVE_EVIDENCE_PATH",
        f"support/evidence/{TASK_ID}/source_search_live_connector_smoke.json",
    )
)


class SmokeDependencyMissing(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_evidence(payload: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
            return response.getcode(), json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError:
            payload = {"raw": text}
        return exc.code, {"url": url, **payload}


def _wait_ready(name: str, base_url: str, evidence: dict[str, Any], timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    last: Any = None
    while time.time() < deadline:
        status, payload = _request_json("GET", f"{base_url}/readyz", timeout=5)
        last = {"status": status, "payload": payload}
        if status == 200 and payload.get("status") == "ok":
            evidence["checks"].append({"name": f"{name}.readyz", "status": "passed", "payload": payload})
            return
        time.sleep(1)
    raise RuntimeError(f"{name} did not become ready: {last}")


def _require_feed_config() -> None:
    if not FEED_URL:
        raise SmokeDependencyMissing("SOURCE_SEARCH_LIVE_FEED_URL is required for live/test connector smoke")
    parsed = urllib.parse.urlparse(FEED_URL)
    if parsed.scheme not in {"http", "https", "file"}:
        raise SmokeDependencyMissing("SOURCE_SEARCH_LIVE_FEED_URL must use http, https, or file")
    if not ALLOWLIST_PREFIXES:
        raise SmokeDependencyMissing("SOURCE_SEARCH_LIVE_ALLOWED_URL_PREFIXES is required")
    if not any(FEED_URL.startswith(prefix) for prefix in ALLOWLIST_PREFIXES):
        raise SmokeDependencyMissing("SOURCE_SEARCH_LIVE_FEED_URL is outside SOURCE_SEARCH_LIVE_ALLOWED_URL_PREFIXES")


def _connector_payload(connector_id: str) -> dict[str, Any]:
    connector: dict[str, Any] = {
        "connector_id": connector_id,
        "source_type": SOURCE_TYPE,
        "provider": os.getenv("SOURCE_SEARCH_LIVE_PROVIDER", "Pantheon source-search live smoke"),
        "license_scope": LICENSE_SCOPE,
        "metadata": {
            "entitlement_tags": [ENTITLEMENT_TAG],
            "access_scope": ACCESS_SCOPE,
            "task_id": TASK_ID,
            "direct_execution_allowed": False,
            "canonical_sink": "SourceRecord/EvidenceBundle",
        },
        "license_policy": {
            "license_scope": LICENSE_SCOPE,
            "allowed_use": ["research", "search_index"],
            "attribution_required": True,
            "policy_ref": f"source-ingest://license/{TASK_ID.lower()}",
        },
        "rate_limit_policy": {
            "requests_per_minute": int(os.getenv("SOURCE_SEARCH_LIVE_RPM", "30")),
            "burst": int(os.getenv("SOURCE_SEARCH_LIVE_BURST", "5")),
            "retry_after_seconds": 60,
            "policy_ref": f"source-ingest://policy/{TASK_ID.lower()}",
        },
    }
    if SECRET_REF_ID:
        connector["auth_type"] = "api_key"
        connector["secret_ref_id"] = SECRET_REF_ID
        connector["auth_policy"] = {
            "auth_type": "api_key",
            "secret_ref": {"secret_ref_id": SECRET_REF_ID},
            "auth_scope": ["source_ingest:read"],
        }
    return connector


def _assert_source_record(source: dict[str, Any], expected_source_id: str) -> None:
    metadata = source.get("metadata") or {}
    if source.get("source_id") != expected_source_id:
        raise RuntimeError(f"unexpected source_id readback: {source}")
    required = ["license_scope", "entitlement_tags", "available_time", "pit", "governance"]
    missing = [key for key in required if key not in metadata]
    if missing:
        raise RuntimeError(f"source record missing governed metadata {missing}: {source}")
    if metadata.get("governance", {}).get("direct_execution_allowed") is not False:
        raise RuntimeError(f"source record permits direct execution: {source}")


def _assert_search_result(payload: dict[str, Any], expected_source_id: str) -> dict[str, Any]:
    for result in payload.get("results") or []:
        matched_items = result.get("matched_items") or []
        source_ids = {str(item.get("source_id")) for item in matched_items}
        if expected_source_id in source_ids:
            if not result.get("evidence_bundle_id") or not result.get("citations"):
                raise RuntimeError(f"search result lacks citation/evidence refs: {result}")
            return result
    raise RuntimeError(f"search readback did not return source_id={expected_source_id}: {payload}")


def _optional_bff_readback(evidence: dict[str, Any]) -> None:
    if not BFF_URL:
        evidence["checks"].append({"name": "bff.ops_readback", "status": "skipped", "reason": "BFF_URL not configured"})
        return
    headers = {"Authorization": os.getenv("BFF_AUTHORIZATION", "Bearer operator")}
    status, payload = _request_json("GET", f"{BFF_URL}/api/v1/operator/source/ops", headers=headers)
    if status != 200:
        raise RuntimeError(f"BFF source ops readback failed: {status} {payload}")
    status_search, payload_search = _request_json("GET", f"{BFF_URL}/api/v1/operator/search/ops", headers=headers)
    if status_search != 200:
        raise RuntimeError(f"BFF search ops readback failed: {status_search} {payload_search}")
    evidence["checks"].append(
        {
            "name": "bff.ops_readback",
            "status": "passed",
            "source_surface": payload.get("meta", {}).get("surfaces", {}).get("source_ops"),
            "search_surface": payload_search.get("meta", {}).get("surfaces", {}).get("search_ops"),
        }
    )


def _optional_openclaw_readback(query_text: str, evidence: dict[str, Any]) -> None:
    if not OPENCLAW_ADAPTER_URL:
        evidence["checks"].append(
            {"name": "openclaw_adapter.search_readback", "status": "skipped", "reason": "OPENCLAW_ADAPTER_URL not configured"}
        )
        return
    status, payload = _request_json(
        "POST",
        f"{OPENCLAW_ADAPTER_URL}/api/openclaw-adapter/search/query",
        headers={"X-Operator-Id": "source-search-smoke"},
        body={
            "request_id": f"openclaw-{TASK_ID.lower()}",
            "query": query_text,
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "source_types": [SOURCE_TYPE],
            "access_scopes": ACCESS_SCOPE,
            "license_scopes": [LICENSE_SCOPE],
            "top_k": 5,
        },
    )
    if status != 200 or not payload.get("results"):
        raise RuntimeError(f"OpenClaw SearchGateway readback failed: {status} {payload}")
    evidence["checks"].append({"name": "openclaw_adapter.search_readback", "status": "passed", "result_count": len(payload["results"])})


def run() -> dict[str, Any]:
    _require_feed_config()
    suffix = str(int(time.time()))
    connector_id = f"conn-source-search-live-{suffix}"
    trace_id = f"trace-source-search-live-{suffix}"
    query_token = os.getenv("SOURCE_SEARCH_LIVE_QUERY_TOKEN", f"source-search-live-{suffix}")
    evidence: dict[str, Any] = {
        "schema_version": "source_search_live_connector_smoke.v1",
        "task_id": TASK_ID,
        "started_at": _utc_now(),
        "mode": "credentialed_or_live_test",
        "feed": {
            "url_scheme": urllib.parse.urlparse(FEED_URL).scheme,
            "url_host": urllib.parse.urlparse(FEED_URL).netloc,
            "allowed_url_prefix_count": len(ALLOWLIST_PREFIXES),
            "secret_ref_id": SECRET_REF_ID,
        },
        "checks": [],
    }

    _wait_ready("source-ingest", SOURCE_INGEST_URL, evidence)
    _wait_ready("search", SEARCH_URL, evidence)

    status, configured = _request_json(
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/connectors",
        body={
            "connector": _connector_payload(connector_id),
            "fetch": {
                "mode": "external_feed",
                "url": FEED_URL,
                "allowed_url_prefixes": ALLOWLIST_PREFIXES,
                "timeout_seconds": float(os.getenv("SOURCE_SEARCH_LIVE_TIMEOUT_SECONDS", "10")),
                "max_bytes": int(os.getenv("SOURCE_SEARCH_LIVE_MAX_BYTES", "1000000")),
                "max_records": int(os.getenv("SOURCE_SEARCH_LIVE_MAX_RECORDS", "5")),
                "default_access_scope": ACCESS_SCOPE,
                "respect_robots_txt": os.getenv("SOURCE_SEARCH_LIVE_RESPECT_ROBOTS", "true").lower() not in {"0", "false", "no"},
            },
        },
    )
    if status != 201:
        raise RuntimeError(f"connector configuration failed: {status} {configured}")
    evidence["checks"].append({"name": "source.configure_connector", "status": "passed", "connector_id": connector_id})

    status, run_payload = _request_json(
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/jobs",
        body={"connector_id": connector_id, "trace_id": trace_id, "trigger_type": "live_connector_smoke"},
        timeout=30,
    )
    if status != 201 or run_payload.get("run", {}).get("status") != "completed":
        raise RuntimeError(f"ingest run failed: {status} {run_payload}")
    evidence_refs = run_payload.get("evidence_refs") or {}
    source_ids = list(evidence_refs.get("source_ids") or [])
    if not source_ids:
        raise RuntimeError(f"ingest run did not persist SourceRecord refs: {run_payload}")
    source_id = source_ids[0]
    evidence["checks"].append(
        {
            "name": "source.ingest",
            "status": "passed",
            "ingest_run_id": run_payload.get("run", {}).get("ingest_run_id"),
            "source_id": source_id,
            "evidence_bundle_id": evidence_refs.get("evidence_bundle_id"),
        }
    )

    status, source_readback = _request_json("GET", f"{SOURCE_INGEST_URL}/api/source-ingest/source-records/{source_id}")
    if status != 200:
        raise RuntimeError(f"source readback failed: {status} {source_readback}")
    _assert_source_record(source_readback.get("source_record") or {}, source_id)
    evidence["checks"].append({"name": "source.source_record_readback", "status": "passed", "source_id": source_id})

    status, bundle_readback = _request_json(
        "GET",
        f"{SOURCE_INGEST_URL}/api/source-ingest/evidence/bundles/{evidence_refs.get('evidence_bundle_id')}",
    )
    if status != 200:
        raise RuntimeError(f"EvidenceBundle readback failed: {status} {bundle_readback}")
    evidence["checks"].append({"name": "source.evidence_bundle_readback", "status": "passed"})

    status, refresh = _request_json(
        "POST",
        f"{SEARCH_URL}/api/search/index/refresh",
        body={"triggered_by": "source_search_live_connector_smoke", "trigger_ref": run_payload["run"]["ingest_run_id"]},
    )
    if status != 200 or (refresh.get("pipeline_snapshot") or {}).get("schema_version") != "index_pipeline_snapshot.v1":
        raise RuntimeError(f"search refresh failed: {status} {refresh}")
    evidence["checks"].append({"name": "search.index_refresh", "status": "passed"})

    query_text = os.getenv("SOURCE_SEARCH_LIVE_QUERY", query_token)
    status, query = _request_json(
        "POST",
        f"{SEARCH_URL}/api/search/query",
        body={
            "request_id": f"source-search-live-{suffix}",
            "trace_id": trace_id,
            "query": query_text,
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "source_types": [SOURCE_TYPE],
            "access_context": {
                "persona_id": "operator-workbench",
                "workspace_id": "research-workbench",
                "environment": "paper",
                "access_scopes": ACCESS_SCOPE,
                "license_scopes": [LICENSE_SCOPE],
            },
        },
    )
    if status != 200:
        raise RuntimeError(f"search query failed: {status} {query}")
    result = _assert_search_result(query, source_id)
    evidence["checks"].append(
        {
            "name": "search.gateway_query",
            "status": "passed",
            "result_id": result.get("result_id"),
            "evidence_bundle_id": result.get("evidence_bundle_id"),
            "citation_count": len(result.get("citations") or []),
        }
    )

    _optional_bff_readback(evidence)
    _optional_openclaw_readback(query_text, evidence)
    evidence["completed_at"] = _utc_now()
    evidence["status"] = "passed"
    return evidence


def main() -> int:
    try:
        evidence = run()
        _write_evidence(evidence)
        print(json.dumps({"status": "passed", "evidence_path": str(OUTPUT_PATH)}, sort_keys=True))
        return 0
    except SmokeDependencyMissing as exc:
        evidence = {
            "schema_version": "source_search_live_connector_smoke.v1",
            "task_id": TASK_ID,
            "status": "dependency_missing",
            "completed_at": _utc_now(),
            "reason": str(exc),
            "required_env": [
                "SOURCE_SEARCH_LIVE_FEED_URL",
                "SOURCE_SEARCH_LIVE_ALLOWED_URL_PREFIXES",
            ],
            "optional_env": [
                "SOURCE_SEARCH_LIVE_SECRET_REF_ID",
                "BFF_URL",
                "OPENCLAW_ADAPTER_URL",
            ],
        }
        _write_evidence(evidence)
        print(json.dumps({"status": "dependency_missing", "evidence_path": str(OUTPUT_PATH), "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        evidence = {
            "schema_version": "source_search_live_connector_smoke.v1",
            "task_id": TASK_ID,
            "status": "failed",
            "completed_at": _utc_now(),
            "reason": str(exc),
        }
        _write_evidence(evidence)
        print(json.dumps({"status": "failed", "evidence_path": str(OUTPUT_PATH), "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
