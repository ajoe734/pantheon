#!/usr/bin/env python3
"""Smoke production posture for source-ingest and search services."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Any


SOURCE_INGEST_URL = os.getenv("SOURCE_INGEST_URL", "http://127.0.0.1:8097").rstrip("/")
SEARCH_URL = os.getenv("SEARCH_URL", "http://127.0.0.1:8098").rstrip("/")


def _request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload) if payload else {}


def _posture_from_health(payload: dict[str, Any]) -> dict[str, Any]:
    if "source_search_posture" in payload:
        return dict(payload["source_search_posture"])
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    return dict(details.get("source_search_posture") or {})


def _assert_service(name: str, base_url: str, expected_backends: dict[str, str]) -> None:
    ready = _request_json(f"{base_url}/readyz")
    metrics = _request_json(f"{base_url}/metrics")
    health = _request_json(f"{base_url}/health")

    posture = _posture_from_health(health)
    if ready.get("status") != "ok":
        raise RuntimeError(f"{name} readyz not ok: {ready}")
    if posture.get("status") != "ok" or posture.get("enforced") is not True:
        raise RuntimeError(f"{name} posture is not enforced and ok: {posture}")
    if posture.get("object_store_configured") is not True:
        raise RuntimeError(f"{name} object-store posture is not configured: {posture}")
    if metrics.get("metrics", {}).get("posture_alert_count") != 0:
        raise RuntimeError(f"{name} reports posture alerts: {metrics}")
    backends = posture.get("backends") if isinstance(posture.get("backends"), dict) else {}
    for key, value in expected_backends.items():
        if backends.get(key) != value:
            raise RuntimeError(f"{name} backend {key} expected {value}, got {backends.get(key)}")
    print(f"ok  {name} production posture enforced with {', '.join(sorted(expected_backends))}")


def main() -> int:
    _assert_service(
        "source-ingest",
        SOURCE_INGEST_URL,
        {"SOURCE_INGEST_EVIDENCE_BACKEND": "postgres"},
    )
    _assert_service(
        "search-svc",
        SEARCH_URL,
        {"SEARCH_INDEX_STORE_BACKEND": "postgres", "SEARCH_EVIDENCE_BACKEND": "postgres"},
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
