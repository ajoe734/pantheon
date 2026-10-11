"""Authenticated read of the telemetry-owned runtime-summary projection."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class TelemetryError(RuntimeError):
    """Telemetry runtime summaries could not be read."""


class TelemetryAuthError(TelemetryError):
    """Telemetry rejected the service credential or tenant (HTTP 401/403)."""


class TelemetryUnavailable(TelemetryError):
    """Telemetry was unreachable or returned an unusable response."""


_RETRYABLE_HTTP_STATUSES = {408, 425, 429}


def _telemetry_response_body(raw_body: bytes | None) -> tuple[dict[str, Any] | None, str | None]:
    if not raw_body:
        return None, None
    try:
        decoded = json.loads(raw_body.decode("utf-8"))
        return (decoded, None) if isinstance(decoded, dict) else (None, "telemetry response must be a JSON object")
    except Exception as exc:  # noqa: BLE001
        return None, f"telemetry response was not valid JSON: {exc}"


def fetch_runtime_summaries(
    telemetry_url: str,
    *,
    tenant_id: str | None = None,
    service_token: str | None = None,
    timeout_seconds: float = 10.0,
) -> list[dict[str, Any]]:
    if not telemetry_url:
        raise TelemetryUnavailable("PANTHEON_TELEMETRY_API_URL is required")
    if service_token is None:
        service_token = os.getenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", "")
    token = service_token.strip()
    tenant = (tenant_id or os.getenv("PANTHEON_TENANT_ID") or "default").strip()
    headers = {"Accept": "application/json", "X-Tenant-Id": tenant}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        telemetry_url.rstrip("/") + "/api/telemetry/runtime-summaries",
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_type = TelemetryAuthError if exc.code in (401, 403) else TelemetryUnavailable
        raise error_type(f"telemetry runtime summaries rejected: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise TelemetryUnavailable(f"telemetry service unavailable: {exc}") from exc

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise TelemetryUnavailable("telemetry runtime summaries returned invalid JSON") from exc
    if isinstance(payload, dict):
        payload = payload.get("summaries") or payload.get("items") or []
    if not isinstance(payload, list):
        raise TelemetryUnavailable("telemetry runtime summaries must be a list")
    return [item for item in payload if isinstance(item, dict)]


def append_lifecycle_event(
    telemetry_url: str,
    event: dict[str, Any],
    *,
    tenant_id: str | None = None,
    service_token: str | None = None,
    timeout_seconds: float = 5.0,
    urlopen: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    """Append a lifecycle event to telemetry with authenticated service identity."""
    if not telemetry_url:
        return {
            "status": "retryable_error",
            "terminal": False,
            "retryable": True,
            "outcome": "ambiguous",
            "http_status": None,
            "response": None,
            "error": "PANTHEON_TELEMETRY_API_URL is required",
        }
    if timeout_seconds <= 0:
        return {
            "status": "retryable_error",
            "terminal": False,
            "retryable": True,
            "outcome": "ambiguous",
            "http_status": 504,
            "response": None,
            "error": "scheduled reconciliation SLA budget exhausted",
        }
    if service_token is None:
        service_token = os.getenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", "")
    token = service_token.strip()
    tenant = (
        tenant_id
        or str(event.get("tenant_id") or "").strip()
        or os.getenv("PANTHEON_TENANT_ID")
        or "default"
    ).strip()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Tenant-Id": tenant,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = telemetry_url.rstrip("/") + "/api/telemetry/ingest"
    request = urllib.request.Request(
        url,
        data=json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            response_status = getattr(response, "status", None)
            http_status = int(response_status if response_status is not None else response.getcode())
            raw_body = response.read()
    except urllib.error.HTTPError as exc:
        raw_body = exc.read()
        response_body, parse_error = _telemetry_response_body(raw_body)
        http_status = int(exc.code)
        retryable = http_status >= 500 or http_status in _RETRYABLE_HTTP_STATUSES
        return {
            "status": "retryable_error" if retryable else "terminal_rejected",
            "terminal": not retryable,
            "retryable": retryable,
            "outcome": "failed",
            "http_status": http_status,
            "response": response_body,
            "error": parse_error or f"telemetry ingest returned HTTP {http_status}",
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "status": "retryable_error",
            "terminal": False,
            "retryable": True,
            "outcome": "ambiguous",
            "http_status": None,
            "response": None,
            "error": str(getattr(exc, "reason", exc)),
        }

    response_body, parse_error = _telemetry_response_body(raw_body)
    if http_status == 202 and response_body is not None and response_body.get("status") == "accepted":
        return {
            "status": "accepted",
            "terminal": True,
            "retryable": False,
            "outcome": "accepted",
            "http_status": http_status,
            "response": response_body,
            "error": None,
        }
    return {
        "status": "retryable_error",
        "terminal": False,
        "retryable": True,
        "outcome": "ambiguous",
        "http_status": http_status,
        "response": response_body,
        "error": parse_error or "telemetry ingest did not return terminal accepted status",
    }


def _event_get(url: str, tenant_id: str | None, service_token: str | None, timeout: float) -> tuple[int, dict[str, Any] | None]:
    token = (service_token if service_token is not None else os.getenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", "")).strip()
    tenant = (tenant_id or os.getenv("PANTHEON_TENANT_ID") or "").strip()
    if not tenant: raise TelemetryUnavailable("tenant_id is required")
    headers = {"Accept": "application/json", "X-Tenant-Id": tenant, **({"Authorization": f"Bearer {token}"} if token else {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method="GET"), timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return int(getattr(resp, "status", resp.getcode())), json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        try: payload = json.loads(exc.read().decode("utf-8"))
        except Exception: payload = None
        return int(exc.code), payload
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TelemetryUnavailable(str(exc)) from exc


def fetch_accepted_event(telemetry_url: str, event_id: str, *, tenant_id: str | None = None, service_token: str | None = None, timeout_seconds: float = 5.0) -> dict[str, Any] | None:
    if not telemetry_url or not event_id: raise TelemetryUnavailable("PANTHEON_TELEMETRY_API_URL and event_id required")
    status, body = _event_get(f"{telemetry_url.rstrip('/')}/api/telemetry/events/{urllib.parse.quote(event_id.strip())}", tenant_id, service_token, timeout_seconds)
    if status == 200: return body
    if status == 404: return None
    raise (TelemetryAuthError if status in (401, 403) else TelemetryUnavailable)(f"HTTP {status}")


def verify_durable_event_order(telemetry_url: str, *, accepted_event_id: str, observed_event_id: str, tenant_id: str | None = None, service_token: str | None = None, timeout_seconds: float = 5.0, expected_binding_id: str | None = None, expected_runtime_id: str | None = None, expected_artifact_id: str | None = None, expected_artifact_version: str | None = None) -> tuple[bool, str | None, dict[str, Any]]:
    if not telemetry_url or not accepted_event_id or not observed_event_id: return False, "missing_parameter", {}
    params = {"observed_event_id": observed_event_id.strip(), **{k: v.strip() for k, v in [("binding_id", expected_binding_id), ("runtime_id", expected_runtime_id), ("artifact_id", expected_artifact_id), ("artifact_version", expected_artifact_version)] if v and v.strip()}}
    status, body = _event_get(f"{telemetry_url.rstrip('/')}/api/telemetry/events/{urllib.parse.quote(accepted_event_id.strip())}?{urllib.parse.urlencode(params)}", tenant_id, service_token, timeout_seconds)
    if status == 200 and isinstance(body, dict) and body.get("status") == "verified": return True, None, body.get("pair") or {}
    if status == 404: return False, "event_not_found", {}
    if status == 409: return False, (body or {}).get("error", {}).get("reason") or "conflict", {}
    raise (TelemetryAuthError if status in (401, 403) else TelemetryUnavailable)(f"HTTP {status}")
