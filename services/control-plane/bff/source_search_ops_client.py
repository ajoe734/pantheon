"""Service client for BFF-owned source-ingestion and search operator command surfaces.

The BFF must not read source or search volumes directly.  This client talks to
the source-ingest service and the search service via their published HTTP APIs
only.  All write commands are idempotent at the BFF boundary.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


_SOURCE_INGEST_URL_ENVS = (
    "PANTHEON_SOURCE_INGEST_API_URL",
    "PANTHEON_SOURCE_INGEST_URL",
    "SOURCE_INGEST_URL",
)

_SEARCH_URL_ENVS = (
    "PANTHEON_SEARCH_API_URL",
    "PANTHEON_SEARCH_SERVICE_URL",
)


class SourceSearchOpsClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error_code: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.payload = payload or {}

    def to_surface(self) -> Dict[str, Any]:
        return {
            "status": "unavailable" if self.status_code in {0, 503, 504} else "degraded",
            "source": "service_client",
            "reason": self.error_code,
            "message": self.message,
            "http_status": self.status_code if self.status_code else None,
        }


def _base_url_from_env(env_names: tuple) -> Optional[str]:
    for name in env_names:
        raw = os.getenv(name, "").strip()
        if raw:
            return raw.rstrip("/")
    return None


def _timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "2.0").strip()
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return 2.0


def _safe_json(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return loaded if isinstance(loaded, dict) else {"data": loaded}


def _do_post(
    url: str,
    body: Dict[str, Any],
    *,
    timeout: float,
    idempotency_key: str,
) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Idempotency-Key": idempotency_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _safe_json(resp.read().decode("utf-8").strip())
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        detail = _safe_json(body_text).get("detail") or body_text or str(exc)
        raise SourceSearchOpsClientError(
            str(detail),
            status_code=exc.code,
            error_code=f"HTTP_{exc.code}",
            payload=_safe_json(body_text),
        ) from exc
    except OSError as exc:
        raise SourceSearchOpsClientError(
            str(exc),
            status_code=0,
            error_code="CONNECTION_ERROR",
        ) from exc


class SourceIngestCommandClient:
    """Thin client for source-ingest write surfaces consumed by the BFF."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        raw = base_url if base_url is not None else _base_url_from_env(_SOURCE_INGEST_URL_ENVS)
        self._base_url = raw.rstrip("/") if raw else ""
        self._timeout = timeout_seconds if timeout_seconds is not None else _timeout_seconds()

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    def replay_dlq(
        self,
        *,
        entry_ids: Optional[List[str]] = None,
        tag: str = "retry_exhausted",
        reason: str = "operator-approved BFF DLQ replay",
        actor_id: str = "bff-operator",
        idempotency_key: str,
    ) -> Dict[str, Any]:
        if not self._base_url:
            raise SourceSearchOpsClientError(
                "Source ingest service URL is not configured",
                status_code=503,
                error_code="SERVICE_NOT_CONFIGURED",
            )
        url = f"{self._base_url}/api/source-ingest/dlq/replay"
        body: Dict[str, Any] = {
            "tag": tag,
            "reason": reason,
            "actor_id": actor_id,
        }
        if entry_ids is not None:
            body["entry_ids"] = entry_ids
        return _do_post(url, body, timeout=self._timeout, idempotency_key=idempotency_key)

    def replay_frontier(
        self,
        *,
        frontier_id: str,
        trace_id: Optional[str] = None,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        if not self._base_url:
            raise SourceSearchOpsClientError(
                "Source ingest service URL is not configured",
                status_code=503,
                error_code="SERVICE_NOT_CONFIGURED",
            )
        url = f"{self._base_url}/api/source-ingest/frontier/{urllib.parse.quote(frontier_id, safe='')}/replay"
        body: Dict[str, Any] = {}
        if trace_id:
            body["trace_id"] = trace_id
        return _do_post(url, body, timeout=self._timeout, idempotency_key=idempotency_key)


class SearchIndexCommandClient:
    """Thin client for search-index write surfaces consumed by the BFF."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        raw = base_url if base_url is not None else _base_url_from_env(_SEARCH_URL_ENVS)
        self._base_url = raw.rstrip("/") if raw else ""
        self._timeout = timeout_seconds if timeout_seconds is not None else _timeout_seconds()

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    def trigger_refresh(
        self,
        *,
        triggered_by: str = "bff-operator",
        trigger_ref: Optional[str] = None,
        force_full: bool = False,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        if not self._base_url:
            raise SourceSearchOpsClientError(
                "Search service URL is not configured",
                status_code=503,
                error_code="SERVICE_NOT_CONFIGURED",
            )
        url = f"{self._base_url}/api/search/index/refresh"
        body: Dict[str, Any] = {
            "triggered_by": triggered_by,
            "force_full": force_full,
        }
        if trigger_ref:
            body["trigger_ref"] = trigger_ref
        return _do_post(url, body, timeout=self._timeout, idempotency_key=idempotency_key)

    def trigger_materialize(
        self,
        *,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        if not self._base_url:
            raise SourceSearchOpsClientError(
                "Search service URL is not configured",
                status_code=503,
                error_code="SERVICE_NOT_CONFIGURED",
            )
        url = f"{self._base_url}/api/search/index/materialize"
        return _do_post(url, {}, timeout=self._timeout, idempotency_key=idempotency_key)
