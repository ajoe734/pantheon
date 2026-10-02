"""Typed Research Commands port (ResearchCommandsPort).

Implements the command dispatch seam for the research orchestrator service,
enabling real action execution closure (cancel, retry, and cancellation fencing)
for research orchestrator runs in U10B
(see ``docs/operations/bff-upstream-v2-20260911/decisions/research-jobs.md`` §2–§4).

Mirrors the pattern established in ``ports/job_read.py``:
- standard urllib POST requests with JSON payloads
- base URL resolved from ``PANTHEON_RESEARCH_ORCHESTRATOR_API_URL``
- dependency injection via ``http_post`` callable for test isolation
- distinguished domain error types (Unavailable 503, Conflict 409, NotFound 404)
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

log = logging.getLogger(__name__)

_BASE_URL_ENVS: Tuple[str, ...] = (
    "PANTHEON_RESEARCH_ORCHESTRATOR_API_URL",
    "RESEARCH_ORCHESTRATOR_URL",
    "RESEARCH_ORCHESTRATOR_API_URL",
)


class ResearchCommandError(RuntimeError):
    """Base exception for research command failures."""

    def __init__(self, message: str, *, status_code: int = 500, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail or message


class ResearchCommandUnavailableError(ResearchCommandError):
    """Raised when research orchestrator service is unconfigured or unreachable (503)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=503)


class ResearchCommandNotFoundError(ResearchCommandError):
    """Raised when the target run or task does not exist (404)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=404)


class ResearchCommandConflictError(ResearchCommandError):
    """Raised when the action conflicts with run lifecycle state (409)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409)


def _resolve_orchestrator_base_url() -> Optional[str]:
    for env_name in _BASE_URL_ENVS:
        raw = os.getenv(env_name, "").strip()
        if raw:
            return raw.rstrip("/")
    return None


def _timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "3.0").strip()
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return 3.0


HttpPostFn = Callable[[str, Dict[str, Any], Optional[Dict[str, str]]], Tuple[int, Optional[Dict[str, Any]]]]


def _default_http_post(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """POST JSON ``payload`` to ``url`` and return ``(status_code, parsed_json_or_none)``."""
    req_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_timeout_seconds()) as resp:
            status_code = resp.status
            raw = resp.read()
            body = json.loads(raw.decode("utf-8")) if raw else None
            return status_code, body
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body = None
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            body = {"detail": raw.decode("utf-8", errors="replace")}
        return exc.code, body
    except Exception as exc:
        log.warning("Research command POST %s failed transport: %s", url, exc)
        return 503, {"detail": str(exc)}


@dataclass
class ResearchCommandsPort:
    """Port for dispatching operational commands to the research orchestrator service."""

    http_post: HttpPostFn = field(default=_default_http_post)
    base_url: Optional[str] = None

    def _get_base_url(self) -> str:
        url = self.base_url or _resolve_orchestrator_base_url()
        if not url:
            raise ResearchCommandUnavailableError(
                "Research orchestrator service is not configured (missing PANTHEON_RESEARCH_ORCHESTRATOR_API_URL)."
            )
        return url

    def cancel_run(
        self,
        run_id: str,
        *,
        reason: Optional[str] = None,
        actor_id: Optional[str] = None,
        canceled_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cancel an in-flight research orchestrator run and enforce cancellation fence."""
        clean_run_id = str(run_id or "").strip()
        if not clean_run_id:
            raise ValueError("run_id is required for cancel_run")

        base = self._get_base_url()
        url = f"{base}/api/research-orchestrator/runs/{clean_run_id}/cancel"
        payload = {
            "reason": reason or "Canceled via Management Job Action",
            "actor_id": actor_id or "operator",
            "canceled_at": canceled_at,
        }

        status_code, body = self.http_post(url, payload, None)
        if status_code == 404:
            detail = (body or {}).get("detail") or f"Research run '{clean_run_id}' not found."
            raise ResearchCommandNotFoundError(detail)
        if status_code == 409:
            detail = (body or {}).get("detail") or f"Research run '{clean_run_id}' cannot be canceled (state conflict)."
            raise ResearchCommandConflictError(detail)
        if status_code in (502, 503, 504):
            detail = (body or {}).get("detail") or "Research orchestrator service unreachable."
            raise ResearchCommandUnavailableError(detail)
        if status_code not in (200, 201, 202):
            detail = (body or {}).get("detail") or f"Research run cancel failed with HTTP {status_code}."
            raise ResearchCommandError(detail, status_code=status_code)

        return body or {}

    def retry_run(
        self,
        run_id: str,
        *,
        actor_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        requested_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retry a failed or canceled research orchestrator run under the same parent task."""
        clean_run_id = str(run_id or "").strip()
        if not clean_run_id:
            raise ValueError("run_id is required for retry_run")

        base = self._get_base_url()
        url = f"{base}/api/research-orchestrator/runs/{clean_run_id}/retry"
        payload = {
            "actor_id": actor_id or "operator",
            "idempotency_key": idempotency_key,
            "requested_at": requested_at,
        }

        status_code, body = self.http_post(url, payload, None)
        if status_code == 404:
            detail = (body or {}).get("detail") or f"Research run '{clean_run_id}' not found."
            raise ResearchCommandNotFoundError(detail)
        if status_code == 409:
            detail = (body or {}).get("detail") or f"Research run '{clean_run_id}' is not eligible for retry."
            raise ResearchCommandConflictError(detail)
        if status_code in (502, 503, 504):
            detail = (body or {}).get("detail") or "Research orchestrator service unreachable."
            raise ResearchCommandUnavailableError(detail)
        if status_code not in (200, 201, 202):
            detail = (body or {}).get("detail") or f"Research run retry failed with HTTP {status_code}."
            raise ResearchCommandError(detail, status_code=status_code)

        return body or {}


def create_research_commands_port(
    *,
    base_url: Optional[str] = None,
    http_post: Optional[HttpPostFn] = None,
) -> ResearchCommandsPort:
    """Factory creating a configured ResearchCommandsPort."""
    return ResearchCommandsPort(
        http_post=http_post or _default_http_post,
        base_url=base_url,
    )
